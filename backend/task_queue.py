"""One execution slot, a finite waiting queue and an independent expiry clock."""
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
import threading
import time


class AdmissionError(ValueError):
    def __init__(self, code):
        self.code = code
        self.status_code = 429 if code == 'QUEUE_FULL' else 503
        super().__init__('任务队列已满，请稍后使用原request_id重试' if code == 'QUEUE_FULL' else '服务正在关闭，请稍后重试')


@dataclass
class _Item:
    fn: object
    reject: object
    deadline: float
    future: Future


class TaskQueue:
    def __init__(self, policy):
        self.policy = policy
        self.stop = threading.Event()
        self.condition = threading.Condition()
        self.waiting = deque()
        self.running = False
        self.closing = False
        self.worker = threading.Thread(target=self._work, name='mission-dispatch', daemon=True)
        self.clock = threading.Thread(target=self._expire, name='mission-queue-clock', daemon=True)
        self.worker.start()
        self.clock.start()

    def check_capacity(self):
        with self.condition:
            if self.closing:
                raise AdmissionError('SERVICE_STOPPING')
            # Before the dispatcher takes the head, that head reserves the run slot.
            if len(self.waiting) >= self.policy.queue_capacity + (not self.running):
                raise AdmissionError('QUEUE_FULL')

    def submit(self, fn, reject):
        with self.condition:
            self.check_capacity()
            future = Future()
            self.waiting.append(_Item(fn, reject, time.monotonic() + self.policy.queue_wait_seconds, future))
            self.condition.notify_all()
            return future

    @staticmethod
    def _finish(item, fn):
        try:
            fn()
        except BaseException as exc:
            item.future.set_exception(exc)
        else:
            item.future.set_result(None)

    def discard(self, future):
        """Remove this accepted but cancelled waiting item without invoking its work."""
        with self.condition:
            item = next((item for item in self.waiting if item.future is future), None)
            if item is None:
                return False
            self.waiting.remove(item)
            self.condition.notify_all()
        item.future.set_result(None)
        return True

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.waiting or self.closing)
                if self.closing and not self.waiting:
                    return
                item = self.waiting.popleft()
                self.running = True
            if time.monotonic() >= item.deadline:
                self._finish(item, lambda: item.reject('QUEUE_TIMEOUT'))
            else:
                self._finish(item, item.fn)
            with self.condition:
                self.running = False
                self.condition.notify_all()

    def _expire(self):
        while True:
            with self.condition:
                if self.closing:
                    return
                now = time.monotonic()
                expired = [item for item in self.waiting if item.deadline <= now]
                self.waiting = deque(item for item in self.waiting if item.deadline > now)
                timeout = max(0.01, min((item.deadline - now for item in self.waiting), default=1.0))
                if not expired:
                    self.condition.wait(timeout)
                    continue
            for item in expired:
                self._finish(item, lambda item=item: item.reject('QUEUE_TIMEOUT'))

    def begin_shutdown(self):
        with self.condition:
            self.closing = True
            self.stop.set()
            pending = list(self.waiting)
            self.waiting.clear()
            self.condition.notify_all()
        for item in pending:
            self._finish(item, lambda item=item: item.reject('SERVICE_SHUTDOWN'))

    def join(self, timeout):
        deadline = time.monotonic() + timeout
        for thread in (self.worker, self.clock):
            thread.join(max(0, deadline - time.monotonic()))
        if self.worker.is_alive() or self.clock.is_alive():
            raise RuntimeError('执行资源未在关闭预算内退出；保留写入所有权，需检查后端')

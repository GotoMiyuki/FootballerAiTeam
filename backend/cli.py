"""Blocking CLI adapter over the existing task application and durable runtime."""
from backend.service import MissionService


class CLIMissionService(MissionService):
    def __init__(self, *args, **kwargs):
        self.pending = None
        super().__init__(*args, **kwargs)

    def _enqueue(self, mission_id, fn, *args, **kwargs):
        self.pending = super()._enqueue(mission_id, fn, *args, **kwargs)
        return self.pending

    def wait(self):
        if self.pending:
            self.pending.result()

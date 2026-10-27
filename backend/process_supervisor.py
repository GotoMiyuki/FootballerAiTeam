"""Windows-owned process trees with kill-on-owner-exit and bounded cleanup.

Used by MissionService's M4.3 execution runner and isolated fault fixtures.
Children must call await_start() before opening a database or running work.
The job handle is private to its owner, and only this owner's Popen is managed.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys


class ProcessIsolationError(RuntimeError):
    pass


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IOCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class WindowsJob:
    def __init__(self):
        if os.name != 'nt':
            raise ProcessIsolationError('This execution isolation backend supports Windows only')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ProcessIsolationError(f'Cannot isolate own worker: Windows error {ctypes.get_last_error()}')

    def terminate(self):
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


def python_module_command(module, *arguments, root=None, environ=None):
    """Avoid Windows venv launcher PID indirection; retain this venv's imports."""
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    environment = dict(os.environ if environ is None else environ)
    paths = [str(root)]
    if sys.prefix != sys.base_prefix:
        paths.append(str(Path(sys.prefix) / 'Lib/site-packages'))
    if environment.get('PYTHONPATH'):
        paths.append(environment['PYTHONPATH'])
    environment['PYTHONPATH'] = os.pathsep.join(paths)
    environment['PYTHONUTF8'] = '1'
    command = [getattr(sys, '_base_executable', sys.executable), '-m', module, *map(str, arguments)]
    return command, environment


def await_start():
    """Bootstrap gate: EOF/malformed control input fails before business work."""
    raw = sys.stdin.buffer.readline(1025)
    if len(raw) > 1024 or json.loads(raw or b'null') != {'control': 'start', 'version': 1}:
        raise ProcessIsolationError('Worker was not admitted by its owner')


class OwnedProcess:
    def __init__(self, module, *arguments, root=None, environ=None, output=None, error_output=None, cleanup_seconds=3):
        if isinstance(cleanup_seconds, bool) or not isinstance(cleanup_seconds, (int, float)) or not 0 < cleanup_seconds <= 86400:
            raise ValueError('Cleanup seconds must be in (0, 86400]')
        self.cleanup_seconds = cleanup_seconds
        self.process = None
        self.job = WindowsJob()
        command, environment = python_module_command(module, *arguments, root=root, environ=environ)
        self.command = command
        try:
            self.process = subprocess.Popen(command, cwd=root, env=environment,
                stdin=subprocess.PIPE, stdout=output if output is not None else subprocess.DEVNULL,
                stderr=error_output if error_output is not None else subprocess.STDOUT,
                close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.job.attach(self.process)
            self.process.stdin.write(b'{"control":"start","version":1}\n')
            self.process.stdin.flush()
        except BaseException:
            if self.process is not None:
                self.process.kill()  # only our handle, before bootstrap admission
                self.process.wait(timeout=self.cleanup_seconds)
                self.process.stdin.close()
            self.job.close()
            raise

    @property
    def pid(self):
        return self.process.pid

    def wait(self, timeout):
        return self.process.wait(timeout=timeout)

    def close(self):
        try:
            # Close also removes descendants after an otherwise normal parent exit.
            self.job.close()
            if self.process is not None:
                self.process.wait(timeout=self.cleanup_seconds)
        finally:
            if self.process is not None and self.process.stdin:
                self.process.stdin.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

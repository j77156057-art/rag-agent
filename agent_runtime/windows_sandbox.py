"""Small Windows Job Object wrapper for generated adapter processes.

The adapter runtime must fail closed when Windows cannot provide the requested
process boundary. Non-Windows callers get a bounded subprocess fallback so the
contract remains testable in CI.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
from typing import Any, Mapping


class SandboxUnavailable(RuntimeError):
    pass


JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTime", ctypes.c_longlong),
                ("PerJobUserTime", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_ulong),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_ulong),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_ulong),
                ("SchedulingClass", ctypes.c_ulong)]


class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _win32_error(prefix: str) -> SandboxUnavailable:
    code = ctypes.get_last_error() or 0
    return SandboxUnavailable("%s（Win32=%s）" % (prefix, code))


def _create_job(memory_bytes: int):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetInformationJobObject.restype = ctypes.wintypes.BOOL if hasattr(ctypes, "wintypes") else ctypes.c_int
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise _win32_error("无法创建适配器 Job Object")
    limits = _EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_PROCESS_MEMORY |
        JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION |
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    # The Windows Python launcher may create one short-lived runtime child;
    # allow a small fixed process tree while still preventing fan-out.
    limits.BasicLimitInformation.ActiveProcessLimit = 4
    limits.ProcessMemoryLimit = int(memory_bytes)
    limits.JobMemoryLimit = int(memory_bytes)
    ok = kernel32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                          ctypes.byref(limits), ctypes.sizeof(limits))
    if not ok:
        kernel32.CloseHandle(handle)
        raise _win32_error("无法设置适配器 Job Object 限制")
    return kernel32, handle


def run_isolated(command: list[str], *, cwd: str, env: Mapping[str, str], timeout: int,
                 memory_bytes: int = 512 * 1024 * 1024):
    """Run one process under a bounded Job Object and return CompletedProcess."""
    if os.name != "nt":
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env=dict(env))
    kernel32, job = _create_job(memory_bytes)
    process = None
    try:
        # CPython does not expose the primary thread handle needed to resume a
        # CREATE_SUSPENDED process. Start immediately, then assign the process
        # to the Job Object before any adapter output is consumed; failures are
        # terminated and never fall back to an unbounded run.
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=dict(env),
                                   creationflags=CREATE_NO_WINDOW)
        process_handle = ctypes.c_void_p(int(getattr(process, "_handle", 0)))
        if not process_handle.value or not kernel32.AssignProcessToJobObject(job, process_handle):
            raise _win32_error("无法将适配器加入 Job Object")
        try:
            stdout, stderr = process.communicate(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            kernel32.TerminateJobObject(job, 124)
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if process is not None and process.poll() is None:
            try:
                kernel32.TerminateJobObject(job, 125)
            except Exception:
                pass
            try:
                process.kill()
            except Exception:
                pass
        kernel32.CloseHandle(job)


def spawn_isolated(command: list[str], *, cwd: str, env: Mapping[str, str],
                   memory_bytes: int = 1024 * 1024 * 1024):
    """Start a command under a bounded Job Object for background supervision.

    Returns ``(popen, kernel32, job_handle)`` so the caller can terminate the
    whole process tree later. Non-Windows callers get a session-led subprocess
    with ``(popen, None, None)`` so the same supervision code stays testable.
    """
    if os.name != "nt":
        popen = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=False, env=dict(env), start_new_session=True)
        return popen, None, None
    kernel32, job = _create_job(memory_bytes)
    popen = None
    try:
        popen = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=False, env=dict(env),
                                 creationflags=CREATE_NO_WINDOW)
        process_handle = ctypes.c_void_p(int(getattr(popen, "_handle", 0)))
        if not process_handle.value or not kernel32.AssignProcessToJobObject(job, process_handle):
            raise _win32_error("无法将进程加入 Job Object")
        return popen, kernel32, job
    except BaseException:
        if popen is not None and popen.poll() is None:
            try:
                popen.kill()
            except Exception:
                pass
        kernel32.CloseHandle(job)
        raise


def terminate_isolated(kernel32, job_handle, *, exit_code: int = 124) -> None:
    """Kill every process still attached to a Job Object."""
    if kernel32 is None or job_handle is None:
        return
    try:
        kernel32.TerminateJobObject(job_handle, int(exit_code))
    finally:
        try:
            kernel32.CloseHandle(job_handle)
        except Exception:
            pass


__all__ = ["SandboxUnavailable", "run_isolated", "spawn_isolated", "terminate_isolated"]

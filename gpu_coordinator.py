"""单机 GPU 资源协调：避免 Ollama/ComfyUI/导出等重任务并发抢占显存。

serial 模式下提供 FIFO 租约队列：后来的任务排队等待，释放时按入队顺序转交；
支持租约 TTL（持有者崩溃时自动回收）与显存余量门槛（不足直接拒绝，不排队）。
"""
import collections
import os
import subprocess
import threading
import time

GPU_MODE = os.getenv("DOCMIND_GPU_MODE", "serial").lower()  # serial|parallel
DEFAULT_TTL = float(os.getenv("DOCMIND_GPU_LEASE_TTL", "0") or 0)  # 秒，0=不限

_lock = threading.Lock()
_cv = threading.Condition(_lock)
_held_owner = None
_held_purpose = ""
_held_since = None
_held_ttl = 0.0
_waiters = collections.deque()  # (owner, purpose, event)


def memory_info():
    """读取 nvidia-smi 显存；无 NVIDIA 或命令不可用时返回 None。"""
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=2)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        used, total = [int(x.strip()) for x in p.stdout.splitlines()[0].split(',')[:2]]
        return {"used_mb": used, "total_mb": total,
                "free_mb": max(0, total - used),
                "utilization": used / total if total else 0}
    except Exception:
        return None


def _expire_locked(now):
    """TTL 到期的持有者自动回收，并尝试把租约转交队首等待者。返回是否发生了回收。"""
    global _held_owner, _held_purpose, _held_since, _held_ttl
    if not (_held_owner and _held_ttl and _held_since and now - _held_since > _held_ttl):
        return False
    _handover_locked()
    return True


def _handover_locked():
    """释放当前持有者并把租约交给队首等待者；队列为空则真正空闲。调用方须持 _cv。"""
    global _held_owner, _held_purpose, _held_since, _held_ttl
    while _waiters:
        owner, purpose, event = _waiters.popleft()
        if not event.is_set():
            _held_owner, _held_purpose = owner, purpose
            _held_since, _held_ttl = time.time(), DEFAULT_TTL
            event.set()
            return
    _held_owner, _held_purpose, _held_since, _held_ttl = None, "", None, 0.0


def status():
    mem = memory_info()
    with _cv:
        _expire_locked(time.time())
        held_for = round(time.time() - _held_since, 1) if _held_since else None
        return {
            "mode": GPU_MODE,
            "active": _held_owner,
            "purpose": _held_purpose,
            "held_for_seconds": held_for,
            "lease_ttl": _held_ttl,
            "queue": [{"owner": o, "purpose": pu} for o, pu, _ in _waiters],
            "queue_length": len(_waiters),
            "ollama_keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "0"),
            "memory": mem,
        }


def acquire(owner, timeout=2, purpose="", ttl=None):
    """申请 GPU 租约。成功返回 True；超时/显存不足返回 False。

    - 同一 owner 重复申请直接放行（与旧版行为一致，调用方仍须配对 release）。
    - timeout 内排队等待，FIFO 转交。
    - ttl 秒后租约可被自动回收（防持有者崩溃死锁）；None 取 DOCMIND_GPU_LEASE_TTL。
    """
    global _held_owner, _held_purpose, _held_since, _held_ttl
    if GPU_MODE == "parallel":
        return True
    owner = str(owner)
    timeout = max(0.0, float(timeout))
    lease_ttl = DEFAULT_TTL if ttl is None else max(0.0, float(ttl))

    threshold = int(os.getenv("DOCMIND_GPU_MIN_FREE_MB", "0") or 0)
    mem = memory_info()
    if threshold and mem and mem["free_mb"] < threshold:
        return False

    event = threading.Event()
    with _cv:
        _expire_locked(time.time())
        if _held_owner is None:
            _held_owner, _held_purpose = owner, str(purpose)
            _held_since, _held_ttl = time.time(), lease_ttl
            return True
        if _held_owner == owner:
            return True
        if timeout <= 0:
            return False
        _waiters.append((owner, str(purpose), event))

    # 小步等待：TTL 过期是惰性检测的，等待者需周期性醒来触发回收与转交。
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if event.wait(min(0.1, remaining)):
            break
        with _cv:
            _expire_locked(time.time())
        if event.is_set():
            break
    if not event.is_set():
        with _cv:
            for item in _waiters:
                if item[2] is event:
                    _waiters.remove(item)
                    break
        return False
    with _cv:
        return _held_owner == owner


def release(owner):
    """归还租约并唤醒队首。owner 不匹配（含已 TTL 转交）时静默忽略。"""
    if GPU_MODE == "parallel":
        return
    with _cv:
        if _held_owner != str(owner):
            return
        _handover_locked()
        _cv.notify_all()


def force_release():
    """强制回收当前租约（管理操作）。返回被回收的 owner 或 None。"""
    with _cv:
        prev = _held_owner
        if prev is not None:
            _handover_locked()
            _cv.notify_all()
        return prev

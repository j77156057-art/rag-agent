"""单机 GPU 资源协调：避免 Ollama/ComfyUI/导出等重任务并发抢占显存。

三种模式（``DOCMIND_GPU_MODE``）：

- ``serial``（默认）：全机一把 FIFO 租约锁，后来者排队，释放按入队顺序转交；
  支持租约 TTL（持有者崩溃自动回收）与显存余量门槛；
- ``parallel``：完全不协调（信任调用方自行管理），采样/空闲回收仍可用；
- ``multi``：每张 GPU 一条独立租约，``acquire_lease`` 按显存余量挑卡，
  租约结果里带 ``gpu`` 序号——**调用方必须自己在启动子进程时用该序号
  （CUDA_VISIBLE_DEVICES）才会生效**，协调器本身不做任何 CUDA 隔离。

其余能力：排队等待可取消（``cancel_wait``）；显存探测可注入（测试用
``set_gpu_probe``）；后台线程做显存采样、TTL 惰性回收与 Ollama 空闲卸载；
显存竞争时调用方可指名触发"驱逐钩子"（如把 Ollama 驻留模型 keep_alive=0）。

硬件验收状态（截至 2026-09-14，提交 ``82d092c``）：本机为**单卡**
RTX 5070 Ti Laptop。serial 全部能力、真实 nvidia-smi 采样、Ollama 双模型
空闲卸载均已真机验证；multi 调度仅由 ``set_gpu_probe`` 假双卡单测覆盖，
**物理多卡调度与 CUDA 设备隔离均未实测，禁止在任何 UI/文档中宣称已实现**。
多卡到位后的逐项验收步骤（含负对照）见 HANDOFF.md 第 5 节 P2-1
"待验收项 A/B"。
"""
import collections
import os
import subprocess
import threading
import time
import json
from pathlib import Path

from config import STATE_ROOT, state_path

GPU_MODE = os.getenv("DOCMIND_GPU_MODE", "serial").lower()  # serial|parallel|multi
DEFAULT_TTL = float(os.getenv("DOCMIND_GPU_LEASE_TTL", "0") or 0)  # 秒，0=不限


def _env_index():
    """DOCMIND_GPU_INDEX 指定的本机首选卡（无多卡协调时的单卡选择）。"""
    try:
        return int(os.getenv("DOCMIND_GPU_INDEX", "0") or 0)
    except ValueError:
        return 0


POLL_INTERVAL = float(os.getenv("DOCMIND_GPU_POLL_INTERVAL", "5") or 5)  # 显存采样秒
SAMPLE_MAX = 240  # 240 个采样点 ×5s ≈ 20 分钟迷你曲线
IDLE_UNLOAD_DEFAULT = float(os.getenv("DOCMIND_OLLAMA_IDLE_UNLOAD", "0") or 0)  # 秒，0=关闭
IDLE_UNLOAD_COOLDOWN = 60.0
# 真实 nvidia-smi 探测的短 TTL 缓存：status() 轮询 + 后台 pump + acquire/release
# 会在同一瞬间扎堆调用，一次 nvidia-smi 子进程 50~200ms，0.5s 内复用结果即可。
PROBE_CACHE_TTL = float(os.getenv("DOCMIND_GPU_PROBE_CACHE_TTL", "0.5") or 0.5)

_lock = threading.Lock()
_cv = threading.Condition(_lock)

# gpu_key -> {"owner","purpose","since","ttl","exclusive"}；serial 只有一个键 0。
# exclusive=True（默认）为**硬租约**，互相独占；exclusive=False 为软租约，见 _soft_leases。
_leases: dict = {}
# 软租约登记：owner -> {"owner","purpose","since","ttl","gpu","exclusive":False}。
# 软租约**只登记、不占 _leases 槽位**：不参与互斥判定、不被任何人阻挡，也不阻挡任何人，
# 仅供 status() 展示与 TTL / 显式 release 回收（引擎“多开”所需）。
_soft_leases: dict = {}
_waiters = collections.deque()  # token: {owner,purpose,event,gpu,min_free,granted,canceled}
_activities = {}                # owner -> 最近活动时间戳（Ollama 空闲判定）
_hooks = {}                     # 名字 -> 无参可调用（驱逐钩子，在锁外执行）
_samples = collections.deque(maxlen=SAMPLE_MAX)

_idle_unload = IDLE_UNLOAD_DEFAULT
_poll_interval = POLL_INTERVAL
_last_idle_unload = 0.0
_bg_thread = None
_bg_stop = threading.Event()

# 测试注入点：fn() -> [gpu 行] 或 None
_probe_override = None
# 仅缓存真实 nvidia-smi 探测；注入探测（测试）永不缓存，保证假数据即时生效
_probe_cache = {"t": 0.0, "rows": None}
_processes = {}  # pid -> owner/gpu/purpose/status/heartbeat
_recovery_events = collections.deque(maxlen=200)
_process_probe_cache = {"t": 0.0, "rows": None, "available": False}
PROCESS_HEARTBEAT_TTL = float(os.getenv("DOCMIND_GPU_PROCESS_HEARTBEAT_TTL", "30") or 30)
STATE_FILE = Path(state_path("DOCMIND_GPU_STATE_FILE", str(Path(STATE_ROOT) / ".docmind" / "gpu_state.json")))
# 采样曲线单独落一份文件：后台采样线程（默认 5s）此前会把 {samples,...} 覆盖写到
# STATE_FILE，冲掉租约快照 {leases,queue}，导致 restore_runtime_state() 静默失效。
SAMPLES_FILE = Path(state_path("DOCMIND_GPU_SAMPLES_FILE", str(Path(STATE_ROOT) / ".docmind" / "gpu_samples.json")))
def _persist_runtime_locked():
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        queue=[{'owner': t['owner'], 'purpose': t['purpose'], 'gpu': t['gpu'], 'min_free': t['min_free'], 'status': 'waiting'} for t in _waiters if not t.get('canceled')]
        STATE_FILE.write_text(json.dumps({'leases': {str(k): v for k,v in _leases.items()}, 'queue': queue, 'updated_at': time.time()}, ensure_ascii=False), encoding='utf-8')
    except Exception: pass

def restore_runtime_state():
    """恢复上次运行的状态；旧租约不可安全复用，转为 recovered 事件。"""
    try:
        data=json.loads(STATE_FILE.read_text(encoding='utf-8'))
        leases=data.get('leases') or {}
        queue=data.get('queue') or []
    except Exception:
        return 0
    recovered=0; now=time.time()
    with _lock:
        for key, lease in leases.items():
            item=dict(lease); item['gpu']=int(key) if str(key).isdigit() else key
            item['status']='recovered'; item['recovered_at']=now
            _recovery_events.append(item); recovered += 1
        for item in queue:
            if isinstance(item, dict):
                _recovery_events.append({**item, 'status':'recovered_waiting', 'recovered_at':now}); recovered += 1
    try:
        STATE_FILE.unlink()
    except Exception: pass
    return recovered


def init():
    """显式初始化：恢复上次运行残留的租约/队列（转 recovered 事件）。

    必须由调用方（api.py 的 lifespan）在 ``start_background()`` 之前调用；
    **严禁**在模块导入期执行任何磁盘 I/O（导入副作用会导致测试隔离失效、
    空转读写状态文件）。
    """
    return restore_runtime_state()


# --------------------------------------------------------------------------- 探测

def _default_probe():
    """调 nvidia-smi 读全部 GPU；无 NVIDIA/命令失败返回 None。"""
    try:
        p = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        rows = []
        for line in p.stdout.splitlines():
            parts = [x.strip() for x in line.split(',')]
            if len(parts) < 4:
                continue
            try:
                rows.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "used_mb": int(float(parts[2])),
                    "total_mb": int(float(parts[3])),
                    "utilization": float(parts[4]) if len(parts) > 4 and parts[4] not in ("", "[N/A]") else 0.0,
                    "temperature_c": float(parts[5]) if len(parts) > 5 and parts[5] not in ("", "[N/A]") else None,
                })
            except ValueError:
                continue
        return rows or None
    except Exception:
        return None

def _default_process_probe():
    """读取真实计算进程显存；失败时明确 available=False。"""
    try:
        p = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
        if p.returncode != 0:
            return {"available": False, "processes": []}
        out = []
        for line in p.stdout.splitlines():
            parts = [x.strip() for x in line.split(',')]
            if len(parts) >= 3:
                try: out.append({"pid": int(parts[0]), "process_name": parts[1], "used_mb": int(float(parts[2]))})
                except ValueError: pass
        # nvidia-smi 在权限不足或驱动不支持时会返回进程名但显存为 [N/A]；
        # 这不构成可用的进程级显存数据，必须明确报告不可用。
        return {"available": bool(out), "processes": out}
    except Exception:
        return {"available": False, "processes": []}

def register_process(pid, owner, gpu=None, purpose="", heartbeat=None):
    """注册实际子进程，返回注册记录。"""
    now = time.time(); key = int(pid)
    with _lock:
        old = _processes.get(key)
        if old:
            old.update({"owner": str(owner), "gpu": gpu, "purpose": str(purpose), "status": "running",
                        "heartbeat_required": heartbeat is not None,
                        "last_heartbeat": heartbeat if heartbeat is not None else old.get("last_heartbeat", now)})
            return dict(old)
        rec = {"pid": key, "owner": str(owner), "gpu": gpu, "purpose": str(purpose),
               "started_at": now, "status": "running", "heartbeat_required": heartbeat is not None,
               "last_heartbeat": heartbeat if heartbeat is not None else now}
        _processes[key] = rec
    return dict(rec)

def heartbeat_process(pid):
    with _lock:
        rec = _processes.get(int(pid))
        if not rec: return False
        rec["last_heartbeat"] = time.time(); return True

def unregister_process(pid, status="stopped"):
    with _lock:
        rec = _processes.pop(int(pid), None)
        if rec:
            rec["status"] = status
            return rec
    return None

def _pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if h: ctypes.windll.kernel32.CloseHandle(h); return True
    except Exception: pass
    try:
        os.kill(int(pid), 0); return True
    except Exception: return False

def _recover_processes(now):
    stale = []
    with _lock:
        for pid, rec in list(_processes.items()):
            if (not _pid_alive(pid)) or (rec.get("heartbeat_required") and PROCESS_HEARTBEAT_TTL and now - rec.get("last_heartbeat", now) > PROCESS_HEARTBEAT_TTL):
                stale.append((pid, rec))
                _processes.pop(pid, None)
    for pid, rec in stale:
        force_release(rec["owner"])
        rec["status"] = "orphan_recovered"
        rec["recovered_at"] = now
        with _lock: _recovery_events.append(rec)

def process_status():
    with _lock:
        registered = {int(k): v for k,v in _processes.items()}
        apps=[]
        for app in (_process_probe_cache.get("rows") or []):
            item=dict(app); rec=registered.get(int(item.get('pid',-1)))
            if rec: item.update({'owner':rec.get('owner'), 'gpu':rec.get('gpu'), 'purpose':rec.get('purpose')})
            else: item.update({'owner':None, 'gpu':None, 'purpose':''})
            apps.append(item)
        return {"available": _process_probe_cache["available"], "compute_apps": apps, "processes": [dict(v) for v in _processes.values()],
                "recovery_events": list(_recovery_events)}


def set_gpu_probe(fn):
    """注入探测函数（测试用）；传 None 恢复 nvidia-smi。"""
    global _probe_override
    _probe_override = fn


def query_gpus():
    """返回全部 GPU 行（list[dict]）；探测不可用返回 None。

    注入探测实时调用；真实 nvidia-smi 探测在 ``PROBE_CACHE_TTL`` 秒内复用缓存，
    避免状态轮询与队列 pump 在同一瞬间反复拉起子进程。
    """
    if _probe_override is not None:
        try:
            return _probe_override()
        except Exception:
            return None
    now = time.monotonic()
    if now - _probe_cache["t"] <= PROBE_CACHE_TTL:
        return _probe_cache["rows"]
    try:
        rows = _default_probe()
    except Exception:
        rows = None
    _probe_cache["t"] = now
    _probe_cache["rows"] = rows
    return rows


def memory_info():
    """聚合显存（保留旧键 used_mb/total_mb/free_mb/utilization）+ 每卡 ``gpus``。"""
    rows = query_gpus()
    if not rows:
        return None
    total = sum(r["total_mb"] for r in rows)
    used = sum(r["used_mb"] for r in rows)
    weighted = sum(r["utilization"] * max(1, r["total_mb"]) for r in rows)
    return {
        "used_mb": used,
        "total_mb": total,
        "free_mb": max(0, total - used),
        "utilization": weighted / sum(max(1, r["total_mb"]) for r in rows) / 100.0 if total else 0.0,
        "gpus": rows,
    }


# --------------------------------------------------------------------------- 内部工具

def _universe(mem):
    """当前可出租的 GPU 键集合。multi 用物理序号；无探测/serial 退化为单卡 0。"""
    if GPU_MODE == "multi" and mem and mem.get("gpus"):
        return [g["index"] for g in mem["gpus"]]
    return [0]


def _mem_of(mem, key):
    if not mem:
        return None
    for g in mem.get("gpus", []):
        if g["index"] == key:
            return g
    # 单卡退化：聚合值即该卡
    return {"index": 0, "name": "GPU", "used_mb": mem["used_mb"],
            "total_mb": mem["total_mb"], "free_mb": mem["free_mb"],
            "utilization": mem["utilization"] * 100, "temperature_c": None}


def _drop_expired_locked(now):
    """TTL 到期租约清空（只清不转交，转交统一走 _pump_locked）。硬 / 软租约都清理。"""
    for key, lease in list(_leases.items()):
        ttl = lease.get("ttl") or 0
        if ttl and lease["since"] and now - lease["since"] > ttl:
            del _leases[key]
    for owner, lease in list(_soft_leases.items()):
        ttl = lease.get("ttl") or 0
        if ttl and lease["since"] and now - lease["since"] > ttl:
            del _soft_leases[owner]


def _pump_locked(now, mem):
    """从队首开始授予可满足的等待者；队首不满足则停止（严格 FIFO）。

    被取消的等待者直接丢弃。返回被唤醒的 token 数。
    """
    granted = 0
    while _waiters:
        tok = _waiters[0]
        if tok["canceled"]:
            _waiters.popleft()
            tok["event"].set()
            continue
        # 已持同 owner：直接重入
        held_gpu = next((k for k, v in _leases.items() if v["owner"] == tok["owner"]), None)
        if held_gpu is not None:
            _waiters.popleft()
            tok["granted"] = True
            tok["gpu"] = held_gpu
            tok["event"].set()
            granted += 1
            continue
        keys = [tok["gpu"]] if tok["gpu"] is not None else _order_keys(mem)
        chosen = None
        for key in keys:
            if key in _leases:
                continue
            g = _mem_of(mem, key)
            if tok["min_free"] and g and g["total_mb"] - g["used_mb"] < tok["min_free"]:
                continue
            chosen = key
            break
        if chosen is None:
            break  # 队首要的卡忙/显存不足，后面一律不许插队
        _waiters.popleft()
        _leases[chosen] = {"owner": tok["owner"], "purpose": tok["purpose"],
                           "since": now, "ttl": tok["ttl"], "exclusive": True}
        _persist_runtime_locked()
        tok["granted"] = True
        tok["gpu"] = chosen
        tok["event"].set()
        granted += 1
    return granted


def _order_keys(mem):
    """any 模式下的挑卡顺序：空闲显存多的优先，同量序号小的优先。"""
    keys = _universe(mem)
    if mem and mem.get("gpus") and GPU_MODE == "multi":
        free = {g["index"]: g["total_mb"] - g["used_mb"] for g in mem["gpus"]}
        return sorted(keys, key=lambda k: (-free.get(k, 0), k))
    return keys


def _try_grant_locked(owner, purpose, ttl, gpu, min_free, now, mem):
    """一次性尝试：返回 ('granted', key, reentrant) / ('mem-deny', key, False) / ('wait', None, False)。"""
    if GPU_MODE == "parallel":
        return ("granted", None, False)
    held = next((k for k, v in _leases.items() if v["owner"] == owner), None)
    if held is not None:
        return ("granted", held, True)
    keys = [gpu] if gpu is not None else _order_keys(mem)
    for key in keys:
        if key in _leases:
            continue
        g = _mem_of(mem, key)
        if min_free and g and g["total_mb"] - g["used_mb"] < min_free:
            return ("mem-deny", key, False)
    # 严格 FIFO：已有等待者时，新请求哪怕在 multi 模式看到别的空闲卡，也不许
    # 插队（队首要的卡没释放前，后面的卡留给队列顺序转交，避免饥饿）。
    if _waiters:
        return ("wait", None, False)
    busy_keys = [k for k in keys if k in _leases]
    free_keys = [k for k in keys if k not in _leases]
    if free_keys:
        key = free_keys[0]
        _leases[key] = {"owner": owner, "purpose": purpose, "since": now, "ttl": ttl,
                        "exclusive": True}
        _persist_runtime_locked()
        return ("granted", key, False)
    return ("wait", busy_keys[0] if busy_keys else None, False)


# --------------------------------------------------------------------------- 租约 API

def _acquire_soft_lease(owner, purpose, ttl, want_gpu, threshold):
    """登记一条**软租约**（``exclusive=False``）并立即成功。

    软租约语义（明确写入此处，供 P5 收口核对）：
    - **登记**在 ``_soft_leases``（键 = owner），供 ``status()`` 展示与 TTL / ``release`` 回收；
      它**不进入** ``_leases`` 互斥槽位，因此**不参与互斥判定**；
    - **不阻挡**任何硬租约（硬租约只看 ``_leases`` 是否空闲），也**不被**任何硬租约阻挡；
    - 多个 ``exclusive=False`` 请求可**同时成功**（各自登记）；
    - 同一 owner 重复申请会覆盖旧登记（幂等重入）；
    - 不写入持久化文件（重启即丢，避免被当作 recovered 事件）；
    - 显存门槛（``min_free_mb``）只产出**软警告**，绝不拒绝——单卡上让用户自己判断。
    """
    now = time.time()
    gpu_key = want_gpu if want_gpu is not None else _env_index()
    warning = None
    if threshold:
        try:
            mem = memory_info()
        except Exception:  # noqa: BLE001
            mem = None
        g = _mem_of(mem, gpu_key)
        if g and (g["total_mb"] - g["used_mb"]) < threshold:
            free = max(0, int(g["total_mb"] - g["used_mb"]))
            warning = (f"显存不足：当前剩余 {free}MB < 门槛 {threshold}MB"
                       f"（软租约不阻断启动，请自行确认）")
    with _cv:
        _drop_expired_locked(now)
        _soft_leases[owner] = {"owner": owner, "purpose": purpose, "since": now,
                               "ttl": ttl, "gpu": gpu_key, "exclusive": False}
    out = {"ok": True, "gpu": gpu_key, "mode": GPU_MODE, "exclusive": False, "soft": True}
    if warning:
        out["warning"] = warning
    return out


def acquire_lease(owner, timeout=2.0, purpose="", ttl=None, gpu=None,
                  min_free_mb=None, evict=(), exclusive=True):
    """申请租约，返回结果字典。

    - ``gpu``：multi 模式指定卡号，None=自动挑最空的卡；
    - ``min_free_mb``：要求该卡至少空余多少 MB（0/None 不检查）；
    - ``evict``：仅当因显存门槛被拒绝（无租约挡路、显存被外部驻留占用）时，
      按名调用已注册的驱逐钩子（锁外执行，例如卸载 Ollama 驻留模型），
      重新探测后只重试一次；有活跃持有者导致的排队等待不触发驱逐；
    - ``ttl``：秒，0/None 取 DEFAULT_TTL；
    - ``exclusive``：**默认 True（保持所有既有调用方语义不变）**——硬租约，互相独占。
      传 ``False`` 得到**软租约**：只登记、不参与互斥、不阻挡也不被阻挡（见
      ``_acquire_soft_lease``），供引擎“多开”；显存门槛对软租约只产出 ``warning`` 不拒绝。

    返回的 ``gpu`` 只是协调层选出的物理卡号，**本身不产生任何设备隔离**：
    消费方若自行 ``Popen`` GPU 子进程，必须在启动前往其环境写入
    ``CUDA_VISIBLE_DEVICES=<gpu>``（CUDA 仅在进程初始化时读取一次）。
    当前代码库没有此类消费方（ComfyUI/Ollama 均为外部 HTTP 服务），
    因此 multi 模式现网无运行时隔离效果——接线与验收口径见
    HANDOFF.md 第 5 节 P2-1 待验收项 B。
    """
    owner = str(owner)
    purpose = str(purpose)
    timeout = max(0.0, float(timeout))
    lease_ttl = DEFAULT_TTL if ttl is None else max(0.0, float(ttl))
    want_gpu = None if gpu is None else int(gpu)
    threshold = int(min_free_mb) if min_free_mb is not None else int(os.getenv("DOCMIND_GPU_MIN_FREE_MB", "0") or 0)

    if GPU_MODE == "parallel":
        return {"ok": True, "gpu": None, "mode": GPU_MODE, "reentrant": False}

    # 软租约（exclusive=False）：只登记、不互斥、不排队、不因显存门槛被拒（只给警告）。
    if not exclusive:
        return _acquire_soft_lease(owner, purpose, lease_ttl, want_gpu, threshold)

    mem = memory_info()
    now = time.time()
    with _cv:
        _drop_expired_locked(now)
        verdict, key, reentrant = _try_grant_locked(owner, purpose, lease_ttl, want_gpu, threshold, now, mem)
        if verdict == "granted":
            return {"ok": True, "gpu": key, "mode": GPU_MODE, "reentrant": reentrant}

    # 只有"显存门槛拒绝"才值得跑驱逐钩子：此时没有租约挡路，显存是被外部驻留
    # （Ollama keep_alive 长驻但不持租约）吃掉的，卸载后重试才有意义。
    # verdict=="wait" 说明有任务正在用卡（租约在手），卸载 Ollama 既不会释放
    # 租约也抢不到卡，只会让用户白付一次冷加载——不跑。
    evicted = False
    if verdict == "mem-deny":
        hooks = [name for name in evict if name in _hooks]
        if hooks:
            for name in hooks:
                try:
                    evicted = bool(_hooks[name]()) or evicted
                except Exception:
                    evicted = evicted or False
            mem = memory_info()
            now = time.time()
            with _cv:
                _drop_expired_locked(now)
                _pump_locked(now, mem)
                verdict, key, _ = _try_grant_locked(owner, purpose, lease_ttl, want_gpu, threshold, now, mem)
            if verdict == "granted":
                return {"ok": True, "gpu": key, "mode": GPU_MODE, "evicted": evicted}

    if verdict == "mem-deny":
        # 显存门槛不过：直接拒绝，不排队（与旧版行为一致）
        return {"ok": False, "reason": "insufficient_memory", "gpu": key, "mode": GPU_MODE,
                "evicted": evicted}
    if timeout <= 0:
        return {"ok": False, "reason": "busy", "gpu": key, "mode": GPU_MODE, "evicted": evicted}

    tok = {"owner": owner, "purpose": purpose, "event": threading.Event(),
           "gpu": want_gpu, "min_free": threshold, "granted": False, "canceled": False,
           "ttl": lease_ttl}
    with _cv:
        _waiters.append(tok)

    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if tok["event"].wait(min(0.1, remaining)):
            break
        # 探测在锁外（nvidia-smi 最多 2s），再短锁转交
        mem_now = memory_info()
        with _cv:
            _drop_expired_locked(time.time())
            _pump_locked(time.time(), mem_now)
        if tok["event"].is_set():
            break

    with _cv:
        if tok["granted"]:
            return {"ok": True, "gpu": tok["gpu"], "mode": GPU_MODE}
        # 取消/超时：确保出队
        if tok in _waiters:
            _waiters.remove(tok)
        reason = "canceled" if tok["canceled"] else "timeout"
    return {"ok": False, "reason": reason, "mode": GPU_MODE}


def acquire(owner, timeout=2, purpose="", ttl=None):
    """旧接口：布尔结果。新代码请用 acquire_lease。"""
    return acquire_lease(owner, timeout=timeout, purpose=purpose, ttl=ttl)["ok"]


def release(owner):
    """归还（硬或软）租约并触发队首转交；owner 不匹配时静默忽略。

    硬租约释放后转交等待者；软租约只从登记表移除（不涉及队列）。
    """
    if GPU_MODE == "parallel":
        return
    owner = str(owner)
    mem_now = memory_info()
    with _cv:
        _soft_leases.pop(owner, None)   # 软租约：直接注销，无需转交
        key = next((k for k, v in _leases.items() if v["owner"] == owner), None)
        if key is None:
            return
        _leases.pop(key, None)
        _persist_runtime_locked()
        _pump_locked(time.time(), mem_now)
        _cv.notify_all()


def reown(old_owner, new_owner, purpose=None, ttl=None):
    """把租约持有者改名（ComfyUI：提交临时 owner -> prompt_id 作业 owner）。"""
    if GPU_MODE == "parallel":
        return True
    with _cv:
        key = next((k for k, v in _leases.items() if v["owner"] == str(old_owner)), None)
        if key is None:
            return False
        lease = _leases[key]
        lease["owner"] = str(new_owner)
        if purpose is not None:
            lease["purpose"] = str(purpose)
        if ttl is not None:
            lease["ttl"] = max(0.0, float(ttl))
        else:
            lease["since"] = time.time()  # 作业 TTL 从改名这一刻重新起算
        return True


def force_release(owner=None):
    """强制回收（硬 + 软租约）：指定 owner 只收它；不指定回收全部并转交等待者。

    返回被回收的 owner 字符串（多个用逗号连接），没有租约时返回 None。
    进程死亡看门狗 `_recover_processes` 走这里回收引擎的软租约。
    """
    if GPU_MODE == "parallel":
        return None
    mem_now = memory_info()
    with _cv:
        if owner is not None:
            owner = str(owner)
            key = next((k for k, v in _leases.items() if v["owner"] == owner), None)
            if key is not None:
                prev = _leases.pop(key)["owner"]
                _persist_runtime_locked()
            elif owner in _soft_leases:
                prev = _soft_leases.pop(owner)["owner"]
            else:
                return None
        else:
            owners = [v["owner"] for v in _leases.values()] + list(_soft_leases)
            if not owners:
                return None
            prev = ",".join(owners)
            _leases.clear()
            _soft_leases.clear()
            _persist_runtime_locked()
        _pump_locked(time.time(), mem_now)
        _cv.notify_all()
        return prev


def cancel_wait(owner):
    """取消该 owner 的全部排队请求（不影响已持有的租约）。返回取消条数。"""
    owner = str(owner)
    n = 0
    mem_now = memory_info()
    with _cv:
        for tok in _waiters:
            if tok["owner"] == owner and not tok["granted"]:
                tok["canceled"] = True
                tok["event"].set()
                n += 1
        if n:
            _pump_locked(time.time(), mem_now)
            _cv.notify_all()
    return n


def queue_position(owner):
    """该 owner 在 FIFO 队列中的位置（1 起）；未排队返回 None。"""
    owner = str(owner)
    with _cv:
        for i, tok in enumerate(_waiters, start=1):
            if tok["owner"] == owner:
                return i
    return None


def register_hook(name, fn):
    """注册驱逐/回收钩子（如 ollama-unload）。传 None 注销。"""
    if fn is None:
        _hooks.pop(name, None)
    else:
        _hooks[name] = fn


def note_activity(owner="ollama"):
    """记录一次占用方活动（用于空闲回收计时；聊天/嵌入请求前后调用）。"""
    with _lock:
        _activities[str(owner)] = time.time()


def configure(idle_unload_seconds=None, poll_interval=None):
    """运行时调参：Ollama 空闲卸载秒数（0 关）、显存采样间隔。"""
    global _idle_unload, _poll_interval
    with _lock:
        if idle_unload_seconds is not None:
            _idle_unload = max(0.0, float(idle_unload_seconds))
        if poll_interval is not None:
            _poll_interval = max(1.0, float(poll_interval))


def recent_samples(limit=SAMPLE_MAX):
    with _lock:
        items = list(_samples)
    return items[-max(1, min(limit, SAMPLE_MAX)):]


# --------------------------------------------------------------------------- 状态

def _holder_view(key, lease, now):
    return {
        "owner": lease["owner"],
        "purpose": lease["purpose"],
        "held_for_seconds": round(now - lease["since"], 1) if lease["since"] else None,
        "lease_ttl": lease.get("ttl") or 0,
        # 硬租约 True（默认）、软租约 False；前端据此区分"独占持有"与"软登记"。
        "exclusive": lease.get("exclusive", True),
    }


def status():
    mem = memory_info()
    now = time.time()
    with _cv:
        _drop_expired_locked(now)
        _pump_locked(now, mem)
        leases_snapshot = {k: dict(v) for k, v in _leases.items()}
        soft_snapshot = [dict(v) for v in _soft_leases.values()]
        queue_snapshot = [{"owner": t["owner"], "purpose": t["purpose"],
                           "gpu": t["gpu"], "waiting_gpu": t["gpu"] is None}
                          for t in _waiters if not t["canceled"]]
        idle_seconds = _idle_unload
        last_act = _activities.get("ollama")
        hooks = sorted(_hooks)

    gpus = []
    if mem and mem.get("gpus"):
        for g in mem["gpus"]:
            key = g["index"]
            gpus.append({
                **{k: g[k] for k in ("index", "name", "used_mb", "total_mb", "utilization", "temperature_c")},
                "free_mb": max(0, g["total_mb"] - g["used_mb"]),
                "holder": _holder_view(key, leases_snapshot[key], now) if key in leases_snapshot else None,
                "queue": [q for q in queue_snapshot if q["gpu"] == key or (q["gpu"] is None and GPU_MODE == "multi")],
            })
    else:
        lease = leases_snapshot.get(0)
        gpus.append({
            "index": 0, "name": None, "used_mb": None, "total_mb": None,
            "free_mb": None, "utilization": None, "temperature_c": None,
            "holder": _holder_view(0, lease, now) if lease else None,
            "queue": queue_snapshot,
        })

    # 软租约也计入 holders（供前端/gpu_status 展示）；附 soft=True 与 exclusive=False 便于区分。
    soft_holders = [{"gpu": v.get("gpu"), "soft": True, **_holder_view(v.get("gpu"), v, now)}
                    for v in soft_snapshot]
    hard_holders = [{"gpu": k, **_holder_view(k, v, now)}
                    for k, v in sorted(leases_snapshot.items(), key=lambda x: str(x[0]))]

    primary = leases_snapshot.get(0) or (next(iter(leases_snapshot.values()), None))
    ps = process_status()
    return {
        "mode": GPU_MODE,
        "coordinating": GPU_MODE != "parallel",
        "active": primary["owner"] if primary else None,
        "purpose": primary["purpose"] if primary else "",
        # 兼容旧消费方：当前（唯一）租约所在卡；无租约时取 DOCMIND_GPU_INDEX
        "device_index": next(iter(leases_snapshot), _env_index()),
        "held_for_seconds": round(now - primary["since"], 1) if primary and primary["since"] else None,
        "lease_ttl": (primary.get("ttl") if primary else 0) or 0,
        "holders": hard_holders + soft_holders,
        "soft_holders": soft_holders,
        "queue": queue_snapshot,
        "queue_length": len(queue_snapshot),
        "memory": mem,
        "gpus": gpus,
        "samples": recent_samples(),
        "processes": ps["processes"],
        "process_probe": {"available": ps["available"]},
        "compute_apps": ps["compute_apps"],
        "recovery_events": ps["recovery_events"],
        "ollama_keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "0"),
        "ollama_idle": {
            "unload_seconds": idle_seconds,
            "last_activity_ago": round(now - last_act, 1) if last_act else None,
            "last_unload_ago": round(now - _last_idle_unload, 1) if _last_idle_unload else None,
        },
        "hooks": hooks,
    }


def process_environment(device_index=None):
    """返回启动 GPU 子进程时应注入的设备环境字典；不修改当前进程环境。

    - ``device_index`` 显式给定时直接使用——它应来自 ``acquire_lease()`` 返回的 ``gpu``；
    - 未给定时取当前唯一租约所在卡；没有唯一租约（无持有/多卡多持有）时退到
      ``DOCMIND_GPU_INDEX``（默认 0）。

    CUDA 只在子进程初始化瞬间读取 ``CUDA_VISIBLE_DEVICES``，必须在 Popen **之前**
    注入；这是协调器租约到物理设备隔离的唯一接线点。验收口径（含 ``=99`` 负对照）
    见 HANDOFF.md 第 5 节 P2-1 待验收项 B。
    """
    if device_index is None:
        with _cv:
            gpu_keys = [k for k in _leases if isinstance(k, int)]
        device_index = gpu_keys[0] if len(gpu_keys) == 1 else _env_index()
    idx = int(device_index)
    return {"CUDA_VISIBLE_DEVICES": str(idx), "DOCMIND_GPU_INDEX": str(idx)}


# --------------------------------------------------------------------------- 后台线程

def _sample_once():
    rows = query_gpus()
    rows = rows or []
    point = {"t": round(time.time(), 1), "available": bool(rows), "gpus": [
        {"index": g["index"], "used_mb": g["used_mb"], "total_mb": g["total_mb"],
         "utilization": g["utilization"]} for g in rows]}
    with _lock:
        _samples.append(point)
        try:
            SAMPLES_FILE.parent.mkdir(parents=True, exist_ok=True)
            SAMPLES_FILE.write_text(json.dumps({"samples": list(_samples), "updated_at": time.time(), "available": bool(rows)}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    try:
        pp = _default_process_probe()
        with _lock: _process_probe_cache.update({"t": time.time(), "rows": pp.get("processes", []), "available": pp.get("available", False)})
    except Exception:
        pass


def _maybe_idle_unload(now):
    """Ollama 空闲超时且没有别的租约持有方时，调注册的卸载钩子。"""
    global _last_idle_unload
    with _lock:
        idle_seconds = _idle_unload
        last_act = _activities.get("ollama")
        hook = _hooks.get("ollama")
    if not idle_seconds or hook is None or not last_act:
        return False
    if now - last_act < idle_seconds:
        return False
    if _last_idle_unload and now - _last_idle_unload < IDLE_UNLOAD_COOLDOWN:
        return False
    with _cv:
        others = [v["owner"] for v in _leases.values() if not str(v["owner"]).startswith("ollama")]
    if others:
        return False  # ComfyUI/导出正持租约，别在这时候动 Ollama（虽然它自己也没在跑）
    try:
        did = bool(hook())
    except Exception:
        did = False
    _last_idle_unload = now
    return did


def _bg_loop():
    last_sample = 0.0
    while not _bg_stop.wait(1.0):
        now = time.time()
        interval = _poll_interval
        if now - last_sample >= interval:
            last_sample = now
            _sample_once()
        if GPU_MODE != "parallel":
            mem_now = memory_info()
            with _cv:
                _drop_expired_locked(now)
                _pump_locked(now, mem_now)
                _cv.notify_all()
        _recover_processes(now)
        try:
            _maybe_idle_unload(now)
        except Exception:
            pass


def start_background():
    """启动采样/TTL/空闲回收守护线程（幂等）。"""
    global _bg_thread, _bg_stop
    if _bg_thread and _bg_thread.is_alive():
        return
    _bg_stop.clear()
    t = threading.Thread(target=_bg_loop, name="docmind-gpu-coordinator", daemon=True)
    _bg_thread = t
    t.start()


def stop_background():
    global _bg_thread
    _bg_stop.set()
    t = _bg_thread
    _bg_thread = None
    if t:
        t.join(timeout=2)

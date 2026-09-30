"""临时状态目录的归属标记与孤儿回收。

`config.STATE_ROOT`（测试隔离时）与 `dev_eval` 的 fixture 目录都建在系统 temp 下。
正常退出时创建方自己删得掉；被硬杀（会话重启、工具超时）时什么都留不下——实测攒到
1,794 个目录 / 239 MB / 1.9 万个文件。空间不是问题（D: 还有几十 GB），**条目数**才是：
这台机器上任何"扫一遍 temp"的逻辑都要为它付钱。

判据是归属 PID 还活不活着，不是目录 mtime。Windows 上往已存在的子目录反复写文件不会
推进父目录 mtime，拿它当"多久没用"实测删掉过正在使用的浏览器 profile（222 个文件删到
95 个），所以这里一律不用时间启发式。没有归属标记的目录也一律不碰：可能是别人建的，
也可能是提权会话建的，两种我们都判断不了。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import time
from pathlib import Path

OWNER_FILE = ".docmind-owner"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_KERNEL32 = None


def _kernel32():
    """私有 WinDLL 实例 + 显式原型。

    不能用 `ctypes.windll.kernel32`：那是进程级共享缓存，在它上面设 argtypes 会改掉
    同进程里所有其他调用方看到的原型。不写 restype 时 ctypes 按 C int 返回，x64 上
    句柄会被截成 32 位。
    """
    global _KERNEL32
    if _KERNEL32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        _KERNEL32 = kernel32
    return _KERNEL32


def pid_alive(pid: int) -> bool:
    """判断 PID 是否存在；判不出来一律当作活着——宁可漏删，不可误删。

    Windows 上 `os.kill(pid, 0)` 会直接终止目标进程，绝对不能用。`OpenProcess` 失败要
    分两种：`ERROR_ACCESS_DENIED` 说明进程存在（多半是提权会话起的），只有"找不到"才是
    死了。
    """
    if pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def claim(path: str | Path) -> None:
    """把归属写进目录里，作为 `sweep_orphans` 唯一认账的"还在用"证据。"""
    try:
        (Path(path) / OWNER_FILE).write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass


def owner_of(path: Path) -> int | None:
    try:
        return int((path / OWNER_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def sweep_orphans(directory: str | Path, prefixes: tuple[str, ...]) -> list[str]:
    """删掉【主人已经没了】且前缀匹配的临时目录，返回真正删掉的路径。

    没有归属标记、或主人还活着、或删不动（提权 ACL、被占用）的一律跳过并留在原地。
    """
    parent = Path(str(directory or ""))
    removed: list[str] = []
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(prefixes):
            continue
        try:
            if not entry.is_dir():
                continue
            owner = owner_of(Path(entry.path))
            if owner is None or pid_alive(owner):
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            # 用 path 而不是 entry 复查：DirEntry 会缓存 stat，删完再问它仍然说「在」。
            if not os.path.isdir(entry.path):
                removed.append(entry.path)
        except OSError:
            continue
    return removed


def release(path: str | Path) -> bool:
    """创建方退出时回收自己的目录。返回是否真的删掉了。"""
    target = str(path)
    shutil.rmtree(target, ignore_errors=True)
    return not os.path.isdir(target)


def sweep_stale_orphans(directory: str | Path, prefixes: tuple[str, ...],
                        min_age_seconds: float = 7200.0) -> list[str]:
    """回收【我们插不上归属标记】的第三方目录，主判据是 Windows 的重命名探针。

    用于 playwright 那一类：`launch_persistent_context` 用的是调用方自持的
    `user_data_dir`，playwright 故意不删，而我们又没法往它的创建路径里塞标记。

    判据为什么是改名而不是时间：活的 Chromium 一直开着 profile 里的 `lockfile`（不带
    FILE_SHARE_DELETE），Windows 上只要有文件被这样打开，其所在目录就改不了名。改名成功
    即证明「此刻无人持有」，比任何年龄阈值都硬。`min_age_seconds` 只是给刚建好、还没来得及
    打开文件的目录留一段余量，不是主判据。

    刻意不收的：`playwright-download-*` 是 playwright 的浏览器下载缓存，这台机器的 CDN
    拉不到 chromium，删了就要不回来——调用方给前缀时请自己排除。
    """
    parent = Path(str(directory or ""))
    removed: list[str] = []
    now = time.time()
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(prefixes):
            continue
        try:
            if not entry.is_dir() or now - entry.stat().st_mtime < min_age_seconds:
                continue
        except OSError:
            continue
        probe = entry.path + ".stale-probe"
        try:
            os.rename(entry.path, probe)
        except OSError:
            continue               # 改名失败 = 有人正开着里面的东西，或 ACL 不允许：都不硬来
        shutil.rmtree(probe, ignore_errors=True)
        if os.path.isdir(probe):
            try:
                os.rename(probe, entry.path)   # 删不干净就放回去，别把目录弄成凭空消失
            except OSError:
                continue
        removed.append(entry.path)
    return removed


__all__ = ["claim", "owner_of", "pid_alive", "release", "sweep_orphans",
           "sweep_stale_orphans"]

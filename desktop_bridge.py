"""可选 Windows 原生子窗口桥接。纯 ctypes，无 pywin32 依赖；非 Windows 安全降级。"""
import ctypes
import os

_HOST_HWND = None

# x64 下必须显式声明指针宽度的签名，否则 Python int 会被按 c_int 截断 HWND。
_WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _user32():
    if os.name != 'nt':
        return None
    u = ctypes.windll.user32
    # 枚举/查询
    u.EnumWindows.argtypes = [_WNDPROCTYPE, ctypes.c_void_p]
    u.EnumWindows.restype = ctypes.c_bool
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.IsWindowVisible.restype = ctypes.c_bool
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    u.GetWindowThreadProcessId.restype = ctypes.c_ulong
    # 样式：64 位系统统一用 Ptr 版本（GWL_STYLE 时内部等价 Long）
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_longlong
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
    u.SetWindowLongPtrW.restype = ctypes.c_longlong
    # 嵌入
    u.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.SetParent.restype = ctypes.c_void_p
    u.MoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_bool]
    u.MoveWindow.restype = ctypes.c_bool
    u.SetFocus.argtypes = [ctypes.c_void_p]
    u.SetFocus.restype = ctypes.c_void_p
    return u


def set_host(hwnd):
    global _HOST_HWND
    _HOST_HWND = int(hwnd) if hwnd else None
    return _HOST_HWND


def get_host():
    return _HOST_HWND


def _enum_windows(user32, match):
    found = []

    def cb(hwnd, _):
        if match(int(hwnd) if hwnd else 0, found):
            return True
        return True

    user32.EnumWindows(_WNDPROCTYPE(cb), None)
    return found[0] if found else None


def _descendant_pids(pid):
    """通过 Toolhelp 快照返回 pid 的全部后代进程（console 版 Godot 会派生 GUI 子进程）。"""
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD),
                    ("th32ProcessID", ctypes.wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", ctypes.wintypes.DWORD),
                    ("cntThreads", ctypes.wintypes.DWORD),
                    ("th32ParentProcessID", ctypes.wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return {pid}
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)
    result = {pid}
    changed = True
    while changed:  # 逐层扩散到全部后代
        changed = False
        for child, parent in parents.items():
            if parent in result and child not in result:
                result.add(child)
                changed = True
    return result


def find_window(pid: int, title_hint: str = ''):
    """通过进程 PID（含其全部后代进程）枚举顶层可见窗口，返回 (hwnd, title)。"""
    user32 = _user32()
    if user32 is None:
        return None
    pids = _descendant_pids(int(pid))

    def match(hwnd, found):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in pids or not user32.IsWindowVisible(hwnd):
            return False
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if not title:
            return False
        if not title_hint or title_hint.lower() in title.lower():
            found.append((hwnd, title))
        return False

    return _enum_windows(user32, match)


def find_host(title_hint='DocMind'):
    """查找桌面宿主窗口；浏览器模式/无桌面壳时返回 None。"""
    user32 = _user32()
    if user32 is None:
        return None

    def match(hwnd, found):
        if not user32.IsWindowVisible(hwnd):
            return False
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if title_hint.lower() in buf.value.lower():
            found.append((hwnd, buf.value))
        return False

    return _enum_windows(user32, match)


def embed(hwnd_child: int, hwnd_host: int, width: int, height: int):
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持 HWND 嵌入'}
    try:
        hwnd_child, hwnd_host = int(hwnd_child), int(hwnd_host)
        GWL_STYLE = -16
        WS_CHILD = 0x40000000
        WS_VISIBLE = 0x10000000
        WS_CAPTION_BORDER = 0x00C00000  # WS_CAPTION | WS_THICKFRAME
        style = user32.GetWindowLongPtrW(hwnd_child, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd_child, GWL_STYLE,
                                 (style | WS_CHILD | WS_VISIBLE) & ~WS_CAPTION_BORDER)
        user32.SetParent(hwnd_child, hwnd_host)
        user32.MoveWindow(hwnd_child, 0, 0, int(width), int(height), True)
        return {'ok': True, 'hwnd': hwnd_child}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def resize(hwnd_child: int, width: int, height: int):
    user32 = _user32()
    if user32 is None:
        return False
    try:
        return bool(user32.MoveWindow(int(hwnd_child), 0, 0, int(width), int(height), True))
    except Exception:  # noqa: BLE001
        return False


def focus(hwnd_child: int):
    user32 = _user32()
    if user32 is None:
        return False
    try:
        return bool(user32.SetFocus(int(hwnd_child)))
    except Exception:  # noqa: BLE001
        return False

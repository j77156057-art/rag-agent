"""可选 Windows 原生子窗口桥接。纯 ctypes，无 pywin32 依赖；非 Windows 安全降级。

设计要点（都是实机踩出来的）：

* **嵌入是可逆的**：`embed` 会把子窗口的原始 parent/style/窗口矩形存下来，`detach`
  原样还回去。不保存状态就没法安全解绑——`SetParent(child, 0)` 之后子窗口会带着
  `WS_CHILD` 样式变成"不可见的顶层窗口"，看起来就是窗口消失了。
* **尺寸按宿主"客户区"算，不是窗口外框**。外框包含标题栏与边框，用它给子窗口定位
  会让引擎画面右侧/底部被裁掉一截，而且换 DPI 后裁得更多。
* **进程要显式声明 DPI 感知**。默认（无清单）的进程在 125%/150% 缩放下坐标会被 Windows
  虚拟化，而 Godot 自己是 per-monitor v2 感知的——父进程按虚拟坐标、子进程按物理像素，
  两边一对就是错位。`ensure_dpi_awareness()` 把进程提到 per-monitor v2。
* **focus 要跨线程**。子窗口属于引擎进程的线程，直接 `SetFocus` 会失败；
  必须先 `AttachThreadInput` 再 `SetFocus`，并用 `GetFocus()` 回读验证，不能只看返回值。
"""
import ctypes
import ctypes.wintypes
import os

_HOST_HWND = None
# child hwnd -> {'parent': 原始父窗口, 'style': 原始样式, 'orig_rect': 原始屏幕矩形,
#               'host': 宿主 hwnd, 'mode': 'rect'|'fill', 'offset_y', 'placed': 当前矩形}
_CHILD_STATE = {}

# x64 下必须显式声明指针宽度的签名，否则 Python int 会被按 c_int 截断 HWND。
_WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
# 顶层窗口的"边框"类样式：嵌入时必须去掉，否则子窗口里会残留一圈标题栏/粗边框
_TOPLEVEL_STYLES = 0x80000000 | 0x00C00000 | 0x00040000 | 0x00030000 | 0x00080000

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001


def _user32():
    if os.name != 'nt':
        return None
    u = ctypes.windll.user32
    # 枚举/查询
    u.EnumWindows.argtypes = [_WNDPROCTYPE, ctypes.c_void_p]
    u.EnumWindows.restype = ctypes.c_bool
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.IsWindowVisible.restype = ctypes.c_bool
    u.IsWindow.argtypes = [ctypes.c_void_p]
    u.IsWindow.restype = ctypes.c_bool
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    u.GetWindowThreadProcessId.restype = ctypes.c_ulong
    # 样式：64 位系统统一用 Ptr 版本（GWL_STYLE 时内部等价 Long）
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_longlong
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
    u.SetWindowLongPtrW.restype = ctypes.c_longlong
    # 嵌入 / 几何
    u.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.SetParent.restype = ctypes.c_void_p
    u.GetParent.argtypes = [ctypes.c_void_p]
    u.GetParent.restype = ctypes.c_void_p
    u.MoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_bool]
    u.MoveWindow.restype = ctypes.c_bool
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = ctypes.c_bool
    u.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
    u.GetClientRect.restype = ctypes.c_bool
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
    u.GetWindowRect.restype = ctypes.c_bool
    u.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.POINT)]
    u.ClientToScreen.restype = ctypes.c_bool
    u.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.POINT)]
    u.ScreenToClient.restype = ctypes.c_bool
    # 焦点
    u.SetFocus.argtypes = [ctypes.c_void_p]
    u.SetFocus.restype = ctypes.c_void_p
    u.GetFocus.argtypes = []
    u.GetFocus.restype = ctypes.c_void_p
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = ctypes.c_void_p
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u.SetForegroundWindow.restype = ctypes.c_bool
    u.BringWindowToTop.argtypes = [ctypes.c_void_p]
    u.BringWindowToTop.restype = ctypes.c_bool
    u.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
    u.AttachThreadInput.restype = ctypes.c_bool
    u.IsIconic.argtypes = [ctypes.c_void_p]
    u.IsIconic.restype = ctypes.c_bool
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.ShowWindow.restype = ctypes.c_bool
    u.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetWindow.restype = ctypes.c_void_p
    # DPI（Win10 1607+；老系统取不到就退化为 96）
    if hasattr(u, 'GetDpiForWindow'):
        u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        u.GetDpiForWindow.restype = ctypes.c_uint
    if hasattr(u, 'SetProcessDpiAwarenessContext'):
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    if hasattr(u, 'GetThreadDpiAwarenessContext'):
        u.GetThreadDpiAwarenessContext.argtypes = []
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    if hasattr(u, 'GetAwarenessFromDpiAwarenessContext'):
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_uint
    return u


def set_host(hwnd):
    global _HOST_HWND
    _HOST_HWND = int(hwnd) if hwnd else None
    return _HOST_HWND


def get_host():
    return _HOST_HWND


# ---------------------------------------------------------------------------
# DPI
# ---------------------------------------------------------------------------
def ensure_dpi_awareness():
    """把当前进程提到 per-monitor v2 DPI 感知；返回 (ok, 模式名)。

    不做这件事的后果：进程在 125%/150% 缩放下拿到的是"虚拟化后的坐标"，
    而 Godot 自己按物理像素渲染，父/子窗口两边坐标系不一致 —— 表现就是嵌入画面
    尺寸对不上、鼠标点不准。必须在建宿主窗口之前调用。
    """
    user32 = _user32()
    if user32 is None:
        return False, 'non-windows'
    if hasattr(user32, 'SetProcessDpiAwarenessContext'):
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True, dpi_awareness()
    try:  # 老系统兜底：系统级感知，至少不虚拟化
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True, dpi_awareness()
    except Exception:  # noqa: BLE001
        pass
    try:
        if user32.SetProcessDPIAware():
            return True, dpi_awareness()
    except Exception:  # noqa: BLE001
        pass
    return False, dpi_awareness()


def dpi_awareness():
    """返回可读的 DPI 感知模式名。"""
    user32 = _user32()
    if user32 is None:
        return 'non-windows'
    names = {0: 'unaware', 1: 'system', 2: 'per-monitor'}
    if hasattr(user32, 'GetThreadDpiAwarenessContext') and hasattr(
            user32, 'GetAwarenessFromDpiAwarenessContext'):
        ctx = user32.GetThreadDpiAwarenessContext()
        return names.get(int(user32.GetAwarenessFromDpiAwarenessContext(ctx)), 'unknown')
    try:
        value = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
        return names.get(value.value, 'unknown')
    except Exception:  # noqa: BLE001
        return 'unknown'


def dpi_of(hwnd):
    """窗口所在显示器的 DPI（96 = 100%）。取不到时返回 96。"""
    user32 = _user32()
    if user32 is None:
        return 96
    try:
        if hasattr(user32, 'GetDpiForWindow'):
            value = int(user32.GetDpiForWindow(int(hwnd)))
            if value:
                return value
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(ctypes.windll.gdi32.GetDeviceCaps(ctypes.windll.user32.GetDC(0), 88)) or 96
    except Exception:  # noqa: BLE001
        return 96


def client_rect(hwnd):
    """客户区尺寸（物理像素）。"""
    user32 = _user32()
    if user32 is None:
        return None
    rect = ctypes.wintypes.RECT()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        return None
    return {'width': int(rect.right - rect.left), 'height': int(rect.bottom - rect.top)}


def window_rect(hwnd):
    """窗口外框在屏幕上的矩形（物理像素）。"""
    user32 = _user32()
    if user32 is None:
        return None
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return None
    return {'x': int(rect.left), 'y': int(rect.top),
            'width': int(rect.right - rect.left), 'height': int(rect.bottom - rect.top)}


def client_origin(hwnd):
    """客户区左上角在屏幕上的坐标。"""
    user32 = _user32()
    if user32 is None:
        return None
    point = ctypes.wintypes.POINT(0, 0)
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(point)):
        return None
    return {'x': int(point.x), 'y': int(point.y)}


def style_of(hwnd):
    user32 = _user32()
    if user32 is None:
        return None
    return int(user32.GetWindowLongPtrW(int(hwnd), GWL_STYLE))


def is_window(hwnd):
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    return bool(user32.IsWindow(int(hwnd)))


def parent_of(hwnd):
    user32 = _user32()
    if user32 is None:
        return None
    return int(user32.GetParent(int(hwnd)) or 0) or None


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
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


def windows_of_pids(pids):
    """枚举属于给定进程集合的所有顶层窗口。

    ⚠️ 注意收集用的列表必须是**本函数自己的**：`_enum_windows` 会把回调的第二个参数
    当作自己的内部收集器，往那个列表里 append 的结果出了 `_enum_windows` 就丢了——
    这个 bug 让"停止后没有孤儿窗口"的断言变成了空转的假通过，务必别再写回去。
    """
    user32 = _user32()
    if user32 is None:
        return []
    target = {int(x) for x in pids}
    out = []

    def match(hwnd, _found):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in target:
            return False
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        out.append({'hwnd': hwnd, 'pid': owner.value, 'title': buf.value,
                    'visible': bool(user32.IsWindowVisible(hwnd)),
                    'parent': int(user32.GetParent(hwnd) or 0) or None})
        return False

    _enum_windows(user32, match)
    return out


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


# ---------------------------------------------------------------------------
# 嵌入 / 解除 / 尺寸同步
# ---------------------------------------------------------------------------
def _target_size(hwnd_host, width, height, offset_y):
    """算出子窗口应该占的尺寸。给定宽高就用给定值，否则按宿主客户区减顶部留白。"""
    if width and height:
        return int(width), int(height), None
    rect = client_rect(hwnd_host)
    if not rect:
        return None, None, '无法读取宿主客户区尺寸'
    return rect['width'], max(1, rect['height'] - int(offset_y)), rect


def embed(hwnd_child: int, hwnd_host: int, width=None, height=None,
          offset_y: int = 0, title: str = '', rect=None):
    """把引擎窗口嵌进宿主客户区，并记录原始状态以便 `detach` 还原。

    尺寸有两种模式：

    * **rect 模式（推荐）**：`rect={'x','y','width','height'}`，由前端给出工作台里那块
      "引擎视窗"的屏幕/客户坐标。引擎只占这一块，工作台 UI 照常可用——否则引擎铺满
      整个窗口，用户连"停止引擎"的按钮都点不到。
    * **fill 模式**：不给 rect 时按宿主客户区铺满（`offset_y` 是顶部留白）。
    """
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持 HWND 嵌入'}
    try:
        hwnd_child, hwnd_host = int(hwnd_child), int(hwnd_host)
        if not is_window(hwnd_child):
            return {'ok': False, 'error': '引擎窗口句柄无效（可能已退出）'}
        if not is_window(hwnd_host):
            return {'ok': False, 'error': '宿主窗口句柄无效（桌面壳可能已关闭）'}

        mode = 'rect'
        if rect and int(rect.get('width') or 0) > 0 and int(rect.get('height') or 0) > 0:
            pos_x, pos_y = int(rect.get('x') or 0), int(rect.get('y') or 0)
            size_w, size_h, host_rect = int(rect['width']), int(rect['height']), None
        else:
            mode = 'fill'
            pos_x, pos_y = 0, int(offset_y)
            size_w, size_h, host_rect = _target_size(hwnd_host, width, height, offset_y)
            if not size_w:
                return {'ok': False, 'error': host_rect}

        if hwnd_child not in _CHILD_STATE:
            _CHILD_STATE[hwnd_child] = {
                'parent': int(user32.GetParent(hwnd_child) or 0) or None,
                'style': style_of(hwnd_child),
                # 原始屏幕矩形：解除嵌入时用它把窗口放回原处
                'orig_rect': window_rect(hwnd_child),
                'host': hwnd_host,
            }
        style = style_of(hwnd_child)
        user32.SetWindowLongPtrW(hwnd_child, GWL_STYLE,
                                 (style | WS_CHILD | WS_VISIBLE) & ~_TOPLEVEL_STYLES)
        user32.SetParent(hwnd_child, hwnd_host)
        # 不带动 SWP_NOZORDER：要把它抬到宿主子窗口的顶层，否则会被 WebView2 盖住。
        user32.SetWindowPos(hwnd_child, ctypes.c_void_p(0), pos_x, pos_y, size_w, size_h,
                            SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        state = _CHILD_STATE[hwnd_child]
        state['mode'] = mode
        state['offset_y'] = int(offset_y)
        state['placed'] = {'x': pos_x, 'y': pos_y, 'width': size_w, 'height': size_h}
        return {'ok': True, 'hwnd': hwnd_child, 'host_hwnd': hwnd_host, 'title': title,
                'mode': mode, 'x': pos_x, 'y': pos_y, 'offset_y': int(offset_y),
                'width': size_w, 'height': size_h,
                'host_client': host_rect or client_rect(hwnd_host),
                'dpi': dpi_of(hwnd_child), 'host_dpi': dpi_of(hwnd_host)}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def place(hwnd_child: int, x: int, y: int, width: int, height: int):
    """把已嵌入的子窗口移到新的矩形（前端布局变化时同步）。"""
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持'}
    try:
        hwnd_child = int(hwnd_child)
        if not is_window(hwnd_child):
            return {'ok': False, 'error': '引擎窗口已失效'}
        x, y, width, height = int(x), int(y), max(1, int(width)), max(1, int(height))
        user32.SetWindowPos(hwnd_child, ctypes.c_void_p(0), x, y, width, height,
                            SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        state = _CHILD_STATE.get(hwnd_child)
        if state is not None:
            state['placed'] = {'x': x, 'y': y, 'width': width, 'height': height}
        return {'ok': True, 'hwnd': hwnd_child, 'x': x, 'y': y,
                'width': width, 'height': height}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def fill_host(hwnd_child: int, offset_y=None):
    """按宿主当前客户区重排"铺满模式"的子窗口（宿主 resize 时调用）。

    不能拿宿主的"窗口外框尺寸"当子窗口尺寸：外框含标题栏与边框，
    直接用会把引擎画面裁掉一截。rect 模式（前端指定的引擎视窗）由前端自己重新 `place`，
    这里不插手，否则两边会互相打架。
    """
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持'}
    try:
        hwnd_child = int(hwnd_child)
        state = _CHILD_STATE.get(hwnd_child) or {}
        if state.get('mode') == 'rect':
            return {'ok': True, 'skipped': 'rect-mode', 'hwnd': hwnd_child}
        hwnd_host = int(state.get('host') or user32.GetParent(hwnd_child) or 0)
        if not hwnd_host or not is_window(hwnd_host):
            return {'ok': False, 'error': '宿主窗口已失效'}
        top = state.get('offset_y', 0) if offset_y is None else offset_y
        size_w, size_h, rect = _target_size(hwnd_host, None, None, top)
        if not size_w:
            return {'ok': False, 'error': rect}
        user32.MoveWindow(hwnd_child, 0, int(top), size_w, size_h, True)
        state['placed'] = {'x': 0, 'y': int(top), 'width': size_w, 'height': size_h}
        return {'ok': True, 'hwnd': hwnd_child, 'host_hwnd': hwnd_host,
                'width': size_w, 'height': size_h, 'offset_y': int(top),
                'host_client': rect}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def fill_all(offset_y=None):
    """把所有"铺满模式"的嵌入窗口按当前宿主客户区重排（桌面壳 resize 事件用）。"""
    return [fill_host(hwnd, offset_y) for hwnd in list(_CHILD_STATE)]


def detach(hwnd_child: int):
    """解除嵌入，恢复原始父窗口 / 样式 / 屏幕位置；返回还原后的状态。"""
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持'}
    try:
        hwnd_child = int(hwnd_child)
        if not is_window(hwnd_child):
            _CHILD_STATE.pop(hwnd_child, None)
            return {'ok': True, 'already_gone': True}
        state = _CHILD_STATE.pop(hwnd_child, None)
        if state is None:
            return {'ok': True, 'was_embedded': False}
        parent = state.get('parent') or None
        style = state.get('style')
        if style is not None:
            user32.SetWindowLongPtrW(hwnd_child, GWL_STYLE, style)
        user32.SetParent(hwnd_child, ctypes.c_void_p(parent or 0))
        rect = state.get('orig_rect')
        flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW
        if rect:
            user32.SetWindowPos(hwnd_child, None, rect['x'], rect['y'],
                                rect['width'], rect['height'], flags)
        else:
            user32.SetWindowPos(hwnd_child, None, 0, 0, 0, 0,
                                flags | SWP_NOMOVE | SWP_NOSIZE)
        return {'ok': True, 'was_embedded': True, 'hwnd': hwnd_child,
                'restored_parent': parent, 'restored_rect': rect,
                'style': style_of(hwnd_child), 'dpi': dpi_of(hwnd_child)}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def embedded_children():
    """当前处于嵌入状态的子窗口列表（供状态查询与"防孤儿"检查）。"""
    user32 = _user32()
    out = []
    for hwnd, state in list(_CHILD_STATE.items()):
        out.append({'hwnd': hwnd, 'host': state.get('host'), 'mode': state.get('mode'),
                    'placed': state.get('placed'),
                    'alive': is_window(hwnd) if user32 else False})
    return out


def forget(hwnd_child):
    """引擎进程已退出时清理登记（窗口随进程销毁，没有可解除的东西）。"""
    return _CHILD_STATE.pop(int(hwnd_child), None) is not None


# ---------------------------------------------------------------------------
# 焦点
# ---------------------------------------------------------------------------
def focus(hwnd_child: int, hwnd_host=None, keep_attached: bool = False):
    """把键盘焦点真正交给子窗口，并用 `GetFocus()` 回读验证。

    跨进程 `SetFocus` 直接调用一定失败（焦点属于线程的输入队列），
    必须先 `AttachThreadInput` 把两个线程的输入队列接起来。

    ``keep_attached=True`` 时**不解除挂接**。这是个真实需要：Windows 只把键盘输入
    投给"活动输入队列"里的焦点窗口；引擎窗口属于另一个进程/线程，一旦解除挂接，
    焦点就退回宿主自己的队列，之后合成的/用户的按键都不会再到引擎——
    表现就是"窗口看着嵌进去了，但按键没反应"。
    """
    user32 = _user32()
    if user32 is None:
        return {'ok': False, 'error': '仅 Windows 支持'}
    try:
        hwnd_child = int(hwnd_child)
        if not is_window(hwnd_child):
            return {'ok': False, 'error': '引擎窗口已失效'}
        host = int(hwnd_host or 0)
        if not host:
            state = _CHILD_STATE.get(hwnd_child) or {}
            host = int(state.get('host') or user32.GetParent(hwnd_child) or 0)
        if host and user32.IsIconic(host):
            user32.ShowWindow(host, 9)  # SW_RESTORE

        k32 = ctypes.windll.kernel32
        target_thread = user32.GetWindowThreadProcessId(hwnd_child, None)
        foreground = user32.GetForegroundWindow()
        foreground_thread = (user32.GetWindowThreadProcessId(foreground, None)
                             if foreground else 0)
        current_thread = k32.GetCurrentThreadId()
        target_state = _CHILD_STATE.get(int(hwnd_child)) or {}
        _CHILD_STATE.setdefault(int(hwnd_child), target_state)
        via = []
        attached = []
        try:
            # 只 AttachThreadInput 到引擎线程还不够：后台进程调用 SetForegroundWindow
            # 会被系统直接拒绝（"不允许抢前台"）。必须同时接上当前前台线程的输入队列，
            # Windows 才认我们是"有资格设置前台"的那个进程。
            for thread in {target_thread, foreground_thread}:
                if thread and thread != current_thread:
                    if user32.AttachThreadInput(current_thread, thread, True):
                        attached.append(thread)
            if host:
                user32.BringWindowToTop(host)
                if user32.SetForegroundWindow(host):
                    via.append('setforeground')
            user32.SetFocus(hwnd_child)
            via.append('setfocus')
            got = int(user32.GetFocus() or 0)
            if got != hwnd_child:
                # 有些引擎会在收到 WM_SETFOCUS 前自己抢回焦点，再试一次
                user32.SetFocus(hwnd_child)
                got = int(user32.GetFocus() or 0)
            now_foreground = int(user32.GetForegroundWindow() or 0)
            if keep_attached:
                target_state['attached'] = attached
        finally:
            if not keep_attached:
                for thread in attached:
                    user32.AttachThreadInput(current_thread, thread, False)
        return {'ok': got == hwnd_child, 'focused': got, 'hwnd': hwnd_child,
                'attached': attached, 'kept_attached': bool(keep_attached),
                'foreground': now_foreground,
                'foreground_is_host': (now_foreground == host),
                'via': via}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


def resize(hwnd_child: int, width: int, height: int):
    """按显式宽高调整（保留给旧调用方）。新代码优先用 `fill_host`。"""
    user32 = _user32()
    if user32 is None:
        return False
    try:
        return bool(user32.MoveWindow(int(hwnd_child), 0, 0, int(width), int(height), True))
    except Exception:  # noqa: BLE001
        return False


def terminate_tree(pid: int, timeout: float = 5.0):
    """结束进程及其全部后代，返回被结束的 pid 列表。

    Godot 的 `*_console.exe` 是转发器（会派生子进程）；只 terminate 直接子进程
    会留下一个真正的 GUI 进程和它的窗口 —— 那就是"孤儿窗口"的来源。
    """
    if os.name != 'nt':
        return []
    k32 = ctypes.windll.kernel32
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    pids = sorted(_descendant_pids(int(pid)) - {os.getpid()}, reverse=True)
    killed = []
    handles = []
    for target in pids:
        handle = k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(target))
        if not handle:
            continue
        k32.TerminateProcess(handle, 1)
        handles.append((target, handle))
        killed.append(target)
    deadline = timeout
    for target, handle in handles:
        k32.WaitForSingleObject(handle, int(deadline * 1000))
        k32.CloseHandle(handle)
    return killed

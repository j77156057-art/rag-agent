"""运行画面截图：纯 ctypes（GDI）+ Pillow，非 Windows/无头安全降级。

设计边界：
* **不引入 pywin32**，只用 user32/gdi32 的 ctypes 绑定；
* **不提供按任意 HWND/PID 截图的入口**：抓取目标只能来自 desktop_bridge 的
  嵌入子窗口登记表或系统前台窗口，Agent 无法借此枚举/窥视其它进程窗口；
* PrintWindow(PW_RENDERFULLCONTENT=2) 优先（对 GPU 渲染窗口兼容性最好），
  失败回退 GetDC + BitBlt；
* 底层取像素函数可注入（``frame_grabber``），测试用替身即可在任何平台跑。
"""
import ctypes
import ctypes.wintypes
import datetime
import io
import os
import random

# PW_RENDERFULLCONTENT：Win10 1607+，对 DirectComposition/GPU 内容也能出图；
# 老系统不认这个 flag 时 PrintWindow 返回失败，调用方自动回退 BitBlt。
PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
CAPTURE_MAX_EDGE = 1600
JPEG_QUALITY = 82


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", ctypes.wintypes.DWORD * 3)]


def _libs():
    """配置并返回 (user32, gdi32)；非 Windows 返回 (None, None)。"""
    if os.name != "nt":
        return None, None
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetClientRect.restype = ctypes.wintypes.BOOL
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.wintypes.UINT]
    user32.PrintWindow.restype = ctypes.wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_int, ctypes.wintypes.DWORD]
    gdi32.BitBlt.restype = ctypes.wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.UINT,
        ctypes.wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO), ctypes.wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.wintypes.BOOL
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.wintypes.BOOL
    return user32, gdi32


def capture_window(hwnd, *, frame_grabber=None):
    """抓取单个窗口客户区，返回 ``(bgra_bytes, width, height)``；失败返回 None。

    ``frame_grabber(hwnd)`` 注入时直接使用注入实现（测试替身），否则走 GDI。
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return None
    if frame_grabber is not None:
        return frame_grabber(hwnd)
    user32, gdi32 = _libs()
    if user32 is None:
        return None
    rect = ctypes.wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = mem_dc = bmp = prev_bmp = None
    try:
        hwnd_dc = user32.GetDC(hwnd)
        if not hwnd_dc:
            return None
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        if not mem_dc or not bmp:
            return None
        prev_bmp = gdi32.SelectObject(mem_dc, bmp)
        printed = int(user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT) or 0)
        if printed != 1:
            # GPU/最小化等场景 PrintWindow 失败：回退屏幕 BitBlt（被遮挡区域会丢内容，
            # 但比没有证据强；观察文本会注明来源方式由调用方按需扩展）。
            if not gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, SRCCOPY):
                return None
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 负值：top-down，免去行序翻转
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = (ctypes.c_ubyte * (width * height * 4))()
        scanned = gdi32.GetDIBits(mem_dc, bmp, 0, height, buffer,
                                  ctypes.byref(info), DIB_RGB_COLORS)
        if scanned != height:
            return None
        return bytes(buffer), width, height
    except Exception:
        return None
    finally:
        if gdi32 is not None:
            # 必须先把位图选出 mem_dc 再 DeleteObject，否则 GDI 删除失败、
            # 每次截图泄漏一个 DIB 对象（长驻进程会累积 GDI 句柄与内存）。
            if bmp and mem_dc and prev_bmp:
                gdi32.SelectObject(mem_dc, prev_bmp)
            if bmp:
                gdi32.DeleteObject(bmp)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if hwnd_dc is not None and user32 is not None:
                user32.ReleaseDC(hwnd, hwnd_dc)


def _embedded_target(project_id=None):
    """从嵌入登记表选当前项目的子窗口 HWND；不接受外部传入任意 HWND。

    多候选时选 placed 面积最大者（引擎主视口通常大于 splash/工具窗）；
    登记表为空 / 窗口已死 / 无有效矩形时返回 None。
    """
    try:
        import desktop_bridge
    except Exception:
        return None
    children = [c for c in desktop_bridge.embedded_children()
                if c.get("alive") and (c.get("placed") or {}).get("width")]
    if not children:
        return None
    host = desktop_bridge.host_hwnd(project_id) if project_id else None
    if host is not None:
        matched = [c for c in children if c.get("host") == host]
        if matched:
            children = matched

    def _area(item):
        placed = item.get("placed") or {}
        return int(placed.get("width") or 0) * int(placed.get("height") or 0)

    return int(max(children, key=_area)["hwnd"])


def grab_embedded(project_id=None, *, frame_grabber=None):
    """抓取当前项目嵌入的引擎窗口，返回 (bgra, w, h, hwnd) 或 None。"""
    hwnd = _embedded_target(project_id)
    if not hwnd:
        return None
    frame = capture_window(hwnd, frame_grabber=frame_grabber)
    if not frame:
        return None
    raw, width, height = frame
    return raw, width, height, hwnd


def grab_foreground(*, frame_grabber=None):
    """抓取系统前台窗口，返回 (bgra, w, h, hwnd) 或 None。"""
    user32, _gdi = _libs()
    if user32 is None:
        return None
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd:
        return None
    frame = capture_window(hwnd, frame_grabber=frame_grabber)
    if not frame:
        return None
    raw, width, height = frame
    return raw, width, height, hwnd


def save_encoded_jpeg(data_url, out_dir, tag="mcp"):
    """把已重编码的 JPEG data URL 落盘，返回保存路径；任何失败返回 None。

    连接器截图源与本地 GDI 抓帧走同一保存目录/时间戳命名，保证观察文本
    对所有成功源都给出可审计的文件路径。
    """
    try:
        import base64
        header, _, b64 = str(data_url or "").partition(",")
        if not b64 or "image/jpeg" not in header:
            return None
        payload = base64.b64decode(b64, validate=False)
        if not payload or payload[:2] != b"\xff\xd8":
            return None
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_tag = "".join(ch for ch in str(tag) if ch.isalnum() or ch in "-_")[:24] or "mcp"
        path = os.path.join(
            out_dir, f"shot-{stamp}-{safe_tag}-{random.randint(0, 9999):04d}.jpg")
        with open(path, "wb") as handle:
            handle.write(payload)
        return path
    except Exception:
        return None


def encode_and_save(raw, width, height, out_dir):
    """BGRA 像素 → 最长边 1600 JPEG q82，落盘并返回 (data_url, path, (w, h))。

    任何编码/落盘失败返回 None；调用方据此走「截图不可用」文案。
    """
    try:
        from PIL import Image
        img = Image.frombytes("RGB", (int(width), int(height)), bytes(raw),
                              "raw", "BGRX")
        if max(img.size) > CAPTURE_MAX_EDGE:
            img.thumbnail((CAPTURE_MAX_EDGE, CAPTURE_MAX_EDGE), Image.LANCZOS)
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(
            out_dir, f"shot-{stamp}-{random.randint(0, 9999):04d}.jpg")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        payload = buf.getvalue()
        with open(path, "wb") as handle:
            handle.write(payload)
        import base64
        data_url = "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii")
        return data_url, path, img.size
    except Exception:
        return None

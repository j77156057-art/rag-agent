"""DocMind 桌面端启动器（控制台模式，最稳）。

为什么是控制台而不是原生窗口：
  早期版本用 pywebview/WebView2 弹原生窗口，在缺少 WebView2 运行时的机器上会
  静默失败（不报错也不弹窗、进程还活着），表现为"双击没反应"。后又试过 tkinter
  控制窗，同样可能在某些机器上建不出窗口。因此改为最朴素的方案：exe 本身带一个
  可见的控制台窗口，启动后打印本地地址并自动尝试打开默认浏览器；窗口常驻直到用户
  按 Enter 退出。界面本身就是 Web 应用，浏览器即桌面端，跨机器稳定。

特性：
  - 单实例保护：若 8000 端口已在提供 DocMind 服务，直接打开浏览器并退出，避免堆积进程。
  - 启动全程写入 docmind_desktop.log（与 exe 同目录）便于排查。

环境变量：
  DOCMIND_SERVER_ONLY=1   仅启动后端服务模式（无界面 / 容器 / 自动化测试用）。
"""
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

from api import app
from tools import dev_capture_bug

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}/"

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "docmind_desktop.log")


def _ensure_bundled_git():
    """分发版随包携带 MinGit（<exe目录>/MinGit/cmd/git.exe）。

    前置进 PATH 后，所有裸 `git` 子进程（regions.workbench_fs 的版本状态、
    分区初始化/提交/回滚）都能命中，用户机器无需自行安装 Git。
    源码运行或包内缺目录时不做任何处理（回落到系统 PATH）。
    """
    if not getattr(sys, "frozen", False):
        return
    git_cmd_dir = os.path.join(BASE, "MinGit", "cmd")
    if os.path.isfile(os.path.join(git_cmd_dir, "git.exe")):
        os.environ["PATH"] = git_cmd_dir + os.pathsep + os.environ.get("PATH", "")
        _log("已启用随包 Git（MinGit）")
    else:
        _log("未找到随包 MinGit，分区 Git 功能需要系统已安装 Git")


def _log(msg: str):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(msg)


def _already_running() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}api/config", timeout=1) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _wait_for_server(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}api/config", timeout=1):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.3)
    return False


def _run_server():
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _open_browser() -> bool:
    try:
        return bool(webbrowser.open(URL))
    except Exception:  # noqa: BLE001
        return False

def _open_native_window() -> bool:
    """Prefer a native pywebview window; return False when its runtime is unavailable."""
    try:
        import webview
        _log("检测到 pywebview %s，尝试创建 Edge 原生窗口" % getattr(webview, '__version__', 'unknown'))
        win = webview.create_window("DocMind 开发工作台", URL + "workbench/", width=1440, height=920,
                                    min_size=(1024, 680), text_select=True)
        embedded_child = {'hwnd': None}
        def _loaded():
            # pywebview 已创建原生窗口后，通过 Win32 标题枚举获取宿主 HWND。
            try:
                from desktop_bridge import find_host
                host = find_host("DocMind")
                if not host:
                    _log("未找到 pywebview 宿主 HWND，Godot 保持独立窗口")
                    return
                _log("已找到 DocMind 宿主 HWND: %s" % host[0])
                import desktop_bridge
                desktop_bridge.set_host(host[0])
                try:
                    req = urllib.request.Request(URL + "api/desktop/host", data=(('{"hwnd": %d}' % host[0]).encode()), headers={'Content-Type':'application/json'}, method='POST')
                    urllib.request.urlopen(req, timeout=2).read()
                except Exception as e:
                    _log("宿主 HWND 注册后端失败：" + str(e))
            except Exception as e:
                _log("宿主 HWND 检测失败：" + str(e))
        def _resized(*_args):
            # pywebview 不同版本的事件参数不同；从窗口对象读取尺寸并转发给桥接层。
            try:
                if not embedded_child.get('hwnd'): return
                w, h = int(win.width), int(win.height)
                from desktop_bridge import resize
                resize(embedded_child['hwnd'], w, h)
            except Exception as e:
                _log("嵌入窗口尺寸同步失败：" + str(e))
        try:
            # pywebview 事件属于具体窗口对象；绑定全局 webview.events 在部分版本不会触发。
            win.events.loaded += _loaded
            if hasattr(win.events, 'resized'):
                win.events.resized += _resized
        except Exception:
            pass
        _log("开始运行 pywebview 事件循环")
        webview.start(gui="edgechromium", debug=False)
        _log("pywebview 事件循环已退出")
        return True
    except Exception as e:
        _log("原生桌面窗口不可用，回退浏览器：" + str(e))
        return False


def _keep_alive():
    """常驻直到用户按 Enter；无法读取输入时退化为保活睡眠。"""
    try:
        input("\n服务运行中。按 Enter 退出 DocMind（关闭浏览器标签不会停止服务）。\n")
    except EOFError:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def _emit_ollama_guidance():
    """若本机 Ollama 不可用，打印清晰引导（而非让用户对着报错发懵）。"""
    try:
        from api import check_ollama
        st = check_ollama()
        if st.get("guidance"):
            _log("Ollama 未就绪或缺少模型")
            print("\n" + "=" * 48)
            print("  WARNING  Ollama 不可用 / 缺少所需模型")
            print("=" * 48)
            for line in (st.get("guidance") or "").splitlines():
                print("  " + line)
            print()
    except Exception as e:  # noqa: BLE001
        _log("Ollama 检查失败：" + str(e))


def main():
    _ensure_bundled_git()
    if os.getenv("DOCMIND_SERVER_ONLY"):
        _log("服务模式启动")
        _emit_ollama_guidance()
        _run_server()
        return

    _log("桌面模式启动")
    print("=" * 48)
    print("  DocMind · 本地 RAG 代码问答")
    print("=" * 48)

    if _already_running():
        _log("检测到已有实例在运行，直接打开浏览器")
        print(f"[DocMind] 已在运行，正在打开浏览器：{URL}")
        ok = _open_browser()
        if not ok:
            print(f"[DocMind] 请手动在浏览器打开：{URL}")
        input("按 Enter 退出本启动器。\n")
        return

    server = threading.Thread(target=_run_server, daemon=True)
    server.start()

    if not _wait_for_server():
        _log("服务启动失败")
        print("[DocMind] 服务启动失败：请检查 8000 端口是否被占用。")
        print(f"          详情见日志：{LOG}")
        input("按 Enter 退出。\n")
        return

    _log("服务就绪")
    print(f"[DocMind] 服务已就绪：{URL}")
    _emit_ollama_guidance()
    ok = _open_native_window()
    _log("原生窗口启动结果 -> %s" % ok)
    if not ok:
        ok = _open_browser()
    _log("浏览器打开尝试 -> %s" % ok)
    if ok:
        print(f"[DocMind] 已尝试用默认浏览器打开页面。")
    else:
        print(f"[DocMind] 未能自动打开浏览器，请手动访问：{URL}")

    _keep_alive()
    _log("退出")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        _log("未预期异常：\n" + tb)
        try:
            # 若已配置代码库，异常会同时归档到 bugs 分区，便于按提交追踪。
            dev_capture_bug(f"title: DocMind 启动异常\nerror: {tb.splitlines()[-1] if tb else 'unknown'}\ntraceback: {tb}")
        except Exception as capture_error:
            _log("Bug 归档失败：" + str(capture_error))
        print("启动过程出现未预期错误：\n" + tb)
        try:
            input("按 Enter 退出。\n")
        except Exception:
            pass

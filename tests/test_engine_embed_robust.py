"""引擎嵌入健壮性纯逻辑测试（不依赖真实 Win32 窗口，沙箱可跑）。

覆盖本轮深扫补强的两块纯逻辑：
* D：find_window 的窗口打分选择 `_rank_windows`（标题命中 > 非最小化 > 面积大）。
* F：`_target_size` 在宿主尺寸读不到（最小化/未布局）时回退缓存矩形，否则报错。
真实 Win32 行为由 `verify_engine_embed.py` 在真机复验。
"""
import unittest

from desktop_bridge import _rank_windows, _target_size


class RankWindowsTest(unittest.TestCase):
    def test_prefers_title_hint(self):
        cands = [
            {'hwnd': 1, 'title': 'Godot Engine', 'iconic': False, 'area': 100},
            {'hwnd': 2, 'title': 'My Game', 'iconic': False, 'area': 100},
        ]
        self.assertEqual(_rank_windows(cands, 'my game')['hwnd'], 2)

    def test_prefers_non_iconic(self):
        cands = [
            {'hwnd': 1, 'title': 'A', 'iconic': True, 'area': 1000},
            {'hwnd': 2, 'title': 'B', 'iconic': False, 'area': 100},
        ]
        self.assertEqual(_rank_windows(cands, '')['hwnd'], 2)

    def test_prefers_larger_area(self):
        cands = [
            {'hwnd': 1, 'title': 'A', 'iconic': False, 'area': 50000},
            {'hwnd': 2, 'title': 'B', 'iconic': False, 'area': 100},
        ]
        self.assertEqual(_rank_windows(cands, '')['hwnd'], 1)

    def test_title_beats_area(self):
        # 标题命中必须压过面积：避免大 splash 窗口盖过真视口
        cands = [
            {'hwnd': 1, 'title': 'Target', 'iconic': False, 'area': 100},
            {'hwnd': 2, 'title': 'Other', 'iconic': False, 'area': 999999},
        ]
        self.assertEqual(_rank_windows(cands, 'target')['hwnd'], 1)

    def test_empty(self):
        self.assertIsNone(_rank_windows([], 'x'))


class ResolveHostSizeTest(unittest.TestCase):
    """`_target_size` 的显式尺寸 / 缓存回退 / 报错三态。

    用 monkey-patch 把 client_rect 钉成 None，模拟"宿主最小化或尚未布局、
    GetClientRect 读不到尺寸"的环境，从而稳定触发缓存回退分支。
    """

    def setUp(self):
        import desktop_bridge
        self._db = desktop_bridge
        self._orig = desktop_bridge.client_rect
        desktop_bridge.client_rect = lambda hwnd: None  # 模拟读不到宿主客户区

    def tearDown(self):
        self._db.client_rect = self._orig

    def test_explicit_size_wins(self):
        w, h, rect = _target_size(999, 800, 600, 0)
        self.assertEqual((w, h), (800, 600))
        self.assertIsNone(rect)

    def test_fallback_to_cache(self):
        w, h, rect = _target_size(999, None, None, 0,
                                  {'width': 1280, 'height': 720})
        self.assertEqual((w, h), (1280, 720))
        self.assertEqual(rect['width'], 1280)

    def test_offset_y_applied_on_fallback(self):
        w, h, rect = _target_size(999, None, None, 40,
                                  {'width': 1280, 'height': 720})
        self.assertEqual((w, h), (1280, 680))

    def test_no_cache_is_error(self):
        w, h, rect = _target_size(999, None, None, 0, None)
        self.assertIsNone(w)
        self.assertIsInstance(rect, str)


if __name__ == '__main__':
    unittest.main()

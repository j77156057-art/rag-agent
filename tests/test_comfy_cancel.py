import unittest, json
from unittest.mock import patch, MagicMock
import game_workbench as gw


def _mock_resp(status=200, body=b'{"deleted": ["p1"]}'):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class CancelTests(unittest.TestCase):
    def test_cancel_deletes_by_prompt_id(self):
        with patch('game_workbench.urllib.request.urlopen', return_value=_mock_resp()) as u:
            x = gw.comfy_cancel('p1')
        # 修正后口径：POST /queue 带 {"delete":["p1"]} 精确取消，不再误用 /interrupt
        self.assertTrue(x['ok'])
        self.assertTrue(x['deleted'])
        self.assertEqual(x['prompt_id'], 'p1')
        self.assertIn('lease_released', x)
        u.assert_called_once()
        called_url = u.call_args.args[0].full_url
        self.assertTrue(called_url.endswith('/queue'), f"应调用 /queue，实际: {called_url}")
        body = json.loads(u.call_args.args[0].data.decode())
        self.assertEqual(body, {'delete': ['p1']})

    def test_cancel_marks_watch_job(self):
        gw._COMFY_JOBS['p2'] = {'prompt_id': 'p2', 'running': True, 'done': False}
        try:
            with patch('game_workbench.urllib.request.urlopen', return_value=_mock_resp(body=b'{"deleted": ["p2"]}')):
                gw.comfy_cancel('p2')
            self.assertTrue(gw._COMFY_JOBS['p2']['cancel_requested'])
            self.assertIn('cancel_requested_at', gw._COMFY_JOBS['p2'])
        finally:
            gw._COMFY_JOBS.pop('p2', None)

    def test_cancel_comfyui_down_returns_error_not_ok(self):
        # ComfyUI 不可达：/queue delete 失败；有 watcher 时不释放租约 → 取消不成功
        gw._COMFY_JOBS['p3'] = {'prompt_id': 'p3', 'running': True, 'done': False}
        try:
            with patch('game_workbench.urllib.request.urlopen', side_effect=OSError('connection refused')):
                x = gw.comfy_cancel('p3')
            self.assertFalse(x['ok'])
            self.assertFalse(x['deleted'])
            self.assertFalse(x['lease_released'])
        finally:
            gw._COMFY_JOBS.pop('p3', None)


if __name__ == '__main__':
    unittest.main()

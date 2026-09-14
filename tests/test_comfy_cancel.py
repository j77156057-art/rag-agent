import unittest
from unittest.mock import patch, MagicMock
import game_workbench as gw


class CancelTests(unittest.TestCase):
    def test_cancel_request(self):
        resp = MagicMock()
        resp.read.return_value = b'ok'
        resp.status = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch('game_workbench.urllib.request.urlopen', return_value=resp) as u:
            x = gw.comfy_cancel('p1')
        # main 口径：POST /interrupt + force_release(comfyui:<id>)，不再是分支的仅打标记
        self.assertTrue(x['ok'])
        self.assertTrue(x['interrupted'])
        self.assertIn('lease_released', x)
        self.assertEqual(x['prompt_id'], 'p1')
        u.assert_called_once()

    def test_cancel_marks_watch_job(self):
        resp = MagicMock()
        resp.read.return_value = b'ok'
        resp.status = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        gw._COMFY_JOBS['p2'] = {'prompt_id': 'p2', 'running': True, 'done': False}
        try:
            with patch('game_workbench.urllib.request.urlopen', return_value=resp):
                gw.comfy_cancel('p2')
            self.assertTrue(gw._COMFY_JOBS['p2']['cancel_requested'])
            self.assertIn('cancel_requested_at', gw._COMFY_JOBS['p2'])
        finally:
            gw._COMFY_JOBS.pop('p2', None)


if __name__ == '__main__':
    unittest.main()

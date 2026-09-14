import os
import unittest
import tempfile
from unittest.mock import patch, MagicMock
import game_workbench as gw


class EngineGpuLeaseTests(unittest.TestCase):
    def test_busy_gpu_blocks_engine(self):
        with tempfile.TemporaryDirectory() as d:
            busy = {'ok': False, 'reason': 'busy', 'gpu': None}
            with patch.object(gw._gpu, 'acquire_lease', return_value=busy), \
                 patch.object(gw, '_resolve_engine_executable', return_value='dummy'):
                x = gw.engine_start(d)
            self.assertFalse(x['ok'])
            self.assertIn('GPU', x['error'])

    def test_start_injects_env_and_stop_releases(self):
        """成功路径：Popen 必须拿到带 CUDA_VISIBLE_DEVICES 的环境副本；engine_stop 释放租约。"""
        with tempfile.TemporaryDirectory() as d:
            proc = MagicMock()
            proc.pid = 4321
            proc.poll.return_value = None
            with patch.object(gw, '_resolve_engine_executable', return_value='dummy'), \
                 patch.object(gw.subprocess, 'Popen', return_value=proc) as popen, \
                 patch.object(gw, 'engine_detach', return_value={'was_embedded': False}), \
                 patch('desktop_bridge.terminate_tree', return_value=[4321]):
                x = gw.engine_start(d)
                self.assertTrue(x.get('ok'), x)
                child_env = popen.call_args.kwargs['env']
                self.assertIsNot(child_env, os.environ)  # 副本注入，不改工作台自身
                self.assertEqual(child_env['CUDA_VISIBLE_DEVICES'], str(x['gpu']))
                self.assertEqual(child_env['DOCMIND_GPU_INDEX'], str(x['gpu']))
                owner = 'engine:' + os.path.abspath(d)
                self.assertIn(owner, [h['owner'] for h in gw._gpu.status()['holders']])

                y = gw.engine_stop(d)
                self.assertTrue(y.get('ok'), y)
                self.assertEqual(gw._gpu.status()['holders'], [])


if __name__ == '__main__':
    unittest.main()

import os
import unittest
import tempfile
from unittest.mock import patch, MagicMock
import game_workbench as gw


class EngineGpuLeaseTests(unittest.TestCase):
    def tearDown(self):
        gw._ENGINE_PROCS.clear()
        gw._ENGINE_LOGS.clear()
        gw._ENGINE_LAUNCH.clear()
        gw._EMBED_STATE.clear()
        gw._gpu.force_release()

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

    def test_reload_restarts_and_preserves_launch_shape(self):
        """热重载应停止旧进程并把原来的项目、矩形和嵌入参数交给新进程。"""
        with tempfile.TemporaryDirectory() as d:
            old = MagicMock(); old.pid = 1001; old.poll.return_value = None
            new = MagicMock(); new.pid = 1002; new.poll.return_value = None
            rect = {'x': 11, 'y': 22, 'width': 640, 'height': 360}
            with patch.object(gw, '_resolve_engine_executable', return_value='dummy'), \
                 patch.object(gw.subprocess, 'Popen', side_effect=[old, new, new]) as popen, \
                 patch.object(gw._gpu, '_probe_override', lambda: [{'index': 0, 'name': 'test', 'used_mb': 0, 'total_mb': 8192, 'free_mb': 8192, 'utilization': 0, 'temperature_c': 0}]), \
                 patch.object(gw, 'engine_detach', return_value={'was_embedded': True}), \
                 patch('desktop_bridge.terminate_tree', return_value=[1001]), \
                 patch.object(gw, 'engine_embed', return_value={'ok': True, 'hwnd': 77, 'width': 640, 'height': 360}) as embed:
                started = gw.engine_start(d, host_hwnd=9, embed=True, rect=rect)
                self.assertTrue(started.get('running'), started)
                result = gw.engine_reload(d)
            self.assertTrue(result.get('ok'), result)
            self.assertTrue(result.get('reloaded'), result)
            self.assertEqual(result.get('previous_pid'), 1001)
            self.assertEqual(embed.call_args.kwargs.get('rect'), rect)
            self.assertEqual(gw._ENGINE_LAUNCH[os.path.abspath(d)]['rect'], rect)
            self.assertEqual(gw._ENGINE_LAUNCH[os.path.abspath(d)]['host_hwnd'], 9)


if __name__ == '__main__':
    unittest.main()

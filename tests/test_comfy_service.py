import os
import tempfile
import unittest
from unittest.mock import patch

import game_workbench as gw


class ComfyServiceTests(unittest.TestCase):
    def test_start_rejects_missing_portable_install(self):
        with tempfile.TemporaryDirectory() as root:
            result = gw.comfy_start(root)
        self.assertFalse(result['ok'])
        self.assertIn('Python', result['error'])

    def test_start_registers_pid_and_stop_unregisters(self):
        with tempfile.TemporaryDirectory() as root:
            py = os.path.join(root, 'python_embeded'); os.makedirs(py)
            comfy = os.path.join(root, 'ComfyUI'); os.makedirs(comfy)
            open(os.path.join(py, 'python.exe'), 'wb').close()
            open(os.path.join(comfy, 'main.py'), 'wb').close()
            class Proc:
                pid = 45678
                def poll(self): return None
                def terminate(self): pass
                def wait(self, timeout): return 0
            with patch('game_workbench.subprocess.Popen', return_value=Proc()), \
                 patch.object(gw._gpu, 'register_process') as register, \
                 patch.object(gw._gpu, 'unregister_process') as unregister:
                gw._COMFY_SERVICE = None
                result = gw.comfy_start(root, 8199)
                self.assertTrue(result['ok']); self.assertEqual(result['pid'], 45678)
                register.assert_called_once_with(45678, 'comfyui:service', None, 'comfyui')
                stopped = gw.comfy_stop()
                self.assertTrue(stopped['stopped']); unregister.assert_called_once_with(45678, 'stopped')
                gw._COMFY_SERVICE = None


if __name__ == '__main__': unittest.main()

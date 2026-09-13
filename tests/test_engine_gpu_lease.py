import unittest,tempfile
from unittest.mock import patch
import game_workbench as gw
class EngineGpuLeaseTests(unittest.TestCase):
 def test_busy_gpu_blocks_engine(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(gw, '_gpu_acquire', return_value=False), patch.object(gw, '_resolve_engine_executable', return_value='dummy'):
    x=gw.engine_start(d)
   self.assertFalse(x['ok']); self.assertIn('GPU', x['error'])
if __name__=='__main__': unittest.main()

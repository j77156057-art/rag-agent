import unittest,os
import gpu_coordinator as g
class DeviceBindingTests(unittest.TestCase):
 def test_status_reports_device(self):
  old=os.environ.get('DOCMIND_GPU_INDEX'); os.environ['DOCMIND_GPU_INDEX']='2'
  try:
   g.force_release(); self.assertTrue(g.acquire('dev-test',.1)); self.assertEqual(g.status()['device_index'],2); g.release('dev-test')
  finally:
   if old is None: os.environ.pop('DOCMIND_GPU_INDEX',None)
   else: os.environ['DOCMIND_GPU_INDEX']=old
if __name__=='__main__': unittest.main()

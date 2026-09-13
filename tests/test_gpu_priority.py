import unittest, threading, time
import gpu_coordinator as g
class PriorityTests(unittest.TestCase):
 def setUp(self):
  g.force_release(); g.cancel('low'); g.cancel('high')
 def test_priority_handover(self):
  self.assertTrue(g.acquire('holder',.1)); got=[]
  def wait(name,p):
   if g.acquire(name,2,purpose=name,priority=p): got.append(name)
  a=threading.Thread(target=wait,args=('low',0)); b=threading.Thread(target=wait,args=('high',10)); a.start(); time.sleep(.03); b.start(); time.sleep(.05); g.release('holder'); a.join(); b.join()
  self.assertEqual(got[0],'high'); g.release('high'); g.release('low')
if __name__=='__main__': unittest.main()

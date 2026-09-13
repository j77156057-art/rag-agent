import unittest,tempfile,os
import game_workbench as gw
class UnusedTests(unittest.TestCase):
 def test_unused(self):
  with tempfile.TemporaryDirectory() as d:
   p=os.path.join(d,'assets','generated'); os.makedirs(p)
   open(os.path.join(p,'unused.png'),'wb').write(b'x'); open(os.path.join(p,'used.png'),'wb').write(b'x')
   open(os.path.join(d,'scene.tscn'),'w').write('used.png')
   x=gw.comfy_unused_resources(d); self.assertEqual(x['unused'],['assets/generated/unused.png'])
if __name__=='__main__': unittest.main()

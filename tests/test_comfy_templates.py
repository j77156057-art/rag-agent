import unittest, tempfile, os
import game_workbench as gw
class T(unittest.TestCase):
 def test_z(self): self.assertTrue(gw.comfy_template_workflow('z-image-turbo')['ok'])
 def test_unknown(self): self.assertFalse(gw.comfy_template_workflow('x')['ok'])
if __name__=='__main__': unittest.main()

import unittest, tempfile, os
import game_workbench as gw
class T(unittest.TestCase):
 def test_z(self): self.assertTrue(gw.comfy_template_workflow('z-image-turbo')['ok'])
 def test_unknown(self): self.assertFalse(gw.comfy_template_workflow('x')['ok'])
 def test_apply_keeps_negative_prompt_separate(self):
  w=gw.comfy_template_workflow('z-image-turbo')['workflow']
  r=gw.comfy_apply_parameters(w, {'prompt':'good','negative_prompt':'bad'})
  self.assertEqual(r['workflow']['3']['inputs']['prompt'],'good')
  self.assertEqual(r['workflow']['4']['inputs']['prompt'],'bad')
 def test_template_exposes_provenance(self):
  t=gw.comfy_templates()['templates'][0]
  self.assertTrue(t['author']); self.assertTrue(t['source_url']); self.assertTrue(t['license'])
if __name__=='__main__': unittest.main()

import unittest, json
from unittest.mock import patch, MagicMock
import game_workbench as g
class P(unittest.TestCase):
 def test_history_has_progress_contract(self):
  self.assertTrue(callable(g.comfy_history))
 def test_unfinished_history_is_not_claimed_live_after_restart(self):
  import inspect
  source = inspect.getsource(g._load_comfy_history)
  self.assertIn("status': 'recovered'", source)
  self.assertIn('recovery_note', source)
 def test_history_classifies_3d_output(self):
  resp=MagicMock(); resp.read.return_value=json.dumps({'p3d':{'outputs':{'n':{'images':[{'filename':'hero.glb'}]}},'status':{'status_str':'success'},'prompt':{}}}).encode(); resp.__enter__.return_value=resp; resp.__exit__.return_value=False
  with patch('game_workbench.urllib.request.urlopen', return_value=resp):
   result=g.comfy_history('p3d')
  self.assertEqual(result['outputs'][0]['asset_kind'],'3d')
if __name__=='__main__': unittest.main()

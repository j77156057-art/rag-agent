import unittest
import game_workbench as g
class P(unittest.TestCase):
 def test_history_has_progress_contract(self):
  self.assertTrue(callable(g.comfy_history))
if __name__=='__main__': unittest.main()

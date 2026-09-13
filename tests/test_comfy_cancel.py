import unittest
from unittest.mock import patch, MagicMock
import game_workbench as gw
class CancelTests(unittest.TestCase):
 def test_cancel_request(self):
  resp=MagicMock(); resp.read.return_value=b'ok'
  with patch('game_workbench.urllib.request.urlopen',return_value=resp) as u:
   x=gw.comfy_cancel('p1'); self.assertTrue(x['cancel_requested']); u.assert_called_once()
if __name__=='__main__': unittest.main()

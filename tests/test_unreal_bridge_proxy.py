import unittest
from unittest.mock import patch, MagicMock
import api
class BridgeProxyTests(unittest.IsolatedAsyncioTestCase):
 async def test_assets_proxy(self):
  resp=MagicMock(); resp.__enter__.return_value=resp; resp.read.return_value=b'{"assets":["/Game/BP"]}'
  with patch('api.urllib.request.urlopen', return_value=resp):
   x=await api.unreal_bridge_assets_ep(); self.assertEqual(x['assets'],['/Game/BP'])
if __name__=='__main__': unittest.main()

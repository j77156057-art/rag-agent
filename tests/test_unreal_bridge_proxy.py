import unittest
from unittest.mock import patch, MagicMock
import api
class BridgeProxyTests(unittest.IsolatedAsyncioTestCase):
 async def test_assets_proxy(self):
  resp=MagicMock(); resp.__enter__.return_value=resp; resp.read.return_value=b'{"assets":["/Game/BP"]}'
  with patch('api.urllib.request.urlopen', return_value=resp):
   x=await api.unreal_bridge_assets_ep(); self.assertEqual(x['assets'],['/Game/BP'])
 async def test_write_requires_confirmation(self):
  x=await api.unreal_bridge_write_ep(api.UnrealWriteReq(task_id='t',target_path='Config/x.ini',property='x',value=1,confirm=False))
  self.assertEqual(x.status_code,400)
 async def test_write_rejects_binary_asset(self):
  x=await api.unreal_bridge_write_ep(api.UnrealWriteReq(task_id='t',target_path='Content/A.uasset',property='x',value=1,confirm=True))
  self.assertEqual(x.status_code,400)
 async def test_write_offline_is_explicit(self):
  x=await api.unreal_bridge_write_ep(api.UnrealWriteReq(task_id='t',target_path='Config/x.ini',property='x',value=1,confirm=True))
  self.assertFalse(x['ok']); self.assertFalse(x['available'])
if __name__=='__main__': unittest.main()

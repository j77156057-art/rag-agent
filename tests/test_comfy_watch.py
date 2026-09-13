import unittest, game_workbench as gw, time
class ComfyWatchTests(unittest.TestCase):
 def test_watch_lifecycle(self):
  old=gw.comfy_wait
  try:
   gw.comfy_wait=lambda *a,**k:{'ok':True,'done':True,'outputs':[]}
   started=gw.comfy_watch('watch-test', timeout=1, interval=.1)
   self.assertTrue(started['ok'])
   for _ in range(20):
    s=gw.comfy_watch_status('watch-test')
    if s['job'] and not s['job']['running']: break
    time.sleep(.01)
   self.assertFalse(s['job']['running']); self.assertTrue(s['job']['done'])
  finally: gw.comfy_wait=old
if __name__=='__main__': unittest.main()

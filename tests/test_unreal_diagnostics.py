import unittest
import game_workbench as gw

class UnrealDiagnosticsTests(unittest.TestCase):
    def test_parse_common_formats(self):
        text='Source/MyActor.cpp(42): error C2065: use of undeclared identifier\nSource/MyActor.h(12,4): warning C4100: unused parameter\n'
        got=gw.parse_unreal_diagnostics(text)
        self.assertEqual(got[0]['path'],'Source/MyActor.cpp'); self.assertEqual(got[0]['line'],42)
        self.assertEqual(got[0]['severity'],'error'); self.assertIn('C2065',got[0]['message'])
        self.assertEqual(got[1]['line'],12); self.assertEqual(got[1]['severity'],'warning')
    def test_deduplicates(self):
        line='Source/A.cpp(1): error: bad\n'
        self.assertEqual(len(gw.parse_unreal_diagnostics(line+line)),1)
if __name__=='__main__': unittest.main()

import os, tempfile, unittest
from game_workbench import validate_task_scope, task_snapshot, engine_logs, scene_tree, runtime_events
from gpu_coordinator import acquire, release, status

class AgentWorkflowTests(unittest.TestCase):
    def test_task_scope_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'behaviors'))
            p=os.path.join(root,'behaviors','player.gd')
            with open(p,'w') as fh: fh.write('x')
            task={'region':'behaviors','files':['behaviors/player.gd'],'allowed_paths':['behaviors']}
            self.assertTrue(validate_task_scope(root,task)['ok'])
            self.assertIsNotNone(task_snapshot(root,task)['files']['behaviors/player.gd'])
            self.assertFalse(validate_task_scope(root, {'region':'behaviors','files':['values/x.gd']})['ok'])

    def test_engine_log_error_parse(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root,'.docmind_engine.log'),'w') as f: f.write('SCRIPT ERROR: res://behaviors/player.gd:12 parse error\n')
            r=engine_logs(root); self.assertEqual(r['errors'][0]['line'],12)
            self.assertEqual(r['errors'][0]['path'],'behaviors/player.gd')

    def test_gpu_serial_gate(self):
        self.assertTrue(acquire('test', 0.1)); self.assertEqual(status()['active'],'test'); release('test'); self.assertIsNone(status()['active'])

    def test_scene_tree_and_runtime_events(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'scenes'))
            with open(os.path.join(root,'scenes','main.tscn'),'w') as f:
                f.write('[node name="Main" type="Node3D"]\n[node name="Player" type="CharacterBody3D" parent="."]\n')
            tree = scene_tree(root, 'scenes/main.tscn')
            self.assertEqual([x['name'] for x in tree['nodes']], ['Main','Player'])
            runtime_events(root, [{'type':'method','name':'Player.take_damage'}])
            self.assertEqual(runtime_events(root)['events'][0]['type'], 'method')

if __name__ == '__main__': unittest.main()

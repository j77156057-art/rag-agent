import json
import os
import tempfile
import unittest
from pathlib import Path

import game_workbench as gw
import project_state


class ProjectRuntimeStateTests(unittest.TestCase):
    def setUp(self):
        gw._COMFY_PROJECT_JOBS.clear()
        gw._COMFY_LOADED.clear()

    def tearDown(self):
        gw._COMFY_PROJECT_JOBS.clear()
        gw._COMFY_LOADED.clear()

    def test_state_paths_are_stable_and_project_specific(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa = project_state.path(a, "comfy_history.json")
            pb = project_state.path(b, "comfy_history.json")
            self.assertNotEqual(os.path.abspath(pa), os.path.abspath(pb))
            self.assertTrue(os.path.abspath(pa).startswith(os.path.abspath(project_state.config.STATE_ROOT)))
            self.assertTrue(os.path.abspath(pb).startswith(os.path.abspath(project_state.config.STATE_ROOT)))

    def test_comfy_history_isolated_and_restored_per_project(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            jobs_a = gw._comfy_jobs(a)
            jobs_a["prompt-a"] = {"prompt_id": "prompt-a", "status": "completed"}
            gw._save_comfy_history(a)
            jobs_b = gw._comfy_jobs(b)
            jobs_b["prompt-b"] = {"prompt_id": "prompt-b", "status": "failed"}
            gw._save_comfy_history(b)
            self.assertEqual([x["prompt_id"] for x in gw.comfy_history_list(root=a)["items"]], ["prompt-a"])
            self.assertEqual([x["prompt_id"] for x in gw.comfy_history_list(root=b)["items"]], ["prompt-b"])

            # Simulate a service restart: buckets are rebuilt from their own files.
            gw._COMFY_PROJECT_JOBS.clear()
            gw._COMFY_LOADED.clear()
            self.assertEqual(gw.comfy_history_list(root=a)["items"][0]["prompt_id"], "prompt-a")
            self.assertEqual(gw.comfy_history_list(root=b)["items"][0]["prompt_id"], "prompt-b")
            self.assertTrue(Path(project_state.path(a, "comfy_history.json")).is_file())
            self.assertTrue(Path(project_state.path(b, "comfy_history.json")).is_file())

    def test_legacy_history_migrates_once_into_project_bucket(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / ".docmind" / "comfy_history.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({"old": {"prompt_id": "old", "status": "completed"}}), encoding="utf-8")
            rows = gw.comfy_history_list(root=root)["items"]
            self.assertEqual(rows[0]["prompt_id"], "old")
            target = Path(project_state.path(root, "comfy_history.json"))
            self.assertTrue(target.is_file())
            self.assertTrue(Path(str(target) + ".migrated").is_file())


if __name__ == "__main__":
    unittest.main()

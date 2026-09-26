import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.game_workflow import GameWorkflowManager
from agent_runtime.project_profile import load_profile, merge_profile, profile_path


class ProjectProfileTests(unittest.TestCase):
    def test_merge_creates_project_local_profile_and_restores_it(self):
        with tempfile.TemporaryDirectory() as root:
            servers = [{"key": "eda", "label": "EasyEDA", "enabled": True,
                        "transport": "http", "url": "https://eda.example/mcp",
                        "headers": {"Authorization": "Bearer secret-token"},
                        "capabilities": ["schematic", "layout"]}]
            state = {
                "project_id": "project-1", "project_root": root, "kind": "eda",
                "sources": ["local", "mcp"],
                "tasks": [{"tools": ["read_file", "apply_edit"], "mcp": "auto",
                           "run_command": "npm run preview"}],
                "acceptance_contract": {"items": [{"method": "运行验收脚本",
                    "evidence": ["tests/acceptance.py"]}]},
                "preview": {"artifacts": [{"adapter": "visual"}]},
            }
            with patch("mcp_client.server_configs", return_value=servers):
                profile = merge_profile(root, state)
            self.assertEqual(profile["project_id"], "project-1")
            self.assertEqual(profile["kind"], "eda")
            self.assertIn("apply_edit", profile["tools"])
            self.assertEqual(profile["mcp"][0]["key"], "eda")
            self.assertIn("npm run preview", profile["run_commands"])
            self.assertIn("运行验收脚本", profile["acceptance_methods"])
            self.assertIn("tests/acceptance.py", profile["acceptance_scripts"])
            self.assertIn("visual", profile["preview_adapters"])

            restarted = load_profile(root)
            self.assertEqual(restarted, profile)
            self.assertEqual(profile_path(root), Path(root, ".docmind", "project-profile.json").resolve())

    def test_profile_never_persists_sensitive_mcp_fields(self):
        with tempfile.TemporaryDirectory() as root:
            servers = [{"key": "cloud", "label": "Cloud", "enabled": True,
                        "transport": "stdio", "command": "node --token secret123",
                        "env": {"API_KEY": "secret123"},
                        "capabilities": ["search"]}]
            with patch("mcp_client.server_configs", return_value=servers):
                merge_profile(root, {"project_root": root, "tasks": []})
            text = Path(root, ".docmind", "project-profile.json").read_text(encoding="utf-8")
            self.assertNotIn("secret123", text)
            self.assertNotIn('"command":', text)
            self.assertNotIn('"env":', text)
            json.loads(text)

    def test_workflow_restart_loads_profile_from_project(self):
        with tempfile.TemporaryDirectory() as project:
            state_root = tempfile.mkdtemp()
            merge_profile(project, {"project_id": "p", "project_root": project,
                                    "kind": "generic", "tasks": [{"tools": ["x"]}]})
            manager = GameWorkflowManager(state_root)
            wid = manager.start("读取项目画像", project_id="p", project_root=project)["workflow_id"]
            restarted = GameWorkflowManager(state_root)
            state = restarted.get(wid)
            self.assertEqual(state["project_profile"]["project_root"], str(Path(project).resolve()))
            self.assertIn("x", state["project_profile"]["tools"])


if __name__ == "__main__":
    unittest.main()

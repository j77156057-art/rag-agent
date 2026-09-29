import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.enterprise_sandbox import SandboxUnavailable, run_agent_command, run_project_command, stage_execution_scope
from agent_runtime.independent_review import review_project
from agent_runtime.project_checkpoint import create_checkpoint
from agent_runtime.game_workflow import GameWorkflowManager


class EnterpriseSandboxReviewTests(unittest.TestCase):
    def test_stage_command_requires_container_by_default(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {"DOCMIND_SANDBOX_IMAGE": "", "DOCMIND_STAGE_HOST_COMMANDS": "0"}), \
                patch("agent_runtime.enterprise_sandbox.shutil.which", return_value=None), \
                patch("agent_runtime.enterprise_sandbox.subprocess.run") as launch:
            with stage_execution_scope(), patch("agent_runtime.enterprise_sandbox.stage_backend", return_value="unavailable"), self.assertRaises(SandboxUnavailable):
                run_agent_command(["python", "-c", "print(1)"], project_root=root)
            launch.assert_not_called()

    def test_host_compat_review_cannot_pass_as_enterprise_isolation(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"DOCMIND_EXECUTION_MODE": "enterprise",
                                     "DOCMIND_SANDBOX_IMAGE": "", "DOCMIND_STAGE_HOST_COMMANDS": "1"}), \
                patch("agent_runtime.enterprise_sandbox.shutil.which", return_value=None):
            root = Path(temporary, "project")
            root.mkdir()
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            checkpoint = create_checkpoint(str(root), str(Path(temporary, "state")), "wf")
            (root / "app.py").write_text("print(2)\n", encoding="utf-8")
            with stage_execution_scope(), patch("agent_runtime.independent_review.run_project_command",
                    return_value=subprocess.CompletedProcess([], 0, "1 passed", "")):
                report = review_project(str(root), checkpoint, str(Path(temporary, "state")), ["pytest -q"])
            self.assertEqual(report["status"], "unverified")
            self.assertFalse(report["ok"])

    def test_missing_runtime_fails_closed_without_launching_host_command(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {"DOCMIND_SANDBOX_IMAGE": "example@sha256:" + "a" * 64}), \
                patch("agent_runtime.enterprise_sandbox.shutil.which", return_value=None), \
                patch("agent_runtime.enterprise_sandbox.subprocess.run") as launch:
            with self.assertRaises(SandboxUnavailable):
                run_project_command(["python", "-c", "print(1)"], project_root=root)
            launch.assert_not_called()

    def test_container_has_no_network_or_host_project_mount(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {"DOCMIND_SANDBOX_IMAGE": "example@sha256:" + "a" * 64}), \
                patch("agent_runtime.enterprise_sandbox.shutil.which", return_value="docker"):
            Path(root, "source.py").write_text("print(1)", encoding="utf-8")
            Path(root, ".env").write_text("SECRET=private", encoding="utf-8")
            def inspect(argv, **kwargs):
                self.assertIn("--network", argv)
                self.assertEqual(argv[argv.index("--network") + 1], "none")
                self.assertIn("--read-only", argv)
                self.assertIn("--cap-drop=ALL", argv)
                mount = argv[argv.index("--mount") + 1]
                self.assertNotIn("source=" + root + ",", mount)
                copied = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
                self.assertTrue((copied / "source.py").is_file())
                self.assertFalse((copied / ".env").exists())
                self.assertNotIn("SECRET", " ".join(argv))
                return subprocess.CompletedProcess(argv, 0, "ok", "")
            with patch("agent_runtime.enterprise_sandbox.subprocess.run", side_effect=inspect):
                result = run_project_command(["python", "-c", "print(1)"], project_root=root)
            self.assertEqual(result.stdout, "ok")

    def test_changed_project_requires_independent_test_evidence(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"DOCMIND_EXECUTION_MODE": "enterprise"}):
            root = Path(temporary, "project")
            root.mkdir()
            source = root / "app.py"
            source.write_text("print(1)\n", encoding="utf-8")
            storage = Path(temporary, "state")
            checkpoint = create_checkpoint(str(root), str(storage), "wf")
            source.write_text("print(2)\n", encoding="utf-8")
            missing = review_project(str(root), checkpoint, str(storage))
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["status"], "unverified")
            self.assertIn("print(2)", missing["diff"])
            with patch("agent_runtime.independent_review.run_project_command",
                       return_value=subprocess.CompletedProcess([], 0, "1 passed", "")):
                passed = review_project(str(root), checkpoint, str(storage), ["pytest -q"])
            self.assertTrue(passed["ok"])
            self.assertEqual(passed["tests"][0]["output"], "1 passed")
            with patch("agent_runtime.independent_review.run_project_command",
                       return_value=subprocess.CompletedProcess([], 1, "failed", "")):
                failed = review_project(str(root), checkpoint, str(storage), ["pytest -q"])
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["status"], "failed")

    def test_workflow_cannot_complete_when_independent_review_is_unverified(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"DOCMIND_EXECUTION_MODE": "enterprise"}):
            root = Path(temporary, "project")
            root.mkdir()
            manager = GameWorkflowManager(str(Path(temporary, "state")))
            workflow_id = manager.start("修改项目文件", project_root=str(root), stage_enabled=True)["workflow_id"]
            manager.choose(workflow_id, "recommended")
            manager.plan(workflow_id, [{"id": "edit", "role": "coder", "task": "修改项目"}])
            def runner(task, context):
                from config import get_runtime
                Path(get_runtime("code_root") or root, "result.txt").write_text("changed", encoding="utf-8")
                return {"status": "ok", "conclusion": "修改完成", "steps": 1}
            try:
                manager.execute(workflow_id, runner)
                manager.approve(workflow_id, True)
                done = manager.execute(workflow_id, runner)
                self.assertEqual(done["status"], "failed")
                self.assertEqual(done["review"]["project"]["status"], "unverified")
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()

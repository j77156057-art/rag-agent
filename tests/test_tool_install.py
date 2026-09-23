import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.tool_install import ToolInstallError, ToolInstallManager


class ToolInstallTests(unittest.TestCase):
    def test_plan_is_project_local_and_shell_free(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ToolInstallManager(root)
            plan = manager.plan({"manager": "python", "package": "ruff", "version": "0.6.9",
                                 "fallback_tools": ["self_verify", "web_research"]})
            self.assertTrue(plan["sandbox"])
            self.assertIn(str(Path(root).resolve()), plan["target"])
            self.assertNotIn(";", " ".join(plan["command"]))
            self.assertEqual(plan["approval_action"], "install_tool")
            self.assertIn("0.6.9", " ".join(plan["verifier"]))

    def test_invalid_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ToolInstallError):
                ToolInstallManager(root).plan({"manager": "python", "package": "bad;rm"})

    def test_requires_approval_and_records_audit(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ToolInstallManager(root)
            result = manager.install({"manager": "python", "package": "ruff"})
            self.assertFalse(result["ok"])
            self.assertTrue(result["approval_required"])
            rows = manager.audit()
            self.assertEqual(rows[-1]["status"], "approval_required")

    def test_approved_install_uses_injected_runner_and_records_success(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ToolInstallManager(root)
            calls = []

            def runner(command, cwd, timeout):
                calls.append((command, cwd, timeout))
                return {"returncode": 0, "stdout": "ok", "stderr": ""}

            result = manager.install({"manager": "python", "package": "ruff"},
                                     approved=True, runner=runner)
            self.assertTrue(result["ok"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(result["verify"]["sandbox_ok"])
            self.assertTrue(result["verify"]["version_ok"])
            self.assertEqual(manager.audit()[-1]["status"], "installed")
            json.dumps(result, ensure_ascii=False)

    def test_verifier_failure_returns_fallback_and_audit(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ToolInstallManager(root)

            def runner(command, cwd, timeout):
                if command == manager.plan({"manager": "python", "package": "ruff"})["verifier"]:
                    return {"returncode": 1, "stdout": "", "stderr": "missing"}
                return {"returncode": 0, "stdout": "installed", "stderr": ""}

            result = manager.install({"manager": "python", "package": "ruff",
                                     "fallback_tools": ["self_verify"]},
                                     approved=True, runner=runner)
            self.assertFalse(result["ok"])
            self.assertEqual(result["fallback_tools"], ["self_verify"])
            self.assertEqual(manager.audit()[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

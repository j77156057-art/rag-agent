# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import tools
from agent_runtime.adapter_catalog import create_generated, approve_generated, generated
from agent_runtime.generated_adapter_runtime import GeneratedAdapterError, execute, validate_source
from game_workbench import approval


class GeneratedAdapterRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_generated_adapter_")
        self.root = self.tmp.name
        self.old_root = config.get_runtime("code_root")
        config.set_runtime("code_root", self.root)

    def tearDown(self):
        if self.old_root:
            config.set_runtime("code_root", self.old_root)
        else:
            config._RUNTIME.pop("code_root", None)
        self.tmp.cleanup()

    def test_python_source_is_staged_only_until_approval_then_executes_isolated(self):
        source = """import json\ndef adapt(payload):\n    return {'kind': 'structured', 'summary': payload.get('name', '').upper()}\n"""
        manifest = create_generated(self.root, {
            "id": "easyeda", "domain": "eda", "runtime": "python", "source": source,
            "refresh_tool": "builtin:game_screenshot",
        })
        self.assertEqual(manifest["status"], "pending")
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".docmind", "preview-adapters", ".pending", "easyeda", "adapter.py")))
        active = approve_generated(self.root, "easyeda", True)
        self.assertEqual(active["status"], "active")
        result = execute(self.root, active, {"name": "board"})
        self.assertEqual(result["value"]["summary"], "BOARD")
        self.assertEqual(generated(self.root)[0]["runtime"], "python")

    def test_static_checks_reject_filesystem_network_and_invalid_exports(self):
        with self.assertRaises(GeneratedAdapterError):
            validate_source("python", "def adapt(payload):\n    return open('x').read()")
        with self.assertRaises(GeneratedAdapterError):
            validate_source("python", "import subprocess\ndef adapt(payload):\n    return {}")
        with self.assertRaises(GeneratedAdapterError):
            validate_source("node", "const fs = require('fs'); module.exports.adapt = p => p")
        with self.assertRaises(GeneratedAdapterError):
            validate_source("python", "def adapt(payload):\n    return payload.__class__\n")

    def test_tool_create_accepts_multiline_source_and_keeps_it_pending(self):
        source = "def adapt(payload):\n    return {'ok': True}\n"
        result = json.loads(tools.dev_preview_adapter_create(
            "id: cad-code\nlabel: CAD\ndomain: cad\nruntime: python\nentrypoint: adapt\n"
            "refresh_tool: builtin:game_screenshot\nsource: " + source))
        self.assertTrue(result["ok"])
        self.assertEqual(result["manifest"]["status"], "pending")
        self.assertEqual(result["manifest"]["runtime"], "python")
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".docmind", "preview-adapters", ".pending", "cad-code", "adapter.py")))

    def test_refresh_runs_generated_adapter_after_approved_builtin(self):
        source = "def adapt(payload):\n    return {'artifact': payload.get('value', '')}\n"
        create_generated(self.root, {"id": "cad", "domain": "cad", "runtime": "python",
                                     "source": source, "refresh_tool": "builtin:game_screenshot"})
        approve_generated(self.root, "cad", True)
        with patch.dict(tools.TOOLS, {"game_screenshot": {"func": lambda _arg: {"value": "ok"}}}):
            result = json.loads(tools.dev_preview_adapter_refresh("id: cad"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["generated"])
        self.assertEqual(result["result"]["artifact"], "ok")

    def test_rollback_requires_approval_and_restores_previous_module(self):
        source_one = "def adapt(payload):\n    return {'version': 1}\n"
        source_two = "def adapt(payload):\n    return {'version': 2}\n"
        create_generated(self.root, {"id": "godot", "runtime": "python", "source": source_one,
                                     "refresh_tool": "builtin:game_screenshot"})
        approve_generated(self.root, "godot", True)
        # Stage and activate a replacement to create a history entry.
        from agent_runtime.generated_adapter_runtime import stage_source, activate_source
        stage_source(self.root, "godot", "python", source_two)
        activate_source(self.root, "godot", "python")
        blocked = json.loads(tools.dev_preview_adapter_rollback("id: godot"))
        self.assertTrue(blocked["approval_required"])
        approval(self.root, "preview_adapter_rollback", "user", approved=True, target="preview-adapter:godot")
        restored = json.loads(tools.dev_preview_adapter_rollback("id: godot"))
        self.assertTrue(restored["ok"])
        manifest = next(row for row in generated(self.root) if row["id"] == "godot")
        self.assertEqual(execute(self.root, manifest, {})["value"]["version"], 1)


if __name__ == "__main__":
    unittest.main()

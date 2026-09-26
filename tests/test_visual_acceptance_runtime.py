# -*- coding: utf-8 -*-
"""正式开发舱视觉适配器与预览证据链的离线契约测试。"""
import os
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime.preview_adapters import build_preview_bundle
from agent_runtime.visual_acceptance import VisualAcceptanceError, _entry_file


class VisualAcceptanceRuntimeTests(unittest.TestCase):
    def test_entry_file_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = os.path.abspath(root)
            with open(os.path.join(root_path, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("<h1>preview</h1>")
            self.assertTrue(_entry_file(__import__("pathlib").Path(root_path)).name == "index.html")
            with self.assertRaises(VisualAcceptanceError):
                _entry_file(__import__("pathlib").Path(root_path), "../outside.html")

    def test_tool_report_registers_screenshot_artifact(self):
        import tools

        report = {
            "passed": True,
            "image": "aGVsbG8=",
            "screenshot": ".docmind/visual-evidence/run/preview.png",
            "checks": {"page_loaded": True},
            "artifacts": [{
                "id": "visual-preview", "kind": "image",
                "path": ".docmind/visual-evidence/run/preview.png",
                "label": "真实浏览器预览截图",
            }],
        }
        with patch.object(tools, "_get_code_root", return_value="C:/project"), \
             patch("agent_runtime.visual_acceptance.capture_project_preview", return_value=report):
            result = tools.preview_project("entry: index.html")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["images"], ["aGVsbG8="])
        self.assertEqual(result.artifacts[0]["kind"], "image")

    def test_workflow_preview_keeps_visual_artifact(self):
        bundle = build_preview_bundle({
            "workflow_id": "wf-visual", "status": "completed",
            "review": {"ok": True}, "results": {"results": {
                "tester": {"status": "ok", "artifacts": [{
                    "id": "visual-preview", "kind": "image",
                    "path": ".docmind/visual-evidence/run/preview.png",
                    "label": "真实浏览器预览截图",
                }]},
            }},
        })
        self.assertEqual(bundle["counts"]["by_kind"]["image"], 1)
        self.assertEqual(bundle["artifacts"][0]["adapter"], "generic")


if __name__ == "__main__":
    unittest.main()

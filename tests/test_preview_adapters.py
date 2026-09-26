import unittest

from agent_runtime.preview_adapters import (build_preview_bundle, infer_artifact_kind,
                                            normalize_artifact, register_preview_adapter,
                                            unregister_preview_adapter)


class PreviewAdapterTests(unittest.TestCase):
    def test_infers_general_artifact_kinds(self):
        self.assertEqual(infer_artifact_kind("screen.png"), "image")
        self.assertEqual(infer_artifact_kind("report.json"), "structured")
        self.assertEqual(infer_artifact_kind("capture.webm"), "video")
        self.assertEqual(infer_artifact_kind("live"), "interactive")
        self.assertEqual(infer_artifact_kind("custom.asset"), "unknown")

    def test_bundle_collects_files_and_custom_artifacts_without_secrets(self):
        bundle = build_preview_bundle({
            "workflow_id": "wf-1", "request": "验证通用预览", "status": "completed",
            "review": {"ok": True}, "results": {"results": {
                "task-a": {"status": "ok", "file_changes": ["src/main.py"],
                           "artifacts": [{"path": "capture.png", "uri": "/api/artifacts/1",
                                          "summary": "截图", "metadata": {"token": "secret"}}]},
            }},
        })
        self.assertEqual(bundle["schema"], "docmind.preview.v1")
        self.assertEqual(bundle["counts"]["artifacts"], 2)
        self.assertEqual({item["kind"] for item in bundle["artifacts"]}, {"code", "image"})
        self.assertNotIn("secret", str(bundle))

    def test_normalize_rejects_unsafe_media_uri(self):
        artifact = normalize_artifact({"url": "javascript:alert(1)", "kind": "image"})
        self.assertEqual(artifact["uri"], "")
        self.assertEqual(artifact["kind"], "image")

    def test_registered_adapter_can_expand_domain_artifact(self):
        register_preview_adapter("demo", lambda raw, context: [
            {"id": "source", "kind": "structured", "label": "领域状态", "content": "{}"},
            {"id": "render", "kind": "image", "path": "preview.png"},
        ])
        try:
            bundle = build_preview_bundle({"workflow_id": "wf-2", "results": {"results": {
                "task": {"artifacts": [{"adapter": "demo", "path": "source.demo"}]}
            }}})
            self.assertEqual(bundle["counts"]["artifacts"], 2)
            self.assertEqual(bundle["changes"]["total"], 2)
        finally:
            unregister_preview_adapter("demo")

    def test_change_summary_is_safe_and_bounded(self):
        item = normalize_artifact({"path": "a.txt", "before": "one\ntwo", "after": "one\nthree"})
        self.assertEqual(item["change_summary"]["before_lines"], 2)
        self.assertEqual(item["change_summary"]["added_lines"], 1)
        self.assertEqual(item["change_summary"]["removed_lines"], 1)

    def test_legacy_preview_url_becomes_interactive_artifact(self):
        bundle = build_preview_bundle({"workflow_id": "wf-live", "results": {"results": {
            "run": {"status": "ok", "preview_url": "/play/session/index.html"}
        }}})
        item = bundle["artifacts"][0]
        self.assertEqual(item["kind"], "interactive")
        self.assertEqual(item["renderer"], "interactive")
        self.assertEqual(item["uri"], "/play/session/index.html")


if __name__ == "__main__":
    unittest.main()

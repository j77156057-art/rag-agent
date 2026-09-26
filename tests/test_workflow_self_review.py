import unittest
import tempfile

from agent_runtime.game_workflow import GameWorkflowManager
from agent_runtime.workflow_reflection import build_self_review


class WorkflowSelfReviewTests(unittest.TestCase):
    def test_terminal_workflow_persists_review_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            manager = GameWorkflowManager(root)
            try:
                wid = manager.start("复盘持久化") ["workflow_id"]
                state = manager._load(wid)
                state.status = "completed"
                state.results = {"results": {"task": {"status": "ok", "conclusion": "已完成"}}}
                state.review = {"ok": True, "checks": [{"name": "build", "ok": True}]}
                manager._save(state)
                restarted = GameWorkflowManager(root)
                try:
                    saved = restarted.get(wid)
                    self.assertEqual(saved["self_review"]["status"], "ready")
                    self.assertIn("build", saved["self_review"]["verified"])
                finally:
                    restarted.close()
            finally:
                manager.close()

    def test_review_summarizes_changes_verification_uncertainty_and_next_steps(self):
        review = build_self_review({
            "status": "failed",
            "results": {"results": {
                "edit": {"status": "ok", "file_changes": ["src/app.ts"],
                          "conclusion": "完成界面修改", "reflection": {"next_step": "运行集成测试"}},
                "test": {"status": "blocked", "error": "等待外部服务"},
            }},
            "review": {"ok": False, "checks": [{"name": "tests", "ok": False, "detail": "未通过"}]},
            "acceptance_contract": {"items": [{"method": "运行测试", "evidence": ["tests/test_app.py"]}],
                                     "final_decision": "pending"},
            "preview": {"artifacts": [{"id": "preview-1"}]},
        })
        self.assertEqual(review["status"], "ready")
        self.assertIn("src/app.ts", review["changed"])
        self.assertTrue(any("blocked" in item for item in review["uncertainties"]))
        self.assertIn("运行集成测试", review["next_steps"])
        self.assertTrue(any("验收" in item for item in review["uncertainties"]))

    def test_review_redacts_sensitive_values_and_records_user_acceptance(self):
        review = build_self_review({
            "status": "completed",
            "results": {"results": {"task": {"status": "ok", "conclusion": "token=secret-value"}}},
            "review": {"ok": True, "checks": [{"name": "build", "ok": True}]},
            "acceptance_contract": {"items": [{"method": "人工检查", "evidence": []}],
                                     "final_decision": "accepted"},
            "capability_lease": {"status": "released"},
        })
        text = str(review)
        self.assertNotIn("secret-value", text)
        self.assertIn("用户已确认最终验收", review["verified"])
        self.assertIn("工具权限租约已释放", review["verified"])
        self.assertGreater(review["confidence"], 0)


if __name__ == "__main__":
    unittest.main()

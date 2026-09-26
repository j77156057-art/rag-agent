import unittest

from agent_runtime.game_workflow import build_recovery_plan


class RecoveryPlanTests(unittest.TestCase):
    def test_failed_task_gets_bounded_retry_option(self):
        plan = build_recovery_plan({
            "results": {"results": {
                "compile": {"status": "failed", "error_kind": "timeout", "error": "工具超时"},
                "preview": {"status": "ok"},
            }},
        })
        self.assertEqual(plan["status"], "required")
        self.assertEqual(plan["evidence"]["failed_task_ids"], ["compile"])
        retry = next(item for item in plan["options"] if item["action"] == "retry_failed")
        self.assertEqual(retry["task_ids"], ["compile"])
        self.assertNotIn("工具超时", retry["detail"])

    def test_uncertain_side_effect_requires_inspection_before_retry(self):
        plan = build_recovery_plan({
            "results": {"results": {
                "publish": {"status": "failed", "error_kind": "idempotency_in_doubt",
                             "error": "不确定执行结果"},
            }},
        })
        self.assertEqual(plan["evidence"]["uncertain_task_ids"], ["publish"])
        self.assertEqual(plan["options"][0]["action"], "inspect")
        self.assertEqual(plan["options"][0]["id"], "inspect_uncertain_side_effect")

    def test_checkpoint_adds_restore_option(self):
        plan = build_recovery_plan({
            "results": {"results": {"build": {"status": "failed"}}},
            "project_checkpoint": {"id": "checkpoint-1"},
        })
        self.assertTrue(any(item["action"] == "rollback" for item in plan["options"]))

    def test_empty_evidence_still_explains_safe_next_step(self):
        plan = build_recovery_plan({}, error="执行未通过复核")
        self.assertEqual(len(plan["options"]), 1)
        self.assertEqual(plan["options"][0]["action"], "inspect")
        self.assertIn("失败证据", plan["options"][0]["title"])


if __name__ == "__main__":
    unittest.main()

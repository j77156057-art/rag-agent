import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import agent
import agent_memory as memory
import sessions
import tools
from tests.test_agent_trace import _FakeLLM, _IsoBase


class MemoryTests(_IsoBase):
    def test_persistent_scoped_memory_and_user_correction(self):
        mid = memory.remember("project-a", "preference", "language", "中文", "请说中文", source="user_explicit")
        memory.remember("project-a", "preference", "language", "英文", "请说英文", source="user_explicit")
        self.assertEqual(memory.recall("project-a")[0]["content"], "英文")
        self.assertEqual(memory.recall("project-b"), [])
        self.assertTrue(memory.forget("project-a", mid))
        self.assertEqual(memory.recall("project-a"), [])

    def test_secrets_scrubbed_and_irrelevant_history_not_recalled(self):
        memory.remember("p", "fact", "Godot", "token=supersecret password=hunter2", "Bearer abcdefghi")
        stored = json.dumps(memory.recall("p"))
        for secret in ("supersecret", "hunter2", "abcdefghi"):
            self.assertNotIn(secret, stored)
        self.assertEqual(memory.recall("p", "天气"), [])

    def test_concurrent_memory_updates_do_not_overwrite_each_other(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: memory.remember("p", "fact", str(i), "content"), range(20)))
        self.assertEqual(len(memory.recall("p", limit=30)), 20)

    def test_memory_tool_requires_explicit_user_preference_evidence(self):
        token = memory.bind("p", "以后请用中文回复")
        try:
            denied = tools.memory_save(json.dumps({"kind": "preference", "content": "住东莞", "evidence": "我住东莞"}))
            self.assertFalse(json.loads(denied)["ok"])
            saved = tools.memory_save(json.dumps({"kind": "preference", "title": "language", "content": "中文", "evidence": "请用中文"}))
            self.assertTrue(json.loads(saved)["ok"])
            self.assertEqual(memory.recall("p")[0]["source"], "user_explicit")
        finally:
            memory.reset(token)

    def test_agent_can_save_memory_without_source_edit_intent(self):
        llm = _FakeLLM(['Action: memory_save\nAction Input: {"kind":"preference","title":"language","content":"中文沟通","evidence":"我喜欢中文沟通"}',
                        "Final Answer: 已记住"])
        a = agent.Agent(llm=llm, session_id="save", project_id="p")
        events = list(a.run("我喜欢中文沟通"))
        self.assertTrue(any(event.get("type") == "observation" and '"ok": true' in str(event.get("text")) for event in events))
        preferences = [row for row in memory.recall(a.memory_scope) if row["kind"] == "preference"]
        self.assertEqual(preferences[0]["content"], "中文沟通")

    def test_auto_preferences_skip_negative_or_hypothetical_request(self):
        for text in ("请不要用中文", "模型为什么不会说中文", "怎么让模型说中文", "请问为什么模型说中文"):
            memory.capture_preferences("p", text)
        self.assertEqual(memory.recall("p"), [])
        memory.capture_preferences("p", "以后请用中文回复")
        self.assertEqual(memory.recall("p")[0]["content"], "使用中文沟通")
        memory.capture_preferences("p", "以后请用英文回复")
        self.assertEqual(len(memory.recall("p")), 1)
        self.assertEqual(memory.recall("p")[0]["content"], "使用英文沟通")

    def test_failure_reason_and_tool_evidence_survive_restart_without_duplicates(self):
        a = agent.Agent(llm=_FakeLLM([]), session_id="fail", project_id="p")
        def broken(*args, **kwargs):
            yield {"type": "observation", "text": "来源：https://example.test token=supersecret"}
            raise RuntimeError("HTTP 400 invalid image token=supersecret")
        with patch.object(a, "_run", side_effect=broken):
            with self.assertRaises(RuntimeError):
                list(a.run("检查图片"))
        restored = agent.Agent(llm=a.llm, session_id="fail", project_id="p")
        again = agent.Agent(llm=a.llm, session_id="fail", project_id="p")
        self.assertEqual(len(restored.history), 1)
        self.assertEqual(again.history, restored.history)
        messages = json.dumps(restored._build_messages("继续"), ensure_ascii=False)
        self.assertIn("HTTP 400 invalid image", messages)
        self.assertIn("example.test", messages)
        self.assertNotIn("supersecret", messages)
        self.assertEqual(memory.latest_checkpoint(a.memory_scope, "fail")["status"], "failed")

    def test_generator_close_saves_interruption_and_observation(self):
        a = agent.Agent(llm=_FakeLLM([]), session_id="stop", project_id="p")
        def interrupted(*args, **kwargs):
            yield {"type": "observation", "text": "已经修改文件，尚未验证"}
            yield {"type": "notice", "text": "waiting"}
        with patch.object(a, "_run", side_effect=interrupted):
            gen = a.run("修改项目")
            next(gen)
            gen.close()
        restored = agent.Agent(llm=a.llm, session_id="stop", project_id="p")
        self.assertIn("GeneratorExit", restored.history[-1]["failure_reason"])
        self.assertIn("尚未验证", restored.history[-1]["resume_context"])

    def test_crash_checkpoint_recovery_does_not_invent_reason(self):
        scope = memory.scope_key("p")
        memory.checkpoint(scope, "crashed", "检查项目", events=["测试未通过"])
        restored = agent.Agent(llm=_FakeLLM([]), session_id="crashed", project_id="p")
        self.assertIn("具体原因未记录", restored.history[-1]["failure_reason"])
        self.assertIn("测试未通过", restored.history[-1]["resume_context"])
        memory.delete_checkpoints(scope, "crashed")
        sessions.delete("crashed", "p")
        self.assertEqual(agent.Agent(llm=restored.llm, session_id="crashed", project_id="p").history, [])

    def test_completed_final_is_durable_even_if_client_closes_immediately(self):
        a = agent.Agent(llm=_FakeLLM(["Final Answer: Godot 完成"]), session_id="done", project_id="p")
        gen = a.run("Godot 任务")
        for event in gen:
            if event.get("type") == "final":
                gen.close()
                break
        self.assertEqual(memory.latest_checkpoint(a.memory_scope, "done")["status"], "completed")
        restored = agent.Agent(llm=a.llm, session_id="done", project_id="p")
        self.assertEqual(len(restored.history), 1)

    def test_deadline_final_is_not_marked_as_completed(self):
        a = agent.Agent(llm=_FakeLLM([]), session_id="deadline", project_id="p")
        def timed_out(*args, turn=None, **kwargs):
            turn.outcome = "deadline_exceeded"
            yield {"type": "final", "text": "超时，尚未完成测试"}
        with patch.object(a, "_run", side_effect=timed_out):
            list(a.run("完成测试"))
        self.assertEqual(memory.latest_checkpoint(a.memory_scope, "deadline")["status"], "deadline_exceeded")
        restored = agent.Agent(llm=a.llm, session_id="deadline", project_id="p")
        self.assertEqual(len(restored.history), 1)
        self.assertIn("超时", restored.history[-1]["failure_reason"])

    def test_cross_session_context_and_recurring_workflow(self):
        a = agent.Agent(llm=_FakeLLM([]), session_id="other", project_id="p")
        memory.remember(a.memory_scope, "workflow", "Godot 检查", "先测试，再预览", "测试成功")
        memory.remember(a.memory_scope, "workflow", "Godot 检查", "先测试，再预览", "测试成功")
        ctx = json.dumps(a._build_messages("Godot 检查"), ensure_ascii=False)
        self.assertIn("先测试，再预览", ctx)
        self.assertIn("dev_skill_create", ctx)
        self.assertIn('repeats', ctx)
        other = agent.Agent(llm=a.llm, session_id="other", project_id="another")
        self.assertNotIn("先测试，再预览", json.dumps(other._build_messages("Godot 检查"), ensure_ascii=False))

    def test_skill_workflow_is_saved_as_pending_draft(self):
        import skills
        with patch.object(skills, "SKILLS_DIR", os.path.join(self.tmp, "skills")), \
                patch.object(skills, "get", return_value=None):
            result = json.loads(tools.dev_skill_create("name: godot-check\ndescription: Godot 检查流程\nbody: 先运行测试，再预览；测试失败先修复。"))
            self.assertEqual(result["status"], "pending")
            draft = os.path.join(skills.SKILLS_DIR, ".pending", "godot-check", "SKILL.md")
            self.assertTrue(os.path.isfile(draft))
            self.assertFalse(os.path.exists(os.path.join(skills.SKILLS_DIR, "godot-check")))

    def test_memory_api_lists_deletes_and_session_delete_clears_checkpoint(self):
        import api
        from starlette.testclient import TestClient
        scope = memory.scope_key("p")
        mid = memory.remember(scope, "fact", "任务", "测试记录")
        memory.checkpoint(scope, "deleted", "检查项目")
        with patch.object(api, "_request_project_id", return_value="p"), TestClient(api.app) as client:
            self.assertEqual(client.get("/api/agent/memory").json()["items"][0]["id"], mid)
            self.assertTrue(client.delete("/api/agent/memory/" + mid).json()["ok"])
            self.assertEqual(client.get("/api/agent/memory").json()["items"], [])
            restored = client.get("/api/sessions/deleted").json()["turns"]
            self.assertEqual(restored[0]["user"], "检查项目")
            self.assertEqual(len(client.get("/api/sessions/deleted").json()["turns"]), 1)
            with patch.object(memory, "latest_checkpoint", side_effect=OSError("unavailable")):
                degraded = client.get("/api/sessions/deleted").json()["turns"]
                self.assertEqual(degraded[0]["resume_context"], restored[0]["resume_context"])
                self.assertEqual(len(degraded), 1)
            client.delete("/api/sessions/deleted")
        self.assertIsNone(memory.latest_checkpoint(scope, "deleted"))


if __name__ == "__main__":
    unittest.main()

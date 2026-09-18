# -*- coding: utf-8 -*-
"""阶段 3b 工具流编排器（flows.py）单元测试。

覆盖：流程定义校验 / 受控动作白名单拒绝 / 参数与路径穿越拦截 / 流程读写删 /
单步执行（注入假受控端点，不真实写盘）/ trace 只记元数据 / HTTP 端点冒烟。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flows  # noqa: E402


class TestFieldValidation(unittest.TestCase):
    def test_relpath_rejects_escape_and_absolute(self):
        for bad in ["../secret.txt", "a/../../b", "/etc/passwd", "C:/win.ini",
                    "values/../../x", "~/.ssh/id_rsa", "http://x/y", ""]:
            self.assertTrue(flows._validate_relpath(bad), bad)
        for good in ["balance.json", "sub/dir/player.gd", "a.b.c"]:
            self.assertEqual(flows._validate_relpath(good), "", good)

    def test_region_key_and_changeset(self):
        self.assertTrue(flows._validate_field_value({"kind": flows._KIND_REGION}, "../x"))
        self.assertTrue(flows._validate_field_value({"kind": flows._KIND_REGION}, "a b"))
        self.assertEqual(flows._validate_field_value({"kind": flows._KIND_REGION}, "values-1"), "")
        self.assertTrue(flows._validate_field_value({"kind": flows._KIND_CHANGESET}, "a/b"))
        self.assertEqual(flows._validate_field_value({"kind": flows._KIND_CHANGESET}, "abc123"), "")

    def test_relpath_rejects_control_chars(self):
        # S1 回归：path 含换行/回车/制表等控制字符会被 keyed 解析截断，必须前置拒绝。
        for bad in ["foo.py\nregion: assets", "foo.py\rregion: assets",
                    "a\tb.py", "x\x00.py", "line\r\nbreak.py", "foo.py\n"]:
            self.assertTrue(flows._validate_relpath(bad), repr(bad))
        self.assertEqual(flows._validate_relpath("sub/ok.py"), "")


class TestValidateStep(unittest.TestCase):
    def test_unknown_action_rejected(self):
        r = flows.validate_step("rm_rf", {})
        self.assertFalse(r["ok"])
        self.assertIn("白名单", r["error"])

    def test_unknown_param_rejected(self):
        r = flows.validate_step("dev_region_verify", {"region": "values", "evil": "1"})
        self.assertFalse(r["ok"])
        self.assertIn("evil", r["error"])

    def test_missing_required(self):
        r = flows.validate_step("dev_region_verify", {})
        self.assertFalse(r["ok"])
        self.assertIn("region", r["error"])

    def test_path_traversal_in_params_rejected(self):
        r = flows.validate_step("dev_region_edit",
                                {"region": "values", "path": "../evil.py", "new_text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("path", r["error"])

    def test_path_with_newline_region_injection_rejected(self):
        # S1 回归（最小复现）：过去 `_validate_relpath` 不拦换行，
        # `_build_edit_arg("values", "foo.py\nregion: assets", "x")` 会被 tools._parse_keyed
        # 以「最后出现的 region」解析，导致「用 values 校验、却写到 assets」的旁路。
        # 现在该路径必须在 validate_step 阶段被拒。
        r = flows.validate_step("dev_region_edit",
                                {"region": "values", "path": "foo.py\nregion: assets",
                                 "new_text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("path", r["error"])

    def test_ok_and_cleans_params(self):
        r = flows.validate_step("dev_region_edit",
                                {"region": "values", "path": "balance.json", "new_text": "{}", "extra": 1})
        self.assertFalse(r["ok"])  # extra 非法
        r = flows.validate_step("dev_region_edit",
                                {"region": "values", "path": "balance.json", "new_text": "{}"})
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["params"]["region"], "values")

    def test_scalar_coercion(self):
        r = flows.validate_step("dev_commit_all", {"message": 123})
        self.assertTrue(r["ok"])
        self.assertEqual(r["params"]["message"], "123")


class TestValidateFlowDefinition(unittest.TestCase):
    def _flow(self, nodes, **kw):
        d = {"name": "t", "nodes": nodes}
        d.update(kw)
        return d

    def test_valid_flow(self):
        v = flows.validate_flow_definition(self._flow([
            {"id": "n1", "action": "dev_verify_contracts"},
            {"id": "n2", "action": "dev_commit_all", "params": {"message": "m"}},
        ], edges=[{"source": "n1", "target": "n2"}]))
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(len(v["flow"]["nodes"]), 2)
        self.assertEqual(v["flow"]["edges"], [{"source": "n1", "target": "n2"}])
        self.assertTrue(v["flow"]["id"].startswith("flow-"))

    def test_empty_nodes_rejected(self):
        v = flows.validate_flow_definition({"name": "x", "nodes": []})
        self.assertFalse(v["ok"])

    def test_bad_action_rejected(self):
        v = flows.validate_flow_definition(self._flow([{"id": "n1", "action": "self_heal"}]))
        self.assertFalse(v["ok"])
        self.assertTrue(any("白名单" in e for e in v["errors"]))

    def test_duplicate_node_id_rejected(self):
        v = flows.validate_flow_definition(self._flow([
            {"id": "n1", "action": "dev_verify_contracts"},
            {"id": "n1", "action": "dev_verify_contracts"},
        ]))
        self.assertFalse(v["ok"])

    def test_edge_to_unknown_node_dropped(self):
        v = flows.validate_flow_definition(self._flow(
            [{"id": "n1", "action": "dev_verify_contracts"}],
            edges=[{"source": "n1", "target": "ghost"}]))
        self.assertTrue(v["ok"])
        self.assertEqual(v["flow"]["edges"], [])

    def test_keeps_positions(self):
        v = flows.validate_flow_definition(self._flow(
            [{"id": "n1", "action": "dev_verify_contracts", "x": 10, "y": 20}]))
        self.assertTrue(v["ok"])
        self.assertEqual(v["flow"]["nodes"][0]["x"], 10.0)
        self.assertEqual(v["flow"]["nodes"][0]["y"], 20.0)


class TestFlowStore(unittest.TestCase):
    def test_save_load_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = flows.save_flow(tmp, {"name": "保存测试", "nodes": [
                {"id": "n1", "action": "dev_list_regions"}]})
            self.assertTrue(res["ok"], res)
            fid = res["flow"]["id"]
            path = os.path.join(tmp, ".docmind", "flows", fid + ".json")
            self.assertTrue(os.path.isfile(path), "流程文件应落在 .docmind/flows/")
            self.assertEqual(len(flows.load_flows(tmp)), 1)
            self.assertIsNotNone(flows.load_flow(tmp, fid))
            # 更新保留 created_at
            res2 = flows.save_flow(tmp, {"id": fid, "name": "改名", "nodes": [
                {"id": "n1", "action": "dev_list_regions"}]})
            self.assertEqual(res2["flow"]["created_at"], res["flow"]["created_at"])
            self.assertEqual(res2["flow"]["updated_at"] >= res["flow"]["updated_at"], True)
            d = flows.delete_flow(tmp, fid)
            self.assertTrue(d["ok"])
            self.assertEqual(flows.load_flows(tmp), [])

    def test_delete_illegal_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(flows.delete_flow(tmp, "../x")["ok"])
            self.assertFalse(flows.delete_flow(tmp, "not_exist")["ok"])

    def test_save_flow_rejects_unknown_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = flows.save_flow(tmp, {"name": "x", "nodes": [{"id": "n1", "action": "nope"}]})
            self.assertFalse(res["ok"])

    def test_load_flows_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(flows.load_flows(tmp), [])

    def test_load_flows_skips_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, ".docmind", "flows")
            os.makedirs(d)
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                f.write("{ not valid json")
            self.assertEqual(flows.load_flows(tmp), [])


class TestExecuteStep(unittest.TestCase):
    def test_whitelist_rejects_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "evil_action", {})
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "fail")

    def test_bad_param_rejected_before_endpoint(self):
        called = {"n": 0}

        def fake(root, params):
            called["n"] += 1
            return {"ok": True, "output": "hi"}

        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "dev_region_verify", {"region": "../x"},
                                   endpoints={"dev_region_verify": fake})
            self.assertFalse(r["ok"])
            self.assertEqual(called["n"], 0, "参数非法时不应触达端点")

    def test_fake_endpoint_ok(self):
        def fake(root, params):
            self.assertEqual(params["region"], "values")
            return {"ok": True, "output": "校验通过", "detail": {"n": 1}}

        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "dev_region_verify", {"region": "values"},
                                   endpoints={"dev_region_verify": fake})
            self.assertTrue(r["ok"])
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["output"], "校验通过")

    def test_fake_endpoint_fail(self):
        def fake(root, params):
            return {"ok": False, "output": "", "error": "模拟失败"}

        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "dev_region_verify", {"region": "values"},
                                   endpoints={"dev_region_verify": fake})
            self.assertFalse(r["ok"])
            self.assertIn("模拟失败", r["error"])

    def test_endpoint_exception_becomes_failure(self):
        def boom(root, params):
            raise RuntimeError("kaboom")

        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "dev_verify_contracts", {}, endpoints={"dev_verify_contracts": boom})
            self.assertFalse(r["ok"])
            self.assertIn("kaboom", r["error"])

    def test_region_injection_in_path_rejected_before_endpoint(self):
        # S1 回归（纵深）：注入路径必须在触达受控端点**之前**被拒，端点绝不被调用。
        called = {"n": 0}

        def fake(root, params):
            called["n"] += 1
            return {"ok": True, "output": "（不应触达）"}

        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(
                tmp, "dev_region_edit",
                {"region": "values", "path": "foo.py\nregion: assets", "new_text": "x"},
                endpoints={"dev_region_edit": fake})
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "fail")
            self.assertEqual(called["n"], 0, "注入路径必须在触达受控端点前被拒")


class TestRegionEditGuard(unittest.TestCase):
    """S1 纵深防御：即便绕过 validate_step，`_ep_dev_region_edit` 自身也要拦保留字段标记行。"""

    def test_endpoint_rejects_reserved_line_in_path(self):
        r = flows._ep_dev_region_edit("/tmp/root", {
            "region": "values", "path": "foo.py\nregion: assets", "new_text": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("path", r["error"])

    def test_endpoint_rejects_reserved_line_in_new_text(self):
        r = flows._ep_dev_region_edit("/tmp/root", {
            "region": "values", "path": "foo.py", "new_text": "x\nold_text: y"})
        self.assertFalse(r["ok"])
        self.assertIn("new_text", r["error"])

    def test_endpoint_allows_clean_params_to_reach_guard(self):
        # 干净参数不应被保留字段护栏误伤（会继续走向 tools，不在本用例断言其副作用）。
        self.assertEqual(flows._has_reserved_line("def f():\n    return 1"), "")


class TestTracePrivacy(unittest.TestCase):
    def test_build_trace_record_shape(self):
        steps = [{"i": 0, "action": "dev_region_edit", "arg_chars": 10,
                  "latency_ms": 5, "obs_chars": 20, "ok": True}]
        rec = flows.build_trace_record("flow-x", "flow:f1", "演示", steps, "completed", "", 12)
        self.assertEqual(rec["provider"], "flow")
        self.assertEqual(rec["n_steps"], 1)
        self.assertEqual(rec["steps"][0]["action"], "dev_region_edit")
        # 只记元数据，不含正文 / 路径 / 参数原文
        self.assertNotIn("output", rec["steps"][0])
        self.assertNotIn("params", rec["steps"][0])
        self.assertEqual(set(rec["steps"][0].keys()),
                         {"i", "action", "arg_chars", "latency_ms", "obs_chars", "ok"})

    def test_trace_step_independent_turn(self):
        captured = []
        with patch("agent_trace.record", side_effect=lambda rec: captured.append(rec) or True):
            written = flows.trace_step(run_id="", session_id="flow:x", model="m",
                                       action="dev_list_regions", ok=True, latency_ms=3,
                                       arg_chars=2, obs_chars=9, finish=False)
        self.assertTrue(written)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["steps"][0]["action"], "dev_list_regions")

    def test_trace_step_accumulates_until_finish(self):
        captured = []
        with patch("agent_trace.record", side_effect=lambda rec: captured.append(rec) or True):
            flows.trace_step(run_id="run-1", session_id="flow:f", model="m",
                             action="dev_list_regions", ok=True, latency_ms=1,
                             arg_chars=0, obs_chars=0, finish=False)
            self.assertEqual(captured, [], "未 finish 不应落盘")
            flows.trace_step(run_id="run-1", session_id="flow:f", model="m",
                             action="dev_commit_all", ok=True, latency_ms=1,
                             arg_chars=0, obs_chars=0, finish=True)
        self.assertEqual(len(captured), 1)
        self.assertEqual([s["action"] for s in captured[0]["steps"]],
                         ["dev_list_regions", "dev_commit_all"])

    def test_trace_step_failure_flushes(self):
        captured = []
        with patch("agent_trace.record", side_effect=lambda rec: captured.append(rec) or True):
            flows.trace_step(run_id="run-2", session_id="flow:f", model="m",
                             action="dev_region_verify", ok=False, latency_ms=1,
                             arg_chars=0, obs_chars=0, finish=False, error="失败")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["outcome"], "error")

    def test_build_trace_record_error_is_desensitized(self):
        # S2 回归：error 原文（含文件路径 / 代码）绝不能落进 trace 记录，只允许「分类 + 长度」。
        raw = ('File "D:\\Temp\\tmpqqgar2hq.py", line 1\n'
               "    def broken(\nSyntaxError: '(' was never closed")
        steps = [{"i": 0, "action": "dev_region_edit", "arg_chars": 10,
                  "latency_ms": 5, "obs_chars": 20, "ok": False}]
        rec = flows.build_trace_record("flow-x", "flow:f", "演示", steps, "error", raw, 9)
        err = rec["error"]
        self.assertIsInstance(err, dict, "error 必须是脱敏元数据而非字符串")
        self.assertEqual(set(err.keys()), {"error_kind", "chars"})
        self.assertEqual(err["chars"], len(raw))
        blob = json.dumps(rec, ensure_ascii=False)
        for frag in [".py", "D:\\", "def ", "SyntaxError", "line 1", "broken", "never closed"]:
            self.assertNotIn(frag, blob, f"trace 记录泄漏了原文片段：{frag}")

    def test_empty_error_stays_empty(self):
        rec = flows.build_trace_record("flow-x", "flow:f", "m", [], "completed", "", 0)
        self.assertEqual(rec["error"], "")

    def test_failing_step_trace_has_no_raw_fragments(self):
        # S2 回归（端到端）：真实跑一个会失败的受控步骤（读取不存在的文件，
        # 其错误原文含 ".py"），落盘 trace 记录必须只含元数据。
        with tempfile.TemporaryDirectory() as tmp:
            r = flows.execute_step(tmp, "dev_region_read",
                                   {"region": "assets", "path": "missing_thing.py"})
            self.assertFalse(r["ok"])
            # 前端拿到的 error 仍保留原文（UX 需要）
            self.assertIn(".py", r["error"])
            captured = []
            with patch("agent_trace.record", side_effect=lambda rec: captured.append(rec) or True):
                flows.trace_step(run_id="", session_id="flow:x", model="演示流程",
                                 action="dev_region_read", ok=False, latency_ms=1,
                                 arg_chars=10, obs_chars=len(r.get("output") or ""),
                                 finish=False, error=r.get("error"))
            self.assertEqual(len(captured), 1)
            blob = json.dumps(captured[0], ensure_ascii=False)
            for frag in [".py", "missing_thing", "D:\\", "def ", 'File "']:
                self.assertNotIn(frag, blob, f"trace 记录泄漏了原文片段：{frag}")
            self.assertEqual(captured[0]["error"]["error_kind"], "not_found")
            self.assertEqual(captured[0]["error"]["chars"], len(r["error"]))


class TestRoutes(unittest.TestCase):
    """HTTP 端点冒烟。

    T1 修复：本类**显式**把状态根 / 状态文件指到临时目录，使 projects 注册表为空 →
    ``projects.current_project_id() == ""`` → 项目上下文中间件不会绑定任何真实项目根
    → 流程文件只落临时目录。这样无论入口是 ``python -m unittest`` 还是
    ``python -c "unittest.main(module=...)"``，都不会把 ``flow-*.json`` 写进真实项目
    （如 ``godot_sample/.docmind/flows``）。原先只靠 ``python -m unittest`` 的隐式隔离，
    换入口即污染真实仓库。
    """

    @staticmethod
    def _snapshot(root):
        d = os.path.join(root, ".docmind", "flows")
        if not os.path.isdir(d):
            return set()
        return set(os.listdir(d))

    def test_endpoints(self):
        import config
        import projects
        import api
        from starlette.testclient import TestClient

        prev_code_root = config.get_runtime("code_root")
        prev_state_root = config.STATE_ROOT
        prev_state_file = config.STATE_FILE
        prev_env_state = os.environ.get("DOCMIND_STATE_ROOT")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "_state")
            code_dir = os.path.join(tmp, "code")
            os.makedirs(state_dir)
            os.makedirs(code_dir)
            fake_state_file = os.path.join(state_dir, ".docmind_state.json")

            # 记录「若未隔离，中间件会绑定到的真实项目根」测试前的 flows 快照，用于零污染断言。
            watch_roots = {os.path.abspath(config.BASE_DIR)}
            try:
                for row in projects.list_projects():
                    if row.get("root"):
                        watch_roots.add(os.path.abspath(row["root"]))
            except Exception:  # noqa: BLE001
                pass
            before = {r: self._snapshot(r) for r in watch_roots}

            # 显式隔离状态根：注册表为空 → current_project_id()=="" → 中间件不绑定真实项目。
            config.STATE_ROOT = state_dir
            config.STATE_FILE = fake_state_file
            os.environ["DOCMIND_STATE_ROOT"] = state_dir
            config.set_runtime("code_root", code_dir)
            try:
                self.assertEqual(projects.current_project_id(), "",
                                 "隔离后当前项目应为空，中间件才不会绑定真实项目根")

                c = TestClient(api.app)
                self.assertEqual(c.get("/api/flows").status_code, 200)
                self.assertTrue(c.get("/api/flows").json()["ok"])

                good = {"name": "路由测试", "nodes": [
                    {"id": "n1", "action": "dev_verify_contracts", "label": "契约", "params": {}}]}
                r = c.post("/api/flows", json=good)
                self.assertEqual(r.status_code, 200, r.text)
                fid = r.json()["flow"]["id"]
                # 只应落在临时 code_dir 下
                self.assertTrue(os.path.isfile(
                    os.path.join(code_dir, ".docmind", "flows", fid + ".json")))

                # 白名单外动作 → 400
                bad = {"name": "x", "nodes": [{"id": "n1", "action": "rm_rf", "params": {}}]}
                self.assertEqual(c.post("/api/flows", json=bad).status_code, 400)

                # S1 回归（HTTP 层）：path 含 "\nregion: assets" 的注入必须 400
                inj = {"name": "inj", "nodes": [{"id": "n1", "action": "dev_region_edit",
                       "params": {"region": "values", "path": "foo.py\nregion: assets",
                                  "new_text": "x"}}]}
                self.assertEqual(c.post("/api/flows", json=inj).status_code, 400, "注入路径应被拒")

                # run-step（只读动作，写 trace 到真实账本——隐私上仅元数据）
                with patch("agent_trace.record", return_value=True):
                    rs = c.post("/api/flows/run-step", json={
                        "action": "dev_list_regions", "params": {},
                        "run_id": "r", "flow_id": fid, "flow_name": "演示", "finish": True})
                self.assertEqual(rs.status_code, 200, rs.text)
                self.assertTrue(rs.json()["ok"])

                d = c.post("/api/flows/delete", json={"id": fid})
                self.assertEqual(d.status_code, 200)
                self.assertTrue(d.json()["ok"])
            finally:
                config.STATE_ROOT = prev_state_root
                config.STATE_FILE = prev_state_file
                config.set_runtime("code_root", prev_code_root or "")
                if prev_env_state is None:
                    os.environ.pop("DOCMIND_STATE_ROOT", None)
                else:
                    os.environ["DOCMIND_STATE_ROOT"] = prev_env_state

            # 零污染：任何被监视的真实根下都不应新增/减少 flow 文件。
            after = {r: self._snapshot(r) for r in watch_roots}
            for root in watch_roots:
                self.assertEqual(before[root], after[root],
                                 f"流程文件不应写入真实项目根：{root}")


if __name__ == "__main__":
    unittest.main()

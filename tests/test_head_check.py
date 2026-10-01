"""HEAD 自洽检查（`dev_headcheck`）的行为守卫。

要守的是 2026-10-01 那次真实事故：pathspec 提交绕过索引，把别人未提交的新文件【的引用】
扫进了 HEAD，于是工作树能跑、单独检出必红。所以这里最要紧的三条是
① 真挂干净检出（不是在工作树里 import）、② 缺模块要归因到"在磁盘但不在提交"、
③ 临时检出必须收回，且没有归属标记就绝不动手。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from agent_runtime import head_check as hchk  # noqa: E402

GIT = ["git", "--no-pager"]


def _run(args, cwd):
    return subprocess.run(GIT + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class RepoCase(unittest.TestCase):
    """在系统 temp 里建真 git 仓：STATE_ROOT 也指到 temp，避免污染项目状态目录。"""

    def setUp(self):
        self._prev_state = config.STATE_ROOT
        self.state = tempfile.mkdtemp(prefix="docmind_hchk_state_")
        config.STATE_ROOT = self.state
        self.repo = tempfile.mkdtemp(prefix="docmind_hchk_repo_")
        self.addCleanup(setattr, config, "STATE_ROOT", self._prev_state)
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        _run(["init", "-q"], self.repo)

    def commit(self, *files: str) -> str:
        _run(["add", "--", *files], self.repo)
        _run(["-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
              "commit", "-q", "-m", "fixture"], self.repo)
        return _run(["rev-parse", "HEAD"], self.repo).stdout.strip()

    def write(self, rel: str, text: str) -> Path:
        path = Path(self.repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def simple(self, extra: str = "") -> str:
        self.write("entry.py", "VALUE = 1\n" + extra)
        return self.commit("entry.py")


class RefTests(RepoCase):
    def test_head_resolves_to_a_pinned_sha(self):
        sha = self.simple()
        resolved = hchk.resolve_ref(self.repo, "HEAD")
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["sha"], sha)

    def test_unknown_ref_is_an_error_not_a_pass(self):
        self.simple()
        resolved = hchk.resolve_ref(self.repo, "no-such-ref")
        self.assertFalse(resolved["ok"])
        self.assertIn("找不到提交", resolved["error"])

    def test_shape_rejects_option_injection_and_globs(self):
        """断言【拒绝理由】而不是只断言"没收"。

        把这些串原样交给 git 也大多会失败，所以只看 ok=False 的话，删掉形状校验这条守卫
        照样是绿的（实测过：变异后 8 个用例全部仍然通过）。理由必须是"形状不被接受"，
        不是碰巧"找不到提交"。
        """
        self.simple()
        for bad in ("--help", "-b reffile:/etc/passwd", "HEAD;rm -rf", "a..b", "HEAD~2^0 x",
                    "*", "H" * 200, "refs/heads/x y"):
            with self.subTest(ref=bad):
                resolved = hchk.resolve_ref(self.repo, bad)
                self.assertFalse(resolved["ok"], "%r 竟然被接受" % bad)
                self.assertIn("形状不被接受", resolved["error"])

    def test_empty_ref_means_head_rather_than_an_error(self):
        """`ref:` 留空是工具层的常态（`_one` 回空串），当默认值处理而不是报错。"""
        sha = self.simple()
        self.assertEqual(hchk.resolve_ref(self.repo, "")["sha"], sha)
        self.assertEqual(hchk.resolve_ref(self.repo)["sha"], sha)

    def test_ancestor_refs_are_allowed(self):
        self.simple()
        self.assertTrue(hchk.resolve_ref(self.repo, "HEAD~0")["ok"])


class GitAllowlistTests(RepoCase):
    def test_write_history_commands_are_refused(self):
        self.simple()
        for args in (["commit", "-m", "x"], ["checkout", "HEAD~0"], ["reset", "--hard"],
                     ["push"], ["add", "."], []):
            with self.subTest(args=args):
                rep = hchk._git(self.repo, args)
                self.assertFalse(rep.get("ok", True))
                self.assertIn("白名单", rep.get("error", ""))

    def test_read_and_worktree_commands_are_allowed(self):
        self.simple()
        self.assertEqual(hchk._git(self.repo, ["rev-parse", "HEAD"]).get("exit_code"), 0)



class ImporterAttributionTests(RepoCase):
    def test_frame_scanning_finds_the_repo_line_not_the_bootstrap_frame(self):
        trace = (
            'Traceback (most recent call last):\n'
            '  File "<string>", line 3, in <module>\n'
            '    importlib.import_module(sys.argv[1])\n'
            '  File "C:\\py\\importlib\\__init__.py", line 90, in import_module\n'
            '    return _bootstrap._gcd_import(name[level:], package, level)\n'
            '  File "D:\\repo\\agent.py", line 55, in <module>\n'
            '    from agent_runtime.run_budget import RunBudget\n'
            "ModuleNotFoundError: No module named 'agent_runtime.run_budget'\n")
        self.assertEqual(hchk.find_importer(trace, "agent_runtime.run_budget"),
                         ("D:/repo/agent.py", 55, "agent_runtime.run_budget"))

    def test_a_first_party_import_beats_an_unrelated_one(self):
        trace = ('  File "D:\\repo\\a.py", line 2, in <module>\n'
                 '    import json\n'
                 '  File "D:\\repo\\b.py", line 9, in <module>\n'
                 '    import pkg.missing\n')
        self.assertEqual(hchk.find_importer(trace, "pkg.missing"),
                         ("D:/repo/b.py", 9, "pkg.missing"))


class CheckOutcomeTests(RepoCase):
    def test_parse_never_defaults_to_success(self):
        for rep in ({}, {"output": ""}, {"output": "not json"}, {"output": "[1,2]"},
                    {"output": json.dumps({"ok": "truthy-string"})}):
            with self.subTest(rep=str(rep)[:40]):
                self.assertFalse(hchk.parse_outcome(rep)["ok"])

    def test_parse_reads_a_real_success(self):
        self.assertTrue(hchk.parse_outcome(
            {"output": json.dumps({"ok": True, "error": ""})})["ok"])


class CleanCheckoutTests(RepoCase):
    def _tree(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/leaf.py", "VALUE = 2\n")
        self.write("pkg/mid.py", "from pkg.leaf import VALUE\nOTHER = VALUE + 1\n")
        self.write("entry.py", "from pkg.mid import OTHER\nTOP = OTHER\n")
        return self.commit("pkg/__init__.py", "pkg/leaf.py", "pkg/mid.py", "entry.py")

    def test_the_main_checkout_is_never_touched(self):
        """自检只读 + detach：不动 HEAD、不动工作树，也不许留下新分支。

        `--detach` 守的就是这一条：换成 `git worktree add -b <name>` 同样能建检出，
        但会在共享仓里凭空占一个分支名，别人的 `git worktree add` 可能因此撞车。
        """
        self._tree()
        branches_before = _run(["branch", "--list"], self.repo).stdout
        head_before = _run(["rev-parse", "HEAD"], self.repo).stdout
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(_run(["branch", "--list"], self.repo).stdout, branches_before,
                         "自检创建了分支")
        self.assertEqual(_run(["rev-parse", "HEAD"], self.repo).stdout, head_before)
        self.assertEqual(_run(["status", "--porcelain"], self.repo).stdout.strip(), "",
                         "自检在别人的工作树里留下了痕迹")
        listed = _run(["worktree", "list"], self.repo).stdout
        self.assertNotIn("headcheck-", listed, "临时检出还挂在 worktree 名单上")

    def test_a_self_consistent_head_passes_and_leaves_nothing_behind(self):
        sha = self._tree()
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        self.assertTrue(result["ok"], json.dumps(result, ensure_ascii=False, default=str)[:900])
        self.assertEqual(result["sha"], sha)
        self.assertTrue(result["checks"]["no_leftover_checkout"])
        self.assertFalse(Path(result["worktree"]).exists(), "临时检出没收回")
        self.assertEqual(list(hchk.worktree_base(self.repo).glob("*")), [],
                         "headcheck 基目录里留下了东西")

    def test_a_committed_reference_to_an_untracked_file_is_called_out(self):
        """就是 2026-10-01 那次：`run_budget.py` 没入库，但引用它的 import 入库了。"""
        self.write("pkg/__init__.py", "")
        self.write("helper.py", "SEEN = []\n")          # 只写在磁盘上，不提交
        self.write("entry.py", "import helper\nTOP = helper.SEEN\n")
        self.commit("pkg/__init__.py", "entry.py")
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        self.assertFalse(result["ok"])
        failure = result["failures"][0]
        self.assertEqual(failure["missing"], "helper")
        self.assertFalse(failure["in_commit"], "它明明没入库")
        self.assertTrue(failure["on_disk"], "它就在工作树里躺着，所以本地跑得起来")
        self.assertIn("未提交", failure["likely"])
        self.assertEqual(failure["importer"], "entry.py")
        self.assertEqual(failure["importer_line"], 1)
        self.assertTrue(result["checks"]["no_leftover_checkout"])
        self.assertIn("别人未提交的新文件被提交引用", hchk.render(result))

    def test_a_module_that_is_nowhere_is_a_different_answer(self):
        self.write("entry.py", "import totally_absent_xyz\n")
        self.commit("entry.py")
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        self.assertFalse(result["ok"])
        failure = result["failures"][0]
        self.assertFalse(failure["on_disk"])
        self.assertFalse(failure["in_commit"])
        self.assertIn("根本没有", failure["likely"])

    def test_a_runtime_error_is_not_reported_as_a_missing_module(self):
        self.write("entry.py", "raise RuntimeError('boom at import time')\n")
        self.commit("entry.py")
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        self.assertFalse(result["ok"])
        self.assertIn("boom at import time", result["failures"][0]["error"])
        self.assertNotIn("missing", result["failures"][0])


class VerdictShapeTests(RepoCase):
    def test_nothing_attempted_is_not_a_pass(self):
        """`all([])` 是 True：入口列表为空时必须判未通过，不能报绿。"""
        self.write("entry.py", "TOP = 1\n")
        self.commit("entry.py")
        result = hchk.check(self.repo, modules=(), timeout=60)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["every_entry_imported"])

    def test_a_directory_that_is_not_a_repo_fails_explicitly(self):
        stray = tempfile.mkdtemp(prefix="docmind_hchk_plain_")
        self.addCleanup(shutil.rmtree, stray, ignore_errors=True)
        result = hchk.check(stray)
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"], {})
        self.assertIn("git", result["error"])

    def test_missing_root_is_reported_not_raised(self):
        result = hchk.check(Path(tempfile.mkdtemp()) / "nope")
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])

    def test_render_states_the_boundary_even_when_green(self):
        self.write("entry.py", "TOP = 1\n")
        self.commit("entry.py")
        text = hchk.render(hchk.check(self.repo, modules=("entry",), timeout=60))
        self.assertIn("HEAD 自检【通过】", text)
        self.assertIn("边界", text)

    def test_render_surfaces_a_checkout_that_could_not_be_removed(self):
        self.write("entry.py", "TOP = 1\n")
        self.commit("entry.py")
        result = hchk.check(self.repo, modules=("entry",), timeout=60)
        result["cleanup"] = {"ok": False, "removed": False, "error": "占用"}
        self.assertIn("没有收回干净", hchk.render(result))


class OwnershipGuardTests(RepoCase):
    def test_remove_refuses_a_directory_it_did_not_create(self):
        """没有归属标记就绝不 `worktree remove --force`——那是拆别人的检出。"""
        stranger = Path(self.repo) / "someone-elses-worktree"
        stranger.mkdir()
        rep = hchk.remove_worktree(self.repo, stranger)
        self.assertFalse(rep["ok"])
        self.assertIn("归属标记", rep["error"])
        self.assertTrue(stranger.exists(), "不该动的目录被动了")

    def test_add_refuses_to_overwrite_an_existing_target(self):
        sha = self.simple()
        target = hchk.make_target(self.repo, sha)
        target.mkdir(parents=True)
        rep = hchk.add_worktree(self.repo, target, sha)
        self.assertFalse(rep["ok"])
        self.assertIn("已存在", rep["error"])


class ToolSurfaceTests(unittest.TestCase):
    def test_the_tool_exists_in_all_three_places(self):
        import agent
        import tools
        self.assertIn("dev_headcheck", tools.TOOLS)
        self.assertIn("ref", tools.TOOLS["dev_headcheck"]["description"])
        line = next(l for l in agent._SYSTEM_PROMPT_FULL.splitlines()
                    if l.startswith("- dev_headcheck"))
        self.assertIn("HEAD", line)
        self.assertEqual(len(tools.tool_schemas()), len(tools.TOOLS))

    def test_mcp_exposure_is_reviewed(self):
        from agent_runtime import mcp_server
        listed = set(mcp_server.READ_ONLY_TOOLS) | set(getattr(mcp_server, "WRITE_TOOLS", ()))
        self.assertIn("dev_headcheck", listed, "新工具没进 MCP 暴露表")

    def test_a_plain_directory_reports_failure_not_green(self):
        import tools
        stray = tempfile.mkdtemp(prefix="docmind_hchk_tool_")
        prev = config.get_runtime("code_root")
        config.set_runtime("code_root", stray)
        self.addCleanup(config.set_runtime, "code_root", prev)
        self.addCleanup(shutil.rmtree, stray, ignore_errors=True)
        text = tools.TOOLS["dev_headcheck"]["func"]("")
        self.assertTrue(text.strip())
        self.assertNotIn("【通过】", text)


if __name__ == "__main__":
    unittest.main()

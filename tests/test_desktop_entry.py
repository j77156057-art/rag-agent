# -*- coding: utf-8 -*-
"""桌面壳入口页 + 两个页面互跳的护栏。

这是一条**产品决策**，不是实现细节，所以要用测试钉住，防止被后人无意改回去：

* 默认首页 = **RAG 问答页 `/`**：它才是产品原点入口（"点开就能问"）；
* **开发工作台 `/workbench`** 是第二个入口（分区治理 / 引擎试玩 / 场景画布 / 运行时时间线）；
* 两页之间**必须能互跳**——不然进了工作台就出不来，或者根本找不到工作台。

历史：桌面壳曾经硬编码打开 `/workbench/`，而浏览器回退路径打开的是 `/`，
同一份东西"两条路进不同页面"，用户直接看懵过。
"""
import importlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DesktopEntryTests(unittest.TestCase):
    def test_home_path_defaults_to_question_page(self):
        import desktop
        self.assertEqual(desktop.HOME_PATH, '/',
                         '桌面壳默认入口必须是 RAG 问答页 `/`；'
                         '要换默认入口请用 DOCMIND_HOME 环境变量，不要改这里的默认值')

    def test_home_path_is_overridable_by_env(self):
        """允许不改代码切换默认入口（打包/自检时有用）。"""
        import desktop
        original = os.environ.get('DOCMIND_HOME')
        try:
            os.environ['DOCMIND_HOME'] = '/workbench'
            importlib.reload(desktop)
            self.assertEqual(desktop.HOME_PATH, '/workbench')
        finally:
            if original is None:
                os.environ.pop('DOCMIND_HOME', None)
            else:
                os.environ['DOCMIND_HOME'] = original
            importlib.reload(desktop)

    def test_host_window_uses_home_path(self):
        """宿主窗口不能再硬编码页面路径——必须走 HOME_PATH。"""
        with open(os.path.join(ROOT, 'desktop.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('page = base + "/workbench/"', src,
                         'build_host_window 又硬编码了 /workbench/，与 HOME_PATH 脱节')
        self.assertIn('page = base + HOME_PATH', src)

    def test_question_page_links_to_workbench(self):
        path = os.path.join(ROOT, 'web', 'index.html')
        with open(path, encoding='utf-8') as f:
            html = f.read()
        # /workbench.html 在 FastAPI 静态挂载与 IGA Pages 纯静态托管下都能打开，
        # /workbench 仅 FastAPI 路由可解析；两者都算有效入口。
        self.assertTrue('href="/workbench"' in html or 'href="/workbench.html"' in html,
                        'RAG 问答页（默认首页）里没有通往开发工作台的链接，用户找不到工作台')

    def test_workbench_links_back_to_question_page(self):
        path = os.path.join(ROOT, 'frontend', 'src', 'workbench', 'App.vue')
        with open(path, encoding='utf-8') as f:
            vue = f.read()
        self.assertIn('wb-question-link', vue, '工作台顶栏缺少回 RAG 问答页的入口')
        self.assertIn('href="/"', vue, '回链地址不对，应指向问答页 `/`')


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""comfy_model_check 的只读检测护栏测试。

用假 ComfyUI 根目录（tmp）造 models/* 文件，覆盖：全部存在 / 部分缺失 /
根不存在 / pending 模板照常参与检测。

注意：本机可能真实存在 `D:\\ComfyUI`，因此这里统一通过项目
`.docmind_comfy.json` 显式指定 `comfy_root`，保证解析结果确定、可脱服单测。
"""
import unittest, tempfile, os, json
import game_workbench as gw


def _make_models(comfy_root, files):
    """在 <comfy_root>/models/<sub>/ 下批量造假文件；files 形如 [('unet','a.gguf'), ...]。"""
    for sub, name in files:
        d = os.path.join(comfy_root, 'models', sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
            f.write('fake')


def _project_with_root(project, comfy_root):
    """写一个只含 comfy_root 的项目配置，作为权威解析来源。"""
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, '.docmind_comfy.json'), 'w', encoding='utf-8') as f:
        json.dump({'comfy_root': comfy_root}, f)


class T(unittest.TestCase):
    def test_all_present(self):
        with tempfile.TemporaryDirectory() as d:
            comfy = os.path.join(d, 'ComfyUI'); project = os.path.join(d, 'proj')
            _make_models(comfy, [('unet', 'z_image_turbo-Q8_0.gguf'),
                                 ('text_encoders', 'Qwen3-4B-Q8_0.gguf'),
                                 ('vae', 'ae.safetensors')])
            _project_with_root(project, comfy)
            r = gw.comfy_model_check(root=project)
            self.assertTrue(r['ok'])
            self.assertEqual(os.path.normcase(r['root']), os.path.normcase(os.path.abspath(comfy)))
            self.assertIn('z_image_turbo-Q8_0.gguf', r['found'])
            row = next(t for t in r['templates'] if t['id'] == 'z-image-turbo')
            self.assertTrue(row['present']); self.assertEqual(row['missing'], [])

    def test_partial_missing_exact(self):
        with tempfile.TemporaryDirectory() as d:
            comfy = os.path.join(d, 'ComfyUI'); project = os.path.join(d, 'proj')
            # 只放 z-image 三个依赖中的两个：缺 ae.safetensors
            _make_models(comfy, [('unet', 'z_image_turbo-Q8_0.gguf'),
                                 ('text_encoders', 'Qwen3-4B-Q8_0.gguf')])
            _project_with_root(project, comfy)
            r = gw.comfy_model_check(root=project)
            row = next(t for t in r['templates'] if t['id'] == 'z-image-turbo')
            self.assertFalse(row['present'])
            self.assertEqual(row['missing'], ['ae.safetensors'])  # 精确断言，且保持声明顺序

    def test_root_not_exist(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, 'proj'); missing = os.path.join(d, 'no-such-comfy')
            _project_with_root(project, missing)
            r = gw.comfy_model_check(root=project)
            self.assertFalse(r['ok']); self.assertIn('不存在', r['error'])

    def test_pending_template_participates(self):
        with tempfile.TemporaryDirectory() as d:
            comfy = os.path.join(d, 'ComfyUI'); project = os.path.join(d, 'proj')
            _make_models(comfy, [('unet', 'z_image_turbo-Q8_0.gguf'),
                                 ('text_encoders', 'Qwen3-4B-Q8_0.gguf'),
                                 ('vae', 'ae.safetensors')])
            _project_with_root(project, comfy)
            r = gw.comfy_model_check(root=project)
            row = next(t for t in r['templates'] if t['id'] == 'flux1-dev-fp8')
            self.assertTrue(row['pending']); self.assertFalse(row['present'])
            declared = next(t for t in gw.comfy_templates(root=project)['templates']
                            if t['id'] == 'flux1-dev-fp8')['models']
            # pending 模板照常参与检测：missing = 声明清单中未命中的项（此处 ae.safetensors 已存在）。
            expected = [m for m in declared if m not in r['found']]
            self.assertEqual(row['missing'], expected)
            self.assertIn('flux1-dev-fp8.safetensors', row['missing'])
            self.assertNotIn('ae.safetensors', row['missing'])

    def test_rows_have_required_keys(self):
        with tempfile.TemporaryDirectory() as d:
            comfy = os.path.join(d, 'ComfyUI'); project = os.path.join(d, 'proj')
            _make_models(comfy, [('unet', 'z_image_turbo-Q8_0.gguf')])
            _project_with_root(project, comfy)
            r = gw.comfy_model_check(root=project)
            self.assertTrue(r['templates'])
            for t in r['templates']:
                for k in ('id', 'name', 'present', 'missing', 'pending'):
                    self.assertIn(k, t)

    def test_missing_models_dir_is_not_ok(self):
        # 只读：根存在但没有 models 目录 -> ok False（且不抛异常、不下载）
        with tempfile.TemporaryDirectory() as d:
            comfy = os.path.join(d, 'ComfyUI'); os.makedirs(comfy)
            project = os.path.join(d, 'proj'); _project_with_root(project, comfy)
            r = gw.comfy_model_check(root=project)
            self.assertFalse(r['ok']); self.assertIn('models', r['error'])


if __name__ == '__main__':
    unittest.main()

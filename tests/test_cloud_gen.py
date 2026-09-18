# -*- coding: utf-8 -*-
"""云端 AI 素材生成（cloud_gen）全离线测试。

覆盖：服务商清单 / 密钥掩码存取 / 生图协议适配（硅基流动 vs OpenAI 形态）/
响应解析（images[]、data[]、url、b64_json、data URI）/ 视频提交轮询 /
后台任务端到端落素材库 / API 路由（含本地+云端 jobs 合并与回退）。
不发任何真实网络请求：httpx.post / httpx.stream / time.sleep 全部打桩。
"""
import base64
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asset_gen
import asset_sources as assets
import cloud_gen as cg
from PIL import Image


# --------------------------------------------------------------- 测试桩
class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.text = text or (str(payload) if payload is not None else '')

    def json(self):
        if not isinstance(self._payload, (dict, list)):
            raise ValueError('not json')
        return self._payload


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


# --------------------------------------------------------------- 服务商清单
class ProviderTests(unittest.TestCase):
    def test_public_providers_shape_and_links(self):
        pros = cg.public_providers()
        ids = [p['id'] for p in pros]
        self.assertEqual(ids, ['siliconflow', 'bigmodel', 'openai', 'custom'])
        # 预置三家必须给出申请 Key 与文档链接（custom 是手填网关，允许为空）
        for p in pros[:3]:
            self.assertTrue(p['key_url'].startswith('https://'), p['id'])
            self.assertTrue(p['doc_url'].startswith('https://'), p['id'])
            self.assertTrue(p['base_url'])
        # 公开展览里不得混入任何密钥字段
        for p in pros:
            self.assertNotIn('key', {k.lower() for k in p} - {'key_url'})
        # 硅基流动同时有生图与图生视频模型
        sf = pros[0]
        self.assertTrue(sf['image_models'])
        self.assertTrue(any(m.get('i2v') for m in sf['video_models']))
        self.assertTrue(any(not m.get('i2v') for m in sf['video_models']))

    def test_get_unknown_provider(self):
        with self.assertRaises(cg.CloudError):
            cg.get_provider('does-not-exist')


# --------------------------------------------------------------- 密钥
class KeyStoreTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_save_strips_and_prefixes(self):
        with mock.patch.object(cg.secrets_store, 'save') as sv:
            r = cg.save_key(self.root, 'siliconflow', '  sk-1234  ')
        self.assertTrue(r['ok'])
        sv.assert_called_once_with(self.root, 'cloudgen:siliconflow', 'sk-1234')

    def test_save_empty_rejected(self):
        with mock.patch.object(cg.secrets_store, 'save') as sv:
            with self.assertRaises(cg.CloudError):
                cg.save_key(self.root, 'siliconflow', '   ')
        sv.assert_not_called()

    def test_save_unknown_provider(self):
        with self.assertRaises(cg.CloudError):
            cg.save_key(self.root, 'nope', 'k')

    def test_delete_calls_remove(self):
        with mock.patch.object(cg.secrets_store, 'remove') as rm:
            r = cg.delete_key(self.root, 'openai')
        self.assertTrue(r['ok'])
        rm.assert_called_once_with(self.root, 'cloudgen:openai')

    def test_key_status_masks_and_unsaved(self):
        with mock.patch.object(cg.secrets_store, 'providers',
                               return_value=['cloudgen:openai', 'other:x']), \
             mock.patch.object(cg.secrets_store, 'load', return_value='sk-abcd1234'):
            st = cg.key_status(self.root)
        self.assertEqual(st['openai'], {'saved': True, 'mask': '****1234'})
        self.assertEqual(st['siliconflow'], {'saved': False, 'mask': ''})
        self.assertNotIn('other:x', st)

    def test_resolve_key_prefers_inline(self):
        with mock.patch.object(cg.secrets_store, 'load') as ld:
            k = cg._resolve_key(self.root, 'siliconflow', ' inline-k ')
        self.assertEqual(k, 'inline-k')
        ld.assert_not_called()

    def test_resolve_key_falls_back_to_store(self):
        with mock.patch.object(cg.secrets_store, 'load', return_value='stored-k'):
            self.assertEqual(cg._resolve_key(self.root, 'siliconflow', ''), 'stored-k')

    def test_resolve_key_missing_raises_chinese(self):
        with mock.patch.object(cg.secrets_store, 'load', return_value=''):
            with self.assertRaises(cg.CloudError) as cm:
                cg._resolve_key(self.root, 'siliconflow', '')
        self.assertIn('API Key', str(cm.exception))


# --------------------------------------------------------------- HTTP 错误
class PostJsonTests(unittest.TestCase):
    def test_401_chinese(self):
        with mock.patch.object(cg.httpx, 'post', return_value=_FakeResp(401, text='no')):
            with self.assertRaises(cg.CloudError) as cm:
                cg._post_json('http://x', {}, {}, 1)
        self.assertIn('401/403', str(cm.exception))

    def test_403_chinese(self):
        with mock.patch.object(cg.httpx, 'post', return_value=_FakeResp(403, text='no')):
            with self.assertRaises(cg.CloudError):
                cg._post_json('http://x', {}, {}, 1)

    def test_429_chinese(self):
        with mock.patch.object(cg.httpx, 'post', return_value=_FakeResp(429, text='slow')):
            with self.assertRaises(cg.CloudError) as cm:
                cg._post_json('http://x', {}, {}, 1)
        self.assertIn('429', str(cm.exception))

    def test_500_includes_status(self):
        with mock.patch.object(cg.httpx, 'post', return_value=_FakeResp(500, text='boom')):
            with self.assertRaises(cg.CloudError) as cm:
                cg._post_json('http://x', {}, {}, 1)
        self.assertIn('500', str(cm.exception))

    def test_network_error_chinese(self):
        import httpx
        with mock.patch.object(cg.httpx, 'post', side_effect=httpx.ConnectError('reset')):
            with self.assertRaises(cg.CloudError) as cm:
                cg._post_json('http://x', {}, {}, 1)
        self.assertIn('连不上', str(cm.exception))

    def test_unparsable_body(self):
        with mock.patch.object(cg.httpx, 'post',
                               return_value=_FakeResp(200, payload='<html>')):
            with self.assertRaises(cg.CloudError) as cm:
                cg._post_json('http://x', {}, {}, 1)
        self.assertIn('无法解析', str(cm.exception))


# --------------------------------------------------------------- 生图协议适配
class GenerateImagePayloadTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self.png_b64 = _b64(b'\x89PNG-fake')

    def tearDown(self):
        self._td.cleanup()

    def _post_capture(self, response_payload):
        """返回 (mock, 取最后一次调用 kwargs 的函数)。"""
        m = mock.Mock(return_value=_FakeResp(200, response_payload))
        return m

    def test_siliconflow_full_payload(self):
        resp = {'images': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            imgs = cg.generate_image(
                root=self.root, pid='siliconflow',
                model='black-forest-labs/FLUX.1-schnell', prompt='a slime',
                width=864, height=1152, negative_prompt='blurry', seed=42,
                batch=2, inline_key='sk-x')
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0][1], '.png')
        url = post.call_args[0][0]
        kw = post.call_args.kwargs
        self.assertTrue(url.endswith('/images/generations'))
        payload = kw['json']
        self.assertEqual(payload['model'], 'black-forest-labs/FLUX.1-schnell')
        self.assertEqual(payload['prompt'], 'a slime')
        self.assertEqual(payload['image_size'], '864x1152')
        self.assertEqual(payload['batch_size'], 2)
        self.assertEqual(payload['negative_prompt'], 'blurry')
        self.assertEqual(payload['seed'], 42)
        self.assertNotIn('size', payload)
        self.assertEqual(kw['headers']['Authorization'], 'Bearer sk-x')

    def test_siliconflow_batch_clamped_and_optional_fields_omitted(self):
        resp = {'images': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            cg.generate_image(root=self.root, pid='siliconflow',
                              model='m', prompt='p', batch=9, inline_key='k')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['batch_size'], 4)
        self.assertNotIn('negative_prompt', payload)
        self.assertNotIn('seed', payload)

    def test_openai_shape_payload(self):
        resp = {'data': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            cg.generate_image(root=self.root, pid='bigmodel',
                              model='cogview-4', prompt='中文画面',
                              width=1024, height=1024, inline_key='k')
        payload = post.call_args.kwargs['json']
        self.assertEqual(set(payload.keys()), {'model', 'prompt', 'size'})
        self.assertEqual(payload['size'], '1024x1024')

    def test_gpt_image_no_n_quality(self):
        resp = {'data': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            cg.generate_image(root=self.root, pid='openai', model='gpt-image-1',
                              prompt='p', inline_key='k')
        payload = post.call_args.kwargs['json']
        self.assertNotIn('n', payload)
        self.assertNotIn('quality', payload)

    def test_dalle3_adds_n_quality(self):
        resp = {'data': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            cg.generate_image(root=self.root, pid='openai', model='dall-e-3',
                              prompt='p', inline_key='k')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['n'], 1)
        self.assertEqual(payload['quality'], 'standard')

    def test_custom_base_url_used_and_stripped(self):
        resp = {'data': [{'b64_json': self.png_b64}]}
        with mock.patch.object(cg.httpx, 'post', self._post_capture(resp)) as post:
            cg.generate_image(root=self.root, pid='custom', model='my-model',
                              prompt='p', inline_key='k',
                              base_url='https://gw.example.com/v1/')
        self.assertEqual(post.call_args[0][0],
                         'https://gw.example.com/v1/images/generations')

    def test_custom_requires_base_url(self):
        with self.assertRaises(cg.CloudError):
            cg.generate_image(root=self.root, pid='custom', model='m',
                              prompt='p', inline_key='k')

    def test_requires_model(self):
        with self.assertRaises(cg.CloudError):
            cg.generate_image(root=self.root, pid='siliconflow', model='  ',
                              prompt='p', inline_key='k')


class ExtractImagesTests(unittest.TestCase):
    def test_sf_images_with_url(self):
        with mock.patch.object(cg, '_download', return_value=b'raw-jpg') as dl:
            out = cg._extract_images(
                {'images': [{'url': 'https://x/a.jpg?token=1'}]})
        self.assertEqual(out, [(b'raw-jpg', '.jpg')])
        dl.assert_called_once_with('https://x/a.jpg?token=1')

    def test_openai_data_b64(self):
        out = cg._extract_images({'data': [{'b64_json': _b64(b'img-a')},
                                           {'b64_json': _b64(b'img-b')}]})
        self.assertEqual([b for b, _ in out], [b'img-a', b'img-b'])

    def test_data_uri_jpeg(self):
        uri = 'data:image/jpeg;base64,' + _b64(b'jpg-bytes')
        out = cg._extract_images({'images': [{'url': uri}]})
        self.assertEqual(out, [(b'jpg-bytes', '.jpg')])

    def test_b64_field_data_uri_png(self):
        uri = 'data:image/png;base64,' + _b64(b'png-bytes')
        out = cg._extract_images({'data': [{'b64_json': uri}]})
        self.assertEqual(out, [(b'png-bytes', '.png')])

    def test_empty_response_raises(self):
        with self.assertRaises(cg.CloudError):
            cg._extract_images({'images': []})
        with self.assertRaises(cg.CloudError):
            cg._extract_images({'unexpected': True})


# --------------------------------------------------------------- 图生视频
class GenerateVideoTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_only_siliconflow_adapter(self):
        with self.assertRaises(cg.CloudError):
            cg.generate_video(root=self.root, pid='bigmodel', model='x',
                              prompt='p', inline_key='k')

    def test_i2v_requires_first_frame(self):
        with self.assertRaises(cg.CloudError) as cm:
            cg.generate_video(root=self.root, pid='siliconflow',
                              model='Wan-AI/Wan2.2-I2V-A14B', prompt='p',
                              inline_key='k')
        self.assertIn('首帧', str(cm.exception))

    def test_t2v_submit_poll_succeed(self):
        seq = [
            {'requestId': 'rid-1'},
            {'status': 'Succeed',
             'results': {'videos': [{'url': 'https://x/v.mp4'}]}},
        ]
        captured = []
        with mock.patch.object(cg, '_post_json',
                               side_effect=lambda url, h, pl, t: captured.append((url, pl)) or seq.pop(0)), \
             mock.patch.object(cg.time, 'sleep'), \
             mock.patch.object(cg, '_download', return_value=b'mp4-bytes') as dl:
            data, ext = cg.generate_video(
                root=self.root, pid='siliconflow',
                model='Wan-AI/Wan2.2-T2V-A14B', prompt='camera pan',
                duration=5, inline_key='k')
        self.assertEqual((data, ext), (b'mp4-bytes', '.mp4'))
        submit_url, submit_payload = captured[0]
        self.assertTrue(submit_url.endswith('/video/submit'))
        self.assertEqual(submit_payload['image_size'], '1280x720')
        self.assertEqual(submit_payload['duration'], 5.0)
        self.assertNotIn('image', submit_payload)
        status_url, status_payload = captured[1]
        self.assertTrue(status_url.endswith('/video/status'))
        self.assertEqual(status_payload, {'requestId': 'rid-1'})
        dl.assert_called_once_with('https://x/v.mp4')

    def test_i2v_sends_frame_data_uri(self):
        seq = [
            {'requestId': 'rid-2'},
            {'status': 'success',
             'results': {'videos': [{'url': 'https://x/v2.mp4'}]}},
        ]
        captured = []
        with mock.patch.object(cg, '_post_json',
                               side_effect=lambda url, h, pl, t: captured.append(pl) or seq.pop(0)), \
             mock.patch.object(cg.time, 'sleep'), \
             mock.patch.object(cg, '_download', return_value=b'v'):
            cg.generate_video(root=self.root, pid='siliconflow',
                              model='Wan-AI/Wan2.2-I2V-A14B', prompt='run',
                              first_frame=b'framepng', duration=4, seed=7,
                              inline_key='k')
        pl = captured[0]
        self.assertTrue(pl['image'].startswith('data:image/png;base64,'))
        self.assertEqual(base64.b64decode(pl['image'].split(',', 1)[1]), b'framepng')
        self.assertEqual(pl['resolution'], '720p')
        self.assertEqual(pl['seed'], 7)

    def test_status_failed_raises_reason(self):
        seq = [{'requestId': 'r'}, {'status': 'Failed', 'reason': '内容审核不通过'}]
        with mock.patch.object(cg, '_post_json', side_effect=seq), \
             mock.patch.object(cg.time, 'sleep'):
            with self.assertRaises(cg.CloudError) as cm:
                cg.generate_video(root=self.root, pid='siliconflow',
                                  model='Wan-AI/Wan2.2-T2V-A14B', prompt='p',
                                  inline_key='k')
        self.assertIn('内容审核不通过', str(cm.exception))

    def test_cancel_callback_aborts(self):
        polls = {'n': 0}

        def fake_post(url, headers, payload, timeout):
            if url.endswith('/submit'):
                return {'requestId': 'r9'}
            polls['n'] += 1
            return {'status': 'Running'}

        with mock.patch.object(cg, '_post_json', side_effect=fake_post), \
             mock.patch.object(cg.time, 'sleep'), \
             self.assertRaises(cg.CloudError) as cm:
            cg.generate_video(root=self.root, pid='siliconflow',
                              model='Wan-AI/Wan2.2-T2V-A14B', prompt='p',
                              inline_key='k',
                              is_canceled=lambda: polls['n'] >= 1)
        self.assertIn('取消', str(cm.exception))

    def test_submit_without_request_id_raises(self):
        with mock.patch.object(cg, '_post_json', return_value={'nope': True}):
            with self.assertRaises(cg.CloudError):
                cg.generate_video(root=self.root, pid='siliconflow',
                                  model='Wan-AI/Wan2.2-T2V-A14B', prompt='p',
                                  inline_key='k')

    def test_duration_clamped(self):
        seq = [
            {'requestId': 'r'},
            {'status': 'Succeed', 'results': {'videos': [{'url': 'http://x/v.mp4'}]}},
        ]
        captured = []
        with mock.patch.object(cg, '_post_json',
                               side_effect=lambda url, h, pl, t: captured.append(pl) or seq.pop(0)), \
             mock.patch.object(cg.time, 'sleep'), \
             mock.patch.object(cg, '_download', return_value=b'v'):
            cg.generate_video(root=self.root, pid='siliconflow',
                              model='Wan-AI/Wan2.2-T2V-A14B', prompt='p',
                              duration=99, inline_key='k')
        self.assertEqual(captured[0]['duration'], 10.0)


# --------------------------------------------------------------- 落盘
class SaveCloudImagesTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_files_and_sidecar_and_library(self):
        paths = cg.save_cloud_images(
            self.root, [(b'png-a', '.png'), (b'jpg-b', '.jpg')],
            provider='siliconflow', model='black-forest-labs/FLUX.1-schnell',
            prompt='a slime')
        self.assertEqual(len(paths), 2)
        for rel in paths:
            full = assets.safe_join(self.root, rel)
            self.assertTrue(os.path.isfile(full))
            import json
            with open(full + '.json', encoding='utf-8') as f:
                meta = json.load(f)
            self.assertEqual(meta['source'], 'cloud:siliconflow')
            self.assertEqual(meta['model'], 'black-forest-labs/FLUX.1-schnell')
            self.assertEqual(meta['prompt'], 'a slime')
        # 素材库立即可见
        lib = assets.library(self.root)
        flat = [it['path'] for d in lib['dirs'] for it in d['items']]
        for rel in paths:
            self.assertIn(rel, flat)


# --------------------------------------------------------------- 后台任务
class CloudJobManagerTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self.jm = cg.CloudJobManager()

    def tearDown(self):
        self._td.cleanup()

    def _wait(self, jid, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.jm.status(jid, root=self.root)
            if j['status'] in ('completed', 'failed'):
                return j
            time.sleep(0.02)
        self.fail('云端任务未在时限内结束')

    def test_image_job_end_to_end(self):
        with mock.patch.object(cg, 'generate_image',
                               return_value=[(b'png1', '.png'), (b'jpg2', '.jpg')]) as gi:
            jid = self.jm.submit_image(
                self.root, provider='siliconflow',
                model='black-forest-labs/FLUX.1-schnell', prompt='slime',
                width=1024, height=1024, negative_prompt='', seed=None,
                batch=1, inline_key='sk-inline')
            j = self._wait(jid)
        self.assertEqual(j['status'], 'completed', j.get('error'))
        self.assertEqual(j['engine'], 'cloud')
        self.assertEqual(j['provider'], 'siliconflow')
        self.assertEqual(len(j['result']['paths']), 2)
        self.assertEqual(j['result']['engine'], 'cloud')
        gi.assert_called_once()
        # inline key 必须透传到协议层
        self.assertEqual(gi.call_args.kwargs['inline_key'], 'sk-inline')
        lib = assets.library(self.root)
        self.assertTrue(any(it['path'] == j['result']['paths'][0]
                            for d in lib['dirs'] for it in d['items']))

    def test_image_job_failure_records_error(self):
        with mock.patch.object(cg, 'generate_image',
                               side_effect=cg.CloudError('API Key 无效或无权限（401/403）')):
            jid = self.jm.submit_image(
                self.root, provider='openai', model='gpt-image-1', prompt='p',
                width=1024, height=1024, negative_prompt='', seed=None,
                batch=1, inline_key='bad')
            j = self._wait(jid)
        self.assertEqual(j['status'], 'failed')
        self.assertIn('401/403', j['error'])

    def test_animation_job_end_to_end(self):
        frames = [Image.new('RGB', (32, 32), (i * 5 % 255, 0, 0)) for i in range(12)]
        info = {'src_fps': 24.0, 'out_fps': 12,
                'src_indices': list(range(12)), 'frame_count': 12}
        with mock.patch.object(cg, 'generate_video', return_value=(b'vid', '.mp4')), \
             mock.patch.object(asset_gen, 'extract_frames',
                               return_value=(frames, info)) as ef:
            jid = self.jm.submit_animation(
                self.root, provider='siliconflow', model='Wan-AI/Wan2.2-I2V-A14B',
                prompt='run cycle', first_frame=b'framepng', duration=5.0,
                seed=3, fps=12, max_frames=64, inline_key='k')
            j = self._wait(jid)
        self.assertEqual(j['status'], 'completed', j.get('error'))
        self.assertEqual(j['result']['frame_count'], 12)
        self.assertEqual(j['result']['cols'], 4)
        self.assertEqual(j['result']['fps'], 12)
        ef.assert_called_once()
        lib = assets.library(self.root)
        anims = [it for d in lib['dirs'] for it in d['items']
                 if it['kind'] == 'animation']
        self.assertEqual(len(anims), 1)
        self.assertEqual(anims[0]['source'], 'cloud:siliconflow')

    def test_cancel_queued_job(self):
        jid = self.jm._new_job(self.root, 'image', 'siliconflow', 'm', 'p')
        r = self.jm.cancel(jid, root=self.root)
        self.assertTrue(r['ok'])
        self.assertEqual(self.jm.status(jid)['status'], 'canceling')

    def test_cancel_missing(self):
        r = self.jm.cancel('no-such-job', root=self.root)
        self.assertFalse(r['ok'])

    def test_status_root_isolation(self):
        jid = self.jm._new_job(self.root, 'image', 'siliconflow', 'm', 'p')
        self.assertIsNone(self.jm.status(jid, root=os.path.abspath('.')))
        self.assertIsNotNone(self.jm.status(jid, root=self.root))


# --------------------------------------------------------------- API 路由
class CloudRouteTests(unittest.TestCase):
    def setUp(self):
        import config
        from starlette.testclient import TestClient
        import api as api_mod
        self.api = api_mod
        self.config = config
        self._td = tempfile.TemporaryDirectory()
        self._old_root = config.get_runtime('code_root')
        config.set_runtime('code_root', self._td.name)
        self.client = TestClient(api_mod.app)

    def tearDown(self):
        self.config.set_runtime('code_root', self._old_root or '')
        self._td.cleanup()

    def test_providers_route(self):
        r = self.client.get('/api/assets/cloud/providers')
        self.assertEqual(r.status_code, 200)
        self.assertIn('siliconflow', [p['id'] for p in r.json()['providers']])

    def test_keys_route(self):
        with mock.patch.object(cg, 'key_status',
                               return_value={'siliconflow': {'saved': False, 'mask': ''}}):
            r = self.client.get('/api/assets/cloud/keys')
        self.assertFalse(r.json()['keys']['siliconflow']['saved'])

    def test_save_key_route(self):
        with mock.patch.object(cg, 'save_key', return_value={'ok': True}) as sv:
            r = self.client.post('/api/assets/cloud/key',
                                 json={'provider': 'siliconflow', 'key': 'sk-1'})
        self.assertTrue(r.json()['ok'])
        sv.assert_called_once()
        self.assertEqual(sv.call_args.args[1:], ('siliconflow', 'sk-1'))

    def test_save_key_route_error(self):
        with mock.patch.object(cg, 'save_key',
                               side_effect=cg.CloudError('请粘贴 API Key 后再保存。')):
            r = self.client.post('/api/assets/cloud/key',
                                 json={'provider': 'siliconflow', 'key': ''})
        self.assertFalse(r.json()['ok'])

    def test_image_validation_and_submit(self):
        r = self.client.post('/api/assets/cloud/image',
                             json={'provider': 'x', 'model': 'm', 'prompt': '   '})
        self.assertFalse(r.json()['ok'])
        r = self.client.post('/api/assets/cloud/image', json={
            'provider': 'siliconflow', 'model': 'm', 'prompt': 'p',
            'width': 99, 'height': 1024})
        self.assertFalse(r.json()['ok'])

        fake = mock.Mock()
        fake.submit_image.return_value = 'cid-1'
        with mock.patch.object(self.api.cloud_gen, 'jobs', fake):
            r = self.client.post('/api/assets/cloud/image', json={
                'provider': 'siliconflow',
                'model': 'black-forest-labs/FLUX.1-schnell',
                'prompt': 'a slime', 'width': 864, 'height': 1152,
                'seed': 42, 'batch': 2, 'api_key': 'sk-z'})
        self.assertTrue(r.json()['ok'], r.text)
        self.assertEqual(r.json()['job_id'], 'cid-1')
        kw = fake.submit_image.call_args.kwargs
        self.assertEqual(kw['provider'], 'siliconflow')
        self.assertEqual((kw['width'], kw['height']), (864, 1152))
        self.assertEqual(kw['inline_key'], 'sk-z')

    def test_animation_reads_project_frame(self):
        frame = os.path.join(self._td.name, 'assets', 'hero.png')
        os.makedirs(os.path.dirname(frame), exist_ok=True)
        with open(frame, 'wb') as f:
            f.write(b'\x89PNG-bytes')
        fake = mock.Mock()
        fake.submit_animation.return_value = 'cid-2'
        with mock.patch.object(self.api.cloud_gen, 'jobs', fake):
            r = self.client.post('/api/assets/cloud/animation', json={
                'provider': 'siliconflow', 'model': 'Wan-AI/Wan2.2-I2V-A14B',
                'prompt': 'run', 'duration': 3,
                'first_frame_path': 'assets/hero.png'})
        self.assertTrue(r.json()['ok'], r.text)
        kw = fake.submit_animation.call_args.kwargs
        self.assertEqual(kw['first_frame'], b'\x89PNG-bytes')
        self.assertEqual(kw['duration'], 3)

    def test_animation_bad_fps(self):
        r = self.client.post('/api/assets/cloud/animation', json={
            'provider': 'siliconflow', 'model': 'm', 'prompt': 'p', 'fps': 99})
        self.assertFalse(r.json()['ok'])

    def test_upload_frame_route(self):
        r = self.client.post(
            '/api/assets/cloud/upload-frame',
            files={'file': ('首帧.png', b'\x89PNG-bytes', 'image/png')})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['path'].endswith('.png'))
        self.assertIn('cloud_firstframe_', r.json()['name'])

    def test_upload_frame_rejects_ext(self):
        r = self.client.post(
            '/api/assets/cloud/upload-frame',
            files={'file': ('a.txt', b'x', 'text/plain')})
        self.assertEqual(r.status_code, 400)

    def test_jobs_list_merges_local_and_cloud_sorted(self):
        local = [{'id': 'ab12local', 'created_at': '2026-09-01T10:00:00'}]
        cloud = [{'id': 'cid9cloud', 'created_at': '2026-09-02T10:00:00'}]
        with mock.patch.object(asset_gen.jobs, 'list_jobs', return_value=local), \
             mock.patch.object(cg.jobs, 'list_jobs', return_value=cloud):
            r = self.client.get('/api/assets/generate/jobs')
        ids = [j['id'] for j in r.json()['jobs']]
        self.assertEqual(ids, ['cid9cloud', 'ab12local'])

    def test_job_detail_falls_back_to_cloud(self):
        cloud_job = {'id': 'cid9cloud', 'status': 'running', 'engine': 'cloud'}
        with mock.patch.object(asset_gen.jobs, 'status', return_value=None), \
             mock.patch.object(cg.jobs, 'status', return_value=cloud_job):
            r = self.client.get('/api/assets/generate/jobs/cid9cloud')
        self.assertEqual(r.json()['job']['id'], 'cid9cloud')

    def test_cancel_routes_to_cloud(self):
        with mock.patch.object(cg.jobs, 'status',
                               return_value={'id': 'cid9', 'status': 'running'}), \
             mock.patch.object(cg.jobs, 'cancel',
                               return_value={'ok': True}) as cc:
            r = self.client.post('/api/assets/generate/jobs/cid9/cancel', json={})
        self.assertTrue(r.json()['ok'])
        cc.assert_called_once()


if __name__ == '__main__':
    unittest.main()

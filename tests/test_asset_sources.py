# -*- coding: utf-8 -*-
"""asset_sources（素材中心）离线单测：全部网络出口用 monkeypatch 拦截。"""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from email.message import Message
from unittest import mock

import asset_sources as a


# --------------------------------------------------------------- 测试夹具

class FakeResp:
    def __init__(self, data: bytes, ctype='application/json'):
        self.data = data
        self.pos = 0
        self.headers = Message()
        self.headers['Content-Type'] = ctype

    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self.pos = self.data[self.pos:], len(self.data)
            return chunk
        chunk, self.pos = self.data[self.pos:self.pos + n], min(len(self.data), self.pos + n)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_zip(entries):
    """entries: [(name, bytes, external_attr?)]"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in entries:
            name, data = item[0], item[1]
            attr = item[2] if len(item) > 2 else None
            zi = zipfile.ZipInfo(name)
            if attr is not None:
                zi.external_attr = attr
            z.writestr(zi, data)
    return buf.getvalue()


class TempProject(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='docmind-assets-test-')
        self.cache = tempfile.mkdtemp(prefix='docmind-cache-test-')
        self._patches = [
            mock.patch.object(a, '_cache_root', lambda: self.cache),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.cache, ignore_errors=True)

    def write(self, rel, data=b'x'):
        p = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = 'wb' if isinstance(data, bytes) else 'w'
        with open(p, mode) as f:
            f.write(data)
        return p


# --------------------------------------------------------------- Poly Haven

class PolyHavenTests(unittest.TestCase):
    def test_normalize_type_code_and_authors(self):
        it = a._normalize_poly('chair_x', {
            'name': 'Chair X', 'type': 2, 'description': 'desc',
            'authors': {'Kirill': 'http://x', 'Bob': 'http://y'},
            'tags': ['wood', 'indoor'], 'thumbnail_url': 'http://cdn/t.jpg'}, 'model')
        self.assertEqual(it['kind'], 'model')
        self.assertEqual(it['author'], 'Kirill, Bob')
        self.assertEqual(it['license'], 'CC0')
        self.assertEqual(it['page_url'], 'https://polyhaven.com/a/chair_x')

    def test_search_pagination_and_filter(self):
        data = {f'item_{i:02d}': {'name': f'Wood Chair {i}', 'type': 2,
                                  'authors': {}, 'tags': ['wood'],
                                  'thumbnail_url': ''} for i in range(30)}
        with mock.patch.object(a, 'http_json', lambda url, timeout=15: data):
            r1 = a.poly_search('chair', 'model', page=1)
            r2 = a.poly_search('chair', 'model', page=2)
        self.assertTrue(r1['ok'])
        self.assertEqual(len(r1['items']), 24)
        self.assertTrue(r1['has_more'])
        self.assertEqual(len(r2['items']), 6)
        self.assertFalse(r2['has_more'])

    def test_search_invalid_kind(self):
        with self.assertRaises(a.AssetError):
            a.poly_search('q', 'audio', 1)

    def test_resolve_model_prefers_glb(self):
        files = {
            'glTF': {'1k': {'glb': {'url': 'https://dl.polyhaven.org/m_1k.glb', 'size': 100, 'md5': 'a'}},
                     '2k': {'glb': {'url': 'https://dl.polyhaven.org/m_2k.glb', 'size': 200, 'md5': 'b'}}},
            'FBX': {'1k': {'fbx': {'url': 'https://dl.polyhaven.org/m.fbx', 'size': 150}}},
            'Textures': {'Diffuse': {'2k': {'jpg': {'url': 'https://dl.polyhaven.org/d.jpg', 'size': 9}}}},
        }
        with mock.patch.object(a, 'http_json', lambda url, timeout=15: files):
            r = a.poly_resolve('m', 'model')
        exts = [o['ext'] for o in r['options']]
        self.assertEqual(exts[0], '.glb')
        self.assertIn('.fbx', exts)
        self.assertNotIn('.jpg', exts)  # 模型自带贴图不单独导入

    def test_resolve_texture_diffuse_2k_jpg_first(self):
        files = {'Normal': {'2k': {'jpg': {'url': 'https://cdn/n.jpg', 'size': 5}}},
                 'Diffuse': {'1k': {'jpg': {'url': 'https://cdn/d1.jpg', 'size': 1}},
                             '2k': {'jpg': {'url': 'https://cdn/d2.jpg', 'size': 4}},
                             '4k': {'png': {'url': 'https://cdn/d4.png', 'size': 40}}}}
        with mock.patch.object(a, 'http_json', lambda url, timeout=15: files):
            r = a.poly_resolve('t', 'texture')
        self.assertEqual(r['options'][0]['url'], 'https://cdn/d2.jpg')

    def test_resolve_hdri_2k_hdr_first(self):
        files = {'hdri': {'1k': {'hdr': {'url': 'https://cdn/1.hdr', 'size': 1}},
                          '2k': {'hdr': {'url': 'https://cdn/2.hdr', 'size': 6}},
                          '4k': {'exr': {'url': 'https://cdn/4.exr', 'size': 60}}}}
        with mock.patch.object(a, 'http_json', lambda url, timeout=15: files):
            r = a.poly_resolve('h', 'hdri')
        self.assertEqual(r['options'][0]['url'], 'https://cdn/2.hdr')

    def test_resolve_bad_id(self):
        with self.assertRaises(a.AssetError):
            a.poly_resolve('../evil', 'model')


# --------------------------------------------------------------- Kenney 解析

class KenneyPageTests(unittest.TestCase):
    def test_parse_pack_page(self):
        html = '''<html><meta property="og:image" content="https://kenney.nl/media/cover.png">
        <a href='https://kenney.nl/media/pages/assets/p/abc-123/kenney_p.zip' class="btn">Continue</a></html>'''
        zip_url, img = a._parse_pack_page(html)
        self.assertTrue(zip_url.endswith('kenney_p.zip'))
        self.assertEqual(img, 'https://kenney.nl/media/cover.png')

    def test_parse_pack_page_double_quote_and_amp(self):
        html = '<a href="https://kenney.nl/media/pages/assets/p/a/kenney_p.zip?x=1&amp;y=2">x</a>'
        zip_url, img = a._parse_pack_page(html)
        self.assertIn('&y=2', zip_url)
        self.assertEqual(img, '')

    def test_parse_pack_page_no_zip(self):
        with self.assertRaises(a.AssetError):
            a._parse_pack_page('<html>nothing</html>')

    def test_list_filter(self):
        r = a.kenney_list('原型', '')
        self.assertTrue(any(p['slug'] == 'prototype-kit' for p in r['items']))
        r2 = a.kenney_list('', 'audio')
        self.assertTrue(all('audio' in p['kinds'] for p in r2['items']))
        self.assertTrue(any(p['slug'] == 'interface-sounds' for p in r2['items']))


# --------------------------------------------------------------- ZIP 安全

class ZipExtractTests(TempProject):
    ENTRIES = [
        ('kenney_pack/Models/a.glb', b'glbdata'),
        ('kenney_pack/Models/a.gltf', b'{}'),
        ('kenney_pack/Models/a.bin', b'bin'),
        ('kenney_pack/Models/textures/t.png', b'\x89PNG'),
        ('kenney_pack/Other/b.glb', b'other'),
        ('kenney_pack/License.txt', b'CC0'),
    ]

    def _extract(self, entries):
        zp = os.path.join(self.root, 'p.zip')
        with open(zp, 'wb') as f:
            f.write(make_zip(entries))
        dest = os.path.join(self.cache, 'tok')
        info = a._extract_zip(zp, dest)
        return dest, info

    def test_extract_basic_and_prefix(self):
        dest, info = self._extract(self.ENTRIES)
        self.assertEqual(info['prefix'], 'kenney_pack/')
        self.assertTrue(os.path.isfile(os.path.join(dest, 'kenney_pack/Models/a.glb')))
        files = a._pack_file_list(dest, info['prefix'])
        shown = {f['show_path'] for f in files}
        self.assertIn('Models/a.glb', shown)
        # _pack.json 不在列表中
        with open(os.path.join(dest, '_pack.json'), 'w') as f:
            f.write('{}')
        files2 = a._pack_file_list(dest, info['prefix'])
        self.assertFalse(any(f['show_path'] == '_pack.json' for f in files2))

    def test_zip_slip_rejected(self):
        evil = ('../evil.txt', b'bad')
        dest, info = self._extract(self.ENTRIES + [evil])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(dest), 'evil.txt')))
        self.assertFalse(os.path.exists(os.path.join(dest, '../evil.txt')))

    def test_symlink_entry_skipped(self):
        # 0120777 = 符号链接
        link = ('kenney_pack/link', b'/etc/passwd', 0o120777 << 16)
        dest, _ = self._extract(self.ENTRIES + [link])
        self.assertFalse(os.path.islink(os.path.join(dest, 'kenney_pack/link')))

    def test_extract_total_limit(self):
        with mock.patch.object(a, 'EXTRACT_TOTAL_MAX', 5):
            with self.assertRaises(a.AssetError):
                self._extract(self.ENTRIES)

    def test_extract_files_count_limit(self):
        many = [(f'kenney_pack/f{i}.png', b'x') for i in range(10)]
        with mock.patch.object(a, 'EXTRACT_FILES_MAX', 5):
            with self.assertRaises(a.AssetError):
                self._extract(many)

    def test_bad_zip(self):
        zp = os.path.join(self.root, 'bad.zip')
        with open(zp, 'wb') as f:
            f.write(b'not a zip')
        with self.assertRaises(a.AssetError):
            a._extract_zip(zp, os.path.join(self.cache, 'x'))

    def test_gltf_dependency_expand(self):
        dest, info = self._extract(self.ENTRIES)
        files = a._pack_file_list(dest, info['prefix'])
        # show_path 选择，转成完整 path
        by_show = {f['show_path']: f['path'] for f in files}
        selected = {by_show['Models/a.gltf']}
        expanded = a._expand_gltf_deps(files, set(selected))
        names = {os.path.basename(p) for p in expanded}
        self.assertIn('a.gltf', names)
        self.assertIn('a.bin', names)
        self.assertIn('t.png', names)
        self.assertNotIn('b.glb', names)


# --------------------------------------------------------------- peek / LRU / 导入

PACK_HTML = ('<meta property="og:image" content="https://kenney.nl/media/c.png">'
             '<a href="https://kenney.nl/media/pages/assets/x/h-111/kenney_x.zip">d</a>')
PACK_ZIP_ENTRIES = [
    ('kenney_x/Models/a.gltf', b'{}'),
    ('kenney_x/Models/a.bin', b'bin'),
    ('kenney_x/Models/t.png', b'\x89PNG'),
    ('kenney_x/Other/b.glb', b'glb'),
    ('kenney_x/License.txt', b'CC0 license'),
]


class PeekImportTests(TempProject):
    def _fake_download(self, entries):
        def _dl(url, dest, limit, timeout=600.0):
            with open(dest, 'wb') as f:
                f.write(make_zip(entries))
            return 1234
        return _dl

    def test_peek_download_and_cache(self):
        with mock.patch.object(a, 'http_text', lambda url, timeout=15: PACK_HTML), \
             mock.patch.object(a, 'download_to_file', self._fake_download(PACK_ZIP_ENTRIES)):
            r = a.kenney_peek('prototype-kit')
        self.assertTrue(r['ok'])
        self.assertFalse(r['cached'])
        self.assertEqual(r['prefix'], 'kenney_x/')
        self.assertTrue(any(f['show_path'] == 'Models/a.gltf' for f in r['files']))
        token = r['token']
        # 第二次走缓存
        with mock.patch.object(a, 'http_text', lambda url, timeout=15: PACK_HTML):
            r2 = a.kenney_peek('prototype-kit')
        self.assertTrue(r2['cached'])
        # preview
        p, ctype = a.kenney_preview_file(token, 'kenney_x/Other/b.glb')
        self.assertTrue(os.path.isfile(p))
        self.assertIn('model/gltf-binary', ctype)

    def test_preview_ext_and_traversal(self):
        with mock.patch.object(a, 'http_text', lambda url, timeout=15: PACK_HTML), \
             mock.patch.object(a, 'download_to_file', self._fake_download(PACK_ZIP_ENTRIES)):
            r = a.kenney_peek('prototype-kit')
        with self.assertRaises(a.AssetError):
            a.kenney_preview_file(r['token'], 'kenney_x/License.txt')  # 文本不允许预览
        with self.assertRaises(a.AssetError):
            a.kenney_preview_file(r['token'], '../../Windows/win.ini')
        with self.assertRaises(a.AssetError):
            a.kenney_preview_file('deadbeef', 'x.glb')

    def test_peek_unknown_slug(self):
        with self.assertRaises(a.AssetError):
            a.kenney_peek('nope')

    def test_pack_import_with_gltf_deps(self):
        with mock.patch.object(a, 'http_text', lambda url, timeout=15: PACK_HTML), \
             mock.patch.object(a, 'download_to_file', self._fake_download(PACK_ZIP_ENTRIES)):
            r = a.kenney_peek('prototype-kit')
        gltf_path = next(f['path'] for f in r['files'] if f['show_path'] == 'Models/a.gltf')
        imp = a.kenney_import(self.root, r['token'], [gltf_path], 'assets')
        self.assertTrue(imp['ok'], msg=json.dumps(imp, ensure_ascii=False))
        self.assertEqual(imp['imported_count'], 3)  # gltf + bin + png
        base = os.path.join(self.root, 'assets')
        names = []
        for dp, _, fns in os.walk(base):
            names += fns
        self.assertIn('a.gltf', names)
        self.assertIn('a.bin', names)
        self.assertIn('t.png', names)
        self.assertNotIn('b.glb', names)
        # 台账已写
        with open(os.path.join(self.root, a.LEDGER_REL), encoding='utf-8') as f:
            ledger = json.load(f)
        self.assertEqual(len(ledger['items']), 3)
        self.assertTrue(all(it['source'] == 'kenney' and it['license'] == 'CC0'
                            for it in ledger['items']))

    def test_pack_import_empty_selection(self):
        with mock.patch.object(a, 'http_text', lambda url, timeout=15: PACK_HTML), \
             mock.patch.object(a, 'download_to_file', self._fake_download(PACK_ZIP_ENTRIES)):
            r = a.kenney_peek('prototype-kit')
        with self.assertRaises(a.AssetError):
            a.kenney_import(self.root, r['token'], [], 'assets')

    def test_lru_eviction(self):
        with mock.patch.object(a, 'PACK_CACHE_KEEP', 2):
            for name, ts in (('aaa', 1.0), ('bbb', 2.0), ('ccc', 3.0)):
                d = os.path.join(self.cache, name)
                os.makedirs(d)
                with open(os.path.join(d, '_pack.json'), 'w') as f:
                    json.dump({'ts': ts}, f)
            a._evict_pack_cache()
        survive = set(os.listdir(self.cache))
        self.assertEqual(survive, {'bbb', 'ccc'})


# --------------------------------------------------------------- 入库/台账/库

class LedgerLibraryTests(TempProject):
    def test_import_dedup_and_ledger(self):
        src = self.write('incoming/a.glb', b'model-bytes')
        ledger = a._Ledger.load(self.root)
        r1 = ledger.import_bytes(self.root, src, 'assets/models/a.glb', 'model',
                                 source='polyhaven', source_id='a', source_url='u',
                                 author='X', license_='CC0')
        self.assertTrue(r1['ok'])
        ledger.save(self.root)
        # 同 sha 再导入 → 拒绝并报告已存在路径
        src2 = self.write('incoming/a-copy.glb', b'model-bytes')
        ledger2 = a._Ledger.load(self.root)
        r2 = ledger2.import_bytes(self.root, src2, 'assets/models/a-copy.glb', 'model',
                                  source='polyhaven', source_id='a', source_url='u',
                                  author='X', license_='CC0')
        self.assertFalse(r2['ok'])
        self.assertEqual(r2['path'], 'assets/models/a.glb')

    def test_import_ext_whitelist_and_sanitize(self):
        bad = self.write('incoming/x.exe', b'MZ')
        ledger = a._Ledger.load(self.root)
        r = ledger.import_bytes(self.root, bad, 'assets/x.exe', 'other',
                                source='x', source_id='', source_url='', author='', license_='CC0')
        self.assertFalse(r['ok'])

    def test_import_item_full_flow_and_overlimit_cleanup(self):
        glb = b'\x00glb' * 10
        def fake_dl(url, dest, limit, timeout=600.0):
            with open(dest, 'wb') as f:
                f.write(glb)
            return len(glb)
        opt = {'url': 'https://dl.polyhaven.org/x.glb', 'size': len(glb), 'md5': ''}
        with mock.patch.object(a, 'download_to_file', fake_dl):
            r = a.import_item(self.root, source='polyhaven', item_id='chair_x', option=opt,
                              kind='model', dest_dir='assets/models')
        self.assertTrue(r['ok'], msg=str(r))
        self.assertTrue(os.path.isfile(os.path.join(self.root, r['path'])))
        # 无 .download 残文件
        self.assertFalse(any(n.endswith('.download') for n in os.listdir(
            os.path.join(self.root, 'assets/models'))))

    def test_download_overlimit_removes_partial(self):
        def fake_open(req, timeout=600):
            return FakeResp(b'x' * 100)
        dest = os.path.join(self.root, 'big.bin')
        with mock.patch.object(a.urllib.request, 'urlopen', fake_open):
            with self.assertRaises(a.AssetError):
                a.download_to_file('https://dl.polyhaven.org/big.glb', dest, 10)
        self.assertFalse(os.path.exists(dest))

    def test_host_allowlist(self):
        with self.assertRaises(a.AssetError):
            a.proxy_fetch('https://evil.example.com/x.png')
        with self.assertRaises(a.AssetError):
            a.download_to_file('https://evil.example.com/x.glb',
                               os.path.join(self.root, 'x'), 999)

    def test_proxy_fetch_ok(self):
        def fake_open(req, timeout=30):
            return FakeResp(b'\x89PNG', 'image/png')
        with mock.patch.object(a.urllib.request, 'urlopen', fake_open):
            data, ctype = a.proxy_fetch('https://cdn.polyhaven.com/t.png')
        self.assertEqual(data, b'\x89PNG')
        self.assertEqual(ctype, 'image/png')

    def test_proxy_size_limit(self):
        def fake_open(req, timeout=30):
            return FakeResp(b'x' * (a.PROXY_MAX_BYTES + 10))
        with mock.patch.object(a.urllib.request, 'urlopen', fake_open):
            with self.assertRaises(a.AssetError):
                a.proxy_fetch('https://cdn.polyhaven.com/big.png')

    def test_safe_join(self):
        p = a.safe_join(self.root, 'assets/a.png')
        self.assertTrue(os.path.abspath(p).startswith(os.path.abspath(self.root)))
        for evil in ('../a', '..', 'C:/Windows/x'):
            with self.assertRaises(a.AssetError):
                a.safe_join(self.root, evil)

    def test_safe_name(self):
        self.assertEqual(a.safe_name('weird name:.glb'), 'weird name_.glb')
        self.assertEqual(a.safe_name('path/name:.glb'), 'name_.glb')  # basename 防目录穿越
        self.assertEqual(a.safe_name('../x.png'), 'x.png')
        self.assertEqual(a.safe_name(''), 'asset')
        self.assertEqual(a.safe_name('椅子.glb'), '椅子.glb')

    def test_library_aggregates_and_duplicates(self):
        # 台账两件（其中一件磁盘缺失，应被忽略）
        self.write('assets/models/a.glb', b'same')
        self.write('assets/models/b.glb', b'same')   # 与 a 内容重复
        self.write('assets/textures/t.jpg', b'jpg')
        os.makedirs(os.path.join(self.root, '.docmind'), exist_ok=True)
        with open(os.path.join(self.root, a.LEDGER_REL), 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'items': [
                {'path': 'assets/models/a.glb', 'kind': 'model', 'source': 'polyhaven',
                 'author': 'A', 'license': 'CC0', 'sha256': 'x', 'size': 4},
                {'path': 'assets/models/b.glb', 'kind': 'model', 'source': 'kenney'},
                {'path': 'assets/models/missing.glb', 'kind': 'model', 'source': 'polyhaven'},
                {'path': 'assets/textures/t.jpg', 'kind': 'texture', 'source': 'kenney'},
            ]}, f, ensure_ascii=False)
        # generated 产物 + sidecar
        gen_dir = os.path.join(self.root, *a.GENERATED_DIR.split('/'))
        os.makedirs(gen_dir, exist_ok=True)
        with open(os.path.join(gen_dir, 'g.png'), 'wb') as f:
            f.write(b'genpng')
        with open(os.path.join(gen_dir, 'g.png.json'), 'w', encoding='utf-8') as f:
            json.dump({'source': 'comfyui', 'asset_kind': 'image', 'license': 'CC0'}, f)
        lib = a.library(self.root)
        paths = {it['path'] for d in lib['dirs'] for it in d['items']}
        self.assertIn('assets/models/a.glb', paths)
        self.assertIn(os.path.join('assets', 'generated', 'g.png').replace('\\', '/'), paths)
        self.assertNotIn('assets/models/missing.glb', paths)
        items = {it['path']: it for d in lib['dirs'] for it in d['items']}
        self.assertTrue(items['assets/models/a.glb']['duplicate'])
        self.assertTrue(items['assets/models/b.glb']['duplicate'])
        self.assertFalse(items['assets/textures/t.jpg']['duplicate'])

    def test_raw_file_guards(self):
        self.write('assets/a.png', b'\x89PNG')
        p, _ = a.raw_file(self.root, 'assets/a.png')
        self.assertTrue(os.path.isfile(p))
        with self.assertRaises(a.AssetError):
            a.raw_file(self.root, 'scripts/main.gd')  # 非媒体白名单
        with self.assertRaises(a.AssetError):
            a.raw_file(self.root, '../secret.png')


if __name__ == '__main__':
    unittest.main()

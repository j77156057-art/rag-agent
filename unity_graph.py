"""Unity GUID 引用图（P1-2）。

纯文本静态分析，不依赖 Unity 编辑器：

1. 扫描工程内全部 ``*.meta``，建立 ``guid -> 资产路径/类型`` 索引
   （meta 是 YAML，首段含 ``guid: <32hex>``；目录和孤儿 meta 也收录）；
2. 扫描 Unity 序列化文本资产（.unity/.prefab/.asset/.mat/...），
   按行提取 ``guid: <32hex>`` 引用——Unity 序列化器输出的 flow mapping
   恒为单行，block 形式的 guid 也独立成行，因此单行正则即可覆盖；
3. 产出与关系图（``workbench_fs.build_relation_graph``）同构的
   nodes/edges：资产引用资产为 ``guid-ref`` 边；项目内解析不到的
   guid 聚合为 missing 节点（断裂引用 / 包外部资源，默认在 UI 折叠）。

注意口径：缺失 guid 可能是 Packages 包资源（Library/PackageCache 不在
版本库里），不等于损坏引用；节点用 external 标记，不宣称"错误"。
"""
from __future__ import annotations

import os
import re

# 与 workbench_fs.Symbol 规模上限同量级；超大工程只截前 N 个文件，skipped 计数
UNITY_GRAPH_MAX_FILES = 20000
# 单个序列化文本超过 1MB 不解析（正常 .unity 多为几百 KB）
MAX_TEXT_BYTES = 1_000_000

_SKIP_DIRS = {
    '.git', 'Library', 'Temp', 'Logs', 'obj', 'Build', 'Builds',
    'UserSettings', '.vs', 'node_modules', '.venv', '__pycache__',
}

_GUID_RE = re.compile(r'guid:\s*([0-9a-fA-F]{32})')

# 资产扩展名 -> 图节点类型（前端按此着色）
EXT_KIND = {
    '.cs': 'script', '.js': 'script',
    '.unity': 'scene', '.prefab': 'prefab', '.asset': 'asset',
    '.mat': 'material', '.physicmaterial': 'material',
    '.shader': 'shader', '.shadergraph': 'shader', '.shadersubgraph': 'shader',
    '.png': 'texture', '.jpg': 'texture', '.jpeg': 'texture',
    '.tga': 'texture', '.psd': 'texture', '.svg': 'texture', '.gif': 'texture',
    '.wav': 'audio', '.mp3': 'audio', '.ogg': 'audio', '.aif': 'audio',
    '.anim': 'animation', '.controller': 'animator', '.overridecontroller': 'animator',
    '.fbx': 'model', '.obj': 'model', '.blend': 'model',
    '.ttf': 'font', '.otf': 'font',
    '.asmdef': 'asmdef', '.uss': 'style', '.uxml': 'ui',
    '.preset': 'preset', '.spriteatlas': 'atlas', '.terrainlayer': 'terrain',
}

# 含 Unity YAML 序列化引用、需要做正文扫描的扩展名（二进制资产只作被引用目标）
SERIALIZED_EXTS = {
    '.unity', '.prefab', '.asset', '.mat', '.controller', '.overridecontroller',
    '.anim', '.physicmaterial', '.shadergraph', '.shadersubgraph', '.preset',
    '.playable', '.signal', '.terrainlayer', '.flare', '.rendertexture',
    '.lighting', '.spriteatlas', '.cubemap', '.fontsettings', '.guiskin',
    '.scenetemplate', '.inputactions',
}


def is_unity_project(root: str) -> bool:
    return os.path.isfile(os.path.join(root, 'ProjectSettings', 'ProjectVersion.txt'))


def _assets_root(root: str) -> str:
    """code_root 可能指 Unity 工程根（含 Assets/）也可能直接指 Assets。"""
    p = os.path.join(root, 'Assets')
    return p if os.path.isdir(p) else root


def _asset_kind(rel_no_ext: str, is_dir: bool, exists: bool) -> str:
    if is_dir:
        return 'folder'
    if not exists:
        return 'orphan-meta'
    ext = os.path.splitext(rel_no_ext)[1].lower()
    return EXT_KIND.get(ext, 'asset')


def _read_meta_guid(path: str) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            head = f.read(2048)
    except OSError:
        return ''
    m = _GUID_RE.search(head)
    return m.group(1).lower() if m else ''


def build_unity_graph(root: str) -> dict:
    """构建 Unity GUID 引用图。返回 {ok, nodes, edges, stats}。"""
    base = os.path.abspath(root) if root else ''
    if not base or not os.path.isdir(base):
        return {'ok': False, 'error': '代码库根目录无效。'}

    scan_root = _assets_root(base)
    scanned_rel = os.path.relpath(scan_root, base).replace('\\', '/')

    # ---- 第一遍：meta -> guid 索引 ----
    # guid -> {rel, kind, exists}；rel_by_asset 为资产路径 -> guid 的反查
    guid_index: dict[str, dict] = {}
    rel_to_guid: dict[str, str] = {}
    duplicate_guids: list[str] = []
    metas_seen = 0

    for dp, dns, fns in os.walk(scan_root):
        dns[:] = sorted(d for d in dns if d not in _SKIP_DIRS and not d.startswith('.'))
        for fn in sorted(fns):
            if not fn.endswith('.meta'):
                continue
            if metas_seen >= UNITY_GRAPH_MAX_FILES:
                break
            metas_seen += 1
            meta_abs = os.path.join(dp, fn)
            guid = _read_meta_guid(meta_abs)
            if not guid:
                continue
            asset_abs = meta_abs[:-5]
            rel = os.path.relpath(asset_abs, base).replace('\\', '/')
            is_dir = os.path.isdir(asset_abs)
            exists = is_dir or os.path.isfile(asset_abs)
            kind = _asset_kind(rel, is_dir, exists)
            if guid in guid_index:
                # GUID 冲突在 Unity 里是致命问题，单独报出来
                duplicate_guids.append(guid)
                continue
            guid_index[guid] = {'rel': rel, 'kind': kind, 'exists': exists}
            rel_to_guid[rel] = guid

    # ---- 第二遍：序列化资产正文提取引用 ----
    # (src_guid, dst_guid) -> 引用计数/首行
    raw_edges: dict[tuple[str, str], dict] = {}
    files_scanned = 0
    skipped = 0

    for dp, dns, fns in os.walk(scan_root):
        dns[:] = sorted(d for d in dns if d not in _SKIP_DIRS and not d.startswith('.'))
        for fn in sorted(fns):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SERIALIZED_EXTS:
                continue
            abs_p = os.path.join(dp, fn)
            try:
                if os.path.getsize(abs_p) > MAX_TEXT_BYTES:
                    skipped += 1
                    continue
                with open(abs_p, encoding='utf-8', errors='replace') as f:
                    text = f.read()
            except OSError:
                skipped += 1
                continue
            files_scanned += 1
            if files_scanned > UNITY_GRAPH_MAX_FILES:
                skipped += 1
                continue
            # 资产 guid 绑定在它的 .meta；正文中出现的 guid 一律是引用
            rel = os.path.relpath(abs_p, base).replace('\\', '/')
            src_guid = rel_to_guid.get(rel, '')
            if not src_guid:
                continue  # 资产没有 meta（未导入/孤儿文件），无法作为引用源定位
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in _GUID_RE.finditer(line):
                    dst = m.group(1).lower()
                    if dst == src_guid:
                        continue  # 理论上不会引用自己，防御
                    key = (src_guid, dst)
                    rec = raw_edges.get(key)
                    if rec is None:
                        raw_edges[key] = {'line': lineno, 'count': 1}
                    else:
                        rec['count'] += 1
            if files_scanned >= UNITY_GRAPH_MAX_FILES:
                break

    # ---- 组图：只为"参与引用"的资产建节点 ----
    active_guids = set()
    missing_refs: dict[str, int] = {}
    for src, dst in raw_edges:
        active_guids.add(src)
        if dst in guid_index:
            active_guids.add(dst)
        else:
            missing_refs[dst] = missing_refs.get(dst, 0) + 1

    def node_id(guid: str, missing: bool) -> str:
        return ('m:' if missing else 'u:') + guid

    nodes: list[dict] = []
    for guid in sorted(active_guids):
        info = guid_index[guid]
        rel = info['rel']
        label = os.path.basename(rel) or rel.rstrip('/')
        sub = os.path.dirname(rel).replace('\\', '/')
        nodes.append({
            'id': node_id(guid, False),
            'guid': guid,
            'label': label,
            'sub': sub,
            'kind': info['kind'],
            'rel': rel,
            'line': 0,
            'region': '',
            'region_name': '',
            'external': False,
            'doc': '',
        })
    for guid in sorted(missing_refs):
        nodes.append({
            'id': node_id(guid, True),
            'guid': guid,
            'label': '缺失/外部: ' + guid[:8] + '…',
            'sub': '项目 Assets 内未找到对应 .meta（可能是 Packages 包资源或断裂引用）',
            'kind': 'missing',
            'rel': '',
            'line': 0,
            'region': '',
            'region_name': '',
            'external': True,
            'doc': f'被 {missing_refs[guid]} 个资产引用',
        })

    edges: list[dict] = []
    resolved_edges = 0
    for (src, dst), rec in sorted(raw_edges.items()):
        missing = dst not in guid_index
        if not missing:
            resolved_edges += 1
        edges.append({
            'source': node_id(src, False),
            'target': node_id(dst, missing),
            'kind': 'guid-ref',
            'label': f'引用 ×{rec["count"]}',
            'line': rec['line'],
            'count': rec['count'],
        })

    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n['kind']] = by_kind.get(n['kind'], 0) + 1

    stats = {
        'unity_project': is_unity_project(base),
        'scanned_root': scanned_rel,
        'metas': metas_seen,
        'serialized_files': files_scanned,
        'assets_total': len(guid_index),
        'nodes': len(nodes),
        'asset_nodes': len(nodes) - len(missing_refs),
        'missing_nodes': len(missing_refs),
        'orphan_meta': sum(1 for v in guid_index.values() if v['kind'] == 'orphan-meta'),
        'duplicate_guids': len(duplicate_guids),
        'edges': len(edges),
        'resolved_edges': resolved_edges,
        'missing_edges': len(edges) - resolved_edges,
        'skipped': skipped,
        'by_kind': by_kind,
    }
    return {'ok': True, 'root': base, 'nodes': nodes, 'edges': edges, 'stats': stats}

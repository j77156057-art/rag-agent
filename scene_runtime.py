"""场景（.tscn）解析 / 可视化图模型 / 受控编辑，以及运行时事件的存储与检索。

三条铁律（与本项目既有约定一致）：

1. **永不执行场景表达式**——只做文本解析与结构化「行块」改写，绝不 eval/exec；
2. **双层沙箱**——所有路径先过 ``workbench_fs._resolve``（code_root 包含性 + 分区/契约保护），
   写操作额外走 ``for_write=True``；契约文件、``.git``/``.chroma``/``.docmind_backups`` 一律拒写；
3. **写完即自检，失败即回滚**——改写后重新解析并校验（父节点存在、同级不重名、父块先于子块），
   任一项不通过就把磁盘文件恢复原样并返回错误，绝不留下半个坏场景。

每个写操作都返回一条**可反向执行的 ``undo`` 描述**（``add``↔``delete``、``rename`` 自逆、
``reparent`` 换回原父、``set_props`` 回填旧值、``delete`` 用 ``restore`` 回填原始行），
前端据此实现撤销/重做，不需要在工程里额外落盘副本——撤销栈只存在于浏览器内存。
"""
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

MAX_SCENE = 2 * 1024 * 1024        # .tscn 解析上限
MAX_EVENT = 8192                   # 单条运行时事件上限
MAX_EVENTS = 1000                  # 事件环形上限
MAX_NAME = 96                      # 节点名长度上限
MAX_VALUE = 1000                   # 单条属性值上限
MAX_BATCH = 64                     # 单次批量属性写入上限
MARKER = "DOCMIND_EVENT "          # Godot 侧运行时事件约定前缀
STATE_FILE = ".docmind_runtime.state"
SESSION_GAP = 120                  # 会话切分的时间间隔（秒）

_event_lock = threading.Lock()

# Godot 自身不允许节点名含 . : @ / " %，这里再加方括号与控制字符防注入
_NAME_BAD = set('.:@/"%[]\r\n\t')
_TYPE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_PROP_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_KIND_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("(?:\\.|[^"\\])*"|\[[^\]]*\]|[^\s\]]+)')
_REF_RE = re.compile(r'(Ext|Sub)Resource\(\s*"([^"]+)"\s*\)')
_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _resolve(root, path, **kwargs):
    from workbench_fs import _resolve as resolve
    return resolve(root, path, **kwargs)


def _fail(status, message):
    from workbench_fs import FsError
    raise FsError(status, message)


def _valid_name(name):
    name = str(name or '')
    if not name or len(name) > MAX_NAME or name != name.strip():
        return False
    return not any(ch in _NAME_BAD for ch in name)


def _valid_value(value):
    value = str(value or '')
    return bool(value) and len(value) <= MAX_VALUE and '\n' not in value and '\r' not in value


def _valid_prop(name):
    return bool(_PROP_RE.match(str(name or '')))


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _read_scene(root, rel, for_write=False):
    path, rel = _resolve(root, rel, must_exist=True, for_write=for_write)
    if not rel.lower().endswith('.tscn'):
        _fail(415, '仅支持 Godot 文本场景（.tscn）。')
    if os.path.getsize(path) > MAX_SCENE:
        _fail(413, '场景超过 2MB 解析上限。')
    with open(path, encoding='utf-8-sig', newline='') as f:
        text = f.read()
    return path, rel, text, text.split('\n')


def _write_scene(path, lines):
    text = '\n'.join(lines)
    temporary = path + '.' + uuid.uuid4().hex + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return text


def _invalidate(root):
    try:
        from workbench_fs import invalidate_status
        invalidate_status(root)
    except Exception:  # noqa: BLE001  状态缓存失效失败不影响写入结果
        pass


# ---------------------------------------------------------------------------
# 行块解析：把 .tscn 拆成 [头行 + 属性行 + 尾随空行] 的块序列
# ---------------------------------------------------------------------------
def _split_attrs(body):
    out = {}
    for key, raw in _ATTR_RE.findall(body or ''):
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            out[key] = raw[1:-1].replace('\\"', '"')
        else:
            out[key] = raw
    return out


def _header_match(stripped):
    """识别 ``[kind ...]`` 头行，返回 ``(kind, 属性串)`` 或 None。

    必须做括号配对而不是简单的 ``[^\\]]*`` 正则：``groups=["hero", "actors"]`` 这类
    数组属性本身就含 ``]``，用朴素正则会把整行判成非头行，导致节点被吞进上一个块
    （这个坑真实踩过：带 groups 的节点会整段消失）。同时跳过字符串内的括号与转义。
    """
    if not stripped.startswith('['):
        return None
    depth, in_str, escape = 0, False, False
    for index, char in enumerate(stripped):
        if in_str:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char in '[(':
            depth += 1
        elif char in '])':
            depth -= 1
            if depth != 0:
                continue
            if char != ']' or stripped[index + 1:].strip():
                return None
            body = stripped[1:index].strip()
            kind, _, rest = body.partition(' ')
            if not _KIND_RE.match(kind):
                return None
            return kind, rest
    return None


def _blocks(lines):
    """返回块列表：``{kind, attrs, start, end, props:[{name,value,line}]}``。

    ``end`` 为不含尾结束行号（含块自身的属性行与尾随空行），因此 ``lines[start:end]``
    就是可整体剪切/搬运的块文本——插入、删除、换父都在这个粒度上做，天然保留原文件风格。
    """
    out, current = [], None
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        match = _header_match(stripped)
        if match:
            current = {'kind': match[0], 'attrs': _split_attrs(match[1]),
                       'start': index, 'end': index + 1, 'props': []}
            out.append(current)
            continue
        if current is None:
            continue
        current['end'] = index + 1
        if '=' in stripped and not stripped.startswith(';'):
            key, _, value = stripped.partition('=')
            current['props'].append({'name': key.strip(), 'value': value.strip(), 'line': index})
    return out


def _node_path(attrs):
    """Godot 规则：根节点无 parent 属性（路径 ``.``），其余为 ``父路径/名称``。"""
    name = attrs.get('name', '')
    parent = attrs.get('parent')
    if parent is None or parent == '':
        return '.'
    if parent == '.':
        return name
    return parent + '/' + name


def _parent_of(node_path):
    if node_path == '.':
        return None
    if '/' not in node_path:
        return '.'
    return node_path.rsplit('/', 1)[0]


def _is_inside(path, ancestor):
    """``.`` 视为全场景的祖先（根节点包含一切）。"""
    if ancestor == '.':
        return True
    return path == ancestor or path.startswith(ancestor + '/')


def _node_map(blocks):
    out = {}
    for block in blocks:
        if block['kind'] == 'node':
            out[_node_path(block['attrs'])] = block
    return out


def _prop(block, name):
    for item in block['props']:
        if item['name'] == name:
            return item
    return None


def _insert_point(lines, block):
    """属性插入位置：块内最后一个非空行的下一行（避开尾随空行）。"""
    last = block['start']
    for index in range(block['start'] + 1, min(block['end'], len(lines))):
        if lines[index].strip():
            last = index
    return last + 1


def _set_attr(line, key, value):
    """在 ``[kind ...]`` 头里设置/删除一个属性，其余属性（含 groups 数组）原样保留。"""
    match = _header_match(line.strip())
    if not match:
        return line
    kind, body = match[0], match[1]
    pattern = re.compile(r'(?<![\w])%s\s*=\s*(?:"(?:\\.|[^"\\])*"|\[[^\]]*\]|[^\s\]]+)'
                         % re.escape(key))
    if value is None:
        if pattern.search(body):
            body = re.sub(r'[ \t]{2,}', ' ', pattern.sub('', body, count=1))
    elif pattern.search(body):
        body = pattern.sub('%s="%s"' % (key, value), body, count=1)
    else:
        body = body.rstrip() + ' %s="%s"' % (key, value)
    return '[%s %s]' % (kind, body) if body.strip() else '[%s]' % kind


def _resources(blocks):
    external, sub = {}, {}
    for block in blocks:
        if block['kind'] == 'ext_resource':
            external[block['attrs'].get('id', '')] = block['attrs']
        elif block['kind'] == 'sub_resource':
            sub[block['attrs'].get('id', '')] = block['attrs']
    return external, sub


def _refs(value):
    return _REF_RE.findall(value or '')


def _numbers(value):
    """只取括号内的数字。

    必须先剥掉类型前缀：``_NUM_RE.findall('Vector2(100, 200)')`` 会把类型名里的 ``2``
    也算进来，得到 ``[2, 100, 200]``——坐标就整体错位了。
    """
    text = str(value or '').strip()
    start, end = text.find('('), text.rfind(')')
    if 0 <= start < end:
        text = text[start + 1:end]
    return [float(x) for x in _NUM_RE.findall(text)]


def _origin(value):
    """从 position / transform / offset 的一行文本里取原点坐标。"""
    value = (value or '').strip()
    if value.startswith('Transform2D'):
        nums = _numbers(value)
        return nums[4:6] if len(nums) >= 6 else None
    if value.startswith('Transform3D'):
        nums = _numbers(value)
        return nums[9:12] if len(nums) >= 12 else None
    if value.startswith('Vector2'):
        nums = _numbers(value)
        return nums[:2] if len(nums) >= 2 else None
    if value.startswith('Vector3'):
        nums = _numbers(value)
        return nums[:3] if len(nums) >= 3 else None
    return None


def _prop_kind(value):
    value = (value or '').strip()
    if _REF_RE.match(value):
        return 'ref'
    if value.startswith('Transform'):
        return 'transform'
    if value.startswith('Vector'):
        return 'vector'
    if value.startswith(('Packed', 'Array', '[')):
        return 'array'
    if value.startswith('"'):
        return 'string'
    if value in ('true', 'false'):
        return 'bool'
    if value and _NUM_RE.fullmatch(value):
        return 'number'
    return 'other'


# ---------------------------------------------------------------------------
# 结构自检：改完先验，坏了就回滚
# ---------------------------------------------------------------------------
def _validate(lines):
    errors = []
    blocks = _blocks(lines)
    node_map = _node_map(blocks)
    order = {path: block['start'] for path, block in node_map.items()}
    if '.' not in node_map:
        errors.append('缺少根节点（没有任何不带 parent 属性的 [node]）。')
    siblings = {}
    for path, block in node_map.items():
        parent = _parent_of(path)
        if parent is not None and parent not in node_map:
            errors.append('节点 %s 的父节点 %s 不存在。' % (path, parent))
        elif parent is not None and parent in order and order[parent] > block['start']:
            errors.append('节点 %s 出现在其父节点 %s 之前，Godot 无法解析。' % (path, parent))
        siblings.setdefault(parent, []).append(path)
    for parent, children in siblings.items():
        seen = set()
        for path in children:
            name = path.rsplit('/', 1)[-1]
            if name in seen:
                errors.append('同级节点重名：%s 下有两个 %s。' % (parent or '根', name))
            seen.add(name)
    return errors, blocks, node_map


# ---------------------------------------------------------------------------
# 图模型：画布直接消费的结构
# ---------------------------------------------------------------------------
def _externals_to_files(external, root, instance_use, script_use, ref_use):
    from workbench_fs import FsError
    files = {}
    for rid, attrs in external.items():
        raw = attrs.get('path', '')
        if not raw.startswith('res://'):
            files[rid] = {'id': 'ext:' + rid, 'rel': '', 'raw': raw, 'kind': 'resource',
                          'chip': (attrs.get('type') or 'RES').upper()[:6], 'resolved': False,
                          'type': attrs.get('type', ''), 'uid': attrs.get('uid', ''),
                          'nodes': [], 'used': 0, 'orphan': True}
            continue
        rel, lower = raw[6:], raw[6:].lower()
        kind = 'scene' if lower.endswith('.tscn') else ('script' if lower.endswith(('.gd', '.cs')) else 'resource')
        try:
            _, rel = _resolve(root, raw[6:], must_exist=True)
            resolved = True
        except FsError:
            resolved = False
        chip = {'scene': 'TSCN', 'script': 'GD' if rel.lower().endswith('.gd') else 'CS'}.get(kind, 'RES')
        files[rid] = {'id': 'ext:' + rid, 'rel': rel, 'raw': raw, 'kind': kind, 'chip': chip,
                      'resolved': resolved, 'type': attrs.get('type', ''), 'uid': attrs.get('uid', ''),
                      'nodes': [], 'used': 0, 'orphan': True}
    # 引用计数覆盖**所有** ExtResource 用法（脚本 / 实例化 / 贴图材质等参考资源）：
    # 贴图这类也常有"这张图还被谁用着"的排查需求，而真正零引用的才叫孤儿资源。
    merged = {}
    for source in (ref_use, script_use, instance_use):
        for rid, owners in source.items():
            merged.setdefault(rid, []).extend(owners)
    for rid, owners in merged.items():
        if rid in files:
            files[rid]['nodes'] = sorted(set(owners))
            files[rid]['used'] = len(owners)
            files[rid]['orphan'] = False
    return files


def _header_of(lines):
    for line in lines[:4]:
        stripped = line.strip()
        if stripped.startswith('[gd_scene'):
            match = _header_match(stripped)
            return _split_attrs(match[1]) if match else {}
    return {}


def _guard(root, path, rel):
    """写护栏汇总：可写性 + 所属分区 + git 跟踪状态，供前端在写入前展示。"""
    from workbench_fs import PROTECTED_PARTS, PROTECTED_ROOT_FILES
    writable, reason = True, ''
    parts = rel.split('/')
    if len(parts) == 1 and parts[0] in PROTECTED_ROOT_FILES:
        writable, reason = False, '契约文件只读'
    elif any(part in PROTECTED_PARTS for part in parts):
        writable, reason = False, '受保护目录'
    elif not os.access(path, os.W_OK):
        writable, reason = False, '文件系统只读'
    region = None
    try:
        from workbench_fs import _region_dirs, _region_of
        region = _region_of(rel, _region_dirs(root))
    except Exception:  # noqa: BLE001
        region = None
    tracked = dirty = None
    try:
        from workbench_fs import _git_fields, _git_snapshot
        tracked, dirty = _git_fields(path, rel, _git_snapshot(root))
    except Exception:  # noqa: BLE001
        tracked = dirty = None
    return {'writable': writable, 'reason': reason,
            'region': (region or {}).get('key'), 'region_name': (region or {}).get('name'),
            'tracked': tracked, 'dirty': dirty}


def scene_graph(root, rel):
    """把 .tscn 解析成「场景节点 + 外部引用 + 三类边」的画布图模型。

    与 ``scene_tree`` 的分工：scene_tree 面向行号跳转且字段保持不变；scene_graph 面向可视化，
    额外给出深度、几何量、实例/脚本引用与边表（层级边 / 脚本边 / 实例化边）。
    """
    path, rel, text, lines = _read_scene(root, rel)
    errors, blocks, node_map = _validate(lines)
    external, sub = _resources(blocks)

    instance_use, script_use, ref_use, nodes = {}, {}, {}, []
    for block in blocks:
        if block['kind'] != 'node':
            continue
        attrs, props = block['attrs'], block['props']
        node_path = _node_path(attrs)
        prop_map = {p['name']: p['value'] for p in props}
        node_type = attrs.get('type', '')
        dims = 3 if node_type.endswith('3D') else 2

        position, geo_from = None, ''
        for key in ('transform', 'position', 'offset'):
            if key in prop_map:
                origin = _origin(prop_map[key])
                if origin:
                    position, geo_from = origin[:dims], key
                    break
        rotation = None
        for key in ('rotation', 'rotation_degrees'):
            if key in prop_map:
                nums = _numbers(prop_map[key])
                rotation = nums[0] if nums else None
                break
        scale = None
        if 'scale' in prop_map:
            nums = _numbers(prop_map['scale'])
            scale = nums[:dims] if len(nums) >= dims else None

        instance_id = next((rid for _, rid in _refs(attrs.get('instance', '')) if rid in external), '')
        script_id = next((rid for _, rid in _refs(prop_map.get('script', '')) if rid in external), '')
        if instance_id:
            instance_use.setdefault(instance_id, []).append(node_path)
        if script_id:
            script_use.setdefault(script_id, []).append(node_path)

        # 参考资源：除 script / instance 之外的 ExtResource 用法（贴图、材质、字体、音频…）
        resources, seen = [], set()
        for prop in props:
            if prop['name'] == 'script':
                continue
            for _, rid in _refs(prop['value']):
                ref_use.setdefault(rid, []).append(node_path)
                if rid in external and rid != instance_id and rid not in seen:
                    seen.add(rid)
                    resources.append(rid)
        if instance_id:
            for _, rid in _refs(attrs.get('instance', '')):
                ref_use.setdefault(rid, []).append(node_path)

        groups = []
        raw_groups = attrs.get('groups', '')
        if raw_groups.startswith('[') and raw_groups.endswith(']'):
            groups = [x.strip().strip('"') for x in raw_groups[1:-1].split(',') if x.strip()]

        nodes.append({
            'id': node_path, 'name': attrs.get('name', ''), 'type': node_type or '实例',
            'parent': _parent_of(node_path), 'parent_attr': attrs.get('parent', ''),
            'line': block['start'] + 1, 'space': '3d' if dims == 3 else '2d',
            'instance_id': instance_id, 'script_id': script_id, 'instance': '', 'script': '',
            # 用与 files[].id 同形的 'ext:<rid>'，前端可以直接拿着去匹配文件卡
            'resource_ids': ['ext:' + r for r in resources],
            'groups': groups, 'index': attrs.get('index', ''),
            'position': position, 'position_from': geo_from,
            'rotation': rotation, 'scale': scale, 'children': [], 'overridden': False,
            'properties': [{'name': p['name'], 'value': p['value'], 'line': p['line'] + 1,
                            'kind': _prop_kind(p['value'])} for p in props],
        })

    by_path = {n['id']: n for n in nodes}
    for node in nodes:
        node['depth'] = node['id'].count('/') if node['id'] != '.' else 0
        parent = node['parent']
        if parent in by_path:
            by_path[parent]['children'].append(node['id'])
        # 实例子树内的节点（祖先带 instance）：可编辑，但语义上是引擎侧覆写
        cursor = parent
        while cursor and cursor != '.':
            owner = by_path.get(cursor)
            if owner is None:
                break
            if owner['instance_id']:
                node['overridden'] = True
                break
            cursor = owner['parent']

    files = _externals_to_files(external, root, instance_use, script_use, ref_use)
    for node in nodes:
        if node['instance_id'] in files:
            node['instance'] = files[node['instance_id']]['rel'] or files[node['instance_id']]['raw']
        if node['script_id'] in files:
            node['script'] = files[node['script_id']]['rel'] or files[node['script_id']]['raw']

    edges = []
    for node in nodes:
        if node['parent'] is not None:
            edges.append({'id': 'hier:' + node['id'], 'source': node['parent'],
                          'target': node['id'], 'kind': 'hierarchy'})
        if node['script_id'] in files:
            edges.append({'id': 'script:' + node['id'], 'source': node['id'],
                          'target': files[node['script_id']]['id'], 'kind': 'script'})
        if node['instance_id'] in files:
            edges.append({'id': 'inst:' + node['id'], 'source': node['id'],
                          'target': files[node['instance_id']]['id'], 'kind': 'instance'})
        for fid in node.get('resource_ids', []):
            rid = fid[4:]
            if rid in files:
                edges.append({'id': 'ref:%s:%s' % (node['id'], rid), 'source': node['id'],
                              'target': fid, 'kind': 'reference'})

    guard = _guard(root, path, rel)
    sub_resources = [{'id': rid, 'type': attrs.get('type', ''), 'line': 0}
                     for rid, attrs in sub.items()]
    return {
        'ok': True, 'path': rel, 'root_id': '.', 'header': _header_of(lines),
        'space': '3d' if any(n['space'] == '3d' for n in nodes) else '2d',
        'nodes': nodes, 'files': list(files.values()), 'edges': edges,
        'sub_resources': sub_resources, 'structure_errors': errors,
        'warnings': list(errors), 'guard': guard,
        'stats': {'nodes': len(nodes), 'edges': len(edges), 'files': len(files),
                  'max_depth': max([n['depth'] for n in nodes] or [0]),
                  'instances': len(instance_use), 'scripts': len(script_use),
                  'orphans': sum(1 for f in files.values() if f.get('orphan')),
                  'lines': len(lines), 'size': len(text.encode('utf-8')),
                  'revision': _sha(text), 'mtime': os.path.getmtime(path),
                  'editable': guard['writable']},
        'read_only': not guard['writable'],
    }


# ---------------------------------------------------------------------------
# 受控编辑
# ---------------------------------------------------------------------------
def _find_node(node_map, spec):
    """节点定位：优先精确节点路径，其次唯一节点名（含根节点名）。"""
    spec = str(spec or '').strip()
    if spec in node_map:
        return spec
    if spec in ('.', ''):
        if '.' in node_map:
            return '.'
        _fail(404, '场景缺少根节点。')
    hits = [path for path, block in node_map.items() if block['attrs'].get('name', '') == spec]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        hits = [path for path in node_map if path.rsplit('/', 1)[-1] == spec]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        _fail(404, '节点不存在：%s' % spec)
    _fail(409, '节点名 %s 不唯一，请改用节点路径（%s）。' % (spec, '、'.join(sorted(hits)[:4])))


def _subtree(node_map, node_path):
    """子树（含自身）在行号上的区间集合，按 start 升序。"""
    return sorted((b['start'], b['end']) for path, b in node_map.items() if _is_inside(path, node_path))


def _subtree_end(node_map, node_path):
    return max(end for _, end in _subtree(node_map, node_path))


def _siblings(node_map, parent_path):
    return {path.rsplit('/', 1)[-1] for path in node_map
            if path != '.' and _parent_of(path) == parent_path}


def _unique_name(node_map, parent_path, base):
    if base not in _siblings(node_map, parent_path):
        return base
    for index in range(2, 1000):
        candidate = '%s%d' % (base, index)
        if candidate not in _siblings(node_map, parent_path):
            return candidate
    _fail(409, '无法为新节点找到唯一名称。')


def _insert_block(lines, at, block):
    """把 ``block`` 当成一个独立段落插到 ``at``：必要时补一条前导空行，并保证后面有分隔空行。

    两条必须记住的规则：

    1. **只动接缝，绝不重排文件里已有的空行**。.tscn 里的空行归属于"上一个块"（解析时
       算进了上一个块的 span），所以任何"顺手把连续空行收敛一下"的清理都会破坏
       「删掉再插回来 = 逐字节还原」这条撤销不变量——这个坑踩过，别再加回来。
    2. **尾随空行只在块自己没有时才补**。从文件里搬过来的块（reparent / duplicate）
       本来就带走了自己的尾随空行，无条件再补一条就会多出一个空行；而新建的节点块
       （只有头行 + 属性）必须补——不补就和后面的兄弟黏在一起。
    """
    at = max(0, min(at, len(lines)))
    before = [''] if at > 0 and lines[at - 1].strip() else []
    tail = [] if (block and not block[-1].strip()) else ['']
    lines[at:at] = before + list(block) + tail
    return at + len(before)


def _rewrite_subtree_headers(moved, node_path, new_path, first_parent=None):
    """把搬运/复制出来的行块里所有 ``parent`` 前缀从 node_path 换成 new_path。

    ``first_parent`` 不为 None 时，同时把首块（子树根）的 parent 改成它。
    """
    out = []
    for index, line in enumerate(moved):
        stripped = line.strip()
        match = _header_match(stripped)
        if not match or match[0] != 'node':
            out.append(line)
            continue
        attrs = _split_attrs(match[1])
        if index == 0 and first_parent is not None:
            out.append(_set_attr(_set_attr(line, 'parent', first_parent), 'index', None))
            continue
        parent_value = attrs.get('parent', '')
        if parent_value and _is_inside(parent_value, node_path):
            out.append(_set_attr(line, 'parent', new_path + parent_value[len(node_path):]))
        else:
            out.append(line)
    return out


def _op_add(lines, node_map, parent_path, type_, name, props, groups):
    if parent_path not in node_map:
        _fail(404, '父节点不存在：%s' % parent_path)
    name = _unique_name(node_map, parent_path, name)
    header = '[node name="%s"' % name
    if type_:
        header += ' type="%s"' % type_
    if parent_path != '.':
        header += ' parent="%s"' % parent_path
    if groups:
        header += ' groups=[%s]' % ', '.join('"%s"' % g for g in groups)
    header += ']'
    body = [header] + ['%s = %s' % (k, v) for k, v in (props or {}).items()]
    at = _subtree_end(node_map, parent_path)
    _insert_block(lines, at, body)
    new_path = name if parent_path == '.' else parent_path + '/' + name
    return lines, {'op': 'delete', 'node': new_path}, new_path


def _op_delete(lines, node_map, node_path):
    if node_path == '.':
        _fail(403, '根节点不能被删除。')
    spans = _subtree(node_map, node_path)
    at = min(start for start, _ in spans)
    removed = []
    for start, end in spans:
        removed.extend(lines[start:end])
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    return lines, {'op': 'restore', 'at': at, 'lines': removed}, node_path


def _op_restore(lines, payload, at):
    if not isinstance(payload, list) or not payload or any(not isinstance(x, str) for x in payload):
        _fail(422, 'restore 需要 lines 字符串数组。')
    if sum(len(x) for x in payload) > MAX_SCENE:
        _fail(413, 'restore 内容超过 2MB 上限。')
    at = max(0, min(int(at), len(lines)))
    # 逐字回填：payload 是被删掉的行块原文（已含自身尾随空行），不能再补空行
    lines[at:at] = list(payload)
    match = _header_match(payload[0].strip())
    node_path = _node_path(_split_attrs(match[1])) if match and match[0] == 'node' else '.'
    return lines, {'op': 'delete', 'node': node_path}, node_path


def _op_set_props(lines, node_map, node_path, props, remove, order=None):
    """改写/新增/删除节点属性。

    实现要点：**先按原始行号算清所有改动，再一次性重建行列表**——避免"边插边删"导致
    后续行号漂移（这是行块模型最容易踩的坑）。

    ``order`` 是「属性名 → 相对块头的行偏移」，撤销回填被删掉的属性时用它放回原位；
    没有 order 信息的新属性统一追加到块尾。
    """
    block = node_map[node_path]
    base = _insert_point(lines, block)
    order = order or {}
    drop, updates, plan, undo, added = set(), {}, {}, {}, 0

    for key in list(remove or []):
        key = str(key)
        if not _valid_prop(key):
            _fail(422, '属性名非法：%s' % key)
        existing = _prop(block, key)
        if existing is None:
            continue
        drop.add(existing['line'])
        # 撤销一次"删除属性"必须把旧值写回去——只记 remove 键名等于什么都没撤销。
        undo.setdefault('properties', {})[key] = existing['value']
        undo.setdefault('order', {})[key] = existing['line'] - block['start']

    for key, value in (props or {}).items():
        key = str(key)
        if not _valid_prop(key):
            _fail(422, '属性名非法：%s' % key)
        value = str(value)
        if not _valid_value(value):
            _fail(422, '属性 %s 的值非法（单行、非空、≤%d 字符）。' % (key, MAX_VALUE))
        existing = _prop(block, key)
        if existing is None:
            added += 1
            if added > MAX_BATCH:
                _fail(413, '单次最多新增 %d 条属性。' % MAX_BATCH)
            offset = order.get(key)
            at = block['start'] + int(offset) if offset is not None else base
            at = max(block['start'] + 1, min(at, block['end']))
            plan.setdefault(at, []).append('%s = %s' % (key, value))
            undo.setdefault('remove', []).append(key)
        else:
            updates[existing['line']] = '%s = %s' % (key, value)
            undo.setdefault('properties', {})[key] = existing['value']

    rebuilt = []
    for index, line in enumerate(lines):
        if index in plan:
            rebuilt.extend(plan[index])
        if index in drop:
            continue
        rebuilt.append(updates.get(index, line))
    for index in sorted(k for k in plan if k >= len(lines)):
        rebuilt.extend(plan[index])
    lines[:] = rebuilt
    return lines, {'op': 'set_props', 'node': node_path, **undo}, node_path


def _op_rename(lines, node_map, node_path, new_name):
    if not _valid_name(new_name):
        _fail(422, '节点名非法（禁用 . : @ / " %% 与方括号，且不超过 %d 字符）。' % MAX_NAME)
    if node_path == '.':
        _fail(403, '根节点改名会破坏外部引用，已拒绝。')
    block = node_map[node_path]
    old_name = block['attrs'].get('name', '')
    parent_path = _parent_of(node_path)
    if new_name != old_name and new_name in _siblings(node_map, parent_path):
        _fail(409, '同级已有名为 %s 的节点。' % new_name)
    new_path = new_name if parent_path == '.' else parent_path + '/' + new_name
    lines[block['start']] = _set_attr(lines[block['start']], 'name', new_name)
    for path, other in node_map.items():
        if path != node_path and _is_inside(path, node_path):
            parent_value = other['attrs'].get('parent', '')
            if parent_value and _is_inside(parent_value, node_path):
                lines[other['start']] = _set_attr(lines[other['start']], 'parent',
                                                 new_path + parent_value[len(node_path):])
    return lines, {'op': 'rename', 'node': new_path, 'name': old_name}, new_path


def _op_reparent(lines, node_map, node_path, new_parent_path):
    if node_path == '.':
        _fail(403, '根节点不能改变父节点。')
    if new_parent_path not in node_map:
        _fail(404, '目标父节点不存在：%s' % new_parent_path)
    if _is_inside(new_parent_path, node_path):
        _fail(409, '不能把节点移动到它自己或它的子孙之下。')
    name = node_map[node_path]['attrs'].get('name', '')
    old_parent = _parent_of(node_path)
    new_path = name if new_parent_path == '.' else new_parent_path + '/' + name
    if new_path in node_map and new_path != node_path:
        _fail(409, '目标位置已有同名节点 %s。' % new_path)
    spans = _subtree(node_map, node_path)
    moved = []
    for start, end in spans:
        moved.extend(lines[start:end])
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    moved = _rewrite_subtree_headers(moved, node_path, new_path,
                                     first_parent='.' if new_parent_path == '.' else new_parent_path)
    fresh = _node_map(_blocks(lines))
    at = _subtree_end(fresh, new_parent_path)
    _insert_block(lines, at, moved)
    return lines, {'op': 'reparent', 'node': new_path, 'parent': old_parent}, new_path


def _op_duplicate(lines, node_map, node_path):
    if node_path == '.':
        _fail(403, '根节点不能整体复制，请复制它的子节点。')
    name = node_map[node_path]['attrs'].get('name', '')
    parent_path = _parent_of(node_path)
    copy_name = _unique_name(node_map, parent_path, name)
    copy_path = copy_name if parent_path == '.' else parent_path + '/' + copy_name
    spans = _subtree(node_map, node_path)
    copied, at = [], max(end for _, end in spans)
    for start, end in spans:
        copied.extend(lines[start:end])
    copied = _rewrite_subtree_headers(copied, node_path, copy_path, first_parent=None)
    copied[0] = _set_attr(copied[0], 'name', copy_name)
    _insert_block(lines, at, copied)
    return lines, {'op': 'delete', 'node': copy_path}, copy_path


def _op_move(lines, node_map, node_path, position):
    block = node_map[node_path]
    if not isinstance(position, (list, tuple)) or not (2 <= len(position) <= 3):
        _fail(422, 'position 必须是长度 2 或 3 的数组。')
    coords = []
    for value in position:
        try:
            coords.append(round(float(value), 4))
        except (TypeError, ValueError):
            _fail(422, 'position 含非数值。')
    is_3d = block['attrs'].get('type', '').endswith('3D')
    if is_3d and len(coords) == 2:
        coords.append(0.0)
    names = {p['name'] for p in block['props']}
    if 'transform' in names:
        _fail(409, '该节点用 transform 定位，请在检查器里直接编辑 transform。')
    key = 'offset' if 'offset' in names and 'position' not in names else 'position'
    value = 'Vector%d(%s)' % (3 if is_3d else 2, ', '.join('%g' % c for c in coords))
    lines, undo, focused = _op_set_props(lines, node_map, node_path, {key: value}, [])
    return lines, undo, focused, '已写入 %s = %s' % (key, value)


def scene_op(root, rel, op, **kwargs):
    """统一的场景编辑入口（写路径永不抛异常，失败一律返回 ``{ok: false, error, status}``）。

    支持的 op：``add`` / ``delete`` / ``rename`` / ``reparent`` / ``duplicate`` /
    ``set_props`` / ``move`` / ``restore``。成功时返回 ``{ok, node, undo, warnings, revision}``，
    其中 ``undo`` 是一条可直接回传本接口的反向操作描述。
    """
    from workbench_fs import FsError
    try:
        return _scene_op(root, rel, op, **kwargs)
    except FsError as exc:
        return {'ok': False, 'error': exc.message, 'status': exc.status}


def _scene_op(root, rel, op, **kwargs):
    path, rel, text, lines = _read_scene(root, rel, for_write=True)
    guard = _guard(root, path, rel)
    if not guard['writable']:
        _fail(403, '该场景不可写入：%s。' % (guard['reason'] or '只读'))
    mtime = os.path.getmtime(path)
    if_mtime = kwargs.get('if_mtime')
    if if_mtime is not None and abs(float(if_mtime) - mtime) > 1e-6:
        return {'ok': False, 'stale': True, 'path': rel, 'mtime': mtime,
                'error': '场景已被外部修改，请重新加载后再操作。'}

    errors, blocks, node_map = _validate(lines)
    if errors:
        return {'ok': False, 'path': rel, 'errors': errors,
                'error': '场景本身结构异常，已拒绝编辑：' + errors[0]}

    op = str(op or '').strip()
    warnings = []
    if op == 'add':
        type_ = str(kwargs.get('type') or '').strip()
        if type_ and not _TYPE_RE.match(type_):
            _fail(422, '节点类型非法：%s' % type_)
        name = str(kwargs.get('name') or type_ or 'Node').strip()
        if not _valid_name(name):
            _fail(422, '节点名非法：%s' % name)
        parent = _find_node(node_map, kwargs.get('parent', '.'))
        lines, undo, focused = _op_add(lines, node_map, parent, type_, name,
                                       kwargs.get('properties') or {}, kwargs.get('groups') or [])
    elif op == 'delete':
        lines, undo, focused = _op_delete(lines, node_map, _find_node(node_map, kwargs.get('node')))
    elif op == 'restore':
        lines, undo, focused = _op_restore(lines, kwargs.get('lines'), kwargs.get('at') or 0)
    elif op == 'rename':
        lines, undo, focused = _op_rename(lines, node_map, _find_node(node_map, kwargs.get('node')),
                                          str(kwargs.get('name') or ''))
    elif op == 'reparent':
        lines, undo, focused = _op_reparent(lines, node_map, _find_node(node_map, kwargs.get('node')),
                                            _find_node(node_map, kwargs.get('parent', '.')))
    elif op == 'duplicate':
        lines, undo, focused = _op_duplicate(lines, node_map, _find_node(node_map, kwargs.get('node')))
    elif op == 'set_props':
        props = kwargs.get('properties') or {}
        remove = kwargs.get('remove') or []
        if not props and not remove:
            _fail(422, 'properties / remove 至少提供一个。')
        if not isinstance(props, dict) or len(props) > MAX_BATCH:
            _fail(413, 'properties 必须是不超过 %d 项的键值对象。' % MAX_BATCH)
        lines, undo, focused = _op_set_props(lines, node_map, _find_node(node_map, kwargs.get('node')),
                                             props, remove, kwargs.get('order') or {})
    elif op == 'move':
        lines, undo, focused, note = _op_move(lines, node_map, _find_node(node_map, kwargs.get('node')),
                                              kwargs.get('position'))
        warnings.append(note)
    else:
        _fail(400, '未知操作：%s' % op)

    # ---- 写完即自检：结构坏了就原样回滚，绝不落半个坏场景 ----
    new_errors, _, _ = _validate(lines)
    if new_errors:
        _write_scene(path, text.split('\n'))
        _invalidate(root)
        return {'ok': False, 'path': rel, 'rolled_back': True, 'errors': new_errors,
                'error': '编辑会导致场景结构非法，已自动回滚：' + new_errors[0]}
    new_text = _write_scene(path, lines)
    _invalidate(root)
    return {'ok': True, 'path': rel, 'op': op, 'node': focused, 'undo': undo,
            'warnings': warnings, 'revision': _sha(new_text), 'lines': len(lines),
            'mtime': os.path.getmtime(path)}


# ---------------------------------------------------------------------------
# 兼容层：原有 scene_tree / set_scene_property 行为不变
# ---------------------------------------------------------------------------
def scene_tree(root, rel):
    """保留给工作台的场景树面板（带行号，可直接跳转脚本）。字段与 1.x 完全一致。"""
    from workbench_fs import FsError
    path, rel, _, lines = _read_scene(root, rel)
    blocks = _blocks(lines)
    external, _ = _resources(blocks)
    nodes, warnings = [], []
    for block in blocks:
        if block['kind'] != 'node':
            continue
        attrs, props = block['attrs'], block['props']
        node_path = _node_path(attrs)
        instance = ''
        for _, rid in _refs(attrs.get('instance', '')):
            instance = external.get(rid, {}).get('path', '')
        current = {'name': attrs.get('name', ''), 'type': attrs.get('type', '') or '实例',
                   'parent': attrs.get('parent', '') or '.', 'node_path': node_path,
                   'parent_path': _parent_of(node_path), 'line': block['start'] + 1,
                   'properties': [{'name': p['name'], 'value': p['value'], 'line': p['line'] + 1}
                                  for p in props],
                   'script': '', 'instance': instance}
        script = _prop(block, 'script')
        if script:
            for _, rid in _refs(script['value']):
                raw = external.get(rid, {}).get('path', '')
                if raw.startswith('res://'):
                    try:
                        _, rel_n = _resolve(root, raw[6:], must_exist=True)
                        current['script'] = rel_n
                    except FsError:
                        warnings.append('脚本无法定位：' + raw)
        nodes.append(current)
    paths = {n['node_path'] for n in nodes}
    for node in nodes:
        if node['parent_path'] is not None and node['parent_path'] not in paths:
            warnings.append('未展开实例内父节点：' + node['parent_path'])
    return {'ok': True, 'path': rel, 'nodes': nodes, 'warnings': warnings, 'read_only': True}


def set_scene_property(root, rel, node_name, prop, value):
    """保留旧签名（前端 fsApi.setSceneProperty 在用），内部走 set_props op。"""
    result = scene_op(root, rel, 'set_props', node=node_name, properties={str(prop): value})
    if not result.get('ok'):
        return result
    return {'ok': True, 'path': result['path'], 'node': result['node'],
            'property': prop, 'value': value}


# ---------------------------------------------------------------------------
# 运行时事件：读取（筛选/统计/会话）与写入
# ---------------------------------------------------------------------------
def _state_path(root):
    return _resolve(root, STATE_FILE)[0]


def _read_state(root):
    try:
        with open(_state_path(root), encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(root, value):
    path = _state_path(root)
    temporary = path + '.' + uuid.uuid4().hex + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _tail(path, size=2 * 1024 * 1024, offset=0):
    """读取文件尾部（最多 size 字节），跳过 offset 之前的内容（清空后的续读语义）。"""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            end = f.tell()
            if end <= offset:
                return []
            start = max(offset, end - size)
            f.seek(start)
            # 只有当窗口截断让我们落在行中间时才丢弃半行；offset 本身是清空时刻的
            # 行边界，绝不能丢——否则清空后新写入的第一条事件会被吃掉。
            if start > offset:
                f.readline()
            return f.read().decode('utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        return []


def _event(value, source):
    if not isinstance(value, dict) or not isinstance(value.get('type'), str):
        raise ValueError('每条事件必须是含 type 的对象。')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_EVENT:
        raise ValueError('单条事件超过 8KB 上限。')
    result = {**value, 'source': source}
    result.setdefault('timestamp', datetime.now(timezone.utc).isoformat())
    result.setdefault('id', uuid.uuid4().hex)
    return result


def _counter(items, field):
    out = {}
    for item in items:
        key = str(item.get(field, '') or 'unknown')
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _within(start, stamp, gap_seconds):
    if not start or not stamp:
        return True
    try:
        left = datetime.fromisoformat(start.replace('Z', '+00:00'))
        right = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return True
    return abs((right - left).total_seconds()) <= gap_seconds


def _sessions(events, gap_seconds=SESSION_GAP):
    """按时间间隔切分会话；事件自带 session 字段时优先用它（显式会话最可靠）。"""
    buckets = []
    for event in events:
        key = str(event.get('session', '') or '')
        stamp = str(event.get('timestamp', '') or '')
        if buckets and key and buckets[-1]['id'] == key:
            buckets[-1]['events'].append(event)
            continue
        if buckets and not key and not buckets[-1]['id'] and _within(buckets[-1]['end'], stamp, gap_seconds):
            buckets[-1]['events'].append(event)
            buckets[-1]['end'] = stamp
            continue
        buckets.append({'id': key, 'start': stamp, 'end': stamp, 'events': [event]})
    out = []
    for index, bucket in enumerate(buckets):
        items = bucket['events']
        out.append({'index': index + 1, 'id': bucket['id'], 'start': bucket['start'],
                    'end': bucket['end'], 'count': len(items),
                    'types': _counter(items, 'type'), 'sources': _counter(items, 'source')})
    return out


def runtime_events(root, events=None, *, from_ts=None, to_ts=None, types=None, sources=None,
                   keyword=None, limit=None, with_sessions=False, gap_seconds=SESSION_GAP):
    """读取/追加运行时事件。写路径只做追加；读路径支持筛选、统计与会话切分。"""
    from workbench_fs import FsError
    path, _ = _resolve(root, '.docmind_runtime.jsonl')
    log_path, _ = _resolve(root, '.docmind_engine.log')
    if events is not None and (not isinstance(events, list) or len(events) > 500):
        raise FsError(413, '每批最多 500 条事件。')
    try:
        incoming = [_event(e, 'api') for e in events or []]
    except (TypeError, ValueError) as exc:
        raise FsError(422, str(exc)) from exc
    with _event_lock:
        stored = []
        for line in _tail(path, MAX_EVENTS * (MAX_EVENT + 512)):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    stored.append(value)
            except ValueError:
                continue
        if incoming:
            stored = (stored + incoming)[-MAX_EVENTS:]
            temporary = path + '.' + uuid.uuid4().hex + '.tmp'
            try:
                with open(temporary, 'x', encoding='utf-8') as f:
                    for event in stored:
                        f.write(json.dumps(event, ensure_ascii=False) + '\n')
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    offset = int((_read_state(root).get('log_offset') or 0))
    captured = []
    for line in _tail(log_path, 2 * 1024 * 1024, offset):
        if MARKER not in line:
            continue
        try:
            raw = line.split(MARKER, 1)[1]
            value = json.loads(raw)
            value.setdefault('id', hashlib.sha256(raw.encode()).hexdigest()[:24])
            value.setdefault('timestamp', '')
            captured.append(_event(value, 'godot'))
        except (ValueError, TypeError, AttributeError):
            continue
    combined = sorted(stored + captured, key=lambda e: str(e.get('timestamp', '')))[-MAX_EVENTS:]

    type_set = {str(t) for t in (types or []) if str(t).strip()}
    source_set = {str(s) for s in (sources or []) if str(s).strip()}
    keyword = (keyword or '').strip().lower()

    def keep(event):
        if type_set and str(event.get('type', '')) not in type_set:
            return False
        if source_set and str(event.get('source', '')) not in source_set:
            return False
        stamp = str(event.get('timestamp', ''))
        if from_ts and stamp and stamp < str(from_ts):
            return False
        if to_ts and stamp and stamp > str(to_ts):
            return False
        if keyword and keyword not in json.dumps(
                {'type': event.get('type'), 'source': event.get('source'), 'data': event.get('data')},
                ensure_ascii=False).lower():
            return False
        return True

    filtered = [e for e in combined if keep(e)]
    if limit and int(limit) > 0:
        filtered = filtered[-int(limit):]
    result = {'ok': True, 'events': filtered, 'limit': MAX_EVENTS,
              'total': len(combined), 'matched': len(filtered),
              'capture': 'stdout', 'captured': len(captured),
              'types': _counter(combined, 'type'), 'sources': _counter(combined, 'source'),
              'range': {'from': str(combined[0].get('timestamp', '')) if combined else '',
                        'to': str(combined[-1].get('timestamp', '')) if combined else ''}}
    if with_sessions:
        result['sessions'] = _sessions(combined, gap_seconds)
    return result


def runtime_sessions(root, gap_seconds=SESSION_GAP):
    data = runtime_events(root)
    return {'ok': True, 'sessions': _sessions(data['events'], gap_seconds), 'total': data['total']}


def runtime_clear(root, scope='stored'):
    """清空运行时事件。

    ``scope='stored'`` 只清 API 侧落盘的事件；``scope='all'`` 连引擎日志也一并"归零"——
    做法是记录当前日志文件字节数作为续读游标，**不截断正在被引擎写入的日志**。
    """
    from workbench_fs import FsError
    if scope not in ('stored', 'all'):
        raise FsError(422, 'scope 只能是 stored 或 all。')
    path, _ = _resolve(root, '.docmind_runtime.jsonl')
    removed = 0
    with _event_lock:
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    removed = sum(1 for line in f if line.strip())
                os.unlink(path)
            except OSError as exc:
                raise FsError(500, '清除事件失败：%s' % exc) from exc
        if scope == 'all':
            log_path, _ = _resolve(root, '.docmind_engine.log')
            try:
                offset = os.path.getsize(log_path)
            except OSError:
                offset = 0
            _write_state(root, {**_read_state(root), 'log_offset': offset})
    return {'ok': True, 'scope': scope, 'removed': removed}

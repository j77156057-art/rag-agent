"""Scene inspection and bounded debug event storage. Never executes scene expressions."""
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

MAX_SCENE = 2 * 1024 * 1024
MAX_EVENT = 8192
MAX_EVENTS = 1000
MARKER = "DOCMIND_EVENT "
_event_lock = threading.Lock()
_attrs = re.compile(r'(\w+)="((?:\\.|[^"\\])*)"')
_resource = re.compile(r'ExtResource\(\s*"([^"]+)"\s*\)')


def _resolve(root, path, **kwargs):
    from workbench_fs import _resolve as resolve
    return resolve(root, path, **kwargs)


def scene_tree(root, rel):
    from workbench_fs import FsError
    path, rel = _resolve(root, rel, must_exist=True)
    if not rel.lower().endswith('.tscn'):
        raise FsError(415, '仅支持 .tscn 场景。')
    if os.path.getsize(path) > MAX_SCENE:
        raise FsError(413, '场景超过 2MB 解析上限。')
    with open(path, encoding='utf-8-sig') as f:
        lines = f.read().splitlines()
    nodes, resources, warnings = [], {}, []
    current = None
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.startswith('['):
            current = None
            attrs = dict(_attrs.findall(line))
            if line.startswith('[ext_resource '):
                resources[attrs.get('id', '')] = attrs.get('path', '')
            elif line.startswith('[node '):
                name = attrs.get('name', '')
                parent = attrs.get('parent', '')
                node_path = '.' if not parent else (name if parent == '.' else parent + '/' + name)
                instance = _resource.search(line)
                current = {'name': name, 'type': attrs.get('type', '实例'),
                           'parent': parent or '.', 'node_path': node_path,
                           'parent_path': parent if parent else None,
                           'line': number, 'properties': [], 'script': '',
                           'instance': resources.get(instance[1], '') if instance else ''}
                nodes.append(current)
            continue
        if current is not None and '=' in line and not line.startswith(';'):
            key, value = line.split('=', 1)
            current['properties'].append({'name': key.strip(), 'value': value.strip(), 'line': number})
            if key.strip() == 'script':
                match = _resource.search(value)
                current['script'] = resources.get(match[1], '') if match else ''
    paths = {n['node_path'] for n in nodes}
    for node in nodes:
        if node['parent_path'] is not None and node['parent_path'] not in paths:
            warnings.append('未展开实例内父节点：' + node['parent_path'])
        if node['script'].startswith('res://'):
            try:
                _, node['script'] = _resolve(root, node['script'][6:], must_exist=True)
            except FsError:
                warnings.append('脚本无法定位：' + node['script'])
                node['script'] = ''
        else:
            node['script'] = ''
    return {'ok': True, 'path': rel, 'nodes': nodes, 'warnings': warnings,
            'read_only': True}

def set_scene_property(root, rel, node_name, prop, value):
    from workbench_fs import _resolve
    path, rel = _resolve(root, rel, must_exist=True, for_write=True)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(prop)) or '\n' in str(value) or len(str(value)) > 1000:
        return {'ok': False, 'error': '属性名或值无效。'}
    with open(path, encoding='utf-8-sig') as f: lines=f.read().splitlines()
    start = next((i for i,x in enumerate(lines) if x.startswith('[node ') and f'name="{node_name}"' in x), -1)
    if start < 0: return {'ok': False, 'error': '节点不存在。'}
    end = next((i for i in range(start+1,len(lines)) if lines[i].startswith('[')), len(lines))
    idx = next((i for i in range(start+1,end) if re.match(rf'^\s*{re.escape(prop)}\s*=', lines[i])), None)
    line=f'{prop} = {value}'
    if idx is None: lines.insert(end,line)
    else: lines[idx]=line
    tmp=path+'.scene.tmp'
    with open(tmp,'w',encoding='utf-8-sig',newline='\n') as f: f.write('\n'.join(lines)+'\n')
    os.replace(tmp,path)
    return {'ok': True, 'path': rel, 'node': node_name, 'property': prop, 'value': value}


def _tail(path, size=2 * 1024 * 1024):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            start = max(0, f.tell() - size)
            f.seek(start)
            if start:
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


def runtime_events(root, events=None):
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
    captured = []
    for line in _tail(log_path):
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
    return {'ok': True, 'events': combined, 'limit': MAX_EVENTS,
            'capture': 'stdout', 'captured': len(captured)}

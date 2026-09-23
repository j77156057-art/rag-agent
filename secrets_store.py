"""本地 API 密钥加密存储：优先 Windows DPAPI，其他平台使用受保护密钥文件。"""
import base64, json, os
import ctypes
from pathlib import Path
import project_state
try:
    from cryptography.fernet import Fernet
except Exception: Fernet = None

def _key_path(root): return Path(project_state.path(root, 'secret.key', legacy='.docmind_secret.key'))
def _box(root):
    p=_key_path(root); p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists(): p.write_bytes(Fernet.generate_key() if Fernet else os.urandom(32));
    return Fernet(p.read_bytes()) if Fernet else None

def _dpapi_encrypt(value):
    if os.name != 'nt': return None
    try:
        class B(ctypes.Structure): _fields_=[('cbData',ctypes.c_uint32),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
        raw=value.encode(); src=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw); inp=B(len(raw),src); out=B()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp),None,None,None,None,0,ctypes.byref(out)): return None
        data=ctypes.string_at(out.pbData,out.cbData); ctypes.windll.kernel32.LocalFree(out.pbData)
        return base64.b64encode(data).decode()
    except Exception: return None

def _dpapi_decrypt(token):
    if os.name != 'nt': return None
    try:
        class B(ctypes.Structure): _fields_=[('cbData',ctypes.c_uint32),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
        raw=base64.b64decode(token); src=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw); inp=B(len(raw),src); out=B()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp),None,None,None,None,0,ctypes.byref(out)): return None
        data=ctypes.string_at(out.pbData,out.cbData); ctypes.windll.kernel32.LocalFree(out.pbData); return data.decode()
    except Exception: return None
def save(root, provider, value):
    if not value: return {'ok':False,'error':'空密钥不保存'}
    p=Path(project_state.path(root, 'secrets.json', legacy='.docmind_secrets.json')); data={}
    if p.exists():
        try: data=json.loads(p.read_text(encoding='utf-8'))
        except Exception: data={}
    token=_dpapi_encrypt(value)
    if token: scheme='dpapi'
    else:
        box=_box(root); token=box.encrypt(value.encode()).decode() if box else base64.b64encode(value.encode()).decode(); scheme='fernet' if box else 'base64'
    data[str(provider)]={'ciphertext':token,'scheme':scheme}; p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ok':True,'provider':provider,'stored':True}
def load(root, provider):
    """Load a provider key, retaining compatibility with pre-project storage.

    A migrated ``secrets.json`` can outlive the legacy file.  If its DPAPI
    record cannot be opened (for example after switching from an old packaged
    launcher), try the original project-local file before giving up.  This is
    deliberately read-only: an explicit save is still required to rewrite a
    key with the current user's protection context.
    """
    candidates = []
    try:
        candidates.append(Path(project_state.path(root, 'secrets.json', legacy='.docmind_secrets.json')))
    except Exception:
        pass
    legacy = Path(os.path.abspath(root or '')) / '.docmind_secrets.json'
    if legacy not in candidates:
        candidates.append(legacy)

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            item = data.get(str(provider), {})
            token = item.get('ciphertext', '')
            if not token:
                continue
            if item.get('scheme') == 'dpapi':
                value = _dpapi_decrypt(token) or ''
            else:
                box = _box(root)
                value = box.decrypt(token.encode()).decode() if box else base64.b64decode(token).decode()
            if value:
                return value
        except Exception:
            continue
    return ''

def providers(root):
    try: return sorted(json.loads(Path(project_state.path(root, 'secrets.json', legacy='.docmind_secrets.json')).read_text(encoding='utf-8')).keys())
    except Exception: return []

def remove(root, provider):
    p=Path(project_state.path(root, 'secrets.json', legacy='.docmind_secrets.json'))
    try: data=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    except Exception: data={}
    existed=str(provider) in data; data.pop(str(provider),None)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ok':True,'removed':existed,'provider':str(provider)}

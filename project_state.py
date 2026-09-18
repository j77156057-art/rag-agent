"""Private workbench state, keyed by explicit project root (never current UI selection).

Legacy project files are copied once, never removed. A migration marker prevents
deleted/cleared state from reappearing from the retained legacy copy.
User assets, export presets, web builds and Git backups remain in the project.
"""
import os
import shutil
import tempfile
import threading

import config
import projects

_lock = threading.RLock()


def directory(root):
    if not root:
        raise ValueError('项目状态需要明确的项目根目录')
    return os.path.join(os.path.abspath(config.STATE_ROOT), '.docmind', 'projects', projects.project_id(root))


def path(root, name, *, legacy=None):
    """Resolve internal state and lazily copy only its own project's old file.

    Names are internal constants, but reject traversal/symlinks nevertheless.
    No import-time disk access; STATE_ROOT is resolved at call time for tests.
    """
    base = directory(root)
    name = os.fspath(name).replace('\\', '/')
    if not name or name.startswith('/') or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('非法项目状态路径')
    target = os.path.join(base, *name.split('/'))
    anchor = os.path.realpath(config.STATE_ROOT)
    if os.path.commonpath([anchor, os.path.realpath(target)]) != anchor:
        raise ValueError('项目状态路径越界')
    with _lock:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        marker = target + '.migrated'
        if not os.path.exists(marker):
            source = os.path.join(os.path.abspath(root), legacy or name)
            project = os.path.realpath(root)
            if os.path.commonpath([project, os.path.realpath(source)]) != project:
                raise ValueError('旧项目状态路径越界')
            if not os.path.exists(target) and os.path.isfile(source):
                fd, temporary = tempfile.mkstemp(dir=os.path.dirname(target), prefix='.migrate-')
                try:
                    with os.fdopen(fd, 'wb') as out, open(source, 'rb') as inp:
                        shutil.copyfileobj(inp, out)
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            with open(marker, 'a', encoding='utf-8'):
                pass
    return target

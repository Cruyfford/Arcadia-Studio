"""Store version history: every save keeps a compressed snapshot of the menus (XML + Arabic sidecar + version)."""
import json
import os
import time
import zipfile

KEEP = 40


def _hist(root):
    return os.path.join(root, '.history')


def _menu_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.lower().endswith(('.xml', '.json', '.txt')) and not f.startswith('.'):
                yield os.path.relpath(os.path.join(base, f), root).replace(os.sep, '/')


def snapshot(root, note=''):
    d = _hist(root); os.makedirs(d, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S')
    out = os.path.join(d, name + '.zip')
    with zipfile.ZipFile(out + '.tmp', 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in _menu_files(root):
            z.write(os.path.join(root, rel), rel)
        z.writestr('.meta.json', json.dumps({'note': note, 'time': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False))
    os.replace(out + '.tmp', out)
    snaps = list_snapshots(root)
    for s in snaps[KEEP:]:
        try: os.remove(s['path'])
        except OSError: pass
    return out


def list_snapshots(root):
    d = _hist(root)
    if not os.path.isdir(d): return []
    res = []
    for f in sorted(os.listdir(d), reverse=True):
        if not f.endswith('.zip'): continue
        p = os.path.join(d, f)
        try:
            with zipfile.ZipFile(p) as z:
                meta = json.loads(z.read('.meta.json')) if '.meta.json' in z.namelist() else {}
                n = len(z.namelist()) - 1
        except Exception:  # noqa  damaged snapshot
            continue
        res.append({'path': p, 'time': meta.get('time', f[:-4]), 'note': meta.get('note', ''), 'files': n,
                    'size': os.path.getsize(p)})
    return res


def restore(root, snap_path):
    """put the menus back as they were (a snapshot of the current state is taken first)"""
    snapshot(root, 'قبل الاسترجاع')
    with zipfile.ZipFile(snap_path) as z:
        names = [n for n in z.namelist() if n != '.meta.json']
        cur = set(_menu_files(root))
        for rel in cur - set(names):
            if rel.lower().endswith('.xml'):          # menus added after the snapshot
                os.remove(os.path.join(root, rel))
        for n in names:
            p = os.path.join(root, n); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as f: f.write(z.read(n))
    return names

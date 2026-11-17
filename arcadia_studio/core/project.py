"""Light store project (.arcadia): the whole store menu in one small file instead of the full PKG.

An .arcadia file is a ZIP with:
  store.json        every XMB menu (sections, games, links, titles, pictures paths) as JSON
  thumbs/<path>.webp small previews of every icon/background, so the program shows everything
  files/<path>      full-size files you added yourself (new icons, backgrounds ...)

A plain store.json (without the zip) also opens: everything shows, only without pictures.
Building a PKG from a project needs the base store once (the original ARCADIA PKG or its extracted folder):
the program overlays the project on top of it.
"""
import io
import json
import os
import re
import shutil
import time
import zipfile

from .xmb import _view_re, _table_re, _pair_re, _elem_re, _attr_re, SIDECAR, STORE_PREFIX

FORMAT = 'arcadia-project'
TEXTS = ('USRDIR/ARCADIA_VERSION.txt',)
IMG_KEYS = ('icon', 'pic')


def _read(path):
    raw = open(path, 'rb').read()
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('latin-1'), 'latin-1'


def parse_xml(text):
    views = []
    for vid, body in _view_re.findall(text):
        tables = {k: dict(_pair_re.findall(b)) for k, b in _table_re.findall(body)}
        items = []
        if '<Items>' in body:
            for kind, attrs in _elem_re.findall(body[body.find('<Items>'):]):
                a = dict(_attr_re.findall(attrs)); a['_'] = kind; items.append(a)
        views.append({'id': vid, 'tables': tables, 'items': items})
    return views


def gen_xml(views):
    out = ['<XMBML version="1.0">\n']
    for v in views:
        out.append('\t<View id="%s">\n\t\t<Attributes>\n' % v['id'])
        for k, pairs in v['tables'].items():
            out.append('\t\t\t<Table key="%s">\n' % k)
            for pk, pv in pairs.items():
                out.append('\t\t\t\t<Pair key="%s"><String>%s</String></Pair>\n' % (pk, pv))
            out.append('\t\t\t</Table>\n')
        out.append('\t\t</Attributes>\n\t\t<Items>\n')
        for it in v['items']:
            kind = it.get('_', 'Query')
            attrs = ' '.join('%s="%s"' % (a, b) for a, b in it.items() if a != '_')
            out.append('\t\t\t<%s %s/>\n' % (kind, attrs))
        out.append('\t\t</Items>\n\t</View>\n')
    out.append('</XMBML>\n')
    return ''.join(out)


def xml_files(root):
    res = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for f in sorted(files):
            if f.lower().endswith('.xml'):
                res.append(os.path.relpath(os.path.join(base, f), root).replace(os.sep, '/'))
    return res


def _thumb(path, big):
    from PIL import Image
    im = Image.open(path)
    im = im.convert('RGBA')
    im.thumbnail((400, 225) if big else (136, 76))
    b = io.BytesIO(); im.save(b, 'WEBP', quality=50 if big else 55, method=4)
    return b.getvalue()


def export_project(store, out_path, thumbs=True, progress=None, name=None):
    """store: core.xmb.Store (full or light workspace). Writes .arcadia (zip) or .json (no pictures)."""
    store.save()
    root = store.root
    rels = xml_files(root)
    xml, imgs = {}, set()
    for i, rel in enumerate(rels):
        t, enc = _read(os.path.join(root, rel))
        views = parse_xml(t)
        xml[rel] = {'enc': enc, 'views': views}
        for v in views:
            for pairs in v['tables'].values():
                for k in IMG_KEYS:
                    if pairs.get(k, '').startswith(STORE_PREFIX): imgs.add(pairs[k])
        if progress: progress(i + 1, len(rels) * 2)
    texts = {r: open(os.path.join(root, r), encoding='utf-8', errors='replace').read()
             for r in TEXTS if os.path.exists(os.path.join(root, r))}
    pending = sorted(store.changed)
    extra = [r for r in pending if not r.lower().endswith('.xml') and r != SIDECAR and r not in TEXTS
             and os.path.isfile(os.path.join(root, r))]
    doc = {'format': FORMAT, 'format_version': 1, 'name': name or 'ARCADIA', 'saved': time.strftime('%Y-%m-%d %H:%M'),
           'store_version': int(texts.get('USRDIR/ARCADIA_VERSION.txt', '0').strip() or 0),
           'sidecar': store.src, 'texts': texts, 'pending': pending, 'files': extra, 'xml': xml}
    data = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    tmp = out_path + '.tmp'
    if out_path.lower().endswith('.json'):
        open(tmp, 'wb').write(data)
    else:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr('store.json', data)
            for r in extra:
                z.write(os.path.join(root, r), 'files/' + r, compress_type=zipfile.ZIP_STORED if r.lower().endswith(('.png', '.jpg')) else zipfile.ZIP_DEFLATED)
            if thumbs:
                imgs = sorted(imgs)
                for i, ps3 in enumerate(imgs):
                    rel = ps3[len(STORE_PREFIX):]
                    p = store.abs_path(ps3)
                    try:
                        if p and p.endswith('.webp') and os.path.exists(p):
                            b = open(p, 'rb').read()
                        elif p and os.path.exists(p):
                            b = _thumb(p, 'pic' in ps3.lower() or '/BG' in ps3.upper() or os.path.getsize(p) > 400000)
                        else:
                            continue
                        z.writestr('thumbs/' + rel + '.webp', b, compress_type=zipfile.ZIP_STORED)
                    except Exception:  # noqa  broken image in the store: skip its preview
                        pass
                    if progress and i % 50 == 0: progress(len(rels) + int(len(rels) * i / max(1, len(imgs))), len(rels) * 2)
    os.replace(tmp, out_path)
    if progress: progress(1, 1)
    return os.path.getsize(out_path)


def load_doc(path):
    if zipfile.is_zipfile(path):
        z = zipfile.ZipFile(path)
        return json.loads(z.read('store.json').decode('utf-8')), z
    return json.load(open(path, encoding='utf-8')), None


def open_project(path, ws, progress=None):
    """Unpack a project into a light workspace folder that Store can open."""
    doc, z = load_doc(path)
    if doc.get('format') != FORMAT:
        raise ValueError('هذا الملف ليس مشروع أركاديا')
    if os.path.exists(ws):
        shutil.rmtree(ws)
    os.makedirs(ws)
    items = list(doc['xml'].items())
    for i, (rel, x) in enumerate(items):
        p = os.path.join(ws, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(gen_xml(x['views']).encode(x.get('enc', 'utf-8'), 'xmlcharrefreplace'))
        if progress and i % 20 == 0: progress(i, len(items) + 1)
    os.makedirs(os.path.join(ws, 'USRDIR'), exist_ok=True)
    for rel, t in doc.get('texts', {}).items():
        open(os.path.join(ws, rel), 'w', encoding='utf-8').write(t)
    json.dump(doc.get('sidecar', {}), open(os.path.join(ws, SIDECAR), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    if z:
        for n in z.namelist():
            if n.startswith(('thumbs/', 'files/')) and not n.endswith('/'):
                dst = os.path.join(ws, '.thumbs' if n.startswith('thumbs/') else '', n.split('/', 1)[1])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(n) as s, open(dst, 'wb') as d:
                    shutil.copyfileobj(s, d)
    json.dump(doc.get('pending', []), open(os.path.join(ws, '.arcadia_pending.json'), 'w'))
    json.dump({'project': os.path.abspath(path), 'light': True}, open(os.path.join(ws, '.arcadia_project.json'), 'w'))
    if progress: progress(1, 1)
    return ws


def is_store_root(folder):
    return os.path.exists(os.path.join(folder, 'USRDIR', 'VIDEOGAMESSCZ_xmb.xml'))

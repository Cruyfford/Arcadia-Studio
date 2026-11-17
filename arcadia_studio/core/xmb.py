"""Tolerant XMBML reader/editor. Store files are not always well-formed XML, so edits are
done with targeted text replacements that keep the original formatting intact."""
import html
import json
import os
import re

from .arabic import to_xmb, from_xmb

STORE_PREFIX = '/dev_hdd0/game/VIDESTORE/'
ROOT_XML = 'USRDIR/VIDEOGAMESSCZ_xmb.xml'
ROOT_VIEW = 'VIDEOGAMESSCZ_xmb_main'
SIDECAR = 'USRDIR/ARCADIA_SRC.json'

_view_re = re.compile(r'<View\s+id="([^"]+)"\s*>(.*?)</View>', re.S)
_table_re = re.compile(r'<Table\s+key="([^"]+)"\s*>(.*?)</Table>', re.S)
_pair_re = re.compile(r'<Pair\s+key="([^"]+)"\s*>\s*<String>(.*?)</String>\s*</Pair>', re.S)
_elem_re = re.compile(r'<(Query|Item)\b(.*?)/>', re.S)
_attr_re = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


class Store:
    """A VIDEOSTORE-style workspace (extracted PKG folder)."""

    def __init__(self, root):
        self.root = root
        self.files = {}
        self.dirty = set()
        p = os.path.join(root, SIDECAR)
        self.src = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
        pp = os.path.join(root, '.arcadia_pending.json')
        self.changed = set(json.load(open(pp))) if os.path.exists(pp) else set()

    # ---------- files
    def text(self, rel):
        if rel not in self.files:
            raw = open(os.path.join(self.root, rel), 'rb').read()
            try:
                self.files[rel] = (raw.decode('utf-8'), 'utf-8')
            except UnicodeDecodeError:
                self.files[rel] = (raw.decode('latin-1'), 'latin-1')
        return self.files[rel][0]

    def set_text(self, rel, t):
        enc = self.files[rel][1] if rel in self.files else 'utf-8'
        self.files[rel] = (t, enc); self.dirty.add(rel)

    def mark_changed(self, rel):
        self.changed.add(rel.replace(os.sep, '/'))
        json.dump(sorted(self.changed), open(os.path.join(self.root, '.arcadia_pending.json'), 'w'))

    def clear_changed(self):
        self.changed.clear()
        json.dump([], open(os.path.join(self.root, '.arcadia_pending.json'), 'w'))

    def save(self):
        for rel in list(self.dirty):
            self.mark_changed(rel)
            t, enc = self.files[rel]
            open(os.path.join(self.root, rel), 'wb').write(t.encode(enc, 'xmlcharrefreplace'))
        self.dirty.clear()
        json.dump(self.src, open(os.path.join(self.root, SIDECAR), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        self.mark_changed(SIDECAR)

    # ---------- paths
    def ps3_to_rel(self, ps3path):
        return ps3path[len(STORE_PREFIX):] if ps3path.startswith(STORE_PREFIX) else None

    def rel_to_ps3(self, rel):
        return STORE_PREFIX + rel.replace(os.sep, '/')

    def abs_path(self, ps3path):
        rel = self.ps3_to_rel(ps3path)
        return os.path.join(self.root, rel) if rel else None

    def resolve(self, cur_rel, src):
        """Query src -> (rel_file, view_id)"""
        if not src: return None
        if src.startswith('#'):
            return cur_rel, src[1:]
        m = re.match(r'xmb://localhost(/[^#]+)#(.+)', src)
        if m:
            rel = self.ps3_to_rel(m[1])
            if rel and os.path.exists(os.path.join(self.root, rel)):
                return rel, m[2]
        return None

    # ---------- reading
    def view_span(self, rel, vid):
        t = self.text(rel)
        m = re.search(r'<View\s+id="%s"\s*>(.*?)</View>' % re.escape(vid), t, re.S)
        return (m.start(1), m.end(1)) if m else None

    def children(self, rel, vid):
        """ordered list of nodes in a view: dict(key, kind, cls, src, pairs, target)"""
        span = self.view_span(rel, vid)
        if not span: return []
        body = self.text(rel)[span[0]:span[1]]
        tables = {k: dict(_pair_re.findall(b)) for k, b in _table_re.findall(body)}
        out = []
        items = body[body.find('<Items>'):] if '<Items>' in body else ''
        for kind, attrs in _elem_re.findall(items):
            a = dict(_attr_re.findall(attrs))
            key = a.get('key')
            pairs = {k: html.unescape(v) for k, v in tables.get(key, {}).items()}
            out.append(dict(key=key, kind=kind, cls=a.get('class', ''), src=a.get('src'),
                            pairs=pairs, target=self.resolve(rel, a.get('src')), file=rel, view=vid))
        return out

    def display(self, s):
        """XMB string -> editable text"""
        return self.src.get(s) or from_xmb(s)

    # ---------- editing
    def _replace_view_body(self, rel, vid, fn):
        span = self.view_span(rel, vid)
        t = self.text(rel)
        body = fn(t[span[0]:span[1]])
        self.set_text(rel, t[:span[0]] + body + t[span[1]:])

    def set_pair(self, rel, vid, key, pkey, value, arabic=True):
        disp = to_xmb(value) if arabic else value
        if disp != value: self.src[disp] = value
        esc = html.escape(disp, quote=False)

        def fn(body):
            m = re.search(r'<Table\s+key="%s"\s*>(.*?)</Table>' % re.escape(key), body, re.S)
            if not m:
                return body.replace('<Attributes>', '<Attributes>\n\t\t\t<Table key="%s">\n\t\t\t\t<Pair key="%s"><String>%s</String></Pair>\n\t\t\t</Table>' % (key, pkey, esc), 1)
            tb = m[1]
            pm = re.search(r'(<Pair\s+key="%s"\s*>\s*<String>)(.*?)(</String>)' % re.escape(pkey), tb, re.S)
            if pm:
                if value == '':
                    tb = tb[:pm.start()] + tb[tb.index('</Pair>', pm.end()) + 7:]
                else:
                    tb = tb[:pm.start(2)] + esc + tb[pm.end(2):]
            elif value != '':
                tb = tb.rstrip() + '\n\t\t\t\t<Pair key="%s"><String>%s</String></Pair>\n\t\t\t' % (pkey, esc)
            return body[:m.start(1)] + tb + body[m.end(1):]
        self._replace_view_body(rel, vid, fn)

    def add_node(self, rel, vid, key, pairs, cls='type:x-xmb/module-action', src=None, index=None):
        base, n = key, 2
        existing = {c['key'] for c in self.children(rel, vid)}
        while key in existing: key = '%s_%d' % (base, n); n += 1
        lines = []
        for k, v in pairs.items():
            disp = to_xmb(v) if k in ('title', 'info') else v
            if disp != v: self.src[disp] = v
            lines.append('\t\t\t\t<Pair key="%s"><String>%s</String></Pair>' % (k, html.escape(disp, quote=False)))
        table = '\t\t\t<Table key="%s">\n%s\n\t\t\t</Table>\n' % (key, '\n'.join(lines))
        q = '<Query class="%s" key="%s" attr="%s"%s/>' % (cls, key, key, ' src="%s"' % src if src else '')

        def fn(body):
            i = body.index('</Attributes>')
            body = body[:i] + table + '\t\t' + body[i:]
            elems = list(_elem_re.finditer(body, body.index('<Items>')))
            if index is None or index >= len(elems):
                j = body.index('</Items>')
                return body[:j] + '\t' + q + '\n\t\t' + body[j:]
            e = elems[index]
            return body[:e.start()] + q + '\n\t\t\t' + body[e.start():]
        self._replace_view_body(rel, vid, fn)
        return key

    def remove_node(self, rel, vid, key):
        def fn(body):
            body = re.sub(r'\s*<Table\s+key="%s"\s*>.*?</Table>' % re.escape(key), '', body, count=1, flags=re.S)
            body = re.sub(r'\s*<(Query|Item)\b[^>]*?\bkey="%s"[^>]*?/>' % re.escape(key), '', body, count=1, flags=re.S)
            return body
        self._replace_view_body(rel, vid, fn)

    def move_node(self, rel, vid, key, delta):
        def fn(body):
            i0 = body.index('<Items>')
            elems = list(_elem_re.finditer(body, i0))
            idx = next(i for i, e in enumerate(elems) if re.search(r'\bkey="%s"' % re.escape(key), e[0]))
            j = idx + delta
            if not 0 <= j < len(elems): return body
            a, b = sorted((idx, j))
            ea, eb = elems[a], elems[b]
            return body[:ea.start()] + eb[0] + body[ea.end():eb.start()] + ea[0] + body[eb.end():]
        self._replace_view_body(rel, vid, fn)

    def add_view(self, rel, vid, title_comment=''):
        t = self.text(rel)
        block = '\n<!-- %s -->\n\t<View id="%s">\n\t\t<Attributes>\n\t\t</Attributes>\n\t\t<Items>\n\t\t</Items>\n\t</View>\n' % (title_comment or vid, vid)
        i = t.rindex('</XMBML>') if '</XMBML>' in t else len(t)
        self.set_text(rel, t[:i] + block + t[i:])

    # ---------- online update link (every installed copy updates from here)
    UPDATE_FILE = ROOT_XML

    def get_version(self):
        p = os.path.join(self.root, 'USRDIR', 'ARCADIA_VERSION.txt')
        return int(open(p).read().strip() or 0) if os.path.exists(p) else 0

    def set_update(self, url, version):
        rel = ROOT_XML
        self.set_pair(rel, 'seg_update_store_link', 'update_store', 'pkg_src', url, False)
        self.set_pair(rel, 'seg_update_store_link', 'update_store', 'pkg_src_qa', url, False)
        main = self.children(ROOT_XML, ROOT_VIEW)[0]['target']
        self.set_pair(main[0], main[1], 'actualizar_store', 'title', 'تحديث أركاديا')
        self.set_pair(main[0], main[1], 'actualizar_store', 'info', 'نسختك الحالية: %d - اضغط لتنزيل أحدث نسخة' % version)
        self.set_pair(rel, 'actualizar_store_main', 'update_store_link', 'title', 'تنزيل أحدث نسخة من أركاديا')
        self.set_pair(rel, 'actualizar_store_main', 'update_store_link', 'info', 'بعد التثبيت أعد تشغيل الجهاز')
        open(os.path.join(self.root, 'USRDIR', 'ARCADIA_VERSION.txt'), 'w').write(str(version))
        self.mark_changed('USRDIR/ARCADIA_VERSION.txt')

    # ---------- export for the PS3 app
    def export_catalog(self, max_depth=8):
        seen = set()

        def walk(rel, vid, depth):
            if depth > max_depth or (rel, vid) in seen: return []
            seen.add((rel, vid))
            res = []
            for c in self.children(rel, vid):
                p = c['pairs']
                node = {'id': c['key'], 'title': self.display(p.get('title', '')), 'info': self.display(p.get('info', '')),
                        'icon': p.get('icon', ''), 'bg': p.get('pic', '')}
                url = p.get('pkg_src') or p.get('module_action', '')
                if url.startswith(('http://', 'https://')): node['url'] = url
                elif 'download.ps3?url=' in url: node['url'] = url.split('url=', 1)[1]
                if p.get('content_id'): node['content_id'] = p['content_id']
                if c['target']:
                    node['children'] = walk(c['target'][0], c['target'][1], depth + 1)
                res.append(node)
            return res
        main = self.children(ROOT_XML, ROOT_VIEW)
        tgt = main[0]['target'] if main else None
        return {'name': 'ARCADIA', 'version': 1, 'sections': walk(tgt[0], tgt[1], 0) if tgt else []}

"""Find every download link in the store and check whether it still works."""
import html
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_view_re = re.compile(r'<View\s+id="([^"]+)"\s*>(.*?)</View>', re.S)
_table_re = re.compile(r'<Table\s+key="([^"]+)"\s*>(.*?)</Table>', re.S)
_pair_re = re.compile(r'<Pair\s+key="([^"]+)"\s*>\s*<String>(.*?)</String>\s*</Pair>', re.S)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArcadiaStudio/1.0'}

OK, BROKEN, UNSURE = 'ok', 'broken', 'unsure'


def extract_url(pairs):
    for k in ('pkg_src', 'module_action'):
        v = html.unescape(pairs.get(k, '')).strip()
        if not v: continue
        m = re.search(r'[?&]url=(https?://.+)$', v)
        if m: return m.group(1)
        if v.startswith(('http://', 'https://')) and '127.0.0.1' not in v and 'localhost' not in v:
            return v
    return None


def collect_links(store, rel_files=None):
    """-> list of dict(file, view, key, title, url)"""
    out = []
    root = store.root
    if rel_files is None:
        rel_files = []
        for base, _, files in os.walk(os.path.join(root, 'USRDIR')):
            for f in files:
                if f.lower().endswith('.xml'):
                    rel_files.append(os.path.relpath(os.path.join(base, f), root).replace(os.sep, '/'))
    for rel in sorted(rel_files):
        t = store.text(rel)
        for vid, body in _view_re.findall(t):
            for key, tb in _table_re.findall(body):
                pairs = dict(_pair_re.findall(tb))
                url = extract_url(pairs)
                if url:
                    out.append(dict(file=rel, view=vid, key=key, url=url,
                                    title=store.display(html.unescape(pairs.get('title', key)))))
    return out


def _size_of(r):
    cr = r.headers.get('Content-Range', '')
    if '/' in cr and cr.rsplit('/', 1)[1].isdigit():
        return int(cr.rsplit('/', 1)[1])
    cl = r.headers.get('Content-Length', '')
    return int(cl) if cl.isdigit() else None


def check_url(url, timeout=15, session=None):
    """-> (status, http_code, size_bytes, note)"""
    s = session or requests
    try:
        if 'mediafire.com' in url:
            r = s.get(url, headers=UA, timeout=timeout, allow_redirects=True)
            body = r.text[:200000]
            if r.status_code >= 400 or 'error.php' in r.url or 'File Removed' in body or 'file_removed' in body:
                return BROKEN, r.status_code, None, 'الملف محذوف من MediaFire'
            if 'download' in body.lower() and ('aria-label="Download file"' in body or 'downloadButton' in body
                                               or 'download' in r.url):
                m = re.search(r'Download \(([\d.,]+\s*[KMG]B)\)', body)
                return OK, r.status_code, None, ('MediaFire ' + m.group(1)) if m else 'MediaFire'
            return UNSURE, r.status_code, None, 'صفحة MediaFire بدون زر تنزيل واضح'
        r = s.head(url, headers=UA, timeout=timeout, allow_redirects=True)
        if r.status_code in (403, 405, 400, 501) or r.status_code >= 500:
            r = s.get(url, headers=dict(UA, Range='bytes=0-0'), timeout=timeout, allow_redirects=True, stream=True)
            r.close()
        size = _size_of(r)
        if r.status_code in (404, 410):
            return BROKEN, r.status_code, None, 'غير موجود (404)'
        if r.status_code >= 400:
            return BROKEN if r.status_code < 500 else UNSURE, r.status_code, size, 'خطأ من الخادم %d' % r.status_code
        ctype = r.headers.get('Content-Type', '')
        if url.lower().split('?')[0].endswith(('.pkg', '.enc', '.p3t', '.rar', '.zip', '.7z', '.iso', '.bin')) \
                and 'text/html' in ctype and (size or 0) < 200000:
            return UNSURE, r.status_code, size, 'يرجع صفحة ويب بدل الملف'
        if size == 0:
            return BROKEN, r.status_code, 0, 'ملف فارغ'
        return OK, r.status_code, size, ''
    except requests.exceptions.Timeout:
        return UNSURE, None, None, 'انتهت المهلة (الخادم بطيء)'
    except requests.exceptions.SSLError:
        return UNSURE, None, None, 'مشكلة في شهادة HTTPS'
    except requests.exceptions.ConnectionError:
        return BROKEN, None, None, 'تعذّر الاتصال بالخادم'
    except Exception as e:  # noqa
        return UNSURE, None, None, str(e)[:120]


def check_all(links, workers=24, progress=None, on_result=None, cancel=None):
    """checks unique URLs in parallel; returns {url: (status, code, size, note)}"""
    urls = list(dict.fromkeys(l['url'] for l in links))
    results = {}
    local = threading.local()

    def sess():
        if not hasattr(local, 's'):
            local.s = requests.Session()
            a = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=1)
            local.s.mount('http://', a); local.s.mount('https://', a)
        return local.s

    def job(u):
        if cancel and cancel.is_set():
            return u, (UNSURE, None, None, 'أُلغي')
        return u, check_url(u, session=sess())
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(job, u) for u in urls]
        for f in as_completed(futs):
            u, res = f.result()
            results[u] = res; done += 1
            if on_result: on_result(u, res)
            if progress: progress(done, len(urls))
    return results


def human(n):
    if n is None: return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%.1f %s' % (n, unit)) if unit != 'B' else '%d B' % n
        n /= 1024.0

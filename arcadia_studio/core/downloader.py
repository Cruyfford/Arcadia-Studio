"""Fast multi-connection downloader (IDM-style): splits a file into segments downloaded in
parallel, with resume support. Resolves MediaFire pages to the direct file link."""
import json
import os
import re
import threading
import time
import urllib.parse

import requests

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36'}
CHUNK = 1 << 18          # 256 KB writes
MIN_SEG = 2 << 20        # don't split below 2 MB per connection


class DownloadError(Exception):
    pass


def resolve(url, session=None):
    """turn share pages (MediaFire) into direct links; returns (direct_url, suggested_name)"""
    s = session or requests
    if 'mediafire.com' in url and '/download' not in urllib.parse.urlparse(url).netloc:
        r = s.get(url, headers=UA, timeout=20)
        m = re.search(r'href="(https?://download[^"]+mediafire\.com/[^"]+)"', r.text)
        if not m:
            m = re.search(r'data-scrambled-url="([^"]+)"', r.text)
            if m:
                import base64
                return base64.b64decode(m.group(1)).decode(), None
            raise DownloadError('تعذّر استخراج رابط التنزيل من صفحة MediaFire (قد يكون الملف محذوفاً)')
        return m.group(1).replace('&amp;', '&'), None
    return url, None


def _name_from(r, url):
    cd = r.headers.get('Content-Disposition', '')
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    name = urllib.parse.unquote(m.group(1)) if m else urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(r.url or url).path))
    name = re.sub(r'[\\/:*?"<>|]+', '_', name).strip() or 'download.bin'
    return name


class Download:
    """one download job. Call start(); poll .done/.total/.speed/.state; pause() / cancel()."""

    def __init__(self, url, folder, connections=8, name=None):
        self.url, self.folder, self.connections, self.name = url, folder, connections, name
        self.total = 0; self.done = 0; self.speed = 0.0; self.state = 'waiting'; self.error = ''
        self.path = None; self._stop = threading.Event(); self._lock = threading.Lock()
        self._thread = None

    # ---------- control
    def start(self):
        self._stop.clear(); self.state = 'starting'
        self._thread = threading.Thread(target=self._run, daemon=True); self._thread.start()

    def pause(self):
        self._stop.set(); self.state = 'paused'

    def cancel(self):
        self._stop.set(); self.state = 'cancelled'
        if self._thread: self._thread.join(3)
        for p in (self.path and self.path + '.part', self.path and self.path + '.arcadia'):
            if p and os.path.exists(p):
                try: os.remove(p)
                except OSError: pass

    # ---------- worker
    def _run(self):
        try:
            self._download()
        except Exception as e:  # noqa
            if self.state not in ('paused', 'cancelled'):
                self.state = 'error'; self.error = str(e)[:300]

    def _probe(self, s, url):
        r = s.get(url, headers=dict(UA, Range='bytes=0-0'), timeout=30, stream=True, allow_redirects=True)
        r.close()
        if r.status_code >= 400:
            raise DownloadError('الرابط لا يعمل (%d)' % r.status_code)
        size, ranges = None, False
        if r.status_code == 206 and '/' in r.headers.get('Content-Range', ''):
            tail = r.headers['Content-Range'].rsplit('/', 1)[1]
            size, ranges = (int(tail), True) if tail.isdigit() else (None, False)
        if size is None and r.headers.get('Content-Length', '').isdigit() and r.status_code == 200:
            size = int(r.headers['Content-Length'])
        if 'text/html' in r.headers.get('Content-Type', '') and (size or 0) < 300000:
            raise DownloadError('الرابط يفتح صفحة ويب وليس ملفاً')
        return r.url, size, ranges, _name_from(r, url)

    def _download(self):
        s = requests.Session()
        self.state = 'connecting'
        url, _ = resolve(self.url, s)
        final, size, ranges, name = self._probe(s, url)
        self.name = self.name or name
        os.makedirs(self.folder, exist_ok=True)
        self.path = os.path.join(self.folder, self.name)
        part, meta = self.path + '.part', self.path + '.arcadia'
        self.total = size or 0
        if size and ranges and size > MIN_SEG:
            n = max(1, min(self.connections, size // MIN_SEG))
            segs = None
            if os.path.exists(meta) and os.path.exists(part):
                try:
                    st = json.load(open(meta))
                    if st.get('size') == size and st.get('url') == self.url: segs = st['segs']
                except Exception:
                    segs = None
            if segs is None:
                step = size // n
                segs = [[i * step, (size - 1) if i == n - 1 else (i + 1) * step - 1, i * step] for i in range(n)]
                with open(part, 'wb') as f: f.truncate(size)
            self.done = sum(sg[2] - sg[0] for sg in segs)
            self.state = 'downloading'
            self._parallel(final, part, meta, segs, size)
        else:
            self.state = 'downloading'
            self._single(final, part)
        if self._stop.is_set():
            return
        if os.path.exists(self.path): os.remove(self.path)
        os.replace(part, self.path)
        if os.path.exists(meta): os.remove(meta)
        self.state = 'done'; self.speed = 0

    def _speed_loop(self, alive):
        last, t0 = self.done, time.time()
        while alive() and not self._stop.is_set():
            time.sleep(0.5)
            now = time.time(); d = self.done
            self.speed = 0.7 * self.speed + 0.3 * ((d - last) / max(now - t0, 1e-3)); last, t0 = d, now

    def _parallel(self, url, part, meta, segs, size):
        lock = threading.Lock()
        errors = []

        def save_meta():
            with lock:
                json.dump({'url': self.url, 'size': size, 'segs': segs}, open(meta, 'w'))

        def worker(sg):
            tries = 0
            while sg[2] <= sg[1] and not self._stop.is_set():
                try:
                    with requests.get(url, headers=dict(UA, Range='bytes=%d-%d' % (sg[2], sg[1])), stream=True, timeout=30) as r:
                        if r.status_code != 206:
                            raise DownloadError('الخادم رفض التقسيم (%d)' % r.status_code)
                        with open(part, 'r+b') as f:
                            f.seek(sg[2])
                            for chunk in r.iter_content(CHUNK):
                                if self._stop.is_set(): break
                                f.write(chunk); sg[2] += len(chunk)
                                with self._lock: self.done += len(chunk)
                    tries = 0
                except Exception as e:  # noqa
                    tries += 1
                    if tries > 6:
                        errors.append(str(e)); return
                    time.sleep(min(2 * tries, 10))
                save_meta()
        threads = [threading.Thread(target=worker, args=(sg,), daemon=True) for sg in segs]
        for t in threads: t.start()
        sp = threading.Thread(target=self._speed_loop, args=(lambda: any(t.is_alive() for t in threads),), daemon=True)
        sp.start()
        last_save = time.time()
        while any(t.is_alive() for t in threads):
            time.sleep(0.3)
            if time.time() - last_save > 3: save_meta(); last_save = time.time()
        save_meta()
        if errors and not self._stop.is_set():
            raise DownloadError('فشل التنزيل: ' + errors[0][:200])

    def _single(self, url, part):
        sp_alive = [True]
        threading.Thread(target=self._speed_loop, args=(lambda: sp_alive[0],), daemon=True).start()
        start = os.path.getsize(part) if os.path.exists(part) else 0
        hdr = dict(UA, Range='bytes=%d-' % start) if start else UA
        try:
            with requests.get(url, headers=hdr, stream=True, timeout=30) as r:
                if r.status_code >= 400: raise DownloadError('الرابط لا يعمل (%d)' % r.status_code)
                mode = 'ab' if (start and r.status_code == 206) else 'wb'
                self.done = start if mode == 'ab' else 0
                if not self.total and r.headers.get('Content-Length', '').isdigit():
                    self.total = int(r.headers['Content-Length']) + self.done
                with open(part, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        if self._stop.is_set(): return
                        f.write(chunk); self.done += len(chunk)
        finally:
            sp_alive[0] = False

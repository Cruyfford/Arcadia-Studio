"""Talk to a PS3 running webMAN MOD: FTP upload (port 21) + web commands (port 80)."""
import ftplib
import os
import posixpath
import urllib.parse
import urllib.request

STORE_DIR = '/dev_hdd0/game/VIDESTORE'
PKG_DIR = '/dev_hdd0/packages'


class PS3:
    def __init__(self, ip, ftp_port=21, http_port=80, timeout=10):
        self.ip, self.ftp_port, self.http_port, self.timeout = ip.strip(), ftp_port, http_port, timeout

    # ---------- web commands
    def web(self, path):
        url = 'http://%s:%d%s' % (self.ip, self.http_port, urllib.parse.quote(path, safe='/?=&:'))
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return r.read()

    def test(self):
        """returns (ftp_ok, web_ok)"""
        ftp_ok = web_ok = False
        try:
            f = self._ftp(); f.quit(); ftp_ok = True
        except Exception:
            pass
        try:
            self.web('/'); web_ok = True
        except Exception:
            pass
        return ftp_ok, web_ok

    def install_pkg(self, remote_path):
        """webMAN: /install.ps3<pkg-path> installs a pkg already on the console"""
        return self.web('/install.ps3' + remote_path)

    def notify(self, text):
        try: self.web('/popup.ps3/' + text)
        except Exception: pass

    # ---------- FTP
    def _ftp(self):
        f = ftplib.FTP()
        f.connect(self.ip, self.ftp_port, timeout=self.timeout)
        f.login()           # webMAN accepts anonymous login
        f.set_pasv(True)
        return f

    @staticmethod
    def _mkdirs(f, remote_dir):
        parts = [p for p in remote_dir.split('/') if p]
        cur = ''
        for p in parts:
            cur += '/' + p
            try: f.mkd(cur)
            except ftplib.error_perm: pass

    def upload(self, local, remote, progress=None, f=None):
        own = f is None
        f = f or self._ftp()
        self._mkdirs(f, posixpath.dirname(remote))
        total = os.path.getsize(local); sent = [0]

        def cb(chunk):
            sent[0] += len(chunk)
            if progress: progress(sent[0], total)
        with open(local, 'rb') as fh:
            f.storbinary('STOR ' + remote, fh, blocksize=1 << 20, callback=cb)
        if own: f.quit()

    def send_pkg(self, local_pkg, install=True, progress=None):
        remote = PKG_DIR + '/' + os.path.basename(local_pkg).replace(' ', '_')
        self.upload(local_pkg, remote, progress)
        if install:
            self.install_pkg(remote)
        return remote

    def sync_files(self, root, rel_files, progress=None):
        """upload changed store files straight into /dev_hdd0/game/VIDESTORE (no reinstall)"""
        rel_files = [r for r in rel_files if os.path.isfile(os.path.join(root, r))]
        total = sum(os.path.getsize(os.path.join(root, r)) for r in rel_files) or 1
        done = [0]
        f = self._ftp()
        try:
            for r in rel_files:
                base = done[0]
                self.upload(os.path.join(root, r), STORE_DIR + '/' + r.replace(os.sep, '/'),
                            (lambda a, b, base=base: progress and progress(base + a, total)), f)
                done[0] += os.path.getsize(os.path.join(root, r))
        finally:
            f.quit()
        return len(rel_files)


    # ---------- file manager
    def listdir(self, path, f=None):
        """-> list of dict(name, dir, size, mtime) ; tries MLSD then LIST"""
        own = f is None; f = f or self._ftp(); out = []
        try:
            try:
                for name, facts in f.mlsd(path, facts=['type', 'size', 'modify']):
                    if name in ('.', '..'): continue
                    t = facts.get('type', 'file'); md = facts.get('modify', '')
                    if len(md) >= 12 and md[:12].isdigit():
                        facts['modify'] = '%s-%s-%s %s:%s' % (md[:4], md[4:6], md[6:8], md[8:10], md[10:12])
                    out.append({'name': name, 'dir': t in ('dir', 'cdir', 'pdir'), 'size': int(facts.get('size', 0) or 0),
                                'mtime': facts.get('modify', '')})
            except (ftplib.error_perm, ftplib.error_reply):
                lines = []; f.retrlines('LIST ' + path, lines.append)
                for ln in lines:
                    parts = ln.split(None, 8)
                    if len(parts) < 9 or parts[8] in ('.', '..'): continue
                    out.append({'name': parts[8], 'dir': ln.startswith('d'), 'size': int(parts[4]) if parts[4].isdigit() else 0,
                                'mtime': ' '.join(parts[5:8])})
        finally:
            if own: f.quit()
        return sorted(out, key=lambda x: (not x['dir'], x['name'].lower()))

    def download(self, remote, local, progress=None, f=None):
        own = f is None; f = f or self._ftp()
        try:
            try: total = f.size(remote) or 0
            except Exception: total = 0  # noqa
            got = [0]
            os.makedirs(os.path.dirname(local) or '.', exist_ok=True)
            with open(local, 'wb') as fh:
                def cb(chunk):
                    fh.write(chunk); got[0] += len(chunk)
                    if progress: progress(got[0], total or got[0])
                f.retrbinary('RETR ' + remote, cb, blocksize=1 << 20)
        finally:
            if own: f.quit()

    def download_tree(self, remote, local, progress=None):
        """files or whole folders"""
        f = self._ftp(); jobs = []
        try:
            def walk(r, l):
                for e in self.listdir(r, f):
                    rp, lp = posixpath.join(r, e['name']), os.path.join(l, e['name'])
                    if e['dir']: walk(rp, lp)
                    else: jobs.append((rp, lp, e['size']))
            walk(remote, local)
            total = sum(j[2] for j in jobs) or 1; done = [0]
            for rp, lp, sz in jobs:
                base = done[0]
                self.download(rp, lp, lambda a, b, base=base: progress and progress(base + a, total), f)
                done[0] += sz
        finally:
            f.quit()
        return len(jobs)

    def upload_tree(self, local, remote_dir, progress=None):
        f = self._ftp(); jobs = []
        try:
            if os.path.isdir(local):
                root = os.path.dirname(os.path.abspath(local))
                for base, _, files in os.walk(local):
                    for fn in files:
                        lp = os.path.join(base, fn)
                        jobs.append((lp, posixpath.join(remote_dir, os.path.relpath(lp, root).replace(os.sep, '/'))))
            else:
                jobs.append((local, posixpath.join(remote_dir, os.path.basename(local))))
            total = sum(os.path.getsize(j[0]) for j in jobs) or 1; done = [0]
            for lp, rp in jobs:
                base = done[0]
                self.upload(lp, rp, lambda a, b, base=base: progress and progress(base + a, total), f)
                done[0] += os.path.getsize(lp)
        finally:
            f.quit()
        return len(jobs)

    def delete(self, remote, is_dir, f=None):
        own = f is None; f = f or self._ftp()
        try:
            if is_dir:
                for e in self.listdir(remote, f):
                    self.delete(posixpath.join(remote, e['name']), e['dir'], f)
                f.rmd(remote)
            else:
                f.delete(remote)
        finally:
            if own: f.quit()

    def rename(self, src, dst):
        f = self._ftp()
        try: f.rename(src, dst)
        finally: f.quit()

    def mkdir(self, path):
        f = self._ftp()
        try: self._mkdirs(f, path)
        finally: f.quit()

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

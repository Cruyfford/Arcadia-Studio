"""PS1 (bin/cue, ccd, img) -> PS3 PSone Classic PKG, using the bundled pop-fe engine (LGPL-2.1,
https://github.com/sahlberg/pop-fe). External helper tools pop-fe would normally shell out to
(sign3, PSL1GHT pkg.py, wget) are replaced in-process so it also works on Windows."""
import contextlib
import io
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from .pkg import psl1ght_pack

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engines', 'popfe')


class ConvertError(Exception):
    pass


def _sign3(path):
    sys.path.insert(0, ENGINE)
    import sign3
    with open(path, 'rb') as f:
        sig = sign3.calc_sign(f.read())
    with open(path, 'ab') as f:
        f.write(sig)


def _fake_call(real):
    def call(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)):
            s = ' '.join(map(str, cmd))
            if 'sign3' in s:
                _sign3(cmd[-1]); return 0
            if 'pkg.py' in s or cmd[0] == 'pkg.exe':
                i = list(cmd).index('-c')
                psl1ght_pack(cmd[i + 2], cmd[i + 1], cmd[i + 3]); return 0
            if cmd[0] in ('wget', 'wget.exe'):
                url = [c for c in cmd if str(c).startswith('http')][0]
                out = cmd[cmd.index('-O') + 1]
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as r, open(out, 'wb') as f:
                    f.write(r.read())
                return 0
            if cmd[0] in ('git', 'pip', 'make', 'cmake'):
                return 0
        return real(cmd, *a, **k)
    return call


def _fake_run(real_run):
    call = _fake_call(lambda cmd, *a, **k: real_run(cmd, *a, **k))

    def run(cmd, *a, **k):
        r = call(cmd, *a, **k)
        if isinstance(r, int):
            return subprocess.CompletedProcess(cmd, r, b'', b'')
        return r
    return run


class _Exit(Exception):
    pass


def convert_ps1(discs, out_pkg, title=None, cover=None, log=print):
    """discs: list of .cue/.ccd paths (one per disc). Returns out_pkg path."""
    discs = [os.path.abspath(d) for d in discs]
    out_pkg = os.path.abspath(out_pkg)
    if cover: cover = os.path.abspath(cover)
    work = tempfile.mkdtemp(prefix='arcadia_ps1_')
    eng = os.path.join(work, 'engine')
    shutil.copytree(ENGINE, eng)
    argv = ['pop-fe.py', '--ps3-pkg=' + out_pkg]
    if title: argv.append('--title=' + title)
    if cover: argv.append('--cover=' + cover)
    if len(discs) > 1: argv.append('--swap-discs')
    argv += discs
    saved = (sys.argv, os.getcwd(), subprocess.call, subprocess.run, os._exit, sys.exit, list(sys.path))
    buf = io.StringIO()

    def _exit(code=0):
        raise _Exit(code)
    try:
        os.chdir(eng)
        sys.argv = argv
        sys.path.insert(0, eng)
        for m in ('sign3', 'cue', 'popstation', 'gamedb', 'db', 'bchunk', 'document', 'ppf', 'riff', 'vmp', 'make_isoedat'):
            sys.modules.pop(m, None)
        subprocess.call = _fake_call(saved[2])
        subprocess.run = _fake_run(saved[3])
        os._exit = _exit
        with contextlib.redirect_stdout(buf):
            try:
                runpy.run_path(os.path.join(eng, 'pop-fe.py'), run_name='__main__')
            except (_Exit, SystemExit) as e:
                code = e.args[0] if e.args else 0
                if code not in (0, None):
                    raise ConvertError('pop-fe توقف: ' + buf.getvalue()[-600:])
    finally:
        sys.argv, cwd, subprocess.call, subprocess.run, os._exit, sys.exit, sys.path[:] = saved
        os.chdir(cwd)
        for line in buf.getvalue().splitlines():
            log(line)
        shutil.rmtree(work, ignore_errors=True)
    if not os.path.exists(out_pkg):
        raise ConvertError('لم يتم إنشاء الحزمة:\n' + buf.getvalue()[-800:])
    return out_pkg


def content_id_of(pkg_path):
    from .pkg import PkgReader
    return PkgReader(pkg_path).content_id

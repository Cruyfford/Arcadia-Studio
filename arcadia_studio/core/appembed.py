"""Put the ARCADIA PS3 app inside the store PKG (VIDESTORE): one PKG holds the XMB store and the program.

- downloads the latest app EBOOT.BIN (built by CI with TITLE_ID VIDESTORE and the store's content id)
- writes it to USRDIR/EBOOT.BIN of the build workspace
- makes PARAM.SFO bootable (CATEGORY HG, BOOTABLE 1) so pressing X on the store icon starts the app
"""
import os
import struct
import urllib.request

from arcadia_studio.core.sfo import set_sfo_string

EBOOT_URL = 'https://github.com/Cruyfford/Arcadia-Studio/releases/download/ps3app-latest/EBOOT.BIN'


def set_sfo_int(data, key, value):
    data = bytearray(data)
    _, _, kt, dt, n = struct.unpack('<IIIII', data[:20])
    for i in range(n):
        e = 20 + i * 16
        ko, fmt, ln, mx, do = struct.unpack('<HHIII', data[e:e + 16])
        if data[kt + ko:data.index(b'\0', kt + ko)].decode() == key and fmt == 0x0404:
            struct.pack_into('<I', data, dt + do, value)
            return bytes(data)
    raise KeyError(key)


def make_bootable(sfo):
    sfo = set_sfo_string(sfo, 'CATEGORY', 'HG')
    sfo = set_sfo_int(sfo, 'BOOTABLE', 1)
    sfo = set_sfo_int(sfo, 'ATTRIBUTE', 0)
    return sfo


def fetch_eboot(cache_dir, progress=None):
    """latest EBOOT.BIN from the release; falls back to the cached copy when offline"""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, 'EBOOT.BIN')
    try:
        req = urllib.request.Request(EBOOT_URL, headers={'User-Agent': 'ArcadiaStudio'})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if data[:4] == b'SCE\0':
            with open(path + '.tmp', 'wb') as f: f.write(data)
            os.replace(path + '.tmp', path)
    except Exception:  # noqa  offline: use the cached one
        pass
    return path if os.path.exists(path) else None


def embed_app(roots, sfo_bytes, eboot_path):
    """roots: build workspace folder(s) - the first one overrides the rest. sfo_bytes: current PARAM.SFO."""
    top = roots[0] if isinstance(roots, (list, tuple)) else roots
    os.makedirs(os.path.join(top, 'USRDIR'), exist_ok=True)
    with open(eboot_path, 'rb') as f: eb = f.read()
    with open(os.path.join(top, 'USRDIR', 'EBOOT.BIN'), 'wb') as f: f.write(eb)
    with open(os.path.join(top, 'PARAM.SFO'), 'wb') as f: f.write(make_bootable(sfo_bytes))

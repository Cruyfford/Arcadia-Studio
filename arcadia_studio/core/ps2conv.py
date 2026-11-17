"""PS2 ISO/BIN -> PS2 Classic (ISO.BIN.ENC) for PS3, Python port of ps2classic (flatz/2013 USER,
GPLv3). Uses the universal PS2 Classics Placeholder license that HEN/CFW users activate once:
content id 2P0001-PS2U10000_00-0000111122223333."""
import hashlib
import os
import re
import struct

from Crypto.Cipher import AES
from Crypto.Hash import CMAC

PLACEHOLDER_CID = '2P0001-PS2U10000_00-0000111122223333'
PLACEHOLDER_KLIC = bytes.fromhex('E4E54FD67C16C316F47829A30484D843')

_KEY_CEX_META = bytes([0x38, 0x9D, 0xCB, 0xA5, 0x20, 0x3C, 0x81, 0x59, 0xEC, 0xF9, 0x4C, 0x93, 0x93, 0x16, 0x4C, 0xC9])
_KEY_CEX_DATA = bytes([0x10, 0x17, 0x82, 0x34, 0x63, 0xF4, 0x68, 0xC1, 0xAA, 0x41, 0xD7, 0x00, 0xB1, 0x40, 0xF2, 0x57])
_NPD_OMAC_KEY2 = bytes([0x6B, 0xA5, 0x29, 0x76, 0xEF, 0xDA, 0x16, 0xEF, 0x3C, 0x33, 0x9F, 0xB2, 0x97, 0x1E, 0x25, 0x6B])
_NPD_OMAC_KEY3 = bytes([0x9B, 0x51, 0x5F, 0xEA, 0xCF, 0x75, 0x06, 0x49, 0x81, 0xAA, 0x60, 0x4D, 0x91, 0xA5, 0x4E, 0x97])
_NPD_KEK = bytes([0x72, 0xF9, 0x90, 0x78, 0x8F, 0x9C, 0xFF, 0x74, 0x57, 0x25, 0xF0, 0x8E, 0x4C, 0x12, 0x83, 0x87])
SEG = 0x4000
META_ENTRY = 0x20
CHILDREN = SEG // META_ENTRY          # 0x200 data segments per meta segment
ZERO_IV = bytes(16)


def _cmac(key, data):
    return CMAC.new(key, ciphermod=AES, msg=data).digest()


def _cbc_enc(key, data):
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(data)


def build_header(cid, filename, iso_size, npd_hash1=None):
    h = bytearray(SEG)
    struct.pack_into('>IHHII', h, 0, 0x50533200, 1, 1, 2, 1)
    struct.pack_into('>I', h, 0x84, SEG)
    struct.pack_into('>Q', h, 0x88, iso_size)
    h[0x10:0x40] = cid.encode()[:0x30].ljust(0x30, b'\0')
    h[0x40:0x50] = npd_hash1 if npd_hash1 is not None else os.urandom(16)
    h[0x50:0x60] = _cmac(_NPD_OMAC_KEY3, bytes(h[0x10:0x40]) + filename.encode())
    h[0x60:0x70] = _cmac(bytes(a ^ b for a, b in zip(_NPD_KEK, _NPD_OMAC_KEY2)), bytes(h[:0x60]))
    return bytes(h)


# ---------------- image preparation
def bin_to_iso(bin_path, iso_path, progress=None):
    """raw 2352-byte CD image (MODE1/MODE2) -> 2048-byte ISO"""
    size = os.path.getsize(bin_path)
    with open(bin_path, 'rb') as f, open(iso_path, 'wb') as o:
        done = 0
        while True:
            s = f.read(2352 * 512)
            if not s: break
            out = bytearray()
            for i in range(0, len(s) - 2351, 2352):
                sec = s[i:i + 2352]
                off = 16 if sec[15] == 1 else 24
                out += sec[off:off + 2048]
            o.write(out); done += len(s)
            if progress: progress(done, size)
    return iso_path


def resolve_input(path):
    """accept .iso, .bin or .cue; returns (image_path, is_raw_cd)"""
    if path.lower().endswith('.cue'):
        txt = open(path, encoding='utf-8', errors='replace').read()
        m = re.search(r'FILE\s+"([^"]+)"', txt) or re.search(r'FILE\s+(\S+)', txt)
        path = os.path.join(os.path.dirname(path), m.group(1))
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(16)
    raw = head[:12] == b'\x00' + b'\xff' * 10 + b'\x00' and size % 2352 == 0
    return path, raw


def game_id(iso_path):
    """read SLUS_xxx.xx from SYSTEM.CNF (best effort, scans first 2 MB)"""
    with open(iso_path, 'rb') as f:
        blob = f.read(2 << 20)
    m = re.search(rb'cdrom0?:\\?([A-Z]{4})[_-](\d{3})\.(\d{2})', blob)
    return '%s-%s%s' % (m[1].decode(), m[2].decode(), m[3].decode()) if m else None


def add_limg(iso_path):
    """append the LIMG segment ps2_netemu needs (pads to 0x4000 first)"""
    size = os.path.getsize(iso_path)
    with open(iso_path, 'r+b') as f:
        if size % SEG:
            f.seek(0, 2); f.write(bytes(SEG - size % SEG)); size += SEG - size % SEG
        f.seek(size - SEG); last = f.read(16)
        if last[:4] == b'LIMG':
            return
        blk = bytearray(SEG)
        blk[0:4] = b'LIMG'; struct.pack_into('>III', blk, 4, 1, size // 0x800, 0x800)
        f.seek(0, 2); f.write(blk)


# ---------------- encryption
def encrypt_image(iso_path, out_path, cid=PLACEHOLDER_CID, klic=PLACEHOLDER_KLIC, real_name='ISO.BIN.ENC',
                  progress=None, npd_hash1=None):
    data_key = _cbc_enc(_KEY_CEX_DATA, klic)
    meta_key = _cbc_enc(_KEY_CEX_META, klic)
    size = os.path.getsize(iso_path)
    seg_no = 0
    with open(iso_path, 'rb') as f, open(out_path, 'wb') as o:
        o.write(build_header(cid, real_name, size, npd_hash1))
        done = 0
        while True:
            chunk = f.read(SEG * CHILDREN)
            if not chunk: break
            n = (len(chunk) + SEG - 1) // SEG
            chunk = chunk.ljust(n * SEG, b'\0')
            meta = bytearray(SEG)
            enc = bytearray()
            for i in range(n):
                e = _cbc_enc(data_key, chunk[i * SEG:(i + 1) * SEG])
                enc += e
                meta[i * META_ENTRY:i * META_ENTRY + 20] = hashlib.sha1(e).digest()
                struct.pack_into('>I', meta, i * META_ENTRY + 0x14, seg_no); seg_no += 1
            o.write(_cbc_enc(meta_key, bytes(meta)))
            o.write(enc)
            done += len(chunk)
            if progress: progress(min(done, size), size)
    return out_path


def convert_ps2(src, out_enc, progress=None, log=print, tmp_dir=None):
    """iso/bin/cue -> NAME.BIN.ENC. Returns (out_path, game_id)."""
    img, raw = resolve_input(src)
    tmp_dir = tmp_dir or os.path.dirname(os.path.abspath(out_enc))
    work = os.path.join(tmp_dir, '_arcadia_ps2_tmp.iso')
    if raw:
        log('تحويل صورة CD الخام (2352) إلى ISO...')
        bin_to_iso(img, work, progress)
    else:
        log('نسخ ملف ISO...')
        with open(img, 'rb') as a, open(work, 'wb') as b:
            while True:
                c = a.read(16 << 20)
                if not c: break
                b.write(c)
    gid = game_id(work)
    log('رقم اللعبة: %s' % (gid or 'غير معروف'))
    add_limg(work)
    log('إضافة قطاع LIMG... تم')
    log('تشفير الصورة إلى PS2 Classic...')
    encrypt_image(work, out_enc, progress=progress)
    os.remove(work)
    return out_enc, gid

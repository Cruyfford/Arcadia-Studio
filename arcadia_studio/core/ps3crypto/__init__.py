"""PS3 file decryption (SELF/SPRX -> ELF, EDAT/SDAT -> data), Python port of RPCS3's
rpcs3/Crypto (unself.cpp, unedat.cpp, key_vault.cpp) — GPLv2.
NPDRM content bought on PSN needs the user's own license file (.rap)."""
import hashlib
import hmac
import os
import struct
import zlib

from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from Crypto.Util import Counter

from . import keys as K


class DecryptError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _cbc_dec(key, iv, data):
    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)


def _ecb_enc(key, data):
    return AES.new(key, AES.MODE_ECB).encrypt(data)


def _ctr(key, iv, data):
    return AES.new(key, AES.MODE_CTR, counter=Counter.new(128, initial_value=int.from_bytes(iv, 'big'))).decrypt(data)


def _cmac(key, data):
    return CMAC.new(key, ciphermod=AES, msg=data).digest()


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def rap_to_rif(rap):
    """license .rap (16 bytes) -> rif key (klicensee base)"""
    key = bytearray(_cbc_dec(K.RAP_KEY, bytes(16), rap[:16]))
    pbox, e1, e2 = K.RAP_PBOX, K.RAP_E1, K.RAP_E2
    for _ in range(5):
        for i in range(16):
            p = pbox[i]; key[p] ^= e1[p]
        for i in range(15, 0, -1):
            p, pp = pbox[i], pbox[i - 1]; key[p] ^= key[pp]
        o = 0
        for i in range(16):
            p = pbox[i]
            kc = (key[p] - o) & 0xFF
            ec2 = e2[p]
            if o != 1 or kc != 0xFF:
                o = 1 if kc < ec2 else 0
                key[p] = (kc - ec2) & 0xFF
            elif kc == 0xFF:
                key[p] = (kc - ec2) & 0xFF
            else:
                key[p] = kc
    return bytes(key)


def rif_to_rap(rif_key):
    """inverse of rap_to_rif: rif key (16 bytes) -> .rap file contents"""
    key = bytearray(rif_key[:16])
    pbox, e1, e2 = K.RAP_PBOX, K.RAP_E1, K.RAP_E2
    for _ in range(5):
        c = 0                                   # undo the borrow-chain subtraction (add with carry)
        for i in range(16):
            p = pbox[i]
            t = key[p] + e2[p] + c
            key[p] = t & 0xFF; c = t >> 8
        for i in range(1, 16):                  # undo the xor chain
            p, pp = pbox[i], pbox[i - 1]; key[p] ^= key[pp]
        for i in range(16):
            p = pbox[i]; key[p] ^= e1[p]
    return AES.new(K.RAP_KEY, AES.MODE_ECB).encrypt(bytes(key))


def _ecb_dec(key, block):
    return AES.new(key, AES.MODE_ECB).decrypt(block)


def parse_idps(text_or_bytes):
    """IDPS from hex text ('00000001008...') or a dump file's bytes"""
    if isinstance(text_or_bytes, (bytes, bytearray)):
        b = bytes(text_or_bytes)
        if len(b) >= 32 and all(c in b'0123456789abcdefABCDEF \r\n' for c in b[:40]):
            return parse_idps(b.decode('ascii', 'ignore'))
        if len(b) < 16: raise ValueError('ملف IDPS قصير')
        return b[:16]
    h = ''.join(ch for ch in text_or_bytes if ch in '0123456789abcdefABCDEF')
    if len(h) != 32: raise ValueError('رقم IDPS لازم يكون 32 خانة hex (16 بايت)')
    return bytes.fromhex(h)


def parse_rif(data):
    if len(data) < 0x60: raise ValueError('ملف rif غير صالح')
    cid = data[0x10:0x40].split(b'\0')[0].decode('ascii', 'replace')
    return {'account_id': data[8:16], 'content_id': cid, 'enc_index': data[0x40:0x50], 'enc_key': data[0x50:0x60]}


def rif_key_from(rif_data, act_dat, idps):
    """own license -> rif key, using this console's act.dat and IDPS (same as the PS3 does)"""
    r = parse_rif(rif_data)
    idx = struct.unpack('>I', _ecb_dec(K.NP_RIF_KEY, r['enc_index'])[12:16])[0]
    if idx >= 128: raise ValueError('فهرس مفتاح غير صالح (%d)' % idx)
    idps_key = AES.new(idps[:16], AES.MODE_ECB).encrypt(K.NP_IDPS)
    act_key = _ecb_dec(idps_key, act_dat[0x10 + idx * 16:0x20 + idx * 16])
    return r['content_id'], _ecb_dec(act_key, r['enc_key']), r


def find_rap(content_id, rap_dirs):
    for d in rap_dirs or []:
        if not d or not os.path.isdir(d): continue
        for name in os.listdir(d):
            if name.lower() == (content_id + '.rap').lower():
                return open(os.path.join(d, name), 'rb').read(16)
    return None


# ------------------------------------------------------------------ SELF
KEY_TYPES = {1: 'LV0', 2: 'LV1', 3: 'LV2', 4: 'APP', 5: 'ISO', 6: 'LDR', 7: 'UNK7', 8: 'NPDRM'}


def find_self_key(ptype, revision, version):
    cand = [k for k in K.SELF_KEYS if k[3] == ptype]
    if ptype in (1, 6):                                  # LV0 / LDR: single key
        k = cand[0] if cand else None
    elif ptype in (2, 3, 7):                             # by version
        k = next((k for k in cand if k[0] <= version <= k[1]), None)
    elif ptype == 5:                                     # ISO: version + revision
        k = next((k for k in cand if k[0] <= version <= k[1] and k[2] == revision), None)
    else:                                                # APP / NPDRM: revision
        k = next((k for k in cand if k[2] == revision), None)
    if not k:
        raise DecryptError('لا يوجد مفتاح لهذا النوع (type=%s rev=0x%X ver=0x%X)' % (KEY_TYPES.get(ptype, ptype), revision, version))
    return bytes.fromhex(k[4]), bytes.fromhex(k[5])


def self_info(data):
    if data[:4] != b'SCE\x00':
        raise DecryptError('ليس ملف SELF/SPRX (لا يبدأ بـ SCE)')
    magic, hver, flags, stype, meta, hsize, esize = struct.unpack('>IIHHIQQ', data[:0x20])
    ext = struct.unpack('>10Q', data[0x20:0x70])
    (ext_ver, pid_off, ehdr_off, phdr_off, shdr_off, seg_off, ver_off, sup_off, sup_size, _) = ext
    auth_id, vendor, ptype, sceversion, _ = struct.unpack('>QIIQQ', data[pid_off:pid_off + 0x20])
    elf32 = data[ehdr_off + 4] == 1
    if elf32:
        e = struct.unpack('>16sHHIIIIIHHHHHH', data[ehdr_off:ehdr_off + 52])
        phoff, shoff, phentsize, phnum, shentsize, shnum = e[5], e[6], e[9], e[10], e[11], e[12]
    else:
        e = struct.unpack('>16sHHIQQQIHHHHHH', data[ehdr_off:ehdr_off + 64])
        phoff, shoff, phentsize, phnum, shentsize, shnum = e[5], e[6], e[9], e[10], e[11], e[12]
    segs = [struct.unpack('>QQIIQ', data[seg_off + i * 32:seg_off + i * 32 + 32]) for i in range(phnum)]
    npd = None
    o, end = sup_off, sup_off + sup_size
    while o < end:
        t, size, nxt = struct.unpack('>IIQ', data[o:o + 16])
        if t == 3:
            b = data[o + 16:o + 16 + 0x80]
            npd = dict(magic=b[:4], version=struct.unpack('>i', b[4:8])[0], license=struct.unpack('>i', b[8:12])[0],
                       type=struct.unpack('>i', b[12:16])[0], content_id=b[16:64].rstrip(b'\0').decode('ascii', 'replace'),
                       digest=b[64:80], title_hash=b[80:96], dev_hash=b[96:112])
        if size == 0: break
        o += size
    return dict(flags=flags, meta=meta, hsize=hsize, ptype=ptype, sceversion=sceversion, auth_id=auth_id, elf32=elf32,
                ehdr_off=ehdr_off, phdr_off=phdr_off, shdr_off=shdr_off, phoff=phoff, shoff=shoff, phentsize=phentsize,
                phnum=phnum, shentsize=shentsize, shnum=shnum, segs=segs, npd=npd,
                kind=KEY_TYPES.get(ptype, '?'), debug=bool(flags & 0x8000))


def decrypt_self(data, klic=None, rap_dirs=None):
    """SELF/SPRX bytes -> ELF bytes"""
    inf = self_info(data)
    elf32 = inf['elf32']
    ph_fmt, ph_size = ('>IIIIIIII', 32) if elf32 else ('>IIQQQQQQ', 56)
    phdrs = [struct.unpack(ph_fmt, data[inf['phdr_off'] + i * ph_size:inf['phdr_off'] + (i + 1) * ph_size]) for i in range(inf['phnum'])]

    def p_off(p): return p[1] if elf32 else p[2]

    def p_filesz(p): return p[4] if elf32 else p[5]

    sections = []          # (type, prog_idx, compressed, bytes)
    if inf['debug']:
        for i, (off, size, comp, _, enc) in enumerate(inf['segs']):
            if size == 0 or off < inf['hsize']: continue
            sections.append((2, i, comp, data[off:off + size]))
    else:
        mo = inf['meta'] + 0x20
        meta_info = data[mo:mo + 0x40]
        erk, riv = find_self_key(inf['ptype'], inf['flags'], inf['sceversion'])
        npd = inf['npd']
        if npd:                                          # NPDRM layer
            if npd['license'] == 3:
                k = klic or K.NP_KLIC_FREE
            elif npd['license'] in (1, 2):
                rap = find_rap(npd['content_id'], rap_dirs)
                if klic:
                    k = klic
                elif rap:
                    k = rap_to_rif(rap)
                else:
                    raise DecryptError('هذا الملف يحتاج ملف الرخصة \u200e%s.rap\u200f (لعبة مشتراة)' % npd['content_id'])
            else:
                raise DecryptError('نوع رخصة NPDRM غير معروف')
            k = AES.new(K.NP_KLIC_KEY, AES.MODE_ECB).decrypt(k)
            meta_info = _cbc_dec(k, bytes(16), meta_info)
        meta_info = _cbc_dec(erk, riv, meta_info)
        key, key_pad, iv, iv_pad = meta_info[:16], meta_info[16:32], meta_info[32:48], meta_info[48:64]
        if key_pad[0] or iv_pad[0]:
            raise DecryptError('فشل فك تشفير بيانات الملف (مفتاح أو رخصة غير صحيحة)')
        hdrs = _ctr(key, iv, data[mo + 0x40:inf['hsize']])
        _, _, sec_count, key_count = struct.unpack('>QIII', hdrs[:20])
        shdrs = [struct.unpack('>QQIIIIIIII', hdrs[0x20 + i * 0x30:0x20 + (i + 1) * 0x30]) for i in range(sec_count)]
        dk = hdrs[0x20 + sec_count * 0x30:0x20 + sec_count * 0x30 + key_count * 16]
        for (off, size, typ, pidx, hashed, sha_idx, enc, kidx, ividx, comp) in shdrs:
            if enc == 3 and kidx < key_count and ividx < key_count:
                buf = _ctr(dk[kidx * 16:kidx * 16 + 16], dk[ividx * 16:ividx * 16 + 16], data[off:off + size])
            elif enc == 1:
                buf = data[off:off + size]
            else:
                continue
            sections.append((typ, pidx, comp, buf))
    # build ELF
    ehsize = 52 if elf32 else 64
    out = bytearray()

    def put(off, b):
        if len(out) < off + len(b): out.extend(b'\0' * (off + len(b) - len(out)))
        out[off:off + len(b)] = b
    put(0, data[inf['ehdr_off']:inf['ehdr_off'] + ehsize])
    for typ, pidx, comp, buf in sections:
        if typ != 2: continue
        ph = phdrs[pidx]
        if comp == 2:
            buf = zlib.decompress(buf)[:p_filesz(ph)]
        put(p_off(ph), buf)
    if inf['shdr_off'] and inf['shnum']:
        put(inf['shoff'], data[inf['shdr_off']:inf['shdr_off'] + inf['shnum'] * inf['shentsize']])
    if inf['phnum']:
        put(inf['phoff'], data[inf['phdr_off']:inf['phdr_off'] + inf['phnum'] * ph_size])
    return bytes(out)


# ------------------------------------------------------------------ EDAT / SDAT
EDAT_COMPRESSED, EDAT_0x02, EDAT_ENC_KEY, EDAT_0x10, EDAT_0x20, SDAT_FLAG, EDAT_DEBUG = \
    0x1, 0x2, 0x8, 0x10, 0x20, 0x01000000, 0x80000000
KLIC_MODES = {'free': 'NP_KLIC_FREE', 'psx': 'NP_PSX_KEY', 'psp1': 'NP_PSP_KEY_1', 'psp2': 'NP_PSP_KEY_2',
              'omac2': 'NP_OMAC_KEY_2', 'omac3': 'NP_OMAC_KEY_3', 'klic': 'NP_KLIC_KEY'}


def edat_info(data):
    if data[:4] != b'NPD\x00':
        raise DecryptError('ليس ملف EDAT/SDAT (لا يبدأ بـ NPD)')
    h = data[:0x80]
    npd = dict(version=struct.unpack('>i', h[4:8])[0], license=struct.unpack('>i', h[8:12])[0],
               type=struct.unpack('>i', h[12:16])[0], content_id=h[16:64].rstrip(b'\0').decode('ascii', 'replace'),
               digest=h[64:80], title_hash=h[80:96], dev_hash=h[96:112], raw=h)
    flags, block, fsize = struct.unpack('>iIQ', data[0x80:0x90])
    flags &= 0xFFFFFFFF
    return npd, dict(flags=flags, block_size=block, file_size=fsize, sdat=bool(flags & SDAT_FLAG))


def _valid_klic(klic, npd):
    if (npd['license'] & 3) != 3: return True
    return _cmac(_xor(klic, K.NP_OMAC_KEY_2), npd['raw'][:0x60]) == npd['dev_hash']


def edat_key(data, klic=None, rap_dirs=None):
    npd, ed = edat_info(data)
    if ed['sdat']:
        return _xor(npd['dev_hash'], K.SDAT_KEY), 'SDAT'
    if ed['flags'] & EDAT_DEBUG:
        return bytes(16), 'debug'
    tried = []
    if klic: tried.append(('مفتاح مخصص', klic))
    if (npd['license'] & 3) == 3:
        for name, attr in KLIC_MODES.items():
            tried.append((name, getattr(K, attr)))
        for label, k in tried:
            if _valid_klic(k, npd):
                return k, label
        raise DecryptError('لم يُعرف مفتاح هذا الملف (رخصة مجانية لكن klicensee غير معروف) — أدخل المفتاح يدوياً')
    rap = find_rap(npd['content_id'], rap_dirs)
    if klic: return klic, 'مفتاح مخصص'
    if rap: return rap_to_rif(rap), 'RAP'
    raise DecryptError('هذا الملف يحتاج ملف الرخصة \u200e%s.rap\u200f' % npd['content_id'])


def _gen_key(crypto_mode, version, key, iv):
    m = crypto_mode & 0xF0000000
    if m == 0x10000000:
        return _cbc_dec(K.EDAT_KEY_1 if version else K.EDAT_KEY_0, K.EDAT_IV, key), iv
    if m == 0x20000000:
        return (K.EDAT_KEY_1 if version else K.EDAT_KEY_0), K.EDAT_IV
    return key, iv


def decrypt_edat(data, klic=None, rap_dirs=None, verify=False):
    npd, ed = edat_info(data)
    key, how = edat_key(data, klic, rap_dirs)
    flags, bs, fsize = ed['flags'], ed['block_size'], ed['file_size']
    if flags & EDAT_COMPRESSED:
        raise DecryptError('ملفات EDAT المضغوطة غير مدعومة بعد')
    total = (fsize + bs - 1) // bs if bs else 0
    meta_size = 0x20 if (flags & EDAT_0x20) else 0x10
    out = bytearray()
    for b in range(total):
        if flags & EDAT_0x20:
            mso = 0x100 + b * (meta_size + bs)
            md = data[mso:mso + 0x20]
            off = mso + 0x20
        else:
            off = 0x100 + b * bs + total * meta_size
        length = bs if not (b == total - 1 and fsize % bs) else fsize % bs
        plen = (length + 15) & ~15
        enc = data[off:off + plen]
        src = bytes(12) if npd['version'] <= 1 else npd['dev_hash'][:12]
        bkey = _ecb_enc(key, src + struct.pack('>i', b))
        if flags & EDAT_DEBUG:
            dec = enc
        else:
            crypto_mode = 0x2 if not (flags & EDAT_0x02) else 0x1
            if flags & EDAT_ENC_KEY: crypto_mode |= 0x10000000
            iv = bytes(16) if npd['version'] <= 1 else npd['digest']
            k2, iv2 = _gen_key(crypto_mode, npd['version'] == 4, bkey, iv)
            dec = _cbc_dec(k2, iv2, enc) if (crypto_mode & 0xFF) == 2 else enc
        out += dec[:length]
    return bytes(out[:fsize]), how


# ------------------------------------------------------------------ dispatch
def detect(path):
    with open(path, 'rb') as f:
        head = f.read(4)
    if head == b'SCE\x00': return 'self'
    if head == b'NPD\x00': return 'edat'
    return None


def decrypt_file(path, out_path=None, klic=None, rap_dirs=None):
    data = open(path, 'rb').read()
    kind = detect(path)
    if kind == 'self':
        res, how = decrypt_self(data, klic, rap_dirs), 'SELF'
        out_path = out_path or (os.path.splitext(path)[0] + ('.prx' if path.lower().endswith('.sprx') else '.elf'))
    elif kind == 'edat':
        res, how = decrypt_edat(data, klic, rap_dirs)
        base = path[:-5] if path.lower().endswith(('.edat', '.sdat')) else path
        out_path = out_path or (base + '.dec' if base == path else base)
        if os.path.abspath(out_path) == os.path.abspath(path): out_path = path + '.dec'
    else:
        raise DecryptError('الملف غير مشفّر بصيغة PS3 معروفة')
    open(out_path, 'wb').write(res)
    return out_path, how, len(res)

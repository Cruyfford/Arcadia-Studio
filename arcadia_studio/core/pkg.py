"""PS3 debug PKG extract / rebuild (layout verified byte-identical against VIDEOSTORE 4.0)."""
import hashlib
import os
import struct

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def _keystream(key16, start, n):
    k = bytearray(0x40)
    k[0:8] = key16[0:8]; k[8:16] = key16[0:8]
    k[16:24] = key16[8:16]; k[24:32] = key16[8:16]
    out = bytearray()
    for i in range(n):
        k[0x38:0x40] = struct.pack('>Q', (start + i) & 0xFFFFFFFFFFFFFFFF)
        out += hashlib.sha1(k).digest()[:16]
    return bytes(out)


def crypt(key16, data, start=0):
    n = (len(data) + 15) // 16
    ks = _keystream(key16, start, n)[:len(data)]
    if np is not None:
        return (np.frombuffer(data, np.uint8) ^ np.frombuffer(ks, np.uint8)).tobytes()
    return bytes(a ^ b for a, b in zip(data, ks))


class PkgReader:
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.header = bytearray(self.f.read(0x180))
        if self.header[:4] != b'\x7fPKG':
            raise ValueError('ليس ملف PKG صالحاً')
        rev, = struct.unpack('>H', self.header[4:6])
        if rev & 0x8000:
            raise ValueError('هذه حزمة Retail مشفرة بمفتاح سوني؛ البرنامج يدعم حزم Debug فقط')
        self.qa = bytes(self.header[0x60:0x70])
        self.count, = struct.unpack('>I', self.header[0x14:0x18])
        self.data_off, = struct.unpack('>Q', self.header[0x20:0x28])
        self.content_id = self.header[0x30:0x60].rstrip(b'\0').decode()

    def read(self, off, size):
        self.f.seek(self.data_off + (off // 16) * 16)
        pre = off % 16
        blob = self.f.read(((pre + size + 15) // 16) * 16)
        return crypt(self.qa, blob, off // 16)[pre:pre + size]

    def entries(self):
        tbl = self.read(0, self.count * 32)
        for i in range(self.count):
            no, ns, do, ds, fl, _ = struct.unpack('>IIQQII', tbl[i * 32:i * 32 + 32])
            yield self.read(no, ns).decode('utf-8', 'replace'), do, ds, fl

    def extract(self, out_dir, progress=None):
        ents = list(self.entries())
        for i, (name, do, ds, fl) in enumerate(ents):
            p = os.path.join(out_dir, name)
            if fl & 0xFF == 4:
                os.makedirs(p, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'wb') as o:
                    pos = 0
                    while pos < ds:
                        n = min(8 << 20, ds - pos)
                        o.write(self.read(do + pos, n)); pos += n
            if progress and i % 50 == 0:
                progress(i, len(ents))
        return ents


def build_pkg(template_pkg, src_dir, out_path, progress=None):
    """Rebuild a debug PKG from src_dir, reusing header/metadata of template_pkg.
    New files and directories found in src_dir are appended automatically."""
    r = PkgReader(template_pkg)
    h = bytearray(r.header)
    items = [(n.encode(), fl) for n, _, _, fl in r.entries()]
    known = {n for n, _ in items}
    for base, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        files = sorted(f for f in files if not f.startswith('.'))
        rel_base = os.path.relpath(base, src_dir).replace(os.sep, '/')
        for d in dirs:
            rel = (d if rel_base == '.' else rel_base + '/' + d).encode()
            if rel not in known:
                items.append((rel, 0x80000004)); known.add(rel)
        for fn in files:
            rel = (fn if rel_base == '.' else rel_base + '/' + fn).encode()
            if rel not in known:
                items.append((rel, 0x80000003)); known.add(rel)
    # drop items that no longer exist on disk
    items = [(n, fl) for n, fl in items if os.path.exists(os.path.join(src_dir, n.decode()))]
    n = len(items)
    off = n * 32
    noffs = []
    for name, _ in items:
        noffs.append(off); off += (len(name) + 15) & ~15
    entries, sizes = [], []
    for (name, fl), no in zip(items, noffs):
        p = os.path.join(src_dir, name.decode())
        if fl & 0xFF == 4:
            entries.append((no, len(name), off, 0, fl)); sizes.append(0); continue
        sz = os.path.getsize(p)
        entries.append((no, len(name), off, sz, fl)); sizes.append(sz)
        off += (sz + 15) & ~15
    data_size = off
    # qa digest = sha1 over table+names+data (streamed)
    head = bytearray()
    for e in entries:
        head += struct.pack('>IIQQII', *e, 0)
    for name, _ in items:
        head += name + b'\0' * (((len(name) + 15) & ~15) - len(name))

    def data_chunks():
        yield bytes(head)
        for (name, fl), sz in zip(items, sizes):
            if fl & 0xFF == 4:
                continue
            with open(os.path.join(src_dir, name.decode()), 'rb') as f:
                while True:
                    b = f.read(8 << 20)
                    if not b: break
                    yield b
            pad = ((sz + 15) & ~15) - sz
            if pad: yield b'\0' * pad

    qd = hashlib.sha1()
    for c in data_chunks(): qd.update(c)
    qa = qd.digest()[:16]
    struct.pack_into('>I', h, 0x14, n)
    struct.pack_into('>Q', h, 0x18, 0x180 + data_size + 0x60)
    struct.pack_into('>Q', h, 0x28, data_size)
    h[0x60:0x70] = qa
    h[0x70:0x80] = crypt(qa, b'\0' * 16, 0xFFFFFFFFFFFFFFFF)
    o = 0xC0
    while o < 0x140:
        mid, ms = struct.unpack('>II', h[o:o + 8])
        if mid == 4: struct.pack_into('>Q', h, o + 8, data_size)
        o += 8 + ms
    hs = hashlib.sha1(bytes(h[:0x80])).digest()[3:19]
    h[0x80:0x90] = hs; h[0x90:0xC0] = crypt(hs, b'\0' * 0x30)
    ms_ = hashlib.sha1(bytes(h[0xC0:0x140])).digest()[3:19]
    h[0x140:0x150] = ms_; h[0x150:0x180] = crypt(ms_, b'\0' * 0x30)
    full = hashlib.sha1(bytes(h))
    blk = 0; done = 0
    with open(out_path, 'wb') as out:
        out.write(h)
        carry = b''
        for c in data_chunks():
            c = carry + c
            cut = len(c) - len(c) % 16
            enc = crypt(qa, c[:cut], blk)
            blk += cut // 16; carry = c[cut:]
            out.write(enc); full.update(enc)
            done += cut
            if progress: progress(done, data_size)
        if carry:
            enc = crypt(qa, carry, blk); out.write(enc); full.update(enc)
        fsha = full.copy().digest()[3:19]
        tail = fsha + crypt(fsha, b'\0' * 0x30)
        full.update(tail)
        out.write(tail + full.digest() + b'\0' * 12)
    return n, os.path.getsize(out_path)


def psl1ght_pack(folder, contentid, out_path):
    """Build a debug PKG from scratch, same layout as PSL1GHT's pkg.py (used by pop-fe and
    most homebrew). Installs on HEN/CFW."""
    folder = os.path.abspath(folder)
    items = []

    def walk(d):
        ents = sorted(os.listdir(d))
        files = [e for e in ents if not os.path.isdir(os.path.join(d, e))]
        dirs = [e for e in ents if os.path.isdir(os.path.join(d, e))]
        for e in files + dirs:
            p = os.path.join(d, e)
            rel = os.path.relpath(p, folder).replace(os.sep, '/').encode()
            if os.path.isdir(p):
                items.append((rel, 0x80000004, p)); walk(p)
            else:
                items.append((rel, 0x80000003, p))
    walk(folder)
    n = len(items)
    off = 0x20 * n
    noffs = []
    for name, _, _ in items:
        noffs.append(off); off += (len(name) + 15) & ~15
    descs, sizes = [], []
    for (name, fl, p), no in zip(items, noffs):
        if fl & 0xFF == 4:
            descs.append((no, len(name), off, 0, fl)); sizes.append(0)
        else:
            sz = os.path.getsize(p)
            descs.append((no, len(name), off, sz, fl)); sizes.append(sz)
            off += (sz + 15) & ~15
    data_size = off
    table = b''.join(struct.pack('>IIQQII', *d, 0) for d in descs)
    table += b''.join(nm + b'\0' * (((len(nm) + 15) & ~15) - len(nm)) for nm, _, _ in items)

    def chunks():
        yield table
        for (name, fl, p), sz in zip(items, sizes):
            if fl & 0xFF == 4: continue
            with open(p, 'rb') as f:
                while True:
                    b = f.read(8 << 20)
                    if not b: break
                    yield b
            pad = ((sz + 15) & ~15) - sz
            if pad: yield b'\0' * pad

    cid = contentid.encode()[:0x30].ljust(0x30, b'\0')

    def header(qa, kl):
        return struct.pack('>IIIIIIQQQ', 0x7F504B47, 1, 0xC0, 5, 0x80, n, data_size + 0x1A0, 0x140, data_size) + cid + qa + kl
    qd = hashlib.sha1()           # same order as PSL1GHT: file data, header (no content id), descriptors
    for (name, fl, p) in items:
        if fl & 0xFF == 4: continue
        with open(p, 'rb') as f:
            while True:
                b = f.read(8 << 20)
                if not b: break
                qd.update(b)
    qd.update(struct.pack('>IIIIIIQQQ', 0x7F504B47, 1, 0xC0, 5, 0x80, n, data_size + 0x1A0, 0x140, data_size) + b'\0' * 0x50)
    qd.update(table)
    qa = qd.digest()[:16]
    kl = crypt(qa, b'\0' * 16, 0xFFFFFFFFFFFFFFFF)
    head = header(qa, kl)
    meta = struct.pack('>IIII IIII IIIHH IIIHH'.replace(' ', ''), 1, 4, 3, 2, 4, 5, 3, 4, 0xE, 4, 8, 0, 0,
                       data_size, 5, 4, 0x1061, 0)
    hsha = hashlib.sha1(head).digest()[3:19]
    msha = hashlib.sha1(meta).digest()[3:19]
    pad1 = crypt(msha, b'\0' * 0x30)
    with open(out_path, 'wb') as o:
        o.write(head); o.write(hsha); o.write(crypt(hsha, pad1)); o.write(meta); o.write(msha); o.write(pad1)
        blk, carry = 0, b''
        for c in chunks():
            c = carry + c; cut = len(c) - len(c) % 16
            o.write(crypt(qa, c[:cut], blk)); blk += cut // 16; carry = c[cut:]
        if carry: o.write(crypt(qa, carry, blk))
        o.write(b'\0' * 0x60)
    return n, os.path.getsize(out_path)

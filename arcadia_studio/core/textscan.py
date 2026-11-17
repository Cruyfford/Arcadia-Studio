"""Find text inside any game file, translate it, and write it back in place (Arabization helper).

Arabic is written the way games without Arabic support need it: letters joined (presentation forms) and,
optionally, in visual order (right-to-left already applied), encoded as UTF-8 or UTF-16LE like the original.
"""
import csv
import re

from .arabic import to_xmb

_U8 = re.compile(rb'(?:[\x20-\x7e\t\r\n]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){%d,}')
_U16 = re.compile(rb'(?:[\x20-\x7e\t\r\n]\x00|[\x00-\xff][\x06\xfb-\xfe]){%d,}')
_LETTER = re.compile(r'[A-Za-z؀-ۿ぀-ヿ一-鿿가-힯ﭐ-﻿]')


def _slack(data, end, unit):
    z = 0
    while end + z + unit <= len(data) and data[end + z:end + z + unit] == b'\0' * unit and z < 4096:
        z += unit
    return max(0, z - unit)            # keep one terminator


def scan(data, min_len=4, utf16=True, limit=200000):
    out = []
    for m in re.finditer(_U8.pattern % min_len, data):
        raw = m.group()
        try: t = raw.decode('utf-8')
        except UnicodeDecodeError: continue
        if len(_LETTER.findall(t)) < 2: continue
        out.append({'off': m.start(), 'enc': 'utf-8', 'raw': raw, 'text': t, 'max': len(raw) + _slack(data, m.end(), 1), 'tr': ''})
        if len(out) >= limit: break
    if utf16:
        ends = sorted((e['off'], e['off'] + len(e['raw'])) for e in out)
        import bisect
        starts = [a for a, _ in ends]
        rx = re.compile(_U16.pattern % min_len); pos = 0
        while True:
            m = rx.search(data, pos)
            if not m: break
            if m.start() % 2:                 # keep UTF-16 aligned to 2 bytes
                pos = m.start() + 1; continue
            pos = m.end()
            st = m.start()
            i = bisect.bisect_right(starts, st) - 1          # starts inside a UTF-8 string? skip past it
            if i >= 0 and ends[i][1] > st:
                st = ends[i][1] + (ends[i][1] & 1)
                if st >= m.end() or (m.end() - st) // 2 < min_len: continue
            raw = data[st:m.end()]
            try: t = raw.decode('utf-16-le')
            except UnicodeDecodeError: continue
            if len(_LETTER.findall(t)) < 2 or not t.isprintable() and not all(c.isprintable() or c in '\r\n\t' for c in t): continue
            out.append({'off': st, 'enc': 'utf-16-le', 'raw': raw, 'text': t, 'max': len(raw) + _slack(data, m.end(), 2), 'tr': ''})
            if len(out) >= limit: break
    out.sort(key=lambda e: e['off'])
    return out


def game_text(text, shape=True, visual=True):
    if not text: return text
    if shape and visual: return to_xmb(text)
    if shape:
        from .arabic import _rs
        return _rs.reshape(text)
    return text


def encoded(e, shape=True, visual=True):
    return game_text(e['tr'], shape, visual).encode(e['enc'])


def inject(data, entries, shape=True, visual=True, pad=b'\0'):
    """-> (new bytes, written count, list of (entry, reason) not written)"""
    buf = bytearray(data); n = 0; skipped = []
    for e in entries:
        if not e.get('tr'): continue
        b = encoded(e, shape, visual)
        if len(b) > e['max']:
            skipped.append((e, 'أطول من المساحة (%d > %d بايت)' % (len(b), e['max']))); continue
        unit = 2 if e['enc'] == 'utf-16-le' else 1
        fill = e['max'] - len(b)
        if pad != b'\0' and len(e['raw']) > len(b):
            filler = (b' ' if unit == 1 else ' '.encode('utf-16-le')) * ((len(e['raw']) - len(b)) // unit)
            b = b + filler; fill = e['max'] - len(b)
        buf[e['off']:e['off'] + e['max']] = b + b'\0' * fill
        n += 1
    return bytes(buf), n, skipped


def export_csv(entries, path):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['offset', 'encoding', 'max_bytes', 'original', 'translation'])
        for e in entries:
            w.writerow(['0x%X' % e['off'], e['enc'], e['max'], e['text'], e.get('tr', '')])


def import_csv(entries, path):
    by = {e['off']: e for e in entries}; n = 0
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try: off = int(row['offset'], 16)
            except (KeyError, ValueError): continue
            if off in by and row.get('translation') is not None:
                by[off]['tr'] = row['translation']; n += 1
    return n

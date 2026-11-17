"""Add Arabic letters (joined presentation forms + basic block) to a game's TrueType font."""
import os

from fontTools import subset, merge
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem

ARABIC = set(range(0x0600, 0x0700)) | set(range(0xFB50, 0xFE00)) | set(range(0xFE70, 0xFF00))
ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets')


def _strip(font, unicodes):
    o = subset.Options(); o.layout_features = []; o.hinting = False; o.name_IDs = ['*']; o.notdef_outline = True
    o.drop_tables += ['GSUB', 'GPOS', 'GDEF', 'DSIG', 'meta', 'kern', 'gasp', 'prep', 'fpgm', 'cvt ', 'hdmx', 'VDMX', 'LTSH', 'vhea', 'vmtx', 'STAT']
    s = subset.Subsetter(o); s.populate(unicodes=unicodes); s.subset(font)
    return font


def merge_arabic(target, out, arabic_font=None, scale=1.0):
    """-> number of Arabic characters added"""
    arabic_font = arabic_font or os.path.join(ASSETS, 'IBMPlexSansArabic-SemiBold.ttf')
    t = TTFont(target)
    if 'glyf' not in t:
        raise ValueError('الخط ليس TrueType (glyf) — خطوط CFF/OTF غير مدعومة')
    have = set(t.getBestCmap())
    a = TTFont(arabic_font)
    add = [u for u in sorted(ARABIC) if u in a.getBestCmap() and u not in have]
    if not add:
        return 0
    _strip(a, add)
    upem = t['head'].unitsPerEm
    want = int(round(upem / max(scale, 0.01)))
    if a['head'].unitsPerEm != want:
        scale_upem(a, want)
    a['head'].unitsPerEm = upem
    _strip(t, sorted(have))
    tmp_a, tmp_t = out + '.a.ttf', out + '.t.ttf'
    a.save(tmp_a); t.save(tmp_t)
    try:
        m = merge.Merger().merge([tmp_t, tmp_a])
        names = TTFont(target)['name']
        m['name'] = names               # keep the game's font names so it still loads it
        m.save(out)
    finally:
        for p in (tmp_a, tmp_t):
            if os.path.exists(p): os.remove(p)
    return len(add)

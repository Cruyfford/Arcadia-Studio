"""Arabic text for the PS3 XMB: the XMB has no shaping and no RTL, so text is stored
pre-shaped (presentation forms) and in visual order. Editors work on normal text."""
import re
import unicodedata

import arabic_reshaper
from bidi.algorithm import get_display

_rs = arabic_reshaper.ArabicReshaper(configuration={'delete_harakat': True, 'support_ligatures': True})
_AR = re.compile('[؀-ۿ]')
_PF = re.compile('[ﭐ-﷿ﹰ-﻿]')


def has_arabic(s):
    return bool(_AR.search(s) or _PF.search(s))


def to_xmb(text):
    """normal (logical) text -> XMB display string"""
    if not _AR.search(text):
        return text
    return get_display(_rs.reshape(text), base_dir='R')


def from_xmb(s):
    """XMB display string -> normal editable text (heuristic; sidecar map is preferred)"""
    if not _PF.search(s):
        return s
    out = []
    for c in s:
        o = ord(c)
        if 0xFEF5 <= o <= 0xFEFC:          # lam-alef ligature, stored in visual order
            out.append(unicodedata.normalize('NFC', unicodedata.normalize('NFKC', c))[::-1])
        elif 0xFB50 <= o <= 0xFEFF:
            out.append(unicodedata.normalize('NFC', unicodedata.normalize('NFKC', c)))
        else:
            out.append(c)
    return get_display(''.join(out), base_dir='R')

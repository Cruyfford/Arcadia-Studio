"""PARAM.SFO reader."""
import struct

LABELS = {'TITLE': 'الاسم', 'TITLE_ID': 'رقم اللعبة', 'CATEGORY': 'النوع', 'APP_VER': 'إصدار التطبيق',
          'VERSION': 'الإصدار', 'PS3_SYSTEM_VER': 'يتطلب فيرموير', 'CONTENT_ID': 'Content ID',
          'RESOLUTION': 'الدقة', 'SOUND_FORMAT': 'الصوت', 'PARENTAL_LEVEL': 'التصنيف العمري', 'ATTRIBUTE': 'خصائص'}
CATEGORIES = {'HG': 'لعبة HDD', 'DG': 'لعبة قرص', 'GD': 'بيانات/تحديث', 'AP': 'تطبيق', 'AT': 'تطبيق',
              '2P': 'PS2 Classic', '1P': 'PS1 Classic', 'MN': 'PSP Minis', 'PE': 'PSP Remaster', 'PP': 'PSP',
              'HM': 'Home', 'CB': 'شبكي', 'SF': 'متجر', 'WT': 'Web TV', 'DM': 'Disc Movie'}


def parse_sfo(data):
    if not data or data[:4] != b'\x00PSF':
        return {}
    _, _, kt, dt, n = struct.unpack('<IIIII', data[:20])
    out = {}
    for i in range(n):
        ko, fmt, ln, mx, do = struct.unpack('<HHIII', data[20 + i * 16:36 + i * 16])
        key = data[kt + ko:data.index(b'\0', kt + ko)].decode('ascii', 'replace')
        raw = data[dt + do:dt + do + ln]
        if fmt == 0x0404:
            out[key] = struct.unpack('<I', raw[:4])[0]
        else:
            out[key] = raw.rstrip(b'\0').decode('utf-8', 'replace')
    return out

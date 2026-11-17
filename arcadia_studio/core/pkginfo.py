"""Read what a store entry needs straight from a game PKG: title, IDs, version, size, icon and background."""
import io
import os

from .pkg import PkgReader
from .sfo import parse_sfo

CAT_NAMES = {'HG': 'PS3', 'DG': 'PS3', 'GD': 'تحديث', 'AV': 'تطبيق', 'AM': 'تطبيق', 'AT': 'تطبيق', 'HM': 'تطبيق',
             '2P': 'PS2', '1P': 'PS1', 'PE': 'PSP', 'MN': 'PSP mini', 'PP': 'PSP', 'SD': 'حفظ'}


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return ('%.2f %s' % (n, u)) if u in ('GB',) else ('%d %s' % (n, u))
        n /= 1024.0


def pkg_meta(path):
    r = PkgReader(path)
    sfo = parse_sfo(r.read_file('PARAM.SFO') or b'')
    size = os.path.getsize(path)
    cat = str(sfo.get('CATEGORY', ''))
    ver = str(sfo.get('APP_VER') or sfo.get('VERSION') or '').strip()
    title = str(sfo.get('TITLE') or os.path.splitext(os.path.basename(path))[0]).replace('\n', ' ').strip()
    info = ' • '.join(x for x in (CAT_NAMES.get(cat, cat), human(size), ('v' + ver) if ver else '') if x)
    return {'title': title, 'title_id': str(sfo.get('TITLE_ID', '')), 'content_id': r.content_id, 'category': cat,
            'version': ver, 'size': size, 'info': info,
            'icon': r.read_file('ICON0.PNG'), 'pic': r.read_file('PIC1.PNG') or r.read_file('PIC0.PNG')}


def save_png(data, out, size=None):
    """write PNG bytes, resized to fit size if given"""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if size and (im.width > size[0] or im.height > size[1]):
        im.thumbnail(size, Image.LANCZOS)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out, 'PNG', optimize=True)

"""Short game preview videos for the ARCADIA PS3 app.

Format (.avp, big endian), made to be decoded by the PS3's own JPEG decoder:
    'AVP1' | u16 width | u16 height | u16 fps | u16 reserved | u32 frames | u32 offsets[frames + 1] | JPEG frames
Clips come from ScreenScraper.fr (its API is meant for free tools like this one: a free user account plus
developer credentials from their forum) or from any video file the user has.
The file name is the game's key, computed exactly like the PS3 app does (preview_key)."""
import json
import os
import re
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
import zlib

from .audio import ffmpeg

W, H, FPS, SECONDS = 320, 180, 12, 20
SS_API = 'https://api.screenscraper.fr/api2/'
SYSTEMS = {'PS1': 57, 'PS2': 58, 'PS3': 59, 'PSP': 61}
_ID = re.compile(r'\b([A-Z]{4}\d{5})\b')


def preview_key(raw_title, raw_info):
    """same rule as key_of() in the PS3 app: the game ID from info, else a slug of the title, else crc32"""
    m = _ID.search(raw_info or '')
    if m: return m.group(1)
    slug = re.sub(r'[^a-z0-9]', '', (raw_title or '').lower())[:48]
    if slug: return slug
    return '%08x' % (zlib.crc32((raw_title or '').encode('utf-8')) & 0xFFFFFFFF)


def guess_system(path_text):
    t = (path_text or '').upper()
    for s in ('PS1', 'PS2', 'PSP', 'PS3'):
        if s in t: return s
    return 'PS3'


def clean_name(title):
    """search-friendly name: drop bracketed tags, IDs and edition noise"""
    t = re.sub(r'[\(\[].*?[\)\]]', ' ', title or '')
    t = _ID.sub(' ', t)
    t = re.sub(r'\b(PSN|PS3|PS2|PS1|PSP|HD|BETA|DEMO)\b', ' ', t, flags=re.I)
    return re.sub(r'\s+', ' ', t).strip(' -:')


# ---------------------------------------------------------------- ScreenScraper
class ScreenScraper:
    def __init__(self, devid, devpassword, ssid='', sspassword='', softname='ArcadiaStudio'):
        self.base = dict(devid=devid, devpassword=devpassword, softname=softname, output='json')
        if ssid: self.base.update(ssid=ssid, sspassword=sspassword)

    def _get(self, endpoint, **params):
        q = dict(self.base); q.update(params)
        url = SS_API + endpoint + '?' + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={'User-Agent': 'ArcadiaStudio/1.0'})
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode('utf-8', 'replace')
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError('ScreenScraper: ' + body.strip()[:200])

    def find_video(self, name, system):
        """-> (video url, matched game name) or (None, None)"""
        data = self._get('jeuRecherche.php', systemeid=SYSTEMS.get(system, 59), recherche=name)
        games = (data.get('response') or {}).get('jeux') or []
        for g in games:
            if not g or not g.get('id'): continue
            medias = g.get('medias') or []
            for kind in ('video-normalized', 'video'):
                for m in medias:
                    if m.get('type') == kind and m.get('url'):
                        names = g.get('noms') or []
                        return m['url'], (names[0].get('text') if names else name)
        return None, None

    def download(self, url, dest):
        req = urllib.request.Request(url, headers={'User-Agent': 'ArcadiaStudio/1.0'})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, 'wb') as f:
            ct = r.headers.get('Content-Type', '')
            while True:
                b = r.read(1 << 16)
                if not b: break
                f.write(b)
        if os.path.getsize(dest) < 10000 or ct.startswith(('text/', 'application/json')):
            raise RuntimeError('ScreenScraper لم يرسل فيديو')
        return dest


# ---------------------------------------------------------------- RAWG.io (free key, instant: https://rawg.io/apidocs)
class Rawg:
    """trailers from RAWG's /games/{id}/movies (mp4). Free plan: non-commercial, credit RAWG with a link."""
    API = 'https://api.rawg.io/api/'

    def __init__(self, key):
        self.key = key

    def _get(self, path, **params):
        params['key'] = self.key
        url = self.API + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'ArcadiaStudio/1.0'})
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))

    def find_video(self, name, system=None):
        res = self._get('games', search=name, page_size=5, search_precise='true').get('results') or []
        want = re.sub(r'[^a-z0-9]', '', name.lower())
        res.sort(key=lambda g: 0 if re.sub(r'[^a-z0-9]', '', (g.get('name') or '').lower()) == want else 1)
        for g in res[:3]:
            movies = self._get('games/%d/movies' % g['id']).get('results') or []
            for m in movies:
                d = m.get('data') or {}
                url = d.get('480') or d.get('max')
                if url: return url, g.get('name')
        return None, None

    download = ScreenScraper.download


# ---------------------------------------------------------------- conversion
def _split_jpegs(data):
    frames, i = [], 0
    while True:
        s = data.find(b'\xff\xd8', i)
        if s < 0: break
        e = data.find(b'\xff\xd9', s + 2)
        if e < 0: break
        frames.append(data[s:e + 2]); i = e + 2
    return frames


def to_avp(src, dest, start=0.0, seconds=SECONDS, w=W, h=H, fps=FPS, quality=6):
    """any video ffmpeg can read -> .avp; returns (frames, bytes)"""
    vf = ('scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,fps=%d,format=yuvj420p'
          % (w, h, w, h, fps))
    cmd = [ffmpeg(), '-v', 'error', '-ss', str(start), '-t', str(seconds), '-i', src, '-an', '-vf', vf,
           '-q:v', str(quality), '-f', 'image2pipe', '-vcodec', 'mjpeg', '-']
    flags = 0x08000000 if sys.platform.startswith('win') else 0
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    frames = _split_jpegs(r.stdout)
    if not frames:
        raise RuntimeError('تعذّر تحويل الفيديو: ' + r.stderr.decode('utf-8', 'replace')[-300:])
    head = b'AVP1' + struct.pack('>HHHHI', w, h, fps, 0, len(frames))
    base = len(head) + 4 * (len(frames) + 1)
    offs, pos = [], base
    for f in frames:
        offs.append(pos); pos += len(f)
    offs.append(pos)
    tmp = dest + '.tmp'
    with open(tmp, 'wb') as o:
        o.write(head); o.write(struct.pack('>%dI' % len(offs), *offs))
        for f in frames: o.write(f)
    os.replace(tmp, dest)
    return len(frames), pos


def read_avp(path):
    """-> (w, h, fps, [jpeg bytes]) for previews inside the studio"""
    d = open(path, 'rb').read()
    if d[:4] != b'AVP1': raise ValueError('not an AVP file')
    w, h, fps, _, n = struct.unpack('>HHHHI', d[4:16])
    offs = struct.unpack('>%dI' % (n + 1), d[16:16 + 4 * (n + 1)])
    return w, h, fps, [d[offs[i]:offs[i + 1]] for i in range(n)]


# ---------------------------------------------------------------- store scan
def collect_games(store, target=None, max_depth=10):
    """games under a section: dict(title, info, icon, key, path) — a game is an item with a download link,
    or a folder that only opens its download link (PSN style)"""
    from .xmb import ROOT_XML, ROOT_VIEW
    import html as _h
    if target is None:
        main = store.children(ROOT_XML, ROOT_VIEW)
        target = main[0]['target'] if main else None
    out, seen = [], set()

    def has_link(p):
        u = p.get('pkg_src') or p.get('module_action') or ''
        return u.startswith('http') or 'url=' in u

    def walk(rel, vid, path, depth):
        if not rel or depth > max_depth or (rel, vid) in seen: return
        seen.add((rel, vid))
        for c in store.children(rel, vid):
            p = c['pairs']
            raw_t = _h.unescape(p.get('title', c['key'] or ''))
            raw_i = _h.unescape(p.get('info', ''))
            game = has_link(p)
            if not game and c['target']:
                kids = store.children(*c['target'])
                if len(kids) == 1 and has_link(kids[0]['pairs']) and not kids[0]['target']: game = True
            if game:
                title = store.display(raw_t)
                # parts like "God of War 3 - اللعبة ج1" live in a folder named after the game: search with that name
                search = title
                if re.search(r'[\u0600-\u06FF]', title) and path:
                    search = path.rsplit(' / ', 1)[-1]
                out.append(dict(title=title, info=store.display(raw_i), icon=p.get('icon', ''),
                                key=preview_key(raw_t, raw_i), path=path, search=clean_name(search) or clean_name(title)))
            elif c['target']:
                walk(c['target'][0], c['target'][1], path + ' / ' + store.display(raw_t), depth + 1)
    if target: walk(target[0], target[1], '', 0)
    uniq, keys = [], set()
    for g in out:
        if g['key'] in keys: continue
        keys.add(g['key']); uniq.append(g)
    return uniq

"""YouTube Data API v3: verified trailer discovery ONLY.

YouTube does not provide a downloadable MP4 through the Data API.
Open validated watch links in the browser. Use licensed/local media for the PS3 player.
"""
import html
import json
import re
import unicodedata
import urllib.parse
import urllib.request

_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_QUALIFIERS = {
    'official','trailer','gameplay','teaser','launch','announcement','reveal',
    'cinematic','story','video','clip','preview','ps1','ps2','ps3','ps4','ps5',
    'playstation','pc','hd','4k','e3','tgs','gamescom','exclusive','new',
    'debut','first','look','extended','world','premiere','60','fps'
}
_BLOCK = {
    'review','reaction','reactions','comparison','vs','versus','fake','fanmade',
    'fan','made','mod','mods','top','ranking','ranked','compilation',
    'walkthrough','playthrough','ending','explained','analysis','rumour',
    'leak','leaked','tutorial','retrospective','remastered','remaster',
    'remake','reacting','commentary'
}
_PLATFORMS = {'ps1','ps2','ps3','ps4','ps5','xbox','switch','wii','pc'}
_TRUSTED = {'playstation','playstation europe','nintendo','xbox','sega',
            'ubisoft','ea','electronic arts','square enix','capcom',
            'konami','bandai namco entertainment','bethesda softworks'}


def title_words(title):
    title = html.unescape(title or '').casefold()
    title = unicodedata.normalize('NFKD', title)
    title = ''.join(ch for ch in title if not unicodedata.combining(ch))
    title = title.replace('&', ' and ')
    title = re.sub(r'\bplaystation\s*([1-5])\b', r'ps\1', title)
    return re.findall(r'[a-z0-9]+', title)


def matches_game(game_name, video_title, system='PS3'):
    """Reject suffixes such as sequel numbers, editions, reactions and other consoles."""
    name = title_words(game_name)
    video = title_words(video_title)
    if not name or not video: return False
    if len(name) == 1 and len(name[0]) < 4: return False
    for i in range(len(video)-len(name)+1):
        if video[i:i+len(name)] != name: continue
        extras = video[:i]+video[i+len(name):]
        if not any(t in extras for t in ('trailer','gameplay','teaser','preview','cinematic')): continue
        if any(t in _BLOCK or t not in _QUALIFIERS for t in extras): continue
        platform=(system or '').casefold()
        if platform in _PLATFORMS and any(t in _PLATFORMS and t != platform for t in extras):
            continue
        return True
    return False


class YouTube:
    API = 'https://www.googleapis.com/youtube/v3/search'

    def __init__(self, key):
        self.key = key

    def _search(self, **params):
        params['key'] = self.key
        request = urllib.request.Request(
            self.API+'?'+urllib.parse.urlencode(params),
            headers={'User-Agent':'ArcadiaStudio/1.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode('utf-8','replace'))

    def find_video(self, game_name, system='PS3'):
        """Returns video metadata and official watch URL; never downloads YouTube content."""
        if not self.key: return None
        data = self._search(part='snippet', type='video', videoEmbeddable='true',
                            maxResults=20, safeSearch='moderate',
                            q='"'+game_name+'" '+(system or 'PS3')+' official trailer')
        results=[]
        for item in data.get('items') or []:
            video_id=(item.get('id') or {}).get('videoId','')
            detail=item.get('snippet') or {}
            title=html.unescape(detail.get('title') or '')
            channel=html.unescape(detail.get('channelTitle') or '')
            if not _VIDEO_ID.fullmatch(video_id): continue
            if not matches_game(game_name,title,system): continue
            results.append((int(channel.strip().casefold() in _TRUSTED),
                            video_id,title,channel))
        if not results: return None
        results.sort(key=lambda item: -item[0])
        _, video_id, title, channel = results[0]
        return {'id':video_id, 'title':title, 'channel':channel,
                'url':'https://www.youtube.com/watch?v='+video_id}

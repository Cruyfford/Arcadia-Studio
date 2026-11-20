"""Build short game videos (.avp) from RAWG trailers on GitHub Actions and publish them as release assets.
Sharded: game key -> release 'vid<crc32(key) % SHARDS>' — the PS3 app computes the same shard.
Usage: python tools/make_videos.py <shard> <shards>   (env: RAWG_KEY, GH_TOKEN, GITHUB_REPOSITORY)"""
import json
import os
import subprocess
import sys
import tempfile
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from arcadia_studio.core import previews as pv   # noqa: E402

SHARD, SHARDS = int(sys.argv[1]), int(sys.argv[2])
REPO = os.environ.get('GITHUB_REPOSITORY', 'Cruyfford/Arcadia-Studio')
TAG = 'vid%d' % SHARD
BUDGET = float(os.environ.get('BUDGET_MIN', '320')) * 60
t0 = time.time()


def gh(*a, check=True):
    return subprocess.run(['gh'] + list(a), check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def shard_of(key):
    return (zlib.crc32(key.encode()) & 0xFFFFFFFF) % SHARDS


games = [g for g in json.load(open(os.path.join(os.path.dirname(__file__), 'games.json'), encoding='utf-8'))
         if shard_of(g['key']) == SHARD]
if gh('release', 'view', TAG, '-R', REPO, check=False).returncode:
    gh('release', 'create', TAG, '-R', REPO, '-t', 'ARCADIA game videos %d' % SHARD,
       '-n', 'Short silent game videos (.avp) for the ARCADIA PS3 app. Source: RAWG.io trailers.', check=False)
have = set(gh('release', 'view', TAG, '-R', REPO, '--json', 'assets', '-q', '.assets[].name').stdout.split())
missing_file = 'missing_%d.txt' % SHARD
print('shard %d: %d games, %d already published' % (SHARD, len(games), len(have)), flush=True)

rawg = pv.Rawg(os.environ['RAWG_KEY'])
tmp = tempfile.mkdtemp()
cache = {}            # search name -> avp path (regions of one game share a clip)
done = miss = err = 0
for g in games:
    if time.time() - t0 > BUDGET:
        print('time budget reached', flush=True); break
    name = g['key'] + '.avp'
    if name in have: continue
    try:
        avp = cache.get(g['search'])
        if avp is None:
            url, matched = rawg.find_video(g['search'])
            time.sleep(0.4)
            if not url:
                cache[g['search']] = ''; miss += 1
                print('  - %s (%s): no trailer' % (g['search'], g['key']), flush=True); continue
            mp4 = os.path.join(tmp, 'src.mp4')
            rawg.download(url, mp4)
            avp = os.path.join(tmp, g['key'] + '.avp')
            pv.to_avp(mp4, avp, start=3)
            cache[g['search']] = avp
        if not avp: miss += 1; continue
        dest = os.path.join(tmp, name)
        if avp != dest:
            with open(avp, 'rb') as a, open(dest, 'wb') as b: b.write(a.read())
        r = gh('release', 'upload', TAG, dest, '-R', REPO, '--clobber', check=False)
        if r.returncode: raise RuntimeError(r.stderr[-200:])
        done += 1
        print('  + %s (%s) %d KB' % (g['search'], g['key'], os.path.getsize(dest) // 1024), flush=True)
    except Exception as e:  # noqa
        err += 1
        print('  ! %s (%s): %s' % (g['search'], g['key'], str(e)[:200]), flush=True)
print('shard %d finished: %d new, %d without trailer, %d errors' % (SHARD, done, miss, err))

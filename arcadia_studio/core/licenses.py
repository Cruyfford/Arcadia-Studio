"""Back up YOUR OWN PSN licenses as .rap files.

The PS3 keeps each purchased license as exdata/<CONTENT_ID>.rif in the user's home folder, locked to that
console (IDPS) and account (act.dat). With those three things from your own console we recover the license key
and save it as <CONTENT_ID>.rap, which the decrypter uses. Nothing here works without your console's own files.
"""
import ftplib
import io
import os

from .ps3crypto import rif_key_from, rif_to_rap, parse_rif


def collect_ftp(ps3, progress=None):
    """-> list of (user, act_dat_bytes, {rif_name: bytes}) for every user on the console"""
    f = ps3._ftp()
    out = []
    try:
        users = [u for u in f.nlst('/dev_hdd0/home') if os.path.basename(u.rstrip('/')).isdigit()]
        for ui, u in enumerate(sorted(users)):
            user = os.path.basename(u.rstrip('/'))
            ex = '/dev_hdd0/home/%s/exdata' % user
            try:
                names = [os.path.basename(n) for n in f.nlst(ex)]
            except ftplib.error_perm:
                continue
            if 'act.dat' not in names: continue

            def get(name):
                b = io.BytesIO(); f.retrbinary('RETR %s/%s' % (ex, name), b.write); return b.getvalue()
            rifs = {}
            rif_names = [n for n in names if n.lower().endswith('.rif')]
            for i, n in enumerate(rif_names):
                rifs[n] = get(n)
                if progress: progress(i + 1, len(rif_names))
            out.append((user, get('act.dat'), rifs))
    finally:
        try: f.quit()
        except Exception: pass
    return out


def collect_folder(folder):
    """a copied exdata folder (act.dat + *.rif)"""
    act = os.path.join(folder, 'act.dat')
    if not os.path.exists(act): raise ValueError('ما لقيت act.dat في المجلد')
    rifs = {n: open(os.path.join(folder, n), 'rb').read() for n in os.listdir(folder) if n.lower().endswith('.rif')}
    return [(os.path.basename(folder.rstrip('/\\')), open(act, 'rb').read(), rifs)]


def make_raps(sets, idps, out_dir):
    """-> (saved list of content ids, problems list)"""
    os.makedirs(out_dir, exist_ok=True)
    saved, bad = [], []
    for user, act, rifs in sets:
        acc = act[8:16]
        for name, data in sorted(rifs.items()):
            try:
                r = parse_rif(data)
                if r['account_id'] != acc:
                    bad.append('%s: رخصة حساب آخر (لا تطابق act.dat للمستخدم %s)' % (name, user)); continue
                cid, key, _ = rif_key_from(data, act, idps)
                open(os.path.join(out_dir, cid + '.rap'), 'wb').write(rif_to_rap(key))
                saved.append(cid)
            except Exception as e:  # noqa
                bad.append('%s: %s' % (name, e))
    return saved, bad

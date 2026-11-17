"""Publish the store PKG online so every PS3 that installed ARCADIA can update from it.

Hosting: archive.org (free, large files, served over plain HTTP which the PS3's built-in
package downloader handles — the original VIDEOSTORE used the same approach).
Upload uses archive.org's S3-compatible API (keys from https://archive.org/account/s3.php)."""
import http.client
import os
import urllib.parse

S3_HOST = 's3.us.archive.org'


def public_url(item, name='latest.pkg'):
    return 'http://archive.org/download/%s/%s' % (item, name)


def upload_archive(item, access, secret, local, name='latest.pkg', title='ARCADIA PS3 Store',
                   progress=None, host=S3_HOST, port=443, use_tls=True):
    size = os.path.getsize(local)
    conn = (http.client.HTTPSConnection if use_tls else http.client.HTTPConnection)(host, port, timeout=120)
    path = '/%s/%s' % (urllib.parse.quote(item), urllib.parse.quote(name))
    headers = {
        'authorization': 'LOW %s:%s' % (access, secret),
        'x-amz-auto-make-bucket': '1',
        'x-archive-meta-mediatype': 'software',
        'x-archive-meta-collection': 'opensource_media',
        'x-archive-meta-title': title,
        'x-archive-keep-old-version': '0',
        'x-archive-queue-derive': '0',
        'x-archive-size-hint': str(size),
        'Content-Length': str(size),
    }
    conn.putrequest('PUT', path)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    sent = 0
    with open(local, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b: break
            conn.send(b); sent += len(b)
            if progress: progress(sent, size)
    r = conn.getresponse(); body = r.read().decode('utf-8', 'replace')
    conn.close()
    if r.status >= 300:
        raise RuntimeError('فشل الرفع إلى archive.org (%d): %s' % (r.status, body[:300]))
    return public_url(item, name)

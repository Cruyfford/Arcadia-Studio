"""Play PS3 SND0.AT3 (ATRAC3/ATRAC3+): decoded to WAV with the bundled ffmpeg."""
import hashlib
import os
import subprocess
import sys
import tempfile


def ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa
        return 'ffmpeg'


def to_wav(data):
    """AT3 bytes -> path of a temporary WAV (cached by content)"""
    h = hashlib.sha1(data).hexdigest()[:16]
    d = os.path.join(tempfile.gettempdir(), 'arcadia_snd'); os.makedirs(d, exist_ok=True)
    src, out = os.path.join(d, h + '.at3'), os.path.join(d, h + '.wav')
    if os.path.exists(out) and os.path.getsize(out) > 44:
        return out
    open(src, 'wb').write(data)
    flags = 0x08000000 if sys.platform.startswith('win') else 0      # CREATE_NO_WINDOW
    r = subprocess.run([ffmpeg(), '-y', '-v', 'error', '-i', src, '-ac', '2', '-ar', '44100', out],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    if r.returncode or not os.path.exists(out):
        raise RuntimeError('تعذّر فك ترميز الصوت: ' + r.stderr.decode('utf-8', 'replace')[-300:])
    return out


class Player:
    """plays a WAV: winsound on Windows (no extra libraries), QtMultimedia elsewhere if present"""
    def __init__(self, parent):
        self.parent = parent; self._qt = None; self._on = False

    def play(self, wav, loop=True):
        self.stop()
        if sys.platform.startswith('win'):
            import winsound
            winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC | (winsound.SND_LOOP if loop else 0))
        else:
            import importlib
            try:
                mm = importlib.import_module('PySide6.QtMultimedia')
                from PySide6.QtCore import QUrl
                if not self._qt:
                    out = mm.QAudioOutput(self.parent); p = mm.QMediaPlayer(self.parent); p.setAudioOutput(out)
                    self._qt = (p, out)
                p = self._qt[0]
                p.setLoops(mm.QMediaPlayer.Infinite if loop else 1)
                p.setSource(QUrl.fromLocalFile(wav)); p.play()
            except Exception:  # noqa  no audio backend
                return
        self._on = True

    def stop(self):
        if sys.platform.startswith('win'):
            import winsound
            winsound.PlaySound(None, 0)
        elif self._qt:
            self._qt[0].stop()
        self._on = False

    def playing(self):
        return self._on

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
    """tiny wrapper around QMediaPlayer"""
    def __init__(self, parent):
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        self.out = QAudioOutput(parent); self.out.setVolume(0.8)
        self.p = QMediaPlayer(parent); self.p.setAudioOutput(self.out)

    def play(self, wav, loop=True):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        self.p.setLoops(QMediaPlayer.Infinite if loop else 1)
        self.p.setSource(QUrl.fromLocalFile(wav)); self.p.play()

    def stop(self):
        self.p.stop()

    def playing(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return self.p.playbackState() == QMediaPlayer.PlayingState

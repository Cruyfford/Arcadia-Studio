"""XMB-style preview: shows the store the way the PS3 draws it (raw XMB strings, icons, backgrounds), with
keyboard navigation like the controller (arrows, Enter = X, Backspace/Esc = O)."""
import math
import os
import time

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPixmap, QColor, QLinearGradient, QFont, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QWidget

from .core.xmb import ROOT_XML, ROOT_VIEW

FOLDER = 'type:x-xmb/folder-pixmap'

W, H = 1280, 720


class XmbView(QWidget):
    def __init__(self, win):
        super().__init__(); self.win = win; self.st = win.store
        self.setFocusPolicy(Qt.StrongFocus); self.setMinimumSize(640, 360)
        self.cache = {}
        self.root = self.st.children(ROOT_XML, ROOT_VIEW)
        self.cat = 0
        self.stack = []          # [(nodes, index, parent_node)] for opened folders; empty = horizontal level
        self.t0 = time.time()
        self.anim = QTimer(self); self.anim.timeout.connect(self.update); self.anim.start(50)

    # ---------- data
    def pix(self, ps3):
        if not ps3: return QPixmap()
        if ps3 not in self.cache:
            p = self.st.abs_path(ps3)
            self.cache[ps3] = QPixmap(p) if p and os.path.exists(p) else QPixmap()
        return self.cache[ps3]

    def kids(self, n):
        if n and n['cls'] == FOLDER and n['target']:
            try: return self.st.children(*n['target'])
            except Exception: return []  # noqa
        return []

    def level(self):
        """-> (nodes, index, parent)"""
        if self.stack: return self.stack[-1]
        cat = self.root[self.cat] if self.root else None
        return self.kids(cat), self.cat_sel.get(self.cat, 0), cat

    cat_sel = {}

    def open_at(self, n):
        """start the preview at the item selected in the tree"""
        if not n: return
        siblings = self.st.children(n['file'], n['view'])
        idx = next((i for i, c in enumerate(siblings) if c['key'] == n['key']), 0)
        if (n['file'], n['view']) == (ROOT_XML, ROOT_VIEW):
            self.cat = idx
        else:
            self.stack = [(siblings, idx, None)]

    # ---------- input (like the controller)
    def keyPressEvent(self, e):
        k = e.key()
        nodes, idx, par = self.level()
        if k in (Qt.Key_Left, Qt.Key_Right) and not self.stack:
            self.cat = max(0, min(len(self.root) - 1, self.cat + (1 if k == Qt.Key_Right else -1)))
        elif k in (Qt.Key_Up, Qt.Key_Down) and nodes:
            idx = max(0, min(len(nodes) - 1, idx + (1 if k == Qt.Key_Down else -1)))
            self._set_idx(idx)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_X, Qt.Key_Space):
            if not nodes and not self.stack and self.root:
                ch = self.kids(self.root[self.cat])
                if ch: self.stack.append((ch, 0, self.root[self.cat]))
            elif nodes:
                n = nodes[idx]; ch = self.kids(n)
                if ch: self.stack.append((ch, 0, n))
        elif k in (Qt.Key_Backspace, Qt.Key_Escape, Qt.Key_O):
            if self.stack: self.stack.pop()
            elif k == Qt.Key_Escape: self.window().close()
        else:
            return super().keyPressEvent(e)
        self.update()

    def wheelEvent(self, e):
        nodes, idx, _ = self.level()
        if nodes: self._set_idx(max(0, min(len(nodes) - 1, idx + (1 if e.angleDelta().y() < 0 else -1)))); self.update()

    def _set_idx(self, idx):
        if self.stack:
            n, _, p = self.stack[-1]; self.stack[-1] = (n, idx, p)
        else:
            self.cat_sel[self.cat] = idx

    # ---------- drawing (1280x720 canvas scaled to the widget)
    def text(self, qp, x, y, s, size, color, bold=False, raw=True):
        f = QFont(self.win.ui_font, size); f.setBold(bold); qp.setFont(f)
        qp.setLayoutDirection(Qt.LeftToRight)
        if raw: s = '\u202d' + s + '\u202c'     # left-to-right override: draw the stored visual order as-is, like the PS3
        qp.setPen(QColor(0, 0, 0, 120)); qp.drawText(x + 2, y + 2, s)   # PS3 text shadow
        qp.setPen(color); qp.drawText(x, y, s)

    def paintEvent(self, ev):
        qp = QPainter(self); qp.setRenderHint(QPainter.Antialiasing); qp.setRenderHint(QPainter.SmoothPixmapTransform)
        sc = min(self.width() / W, self.height() / H)
        qp.fillRect(self.rect(), QColor('#000'))
        qp.translate((self.width() - W * sc) / 2, (self.height() - H * sc) / 2); qp.scale(sc, sc)
        nodes, idx, par = self.level()
        sel = nodes[idx] if nodes and 0 <= idx < len(nodes) else par
        bg = self.pix((sel or {}).get('pairs', {}).get('pic', '')) if sel else QPixmap()
        if bg.isNull() and par: bg = self.pix(par['pairs'].get('pic', ''))
        if bg.isNull():
            self.wave(qp)
        else:
            qp.drawPixmap(QRectF(0, 0, W, H), bg, QRectF(bg.rect()))
            qp.fillRect(0, 0, W, H, QColor(0, 0, 0, 40))
        # clock
        self.text(qp, W - 230, 52, time.strftime('%d/%m  %H:%M'), 18, QColor('#ffffff'))
        if not self.stack:
            self.draw_categories(qp, nodes, idx)
        else:
            self.draw_folder(qp, nodes, idx, par)

    def wave(self, qp):
        g = QLinearGradient(0, 0, 0, H); g.setColorAt(0, QColor('#0b1a4a')); g.setColorAt(1, QColor('#3a1060'))
        qp.fillRect(0, 0, W, H, g)
        t = time.time() - self.t0
        for j, a in enumerate((60, 40, 25)):
            path = QPainterPath(); path.moveTo(0, 420)
            for x in range(0, W + 20, 20):
                y = 400 + j * 18 + math.sin(x / 180 + t * (0.6 + j * 0.2)) * (28 + j * 8)
                path.lineTo(x, y)
            qp.setPen(QPen(QColor(255, 255, 255, a), 2)); qp.drawPath(path)

    def draw_categories(self, qp, nodes, idx):
        cx, cy = 300, 200
        for i, c in enumerate(self.root):
            d = i - self.cat
            x = cx + d * 150
            if x < -120 or x > W + 120: continue
            big = d == 0
            pm = self.pix(c['pairs'].get('icon', ''))
            w = 150 if big else 92
            if not pm.isNull():
                pm2 = pm.scaled(w, int(w * 0.6) + 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qp.setOpacity(1 if big else 0.6); qp.drawPixmap(x - pm2.width() // 2, cy - pm2.height() // 2, pm2); qp.setOpacity(1)
            if big:
                self.text(qp, x - 70, cy + 70, self.st.text_raw(c, 'title'), 16, QColor('#ffffff'))
        # vertical list under the selected category
        self.draw_list(qp, nodes, idx, cx, cy + 120)

    def draw_list(self, qp, nodes, idx, x, y0):
        y = y0
        for i, n in enumerate(nodes[idx:idx + 6], idx):
            big = i == idx
            h = 96 if big else 64
            if y + h > H - 10: break
            pm = self.pix(n['pairs'].get('icon', ''))
            if not pm.isNull():
                pm2 = pm.scaled(int(h * 1.8), h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qp.setOpacity(1 if big else 0.75); qp.drawPixmap(x - pm2.width() // 2, y, pm2); qp.setOpacity(1)
            tx = x + 110
            self.text(qp, tx, y + h // 2 + (0 if big else 8), self.st.text_raw(n, 'title'), 22 if big else 17,
                      QColor(255, 255, 255, 255 if big else 210), bold=big)
            if big and n['pairs'].get('info'):
                self.text(qp, tx, y + h // 2 + 30, self.st.text_raw(n, 'info'), 14, QColor('#d6e0ff'))
            y += h + 16
        # items above the selection fade upward
        y = y0 - 10
        for i in range(idx - 1, max(-1, idx - 3), -1):
            n = nodes[i]; h = 56; y -= h + 10
            if y < 260 and not self.stack: break
            pm = self.pix(n['pairs'].get('icon', ''))
            qp.setOpacity(0.55)
            if not pm.isNull():
                pm2 = pm.scaled(int(h * 1.8), h, Qt.KeepAspectRatio, Qt.SmoothTransformation); qp.drawPixmap(x - pm2.width() // 2, y, pm2)
            qp.setOpacity(1)
            self.text(qp, x + 110, y + h // 2 + 6, self.st.text_raw(n, 'title'), 15, QColor(255, 255, 255, 170))

    def draw_folder(self, qp, nodes, idx, par):
        if par:
            pm = self.pix(par['pairs'].get('icon', ''))
            if not pm.isNull():
                pm2 = pm.scaled(130, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qp.setOpacity(0.8); qp.drawPixmap(110 - pm2.width() // 2, 300, pm2); qp.setOpacity(1)
        if not nodes:
            self.text(qp, 380, 360, 'لا توجد عناصر', 18, QColor('#ffffff'), raw=False); return
        self.draw_list(qp, nodes, idx, 380, 300)


class XmbDialog(QDialog):
    def __init__(self, win, start=None):
        super().__init__(win); self.setWindowTitle('📺 معاينة شكل الجهاز — XMB'); self.resize(1280, 760)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        self.view = XmbView(win); self.view.open_at(start); v.addWidget(self.view, 1)
        note = QLabel('  ←→↑↓ تنقّل • Enter = ✕ دخول • Backspace = ◯ رجوع • Esc إغلاق   |   النصوص تظهر بنفس الشكل المخزّن للجهاز — لو ظهر حرف مقطّع أو معكوس هنا فسيظهر كذلك على الجهاز.')
        note.setStyleSheet('color:#7f8bc0;padding:4px'); v.addWidget(note)
        self.view.setFocus()


class GamePreview(QWidget):
    """how one game PKG looks on the XMB when selected: PIC1 background, ICON0, title (+ SND0 music)"""
    def __init__(self, win, read, sfo):
        super().__init__(); self.win = win; self.t0 = time.time(); self.setMinimumSize(640, 360)
        self.pic, self.icon = QPixmap(), QPixmap()
        for pm, name in ((self.pic, 'PIC1.PNG'), (self.icon, 'ICON0.PNG')):
            d = read(name)
            if d: pm.loadFromData(d)
        if self.pic.isNull():
            d = read('PIC0.PNG')
            if d: self.pic.loadFromData(d)
        self.title = str(sfo.get('TITLE', '')).replace('\n', ' ')
        self.sub = '  '.join(x for x in (str(sfo.get('TITLE_ID', '')), ('v' + str(sfo.get('APP_VER'))) if sfo.get('APP_VER') else '') if x)
        self.anim = QTimer(self); self.anim.timeout.connect(self.update); self.anim.start(50)

    def paintEvent(self, ev):
        qp = QPainter(self); qp.setRenderHint(QPainter.Antialiasing); qp.setRenderHint(QPainter.SmoothPixmapTransform)
        sc = min(self.width() / W, self.height() / H)
        qp.fillRect(self.rect(), QColor('#000'))
        qp.translate((self.width() - W * sc) / 2, (self.height() - H * sc) / 2); qp.scale(sc, sc)
        if self.pic.isNull():
            XmbView.wave(self, qp)
        else:
            qp.drawPixmap(QRectF(0, 0, W, H), self.pic, QRectF(self.pic.rect()))
        qp.setFont(QFont(self.win.ui_font, 18)); qp.setPen(QColor('#fff'))
        qp.drawText(W - 230, 52, time.strftime('%d/%m  %H:%M'))
        if not self.icon.isNull():
            pm = self.icon.scaled(320, 176, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            qp.drawPixmap(300 - pm.width() // 2, 300, pm)
        f = QFont(self.win.ui_font, 24); f.setBold(True); qp.setFont(f)
        qp.setPen(QColor(0, 0, 0, 120)); qp.drawText(482, 392, self.title)
        qp.setPen(QColor('#fff')); qp.drawText(480, 390, self.title)
        qp.setFont(QFont(self.win.ui_font, 15)); qp.setPen(QColor('#d6e0ff')); qp.drawText(480, 424, self.sub)


class GamePreviewDialog(QDialog):
    def __init__(self, win, read, sfo):
        super().__init__(win); self.setWindowTitle('📺 معاينة اللعبة في الجهاز'); self.resize(1280, 760)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(GamePreview(win, read, sfo), 1)
        self.note = QLabel(); self.note.setStyleSheet('color:#7f8bc0;padding:4px'); v.addWidget(self.note)
        self.player = None
        snd = read('SND0.AT3')
        if snd:
            try:
                from .core.audio import to_wav, Player
                self.player = Player(self); self.player.play(to_wav(snd))
                self.note.setText('  🔊 يعمل صوت SND0 الخاص باللعبة (كما يُسمع عند الوقوف عليها في الجهاز)')
            except Exception as e:  # noqa
                self.note.setText('  🔇 تعذّر تشغيل الصوت: %s' % e)
        else:
            self.note.setText('  🔇 هذه الحزمة لا تحتوي صوت SND0.AT3')

    def closeEvent(self, e):
        if self.player: self.player.stop()
        super().closeEvent(e)

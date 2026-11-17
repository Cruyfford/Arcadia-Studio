"""Editors for files inside a PKG (edit mode): text/XML, images, PARAM.SFO, fonts, hex (any file)."""
from .ui_common import context_menu, add, shortcut
import os
import re
import shutil
import struct
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QDialog, QMenuBar, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit, QLabel, QFileDialog,
                               QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QComboBox,
                               QInputDialog, QScrollArea)

from .core.arabic import to_xmb, from_xmb
from .core.sfo import CATEGORIES

TEXT_EXT = {'.xml', '.txt', '.cfg', '.ini', '.json', '.lua', '.js', '.html', '.htm', '.css', '.csv', '.md', '.conf',
            '.log', '.xmb', '.sh', '.py', '.yml', '.yaml', '.srt', '.cue', '.nfo', '.dat_txt'}
IMG_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}
FONT_EXT = {'.ttf', '.otf'}
MEDIA_EXT = {'.at3', '.pam', '.mp4', '.mp3', '.wav', '.ac3', '.vag', '.mpg', '.m2v', '.avi'}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).upper()
    if name == 'PARAM.SFO' or ext == '.sfo': return 'sfo'
    if ext in IMG_EXT: return 'image'
    if ext in FONT_EXT: return 'font'
    if ext in MEDIA_EXT: return 'media'
    if ext in TEXT_EXT: return 'text'
    try:
        head = open(path, 'rb').read(4096)
    except OSError:
        return 'hex'
    if head[:4] in (b'SCE\x00', b'NPD\x00', b'\x00PSF'): return 'hex'
    if head and (b'\x00' not in head):
        try:
            head.decode('utf-8'); return 'text'
        except UnicodeDecodeError:
            pass
    return 'hex'


def describe(path):
    head = open(path, 'rb').read(16) if os.path.exists(path) else b''
    if head[:4] == b'SCE\x00': return 'ملف SELF/SPRX مشفّر (برنامج PS3) — يُعدّل بالـ Hex فقط بعد فك تشفيره بأدوات خارجية'
    if head[:4] == b'NPD\x00': return 'ملف EDAT/SDAT مشفّر برخصة — يحتاج فك تشفير خارجي'
    if head[:4] == b'\x7fELF': return 'ملف ELF (برنامج غير مشفّر)'
    return ''


def open_external(path):
    if sys.platform.startswith('win'): os.startfile(path)  # noqa
    else: subprocess.Popen(['xdg-open', path])


class _Base(QDialog):
    def __init__(self, parent, path, title):
        super().__init__(parent); self.path = path
        self.setWindowTitle('%s — %s' % (title, os.path.basename(path))); self.resize(1100, 760)
        self.v = QVBoxLayout(self)
        info = describe(path)
        if info:
            l = QLabel('⚠️ ' + info); l.setStyleSheet('color:#ffcc66'); self.v.addWidget(l)
        self.bar = QHBoxLayout()

    def add_btn(self, text, fn, kind='', keys=None):
        """tools go to the menu bar + right-click menu (no buttons)"""
        if not hasattr(self, 'tools'): self.tools = []
        self.tools.append((text, fn, keys))

    def _fill(self, m):
        for text, fn, keys in self.tools:
            if text == '-': m.addSeparator(); continue
            add(m, text, fn, shortcut=keys)

    def common_buttons(self):
        self.add_btn('-', None)
        self.add_btn('📤 تصدير نسخة...', self.export)
        self.add_btn('📥 استبدال بملف...', self.replace)
        self.add_btn('↗ فتح ببرنامج خارجي', lambda: open_external(self.path))
        mb = QMenuBar(self); self.v.setMenuBar(mb)
        self._fill(mb.addMenu('⚙ الأدوات'))
        for text, fn, keys in self.tools:
            if keys and fn: shortcut(self, keys, fn)
        targets = [self] + [w for w in (getattr(self, n, None) for n in ('ed', 'view', 'lbl', 'table')) if w is not None]
        for w in targets:
            if isinstance(w, QPlainTextEdit):
                context_menu(w, lambda m, pos, w=w: (m.addActions(w.createStandardContextMenu().actions()), m.addSeparator(), self._fill(m)))
            else:
                context_menu(w, lambda m, pos: self._fill(m))
        hint = QLabel('💡 الأدوات في قائمة «⚙ الأدوات» بالأعلى أو بالكلك يمين'); hint.setStyleSheet('color:#7f8bc0')
        self.v.addWidget(hint)

    def export(self):
        p, _ = QFileDialog.getSaveFileName(self, 'تصدير', os.path.basename(self.path))
        if p: shutil.copy(self.path, p)

    def replace(self):
        p, _ = QFileDialog.getOpenFileName(self, 'اختر الملف البديل')
        if p:
            shutil.copy(p, self.path); self.reload()

    def reload(self):
        pass


# ---------------------------------------------------------------- text / XML
class TextEditor(_Base):
    def __init__(self, parent, path):
        super().__init__(parent, path, '📝 محرر نصوص')
        self.ed = QPlainTextEdit(); self.ed.setFont(QFont('Consolas', 11)); self.ed.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.ed.setLayoutDirection(Qt.LeftToRight)
        find = QHBoxLayout()
        self.f_find, self.f_rep = QLineEdit(), QLineEdit()
        self.f_find.setPlaceholderText('بحث...'); self.f_rep.setPlaceholderText('استبدال بـ...')
        self.f_find.setPlaceholderText('بحث... (Enter = التالي)'); self.f_rep.setPlaceholderText('استبدال بـ... (Enter = استبدال الكل)')
        self.f_find.returnPressed.connect(self.find_next); self.f_rep.returnPressed.connect(self.replace_all)
        find.addWidget(self.f_find); find.addWidget(self.f_rep)
        self.enc = QComboBox(); self.enc.addItems(['utf-8', 'utf-16', 'latin-1', 'cp1256', 'shift_jis'])
        find.addWidget(QLabel('الترميز:')); find.addWidget(self.enc)
        self.v.addLayout(find)
        self.v.addWidget(self.ed, 1)
        self.add_btn('💾 حفظ', self.save, keys='Ctrl+S')
        self.add_btn('🔍 البحث التالي', self.find_next, keys='F3')
        self.add_btn('🔁 استبدال الكل', self.replace_all)
        self.add_btn('-', None)
        self.add_btn('ع ← XMB: تحويل النص المحدد لعربي الجهاز', self.to_xmb, 'ghost')
        self.add_btn('XMB → ع: قراءة عربي الجهاز', self.from_xmb, 'ghost')
        self.common_buttons()
        self.reload()

    def reload(self):
        raw = open(self.path, 'rb').read()
        for enc in ('utf-8', 'utf-16', 'cp1256', 'latin-1'):
            try:
                txt = raw.decode(enc); self.enc.setCurrentText(enc); break
            except UnicodeDecodeError:
                continue
        self.ed.setPlainText(txt)

    def save(self):
        try:
            data = self.ed.toPlainText().encode(self.enc.currentText())
        except UnicodeEncodeError as e:
            QMessageBox.critical(self, 'خطأ', 'بعض الحروف غير مدعومة في ترميز %s:\n%s' % (self.enc.currentText(), e)); return
        if self.path.lower().endswith('.xml') and b'<' in data:
            pass
        open(self.path, 'wb').write(data)
        QMessageBox.information(self, 'تم', 'تم الحفظ')

    def _transform(self, fn):
        c = self.ed.textCursor()
        if not c.hasSelection():
            QMessageBox.information(self, 'تنبيه', 'حدد النص أولاً'); return
        c.insertText(fn(c.selectedText()))

    def to_xmb(self): self._transform(to_xmb)

    def from_xmb(self): self._transform(from_xmb)

    def find_next(self):
        t = self.f_find.text()
        if t and not self.ed.find(t):
            self.ed.moveCursor(QTextCursor.Start); self.ed.find(t)

    def replace_all(self):
        t = self.f_find.text()
        if not t: return
        txt = self.ed.toPlainText(); n = txt.count(t)
        self.ed.setPlainText(txt.replace(t, self.f_rep.text()))
        QMessageBox.information(self, 'استبدال', 'تم استبدال %d مرة (لم يُحفظ بعد)' % n)


# ---------------------------------------------------------------- images
class ImageEditor(_Base):
    def __init__(self, parent, path):
        super().__init__(parent, path, '🖼 محرر صور')
        self.lbl = QLabel(); self.lbl.setAlignment(Qt.AlignCenter)
        sc = QScrollArea(); sc.setWidget(self.lbl); sc.setWidgetResizable(True); sc.setStyleSheet('background:#0b0f1f')
        self.info = QLabel(); self.v.addWidget(self.info); self.v.addWidget(sc, 1)
        self.add_btn('📥 استبدال بصورة (بنفس المقاس)', self.replace_same)
        self.add_btn('📐 تغيير المقاس', self.resize_img, 'ghost')
        self.add_btn('🔄 تحويل الصيغة', self.convert, 'ghost')
        self.common_buttons(); self.reload()

    def reload(self):
        from PIL import Image
        im = Image.open(self.path); self.size = im.size
        self.info.setText('المقاس: %d × %d   •   النمط: %s   •   الحجم: %d KB' % (im.width, im.height, im.mode, os.path.getsize(self.path) // 1024))
        pm = QPixmap(self.path)
        self.lbl.setPixmap(pm.scaled(min(pm.width(), 1000), min(pm.height(), 600), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _save(self, im):
        ext = os.path.splitext(self.path)[1].lower()
        if ext in ('.jpg', '.jpeg') and im.mode == 'RGBA': im = im.convert('RGB')
        im.save(self.path); self.reload()

    def replace_same(self):
        from PIL import Image
        p, _ = QFileDialog.getOpenFileName(self, 'اختر صورة', '', 'Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)')
        if not p: return
        src = Image.open(p).convert('RGBA'); w, h = self.size
        src.thumbnail((w, h), Image.LANCZOS)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        canvas.paste(src, ((w - src.width) // 2, (h - src.height) // 2), src)
        self._save(canvas)

    def resize_img(self):
        from PIL import Image
        t, ok = QInputDialog.getText(self, 'المقاس', 'العرض×الارتفاع:', text='%dx%d' % self.size)
        m = re.match(r'\s*(\d+)\s*[x×*]\s*(\d+)', t or '')
        if ok and m: self._save(Image.open(self.path).resize((int(m[1]), int(m[2])), Image.LANCZOS))

    def convert(self):
        from PIL import Image
        p, _ = QFileDialog.getSaveFileName(self, 'حفظ بصيغة أخرى', os.path.splitext(self.path)[0] + '.png',
                                           'PNG (*.png);;JPG (*.jpg);;BMP (*.bmp)')
        if p:
            im = Image.open(self.path)
            (im.convert('RGB') if p.lower().endswith(('.jpg', '.jpeg', '.bmp')) else im).save(p)


# ---------------------------------------------------------------- PARAM.SFO (all keys)
class SfoEditor(_Base):
    def __init__(self, parent, path):
        super().__init__(parent, path, '⚙ محرر PARAM.SFO')
        self.t = QTableWidget(0, 4); self.t.setHorizontalHeaderLabels(['المفتاح', 'القيمة', 'النوع', 'الحد الأقصى'])
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.v.addWidget(QLabel('عدّل القيم في عمود "القيمة" ثم احفظ. النصوص لها حد أقصى للطول.'))
        self.v.addWidget(self.t, 1)
        self.add_btn('💾 حفظ', self.save, keys='Ctrl+S')
        self.common_buttons(); self.reload()

    def _entries(self, data):
        _, _, kt, dt, n = struct.unpack('<IIIII', data[:20])
        for i in range(n):
            e = 20 + i * 16
            ko, fmt, ln, mx, do = struct.unpack('<HHIII', data[e:e + 16])
            yield e, data[kt + ko:data.index(b'\0', kt + ko)].decode(), fmt, ln, mx, dt + do

    def reload(self):
        data = open(self.path, 'rb').read(); self.t.setRowCount(0)
        for e, key, fmt, ln, mx, off in self._entries(data):
            r = self.t.rowCount(); self.t.insertRow(r)
            if fmt == 0x0404:
                val, typ = str(struct.unpack('<I', data[off:off + 4])[0]), 'رقم'
            else:
                val, typ = data[off:off + ln].rstrip(b'\0').decode('utf-8', 'replace'), 'نص'
            if key == 'CATEGORY' and val in CATEGORIES: typ += ' (%s)' % CATEGORIES[val]
            for c, x in enumerate([key, val, typ, str(mx)]):
                it = QTableWidgetItem(x)
                if c != 1: it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.t.setItem(r, c, it)

    def save(self):
        data = bytearray(open(self.path, 'rb').read())
        for r, (e, key, fmt, ln, mx, off) in enumerate(list(self._entries(bytes(data)))):
            val = self.t.item(r, 1).text()
            if fmt == 0x0404:
                if not val.isdigit():
                    QMessageBox.warning(self, 'خطأ', '%s يجب أن يكون رقماً' % key); return
                struct.pack_into('<I', data, off, int(val))
            else:
                raw = val.encode('utf-8')
                if len(raw) + 1 > mx:
                    QMessageBox.warning(self, 'خطأ', '%s أطول من الحد (%d بايت)' % (key, mx - 1)); return
                raw += b'\0'
                if fmt == 0x0004: raw = raw[:-1]  # utf8-special (no terminator)
                data[off:off + mx] = raw + b'\0' * (mx - len(raw))
                struct.pack_into('<I', data, e + 4, len(raw))
        open(self.path, 'wb').write(data)
        QMessageBox.information(self, 'تم', 'تم حفظ PARAM.SFO')


# ---------------------------------------------------------------- fonts
class FontViewer(_Base):
    def __init__(self, parent, path):
        super().__init__(parent, path, '🔤 الخط')
        fid = QFontDatabase.addApplicationFont(path)
        fam = QFontDatabase.applicationFontFamilies(fid)
        lbl = QLabel(('اسم الخط: %s\n\n' % (fam[0] if fam else 'غير معروف')))
        prev = QLabel('أبجد هوز حطي كلمن — ألعاب بلايستيشن\nABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789')
        if fam: prev.setFont(QFont(fam[0], 28))
        prev.setStyleSheet('background:#0b0f1f;padding:20px;border-radius:12px')
        self.v.addWidget(lbl); self.v.addWidget(prev, 1)
        self.common_buttons()


# ---------------------------------------------------------------- media (export/replace only)
class MediaFile(_Base):
    def __init__(self, parent, path):
        super().__init__(parent, path, '🎵 ملف وسائط')
        self.v.addWidget(QLabel('ملف %s بحجم %d KB.\nملفات PS3 الصوتية/المرئية (AT3/PAM) تُعدّل ببرامج خارجية:\n'
                                'صدّر الملف، عدّله، ثم استخدم "استبدال بملف".'
                                % (os.path.splitext(self.path)[1].upper(), os.path.getsize(self.path) // 1024)))
        self.v.addStretch(); self.common_buttons()


# ---------------------------------------------------------------- hex (any file)
class HexEditor(_Base):
    ROW = 16
    PAGE = 0x4000

    def __init__(self, parent, path):
        super().__init__(parent, path, '🔢 محرر Hex')
        self.data = bytearray(open(path, 'rb').read()); self.pos = 0
        nav = QHBoxLayout()
        self.e_off = QLineEdit('0'); self.e_off.setMaximumWidth(160)
        self.e_off.returnPressed.connect(self.goto)
        self.e_find = QLineEdit(); self.e_find.setPlaceholderText('بحث: نص أو hex مثل 7F454C46 — Enter للبحث')
        self.e_find.returnPressed.connect(self.search)
        for w in (QLabel('العنوان (hex) + Enter:'), self.e_off, self.e_find): nav.addWidget(w)
        self.v.addLayout(nav)
        self.view = QPlainTextEdit(); self.view.setReadOnly(True); self.view.setFont(QFont('Consolas', 11))
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap); self.view.setLayoutDirection(Qt.LeftToRight)
        self.v.addWidget(self.view, 1)
        self.status = QLabel(); self.v.addWidget(self.status)
        if self.data[:4] in (b'SCE\x00', b'NPD\x00'):
            self.add_btn('🔓 فك التشفير', self.decrypt)
            self.add_btn('-', None)
        self.add_btn('↩ اذهب للعنوان', self.goto, keys='Ctrl+G')
        self.add_btn('◀ الصفحة السابقة', lambda: self.show_at(self.pos - self.PAGE), keys='PgUp')
        self.add_btn('التالي ▶ الصفحة التالية', lambda: self.show_at(self.pos + self.PAGE), keys='PgDown')
        self.add_btn('🔍 بحث', self.search, keys='F3')
        self.add_btn('-', None)
        self.add_btn('✏ كتابة بايتات عند عنوان', self.patch)
        self.add_btn('🔁 بحث واستبدال', self.find_replace, 'ghost')
        self.add_btn('💾 حفظ', self.save, keys='Ctrl+S')
        self.common_buttons(); self.show_at(0)

    def reload(self):
        self.data = bytearray(open(self.path, 'rb').read()); self.show_at(self.pos)

    def show_at(self, pos):
        pos = max(0, min(pos, max(len(self.data) - 1, 0))) // self.ROW * self.ROW
        self.pos = pos; lines = []
        for o in range(pos, min(pos + self.PAGE, len(self.data)), self.ROW):
            ch = self.data[o:o + self.ROW]
            hx = ' '.join('%02X' % b for b in ch).ljust(self.ROW * 3 - 1)
            asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in ch)
            lines.append('%08X  %s  %s' % (o, hx, asc))
        self.view.setPlainText('\n'.join(lines))
        self.status.setText('الحجم: %d بايت (0x%X)   •   يعرض من 0x%X' % (len(self.data), len(self.data), pos))

    def goto(self):
        try: self.show_at(int(self.e_off.text(), 16))
        except ValueError: pass

    def _parse(self, t):
        t = t.strip()
        if re.fullmatch(r'([0-9A-Fa-f]{2}\s*)+', t): return bytes.fromhex(t.replace(' ', ''))
        return t.encode('utf-8')

    def search(self):
        if not self.e_find.text(): return
        start = getattr(self, 'last_hit', -1) + 1
        i = self.data.find(self._parse(self.e_find.text()), start)
        if i < 0: i = self.data.find(self._parse(self.e_find.text()))
        if i < 0: QMessageBox.information(self, 'بحث', 'غير موجود'); return
        self.last_hit = i
        self.show_at(i); self.status.setText(self.status.text() + '   •   وُجد عند 0x%X' % i)

    def patch(self):
        off, ok = QInputDialog.getText(self, 'كتابة', 'العنوان (hex):', text='%X' % self.pos)
        if not ok: return
        val, ok = QInputDialog.getText(self, 'كتابة', 'البايتات (hex مثل 00 FF) أو نص:')
        if not ok or not val: return
        o = int(off, 16); b = self._parse(val)
        if o + len(b) > len(self.data): self.data.extend(b'\0' * (o + len(b) - len(self.data)))
        self.data[o:o + len(b)] = b; self.show_at(o)

    def find_replace(self):
        a, ok = QInputDialog.getText(self, 'بحث واستبدال', 'ابحث عن (hex أو نص):')
        if not ok or not a: return
        b, ok = QInputDialog.getText(self, 'بحث واستبدال', 'استبدل بـ (نفس الطول يُفضّل):')
        if not ok: return
        a, b = self._parse(a), self._parse(b)
        n = self.data.count(a)
        if len(a) != len(b) and QMessageBox.question(self, 'تنبيه', 'الطول مختلف وقد يكسر الملف. متابعة؟') != QMessageBox.Yes:
            return
        self.data = bytearray(bytes(self.data).replace(a, b)); self.show_at(self.pos)
        QMessageBox.information(self, 'استبدال', 'تم استبدال %d مرة (احفظ لتثبيت التغيير)' % n)

    def save(self):
        open(self.path, 'wb').write(self.data); QMessageBox.information(self, 'تم', 'تم الحفظ')

    def decrypt(self):
        from .core.ps3crypto import decrypt_file
        from PySide6.QtCore import QSettings
        rap = QSettings('Cruyfford', 'ArcadiaStudio').value('rap_dir', '')
        try:
            out, how, n = decrypt_file(self.path, rap_dirs=[rap, os.path.dirname(self.path)])
        except Exception as e:  # noqa
            QMessageBox.critical(self, 'فك التشفير', str(e)); return
        QMessageBox.information(self, 'تم', 'تم فك التشفير (%s):\n%s' % (how, out))
        open_editor(self.parent(), out, 'hex')


EDITORS = {'text': TextEditor, 'image': ImageEditor, 'sfo': SfoEditor, 'font': FontViewer, 'media': MediaFile, 'hex': HexEditor}


def open_editor(parent, path, force=None):
    cls = EDITORS[force or kind_of(path)]
    if cls is HexEditor and os.path.getsize(path) > 512 << 20:
        QMessageBox.warning(parent, 'ملف كبير', 'الملف أكبر من 512 MB — استخدم التصدير والتعديل ببرنامج خارجي'); return None
    dlg = cls(parent, path); dlg.show()
    return dlg

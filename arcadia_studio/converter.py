"""Game converter window: PS1 (bin/cue) -> PKG, PS2 (iso/bin) -> PS2 Classic BIN.ENC.
Each console gets its own tab and its own store section."""
import os
import re
import shutil
import tempfile
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from .ui_common import ClickLabel
from PySide6.QtWidgets import (QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
                               QPushButton, QFileDialog, QCheckBox, QLabel, QPlainTextEdit, QProgressBar, QMessageBox)

from .core.ps1conv import convert_ps1
from .core.ps2conv import convert_ps2
from .core.ps3 import PS3
from .core.publish import upload_archive
from .core.xmb import STORE_PREFIX

FOLDER = 'type:x-xmb/folder-pixmap'
ACTION = 'type:x-xmb/module-action'
WEBMAN = 'http://127.0.0.1/download.ps3'

SECTIONS = {   # console -> (main menu key, new section key, title, info, icon, bg)
    'PS1': ('juegos_ps1', 'arc_conv_ps1', 'ألعابي المحوّلة - PS1', 'ألعاب PS1 حوّلتها بأركاديا', 'PS1.png', 'BG_PS1.png'),
    'PS2': ('juegos_ps2', 'arc_conv_ps2', 'ألعابي المحوّلة - PS2', 'تتطلب PS2 Classics Placeholder مفعّل', 'PS2.png', 'BG_PS2.png'),
}


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', s).strip('_') or 'game'


class ConsoleTab(QWidget):
    def __init__(self, dlg, console):
        super().__init__(); self.dlg, self.console = dlg, console
        fl = QFormLayout(self); fl.setLabelAlignment(Qt.AlignRight)
        self.files = []
        self.e_files = ClickLabel('📂 اضغط هنا لاختيار ملف اللعبة، أو اسحبه وأفلته')
        self.e_files.setStyleSheet('background:#0b0f1f;border:1px dashed #4a5bd6;border-radius:9px;padding:12px;color:#9fb4ff')
        self.e_files.setCursor(Qt.PointingHandCursor); self.e_files.clicked.connect(self.pick)
        self.setAcceptDrops(True)
        r = QHBoxLayout(); r.addWidget(self.e_files)
        hint = 'ملف .cue (أو أكثر للألعاب متعددة الأقراص)' if console == 'PS1' else 'ملف .iso أو .bin/.cue'
        fl.addRow('ملف اللعبة', r); fl.addRow('', QLabel(hint))
        self.e_title = QLineEdit(); fl.addRow('اسم اللعبة', self.e_title)
        self.cover = None
        self.cover_lbl = ClickLabel('🖼\nاضغط لاختيار\nالغلاف'); self.cover_lbl.setFixedSize(120, 120); self.cover_lbl.setAlignment(Qt.AlignCenter)
        self.cover_lbl.setStyleSheet('background:#0b0f1f;border:1px dashed #4a5bd6;border-radius:8px;color:#9fb4ff')
        self.cover_lbl.setCursor(Qt.PointingHandCursor); self.cover_lbl.clicked.connect(self.pick_cover)
        r2 = QHBoxLayout(); r2.addWidget(self.cover_lbl); r2.addStretch(); fl.addRow('الغلاف', r2)
        self.c_send = QCheckBox('إرسال للجهاز مباشرة' + (' وتثبيتها' if console == 'PS1' else ' (إلى /dev_hdd0/PS2ISO)'))
        self.c_store = QCheckBox('رفعها على archive.org وإضافتها لقسم "%s" في المتجر' % SECTIONS[console][2])
        self.c_send.setChecked(bool(dlg.win.ps3_ip()))
        fl.addRow(self.c_send); fl.addRow(self.c_store)
        go = QPushButton('🎮 تحويل'); go.clicked.connect(self.run); fl.addRow(go)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        fs = [u.toLocalFile() for u in e.mimeData().urls()]
        imgs = [f for f in fs if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        games = [f for f in fs if f.lower().endswith(('.cue', '.ccd', '.iso', '.bin'))]
        if imgs: self.set_cover(imgs[0])
        if games: self.set_files(games)

    def set_files(self, fs):
        self.files = fs if self.console == 'PS1' else fs[:1]
        self.e_files.setText('🎮 ' + ' + '.join(os.path.basename(f) for f in self.files))
        if not self.e_title.text():
            t = re.sub(r'\s*[\(\[].*?[\)\]]', '', os.path.splitext(os.path.basename(self.files[0]))[0]).strip()
            self.e_title.setText(t)

    def set_cover(self, f):
        self.cover = f
        self.cover_lbl.setPixmap(QPixmap(f).scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def pick(self):
        flt = 'PS1 (*.cue *.ccd)' if self.console == 'PS1' else 'PS2 (*.iso *.bin *.cue)'
        fs, _ = QFileDialog.getOpenFileNames(self, 'اختر ملف اللعبة', '', flt)
        if fs: self.set_files(fs)

    def pick_cover(self):
        f, _ = QFileDialog.getOpenFileName(self, 'اختر صورة الغلاف', '', 'Images (*.png *.jpg *.jpeg)')
        if f: self.set_cover(f)

    def run(self):
        if not self.files:
            QMessageBox.warning(self, 'تنبيه', 'اختر ملف اللعبة أولاً'); return
        win = self.dlg.win
        if self.c_store.isChecked() and not win.store:
            QMessageBox.warning(self, 'تنبيه', 'افتح المتجر في البرنامج أولاً لإضافة اللعبة إليه'); return
        if self.c_store.isChecked() and not all(win.settings.value(k, '') for k in ('ia_item', 'ia_access', 'ia_secret')):
            if not win.publish_setup(): return
        out_dir = QFileDialog.getExistingDirectory(self, 'أين تحفظ الملف الناتج؟')
        if not out_dir: return
        self.dlg.start(self.console, list(self.files), self.e_title.text().strip(), self.cover, out_dir,
                       self.c_send.isChecked(), self.c_store.isChecked())


class ConverterDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('🎮 محوّل الألعاب — PS1 / PS2 إلى PS3'); self.resize(760, 640)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(ConsoleTab(self, 'PS1'), 'PS1 — بلايستيشن 1')
        tabs.addTab(ConsoleTab(self, 'PS2'), 'PS2 — بلايستيشن 2')
        v.addWidget(tabs)
        self.prog = QProgressBar(); v.addWidget(self.prog)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(200); v.addWidget(self.log)
        v.addWidget(QLabel('PS2 يتطلب على الجهاز: PS2 Classics Placeholder مفعّلاً (موجود في المتجر: رخصة PS2 شاملة).'))

    def say(self, t):
        self.log.appendPlainText(t)

    def start(self, console, files, title, cover, out_dir, send, store):
        from .app import Worker
        win = self.win
        name = safe_name(title or os.path.splitext(os.path.basename(files[0]))[0])
        msgs = []

        def job(prog):
            if console == 'PS1':
                out = os.path.join(out_dir, name + '.pkg')
                convert_ps1(files, out, title=title or None, cover=cover, log=msgs.append)
                gid = None
            else:
                out = os.path.join(out_dir, name + '.BIN.ENC')
                out, gid = convert_ps2(files[0], out, progress=prog, log=msgs.append)
            res = {'out': out, 'gid': gid}
            if send:
                ps3 = PS3(win.ps3_ip())
                if console == 'PS1':
                    msgs.append('رفع الحزمة للجهاز وتثبيتها...'); ps3.send_pkg(out, True, prog)
                else:
                    msgs.append('رفع اللعبة إلى /dev_hdd0/PS2ISO ...')
                    ps3.upload(out, '/dev_hdd0/PS2ISO/' + os.path.basename(out), prog)
                res['sent'] = True
            if store:
                item, acc, sec = (win.settings.value(k, '') for k in ('ia_item', 'ia_access', 'ia_secret'))
                msgs.append('رفع الملف إلى archive.org ...')
                res['url'] = upload_archive(item, acc, sec, out, name=os.path.basename(out), progress=prog)
            return res

        def done(res):
            for m in msgs: self.say(m)
            if store and 'url' in res:
                self.add_to_store(console, title or name, res['url'], cover, os.path.basename(res['out']))
            t = 'تم التحويل: %s' % res['out']
            if res.get('sent'): t += '\nأُرسلت للجهاز.'
            if res.get('url'): t += '\nأُضيفت لقسم "%s" — احفظ ثم انشر للجميع.' % SECTIONS[console][2]
            if console == 'PS2' and res.get('sent'):
                t += '\nشغّلها من webMAN (قسم PS2) — يجب أن يكون PS2 Classics Placeholder مفعّلاً.'
            self.say(t); QMessageBox.information(self, 'تم', t)

        def failed(e):
            for m in msgs: self.say(m)
            self.say('خطأ: ' + e); QMessageBox.critical(self, 'خطأ', e)
        self.say('بدء تحويل %s: %s' % (console, ', '.join(os.path.basename(f) for f in files)))
        self.w = Worker(job)
        self.w.progress.connect(lambda a, b: (self.prog.setMaximum(max(b, 1)), self.prog.setValue(a)))
        self.w.done.connect(done); self.w.failed.connect(failed)
        self.w.start()

    # ---- store section per console
    def section_view(self, console):
        s = self.win.store
        main_key, sec_key, title, info, icon, bg = SECTIONS[console]
        from .core.xmb import ROOT_XML, ROOT_VIEW
        main = s.children(ROOT_XML, ROOT_VIEW)[0]['target']
        cons = next(c for c in s.children(*main) if c['key'] == main_key)
        f, v = cons['target']
        if not any(c['key'] == sec_key for c in s.children(f, v)):
            s.add_view(f, sec_key, title)
            s.add_node(f, v, sec_key, {'icon': STORE_PREFIX + 'USRDIR/XMB/' + icon, 'title': title, 'info': info,
                                       'pic': STORE_PREFIX + 'USRDIR/XMB/' + bg}, FOLDER, '#' + sec_key, index=0)
        return f, sec_key

    def add_to_store(self, console, title, url, cover, fname):
        s = self.win.store
        f, v = self.section_view(console)
        icon = STORE_PREFIX + 'USRDIR/XMB/%s.png' % console
        if cover:
            rel = 'USRDIR/ARCADIA/ICONS/%s%s' % (safe_name(fname), os.path.splitext(cover)[1].lower())
            os.makedirs(os.path.join(s.root, os.path.dirname(rel)), exist_ok=True)
            shutil.copy(cover, os.path.join(s.root, rel)); s.mark_changed(rel)
            icon = STORE_PREFIX + rel
        if console == 'PS1':
            action, info = WEBMAN + '?url=' + url, 'PKG — يُنزّل في الخلفية ثم ثبّته من Install Package Files'
        else:
            action, info = WEBMAN + '?to=/dev_hdd0/PS2ISO&url=' + url, 'PS2 Classic — يُنزّل إلى PS2ISO (webMAN)'
        s.add_node(f, v, 'conv_%d' % int(time.time()),
                   {'icon': icon, 'title': title, 'info': info, 'pic': STORE_PREFIX + 'USRDIR/XMB/' + SECTIONS[console][5],
                    'ingame': 'disable', 'module_name': 'webbrowser_plugin', 'module_action': action}, ACTION)
        self.win.save(); self.win.rebuild_tree()

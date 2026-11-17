"""Back up your own PSN licenses (exdata .rif + act.dat + IDPS) as .rap files for the decrypter."""
import os

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QLabel, QPushButton,
                               QPlainTextEdit, QFileDialog, QMessageBox)

from .ui_common import ClickLabel
from .core import licenses as L
from .core.ps3 import PS3
from .core.ps3crypto import parse_idps


def default_rap_dir(win):
    d = win.settings.value('rap_dir', '')
    if d: return d
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser('~')
    return os.path.join(base, 'raps')


class LicensesDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('🔑 استخراج رخص ألعابي من الجهاز'); self.resize(760, 560); self.setAcceptDrops(True)
        v = QVBoxLayout(self)
        info = QLabel('يحوّل رخص الألعاب المشتراة على جهازك (exdata/*.rif) إلى ملفات .rap يستخدمها فك التشفير.\n'
                      'يحتاج ملفات جهازك أنت: act.dat + رخص .rif + رقم IDPS. رخص أي جهاز أو حساب آخر لن تعمل.')
        info.setWordWrap(True); info.setStyleSheet('color:#9fb4ff'); v.addWidget(info)
        fl = QFormLayout(); fl.setLabelAlignment(Qt.AlignRight); v.addLayout(fl)
        self.src = QComboBox()
        self.src.addItems(['📡 مباشرة من الجهاز (FTP — webMAN)', '📁 من مجلد exdata منسوخ على الكمبيوتر'])
        fl.addRow('المصدر', self.src)
        self.ip = QLineEdit(win.ps3_ip()); self.ip.setPlaceholderText('IP الجهاز')
        fl.addRow('IP الجهاز', self.ip)
        self.folder = ClickLabel('📂 اضغط لاختيار مجلد exdata (فيه act.dat وملفات .rif)')
        self.folder.setStyleSheet('background:#0b0f1f;border:1px dashed #4a5bd6;border-radius:9px;padding:10px;color:#9fb4ff')
        self.folder.setCursor(Qt.PointingHandCursor); self.folder.clicked.connect(self.pick_folder); self.folder_path = ''
        fl.addRow('المجلد', self.folder)
        self.idps = QLineEdit(); self.idps.setPlaceholderText('32 خانة hex مثل 0000000100850009... — أو اسحب ملف IDPS.bin هنا')
        self.idps.setEchoMode(QLineEdit.Password)
        fl.addRow('IDPS', self.idps)
        pick = ClickLabel('أو اضغط هنا لاختيار ملف IDPS (idps.bin / idps.hex)'); pick.setStyleSheet('color:#7aa0ff')
        pick.setCursor(Qt.PointingHandCursor); pick.clicked.connect(self.pick_idps); fl.addRow('', pick)
        self.out = QLineEdit(default_rap_dir(win)); fl.addRow('حفظ الرخص في', self.out)
        self.src.currentIndexChanged.connect(self.mode); self.mode()
        go = QPushButton('🔑 استخراج رخصي'); go.clicked.connect(self.run); v.addWidget(go)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log, 1)
        warn = QLabel('🔒 IDPS هوية جهازك: لا يُحفظ في البرنامج ولا يُرسل لأي مكان. لا تشاركه مع أحد.')
        warn.setStyleSheet('color:#ffcc66'); v.addWidget(warn)

    def mode(self):
        ftp = self.src.currentIndex() == 0
        self.ip.setEnabled(ftp); self.folder.setEnabled(not ftp)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'اختر مجلد exdata')
        if d: self.folder_path = d; self.folder.setText('📁 ' + d)

    def pick_idps(self):
        f, _ = QFileDialog.getOpenFileName(self, 'اختر ملف IDPS')
        if f: self.load_idps(f)

    def load_idps(self, f):
        try:
            self.idps.setText(parse_idps(open(f, 'rb').read()).hex())
        except Exception as e:  # noqa
            QMessageBox.warning(self, 'IDPS', str(e))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                self.src.setCurrentIndex(1); self.folder_path = p; self.folder.setText('📁 ' + p)
            elif os.path.isfile(p) and os.path.getsize(p) < 4096:
                self.load_idps(p)

    def run(self):
        try:
            idps = parse_idps(self.idps.text())
        except Exception as e:  # noqa
            QMessageBox.warning(self, 'IDPS', str(e)); return
        out = self.out.text().strip() or default_rap_dir(self.win)
        if self.src.currentIndex() == 0:
            ip = self.ip.text().strip()
            if not ip: QMessageBox.warning(self, 'تنبيه', 'اكتب IP الجهاز'); return
            self.win.settings.setValue('ps3_ip', ip)
            collect = lambda prog: L.collect_ftp(PS3(ip), prog)   # noqa
        else:
            if not self.folder_path: QMessageBox.warning(self, 'تنبيه', 'اختر مجلد exdata'); return
            fp = self.folder_path
            collect = lambda prog: L.collect_folder(fp)   # noqa

        def job(prog):
            sets = collect(prog)
            if not sets: raise ValueError('ما لقيت أي مستخدم فيه act.dat على الجهاز (هل الحساب مفعّل؟)')
            return sets, L.make_raps(sets, idps, out)

        def done(r):
            sets, (saved, bad) = r
            users = ', '.join('%s (%d رخصة)' % (u, len(x)) for u, _, x in sets)
            self.log.appendPlainText('المستخدمون: ' + users)
            for c in saved: self.log.appendPlainText('✅ ' + c + '.rap')
            for b in bad: self.log.appendPlainText('❌ ' + b)
            if saved:
                self.win.settings.setValue('rap_dir', out)
                self.log.appendPlainText('\nتم حفظ %d رخصة في:\n%s\nوتم تعيينه كمجلد الرخص — فك التشفير يستخدمها تلقائياً.\n'
                                         'ملاحظة: إذا IDPS خطأ ستنحفظ الملفات لكن فك التشفير يفشل.' % (len(saved), out))
        self.log.appendPlainText('⏳ جاري جمع الرخص...')
        self.win.run_bg(job, done, 'جاري استخراج رخص ألعابك...')

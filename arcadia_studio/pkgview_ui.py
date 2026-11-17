"""PKG viewer: open any PS3/PSP package (debug or retail), see its info and files, extract."""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTreeWidget,
                               QTreeWidgetItem, QFileDialog, QMessageBox, QProgressBar, QFormLayout, QWidget,
                               QHeaderView, QSplitter)

from .core.pkg import PkgReader
from .core.sfo import parse_sfo, LABELS, CATEGORIES
from .core.linkcheck import human


class PkgViewer(QDialog):
    def __init__(self, win, path=None):
        super().__init__(win); self.win = win; self.reader = None
        self.setWindowTitle('📦 عارض PKG'); self.resize(1300, 820); self.setAcceptDrops(True)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        b = QPushButton('📂 فتح ملف PKG'); b.clicked.connect(self.pick)
        self.lbl = QLabel('افتح ملف PKG أو اسحبه وأفلته هنا'); self.lbl.setStyleSheet('color:#9fb4ff')
        top.addWidget(b); top.addWidget(self.lbl, 1)
        v.addLayout(top)
        sp = QSplitter()
        left = QWidget(); lv = QVBoxLayout(left)
        self.pic = QLabel(); self.pic.setFixedSize(420, 236); self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setStyleSheet('background:#0b0f1f;border-radius:12px;')
        self.icon = QLabel(); self.icon.setFixedSize(320, 176); self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet('background:#0b0f1f;border-radius:12px;')
        lv.addWidget(self.pic); lv.addWidget(self.icon, 0, Qt.AlignHCenter)
        self.form = QFormLayout(); self.form.setLabelAlignment(Qt.AlignRight); lv.addLayout(self.form); lv.addStretch()
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['الملف', 'الحجم', 'النوع'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        sp.addWidget(self.tree); sp.addWidget(left); sp.setSizes([800, 460])
        v.addWidget(sp, 1)
        self.prog = QProgressBar(); self.prog.hide(); v.addWidget(self.prog)
        bot = QHBoxLayout()
        for t, fn, k in [('⬇ استخراج الكل', self.extract_all, ''), ('⬇ استخراج المحدد', self.extract_sel, 'ghost'),
                         ('📤 إرسال الحزمة للجهاز وتثبيتها', self.send, 'ghost')]:
            bb = QPushButton(t); bb.clicked.connect(fn)
            if k: bb.setProperty('kind', k)
            bot.addWidget(bb)
        v.addLayout(bot)
        if path: self.load(path)

    # drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith('.pkg'):
                self.load(u.toLocalFile()); break

    def pick(self):
        p, _ = QFileDialog.getOpenFileName(self, 'اختر ملف PKG', '', 'PKG (*.pkg)')
        if p: self.load(p)

    def load(self, path):
        try:
            r = PkgReader(path); ents = list(r.entries())
        except Exception as e:  # noqa
            QMessageBox.critical(self, 'خطأ', 'تعذّر فتح الحزمة:\n%s' % e); return
        self.reader, self.ents = r, ents
        files = [e for e in ents if e[3] & 0xFF != 4]
        self.lbl.setText('\u200f%s   •   %s   •   عدد الملفات: %d   •   الحجم: \u200e%s' % (os.path.basename(path), r.kind, len(files), human(os.path.getsize(path))))
        # info
        while self.form.rowCount(): self.form.removeRow(0)
        sfo_name = next((e[0] for e in ents if e[0].upper().endswith('PARAM.SFO') and e[0].count('/') == 0), None)
        sfo = parse_sfo(r.read_file(sfo_name)) if sfo_name else {}
        rows = [('Content ID', r.content_id), ('نوع الحزمة', r.kind)]
        for k in ('TITLE', 'TITLE_ID', 'CATEGORY', 'APP_VER', 'VERSION', 'PS3_SYSTEM_VER', 'RESOLUTION'):
            if k in sfo:
                val = sfo[k]
                if k == 'CATEGORY': val = '%s (%s)' % (val, CATEGORIES.get(val, '؟'))
                rows.append((LABELS.get(k, k), str(val)))
        for k, val in rows:
            l = QLabel(val); l.setTextInteractionFlags(Qt.TextSelectableByMouse); l.setWordWrap(True)
            self.form.addRow(k + ':', l)
        for lbl, name in ((self.icon, 'ICON0.PNG'), (self.pic, 'PIC1.PNG')):
            data = r.read_file(name)
            pm = QPixmap()
            if data: pm.loadFromData(data)
            lbl.setPixmap(pm.scaled(lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else QPixmap())
            if pm.isNull(): lbl.setText('لا توجد %s' % name)
        # tree
        self.tree.clear(); nodes = {}
        for name, do, ds, fl in sorted(ents, key=lambda e: e[0].lower()):
            parts = name.split('/')
            parent = self.tree.invisibleRootItem()
            for i in range(len(parts)):
                key = '/'.join(parts[:i + 1])
                if key not in nodes:
                    is_dir = i < len(parts) - 1 or fl & 0xFF == 4
                    it = QTreeWidgetItem([('📁 ' if is_dir else '📄 ') + parts[i], '' if is_dir else human(ds),
                                          'مجلد' if is_dir else os.path.splitext(parts[i])[1].upper().lstrip('.')])
                    it.setData(0, Qt.UserRole, key)
                    it.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
                    parent.addChild(it); nodes[key] = it
                parent = nodes[key]
        for i in range(self.tree.topLevelItemCount()): self.tree.topLevelItem(i).setExpanded(True)

    # actions
    def _run(self, fn, msg_done):
        from .app import Worker
        self.prog.show(); self.prog.setValue(0)
        self.w = Worker(fn)
        self.w.progress.connect(lambda a, b: (self.prog.setMaximum(max(b, 1)), self.prog.setValue(a)))
        self.w.done.connect(lambda r: (self.prog.hide(), msg_done(r)))
        self.w.failed.connect(lambda e: (self.prog.hide(), QMessageBox.critical(self, 'خطأ', e)))
        self.w.start()

    def _open(self, d):
        if sys.platform.startswith('win'): os.startfile(d)  # noqa
        else: subprocess.Popen(['xdg-open', d])

    def extract_all(self):
        if not self.reader: return
        d = QFileDialog.getExistingDirectory(self, 'أين تستخرج الحزمة؟')
        if not d: return
        out = os.path.join(d, self.reader.content_id or os.path.splitext(os.path.basename(self.reader.path))[0])
        r = PkgReader(self.reader.path)
        self._run(lambda prog: (r.extract(out, prog), out)[1],
                  lambda o: (QMessageBox.information(self, 'تم', 'تم الاستخراج إلى:\n' + o), self._open(o)))

    def extract_sel(self):
        if not self.reader: return
        names = [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]
        if not names:
            QMessageBox.information(self, 'تنبيه', 'حدد ملفات أو مجلدات من الشجرة'); return
        d = QFileDialog.getExistingDirectory(self, 'أين تستخرج الملفات المحددة؟')
        if not d: return
        r = PkgReader(self.reader.path)
        self._run(lambda prog: r.extract_some(set(names), d, prog),
                  lambda n: (QMessageBox.information(self, 'تم', 'تم استخراج %d عنصر إلى:\n%s' % (n, d)), self._open(d)))

    def send(self):
        if self.reader: self.win.send_pkg(self.reader.path)

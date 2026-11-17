"""Download manager window (fast multi-connection downloads)."""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QFileDialog,
                               QInputDialog, QMessageBox)

from .core.downloader import Download
from .core.linkcheck import human
from .core.ps3 import PS3

STATE = {'waiting': '⏳ في الانتظار', 'starting': '⏳ يبدأ', 'connecting': '🔌 اتصال', 'downloading': '⬇ يتنزّل',
         'paused': '⏸ متوقف', 'done': '✅ اكتمل', 'error': '❌ خطأ', 'cancelled': '✖ أُلغي'}


class DownloadsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('⬇ التنزيلات — تنزيل سريع متعدد الاتصالات'); self.resize(1100, 560)
        self.jobs = []   # (Download, title, icon)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.folder = win.settings.value('dl_folder', os.path.join(os.path.expanduser('~'), 'Downloads', 'Arcadia'))
        self.lbl_folder = QLabel('مجلد الحفظ: ' + self.folder)
        bf = QPushButton('📁 تغيير المجلد'); bf.setProperty('kind', 'ghost'); bf.clicked.connect(self.pick_folder)
        self.conns = QComboBox(); self.conns.addItems(['1', '2', '4', '8', '16']); self.conns.setCurrentText(str(win.settings.value('dl_conns', '8')))
        self.conns.currentTextChanged.connect(lambda t: win.settings.setValue('dl_conns', t))
        badd = QPushButton('➕ إضافة رابط'); badd.clicked.connect(self.add_url)
        top.addWidget(self.lbl_folder); top.addStretch(); top.addWidget(bf)
        top.addWidget(QLabel('عدد الاتصالات:')); top.addWidget(self.conns); top.addWidget(badd)
        v.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['اللعبة', 'الحجم', 'التقدم', 'السرعة', 'الحالة'])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch); hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for c in (1, 3, 4): hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setIconSize(QSize(64, 36)); self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table)
        bot = QHBoxLayout()
        for t, fn, kind in [('⏸ إيقاف / ▶ استئناف', self.toggle, 'ghost'), ('✖ إلغاء', self.cancel, 'danger'),
                            ('📂 فتح المجلد', self.open_folder, 'ghost'), ('📦 عرض محتوى الحزمة', self.view_pkg, 'ghost'), ('📤 إرسال للجهاز', self.send_ps3, '')]:
            b = QPushButton(t); b.clicked.connect(fn)
            if kind: b.setProperty('kind', kind)
            bot.addWidget(b)
        v.addLayout(bot)
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(500)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'اختر مجلد التنزيلات', self.folder)
        if d:
            self.folder = d; self.win.settings.setValue('dl_folder', d); self.lbl_folder.setText('مجلد الحفظ: ' + d)

    def add_url(self):
        u, ok = QInputDialog.getText(self, 'إضافة رابط', 'الرابط (مباشر أو صفحة MediaFire):')
        if ok and u.strip(): self.add(u.strip(), u.strip().rsplit('/', 1)[-1])

    def add(self, url, title, icon_path=None):
        d = Download(url, self.folder, connections=int(self.conns.currentText()))
        self.jobs.append((d, title, icon_path))
        r = self.table.rowCount(); self.table.insertRow(r)
        it = QTableWidgetItem(title)
        if icon_path and os.path.exists(icon_path): it.setIcon(QIcon(icon_path))
        self.table.setItem(r, 0, it)
        for c in (1, 3, 4): self.table.setItem(r, c, QTableWidgetItem(''))
        pb = QProgressBar(); pb.setRange(0, 1000); self.table.setCellWidget(r, 2, pb)
        d.start(); self.show(); self.raise_()
        return d

    def refresh(self):
        for r, (d, title, _) in enumerate(self.jobs):
            pb = self.table.cellWidget(r, 2)
            if d.total: pb.setValue(int(1000 * d.done / d.total)); pb.setFormat('%.1f%%' % (100.0 * d.done / d.total))
            elif d.state == 'downloading': pb.setRange(0, 0)
            self.table.item(r, 1).setText(human(d.total) if d.total else human(d.done))
            self.table.item(r, 3).setText(('%s/ث' % human(d.speed)) if d.state == 'downloading' else '')
            st = STATE.get(d.state, d.state)
            if d.state == 'error': st += ' — ' + d.error
            if d.state == 'downloading' and d.speed > 0 and d.total:
                left = (d.total - d.done) / d.speed
                st += '  (متبقي %d:%02d)' % (left // 60, left % 60)
            self.table.item(r, 4).setText(st)
            if d.state == 'done' and d.name: self.table.item(r, 0).setToolTip(d.path)

    def selected(self):
        return [self.jobs[i] for i in sorted({x.row() for x in self.table.selectedIndexes()})]

    def toggle(self):
        for d, _, _ in self.selected():
            if d.state in ('downloading', 'connecting', 'starting'): d.pause()
            elif d.state in ('paused', 'error'): d.start()

    def cancel(self):
        rows = sorted({x.row() for x in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.jobs[r][0].cancel(); self.table.removeRow(r); del self.jobs[r]

    def open_folder(self):
        os.makedirs(self.folder, exist_ok=True)
        if sys.platform.startswith('win'): os.startfile(self.folder)  # noqa
        else: subprocess.Popen(['xdg-open', self.folder])

    def view_pkg(self):
        done = [d for d, _, _ in self.selected() if d.state == 'done' and d.path.lower().endswith('.pkg')]
        if done: self.win.open_pkgview(done[0].path)
        else: QMessageBox.information(self, 'تنبيه', 'اختر حزمة PKG مكتملة التنزيل')

    def send_ps3(self):
        done = [d for d, _, _ in self.selected() if d.state == 'done']
        if not done:
            QMessageBox.information(self, 'تنبيه', 'اختر تنزيلاً مكتملاً أولاً'); return
        if not self.win.need_ip(): return
        ps3 = PS3(self.win.ps3_ip())
        from .app import Worker

        def job(prog):
            out = []
            for d in done:
                low = d.path.lower()
                if low.endswith('.pkg'):
                    out.append(ps3.send_pkg(d.path, True, prog))
                elif low.endswith('.bin.enc'):
                    rp = '/dev_hdd0/PS2ISO/' + os.path.basename(d.path); ps3.upload(d.path, rp, prog); out.append(rp)
                else:
                    rp = '/dev_hdd0/packages/' + os.path.basename(d.path); ps3.upload(d.path, rp, prog); out.append(rp)
            return out
        self.w = Worker(job)
        self.w.done.connect(lambda r: QMessageBox.information(self, 'تم', 'أُرسل للجهاز:\n' + '\n'.join(r)))
        self.w.failed.connect(lambda e: QMessageBox.critical(self, 'خطأ', e))
        self.w.start()

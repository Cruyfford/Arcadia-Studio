"""Dialog listing store snapshots; right-click to restore or delete."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QMessageBox

from .core import history as H
from .ui_common import context_menu, add


class HistoryDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('🕘 سجل نسخ المتجر'); self.resize(760, 520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel('كل حفظ ينشئ نسخة (آخر %d نسخة). كلك يمين على نسخة ← استرجاع.' % H.KEEP))
        self.t = QTableWidget(0, 4); self.t.setHorizontalHeaderLabels(['الوقت', 'ملاحظة', 'الملفات', 'الحجم'])
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.t.setSelectionBehavior(QTableWidget.SelectRows); self.t.setEditTriggers(QTableWidget.NoEditTriggers)
        self.t.cellDoubleClicked.connect(lambda r, c: self.restore())
        v.addWidget(self.t)
        context_menu(self.t, self.menu)
        self.fill()

    def fill(self):
        self.snaps = H.list_snapshots(self.win.store.root)
        self.t.setRowCount(0)
        for s in self.snaps:
            r = self.t.rowCount(); self.t.insertRow(r)
            for c, val in enumerate((s['time'], s['note'], str(s['files']), '%d KB' % (s['size'] // 1024))):
                self.t.setItem(r, c, QTableWidgetItem(val))

    def cur(self):
        r = self.t.currentRow()
        return self.snaps[r] if 0 <= r < len(self.snaps) else None

    def menu(self, m, pos):
        it = self.t.itemAt(pos)
        if it: self.t.selectRow(it.row())
        s = self.cur()
        add(m, '↩ استرجاع هذه النسخة', self.restore, bool(s))
        add(m, '🗑 حذف النسخة', self.delete, bool(s))
        m.addSeparator()
        add(m, '📸 حفظ نسخة الآن', self.snap_now)

    def snap_now(self):
        self.win.store.save(); H.snapshot(self.win.store.root, 'نسخة يدوية'); self.fill()

    def restore(self):
        s = self.cur()
        if not s: return
        if QMessageBox.question(self, 'استرجاع', 'استرجاع المتجر كما كان في %s؟\n(تُحفظ النسخة الحالية أولاً)' % s['time']) != QMessageBox.Yes:
            return
        names = H.restore(self.win.store.root, s['path'])
        for n in names: self.win.store.mark_changed(n)
        self.win.load_ws(self.win.store.root)
        self.win.statusBar().showMessage('↩ تم استرجاع نسخة %s — احفظ لتثبيتها في المشروع' % s['time'])
        self.fill()

    def delete(self):
        s = self.cur()
        if s:
            os.remove(s['path']); self.fill()

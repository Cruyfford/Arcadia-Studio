"""Arabization tool: list every text inside a game file, type the Arabic translation, write it back in place."""
import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
                               QLineEdit, QCheckBox, QSpinBox, QFileDialog, QMessageBox, QMenuBar)

from .core import textscan as T
from .ui_common import context_menu, add, shortcut

C_OFF, C_ENC, C_MAX, C_ORIG, C_TR, C_LEN = range(6)


class StringsDialog(QDialog):
    def __init__(self, parent, path):
        super().__init__(parent); self.path = path
        self.setWindowTitle('🔤 أداة التعريب — ' + os.path.basename(path)); self.resize(1250, 780)
        v = QVBoxLayout(self)
        mb = QMenuBar(self); v.setMenuBar(mb)
        m = mb.addMenu('⚙ الأدوات'); self._fill(m)
        top = QHBoxLayout()
        self.find = QLineEdit(); self.find.setPlaceholderText('🔍 بحث في النصوص أو الترجمة...')
        self.find.textChanged.connect(self.filter)
        self.only_tr = QCheckBox('المترجم فقط'); self.only_tr.toggled.connect(self.filter)
        self.min_len = QSpinBox(); self.min_len.setRange(2, 64); self.min_len.setValue(4); self.min_len.setPrefix('أقل طول: ')
        self.min_len.valueChanged.connect(self.rescan)
        top.addWidget(self.find, 1); top.addWidget(self.only_tr); top.addWidget(self.min_len)
        v.addLayout(top)
        opts = QHBoxLayout()
        self.shape = QCheckBox('ربط الحروف العربية'); self.shape.setChecked(True)
        self.visual = QCheckBox('عكس الاتجاه (للألعاب التي لا تدعم العربي)'); self.visual.setChecked(True)
        self.spaces = QCheckBox('تعبئة الباقي بمسافات بدل أصفار'); self.spaces.setChecked(False)
        for c in (self.shape, self.visual, self.spaces):
            c.toggled.connect(self.recheck); opts.addWidget(c)
        opts.addStretch(); v.addLayout(opts)
        self.t = QTableWidget(0, 6)
        self.t.setHorizontalHeaderLabels(['العنوان', 'الترميز', 'المساحة', 'النص الأصلي', 'الترجمة (اكتب هنا)', 'الطول'])
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(C_ORIG, QHeaderView.Stretch); hh.setSectionResizeMode(C_TR, QHeaderView.Stretch)
        self.t.setSelectionBehavior(QTableWidget.SelectRows)
        self.t.itemChanged.connect(self.changed)
        v.addWidget(self.t, 1)
        self.st = QLabel(); self.st.setStyleSheet('color:#9fb4ff'); v.addWidget(self.st)
        hint = QLabel('💡 اكتب الترجمة في عمود «الترجمة» — الأحمر يعني أطول من المساحة المتاحة في الملف. '
                      'Ctrl+S حفظ في الملف • كلك يمين لكل الأدوات (CSV للترجمة في Excel...)')
        hint.setStyleSheet('color:#7f8bc0'); hint.setWordWrap(True); v.addWidget(hint)
        context_menu(self.t, lambda m, p: self._fill(m))
        shortcut(self, 'Ctrl+S', self.save)
        self.data = open(path, 'rb').read()
        self.rescan()

    def _fill(self, m):
        add(m, '💾 كتابة الترجمة في الملف', self.save, shortcut='Ctrl+S')
        m.addSeparator()
        add(m, '📤 تصدير CSV (للترجمة في Excel)...', self.export)
        add(m, '📥 استيراد ترجمة من CSV...', self.import_)
        m.addSeparator()
        add(m, '📋 نسخ النص الأصلي إلى الترجمة (للتعديل عليه)', self.copy_orig)
        add(m, '✖ مسح ترجمة المحدد', self.clear_sel)
        add(m, '🔁 إعادة الفحص', self.rescan)

    # ---------- data
    def rescan(self):
        self.entries = T.scan(self.data, self.min_len.value())
        self.t.blockSignals(True); self.t.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            for c, val in ((C_OFF, '0x%X' % e['off']), (C_ENC, 'UTF-16' if e['enc'] == 'utf-16-le' else 'UTF-8'),
                           (C_MAX, '%d' % e['max']), (C_ORIG, e['text'].replace('\n', '⏎')), (C_TR, e.get('tr', '')), (C_LEN, '')):
                it = QTableWidgetItem(val)
                if c != C_TR: it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c == C_ORIG: it.setToolTip(e['text'])
                self.t.setItem(r, c, it)
        self.t.blockSignals(False)
        self.recheck(); self.filter()

    def changed(self, it):
        if it.column() != C_TR: return
        e = self.entries[it.row()]; e['tr'] = it.text().replace('⏎', '\n')
        self.check_row(it.row())
        self.status()

    def check_row(self, r):
        e = self.entries[r]
        self.t.blockSignals(True)
        cell = self.t.item(r, C_LEN)
        if e.get('tr'):
            n = len(T.encoded(e, self.shape.isChecked(), self.visual.isChecked()))
            ok = n <= e['max']
            cell.setText('%d / %d' % (n, e['max']))
            col = QColor('#1f5131') if ok else QColor('#6b1d2a')
        else:
            cell.setText(''); col = QColor(0, 0, 0, 0)
        for c in range(6):
            self.t.item(r, c).setBackground(col)
        self.t.blockSignals(False)

    def recheck(self, *_):
        for r in range(len(self.entries)):
            if self.entries[r].get('tr') or self.t.item(r, C_LEN).text(): self.check_row(r)
        self.status()

    def status(self):
        tr = [e for e in self.entries if e.get('tr')]
        bad = [e for e in tr if len(T.encoded(e, self.shape.isChecked(), self.visual.isChecked())) > e['max']]
        self.st.setText('النصوص: %d   •   مترجم: %d   •   أطول من المساحة: %d' % (len(self.entries), len(tr), len(bad)))

    def filter(self, *_):
        q = self.find.text().strip().lower(); only = self.only_tr.isChecked()
        for r, e in enumerate(self.entries):
            hide = (q and q not in e['text'].lower() and q not in e.get('tr', '').lower()) or (only and not e.get('tr'))
            self.t.setRowHidden(r, bool(hide))

    def rows(self):
        return sorted({i.row() for i in self.t.selectedIndexes()})

    def copy_orig(self):
        for r in self.rows():
            self.t.item(r, C_TR).setText(self.t.item(r, C_ORIG).text())

    def clear_sel(self):
        for r in self.rows():
            self.t.item(r, C_TR).setText('')

    # ---------- files
    def export(self):
        out, _ = QFileDialog.getSaveFileName(self, 'تصدير', os.path.splitext(self.path)[0] + '_texts.csv', 'CSV (*.csv)')
        if out:
            T.export_csv(self.entries, out)
            QMessageBox.information(self, 'تم', 'تم التصدير. ترجم عمود translation ثم استورد الملف.')

    def import_(self):
        p, _ = QFileDialog.getOpenFileName(self, 'استيراد', os.path.dirname(self.path), 'CSV (*.csv)')
        if not p: return
        n = T.import_csv(self.entries, p)
        self.t.blockSignals(True)
        for r, e in enumerate(self.entries): self.t.item(r, C_TR).setText(e.get('tr', ''))
        self.t.blockSignals(False)
        self.recheck(); QMessageBox.information(self, 'تم', 'تم استيراد %d ترجمة' % n)

    def save(self):
        if not any(e.get('tr') for e in self.entries): return
        new, n, skipped = T.inject(self.data, self.entries, self.shape.isChecked(), self.visual.isChecked(),
                                   b' ' if self.spaces.isChecked() else b'\0')
        bak = self.path + '.bak'
        if not os.path.exists(bak): shutil.copy(self.path, bak)
        open(self.path, 'wb').write(new); self.data = new
        msg = 'تمت كتابة %d ترجمة في الملف.\nنسخة احتياطية: %s' % (n, os.path.basename(bak))
        if skipped:
            msg += '\n\nلم تُكتب %d (أطول من المساحة) — اختصرها وأعد الحفظ.' % len(skipped)
        QMessageBox.information(self, 'تم', msg)

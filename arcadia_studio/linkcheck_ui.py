"""Link checker window."""
import csv
import threading
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QProgressBar,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
                               QMessageBox, QApplication)

from .core.linkcheck import collect_links, check_all, human, OK, BROKEN, UNSURE

ICON = {OK: '✅ يعمل', BROKEN: '❌ معطل', UNSURE: '⚠️ غير مؤكد', None: '⏳'}
COLOR = {OK: '#123d2a', BROKEN: '#4a1420', UNSURE: '#4a3a10'}


class CheckThread(QThread):
    result = Signal(str, object)
    progress = Signal(int, int)

    def __init__(self, links):
        super().__init__(); self.links = links; self.cancel = threading.Event()

    def run(self):
        check_all(self.links, progress=lambda a, b: self.progress.emit(a, b),
                  on_result=lambda u, r: self.result.emit(u, r), cancel=self.cancel)


class LinkCheckDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('🔗 فحص الروابط'); self.resize(1250, 720)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.scope = QComboBox(); self.scope.addItems(['كل المتجر', 'القسم المحدد في الشجرة فقط'])
        self.b_start = QPushButton('▶ بدء الفحص'); self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton('■ إيقاف'); self.b_stop.setProperty('kind', 'ghost'); self.b_stop.clicked.connect(self.stop)
        self.b_stop.setEnabled(False)
        self.filter = QComboBox(); self.filter.addItems(['عرض الكل', 'المعطلة فقط', 'غير المؤكدة فقط', 'السليمة فقط'])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(QLabel('النطاق:')); top.addWidget(self.scope); top.addWidget(self.b_start); top.addWidget(self.b_stop)
        top.addStretch(); top.addWidget(QLabel('عرض:')); top.addWidget(self.filter)
        v.addLayout(top)
        self.prog = QProgressBar(); v.addWidget(self.prog)
        self.summary = QLabel('اضغط "بدء الفحص". الفحص يتم من الكمبيوتر عبر الإنترنت ولا يحتاج الجهاز.')
        v.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['الحالة', 'العنوان', 'الحجم', 'ملاحظة', 'الرابط', 'المكان'])
        hh = self.table.horizontalHeader()
        for i, m in enumerate([QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.ResizeToContents,
                               QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.ResizeToContents]):
            hh.setSectionResizeMode(i, m)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.cellDoubleClicked.connect(lambda r, c: self.open_url())
        v.addWidget(self.table)
        bot = QHBoxLayout()
        for t, fn, kind in [('🌐 فتح الرابط', self.open_url, 'ghost'), ('📋 نسخ الرابط', self.copy_url, 'ghost'),
                            ('💾 تصدير تقرير CSV', self.export, 'ghost'),
                            ('🗑 حذف العناصر المحددة من المتجر', self.delete_selected, 'danger')]:
            b = QPushButton(t); b.setProperty('kind', kind); b.clicked.connect(fn); bot.addWidget(b)
        v.addLayout(bot)
        self.links, self.rows, self.res, self.th = [], {}, {}, None

    # ---------- scope
    def scoped_links(self):
        s = self.win.store
        if self.scope.currentIndex() == 0:
            return collect_links(s)
        it = self.win.tree.currentItem()
        n = it.data(0, Qt.UserRole) if it else None
        if not n or not n['target']:
            QMessageBox.information(self, 'تنبيه', 'حدد قسماً (مجلداً) في الشجرة أولاً'); return None
        views, stack, files = set(), [n['target']], set()
        while stack and len(views) < 3000:
            tg = stack.pop()
            if tg in views: continue
            views.add(tg); files.add(tg[0])
            for c in s.children(*tg):
                if c['target']: stack.append(c['target'])
        return [l for l in collect_links(s, list(files)) if (l['file'], l['view']) in views]

    # ---------- run
    def start(self):
        if not self.win.store:
            QMessageBox.warning(self, 'تنبيه', 'افتح المتجر أولاً'); return
        links = self.scoped_links()
        if links is None: return
        if not links:
            QMessageBox.information(self, 'فحص الروابط', 'لا توجد روابط تنزيل في هذا النطاق'); return
        self.links, self.res, self.rows = links, {}, {}
        self.table.setRowCount(0); self.table.setRowCount(len(links))
        for i, l in enumerate(links):
            for c, val in enumerate([ICON[None], l['title'], '', '', l['url'], '%s › %s' % (l['file'].replace('USRDIR/', ''), l['view'])]):
                self.table.setItem(i, c, QTableWidgetItem(val))
            self.rows.setdefault(l['url'], []).append(i)
        self.prog.setValue(0)
        self.th = CheckThread(links)
        self.th.result.connect(self.on_result)
        self.th.progress.connect(lambda a, b: (self.prog.setMaximum(b), self.prog.setValue(a), self.update_summary()))
        self.th.finished.connect(self.on_finished)
        self.b_start.setEnabled(False); self.b_stop.setEnabled(True)
        self.summary.setText('جاري فحص %d رابط فريد (%d عنصر)...' % (len(self.rows), len(links)))
        self.th.start()

    def stop(self):
        if self.th: self.th.cancel.set()

    def on_result(self, url, r):
        self.res[url] = r
        st, code, size, note = r
        for i in self.rows.get(url, []):
            self.table.item(i, 0).setText(ICON[st])
            self.table.item(i, 2).setText(human(size))
            self.table.item(i, 3).setText(note or (str(code) if code else ''))
            for c in range(6):
                self.table.item(i, c).setBackground(QColor(COLOR[st]))
        if self.filter.currentIndex(): self.apply_filter()

    def counts(self):
        c = {OK: 0, BROKEN: 0, UNSURE: 0}
        for l in self.links:
            r = self.res.get(l['url'])
            if r: c[r[0]] += 1
        return c

    def update_summary(self):
        c = self.counts()
        self.summary.setText('✅ يعمل: %d    ❌ معطل: %d    ⚠️ غير مؤكد: %d    — من %d عنصر'
                             % (c[OK], c[BROKEN], c[UNSURE], len(self.links)))

    def on_finished(self):
        self.b_start.setEnabled(True); self.b_stop.setEnabled(False); self.update_summary()
        self.summary.setText(self.summary.text() + '   ✔ انتهى الفحص')

    def apply_filter(self):
        want = {0: None, 1: BROKEN, 2: UNSURE, 3: OK}[self.filter.currentIndex()]
        for i, l in enumerate(self.links):
            r = self.res.get(l['url'])
            self.table.setRowHidden(i, bool(want) and (not r or r[0] != want))

    # ---------- actions
    def selected(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def open_url(self):
        for r in self.selected()[:5]: webbrowser.open(self.links[r]['url'])

    def copy_url(self):
        QApplication.clipboard().setText('\n'.join(self.links[r]['url'] for r in self.selected()))

    def export(self):
        p, _ = QFileDialog.getSaveFileName(self, 'حفظ التقرير', 'arcadia_links.csv', 'CSV (*.csv)')
        if not p: return
        with open(p, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f); w.writerow(['status', 'title', 'size', 'note', 'url', 'file', 'view', 'key'])
            for l in self.links:
                st, code, size, note = self.res.get(l['url'], ('', '', None, ''))
                w.writerow([st, l['title'], human(size), note, l['url'], l['file'], l['view'], l['key']])
        QMessageBox.information(self, 'تم', 'تم حفظ التقرير:\n' + p)

    def delete_selected(self):
        rows = self.selected()
        if not rows: return
        if QMessageBox.question(self, 'حذف', 'حذف %d عنصر من المتجر؟ (لن يُحذف شيء من الإنترنت)' % len(rows)) != QMessageBox.Yes:
            return
        for r in sorted(rows, reverse=True):
            l = self.links[r]
            self.win.store.remove_node(l['file'], l['view'], l['key'])
            self.table.removeRow(r); del self.links[r]
        self.rows = {}
        for i, l in enumerate(self.links): self.rows.setdefault(l['url'], []).append(i)
        self.win.rebuild_tree(); self.update_summary()
        self.win.statusBar().showMessage('تم الحذف — احفظ ثم انشر')

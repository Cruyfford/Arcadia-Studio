"""Link checker window."""
import csv
import threading
import webbrowser

import multiprocessing as mp
import queue

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtCore import QSize
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QProgressBar,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
                               QMessageBox, QApplication)

from .core.linkcheck import collect_links, check_all, human, proc_main as _proc_main, OK, BROKEN, UNSURE

ICON = {OK: '✅ يعمل', BROKEN: '❌ معطل', UNSURE: '⚠️ غير مؤكد', None: '⏳'}
COLOR = {OK: '#123d2a', BROKEN: '#4a1420', UNSURE: '#4a3a10'}


class CheckThread(QThread):
    def __init__(self, links):
        super().__init__(); self.links = links; self.cancel = threading.Event()
        self.q = queue.Queue(); self.done = 0; self.total = 1

    def run(self):
        # checks run in a separate process so the window never freezes (no GIL contention)
        ctx = mp.get_context('spawn')
        out, stop = ctx.Queue(), ctx.Event()
        p = ctx.Process(target=_proc_main, args=(self.links, out, stop), daemon=True)
        p.start()
        while True:
            if self.cancel.is_set(): stop.set()
            try:
                msg = out.get(timeout=0.3)
            except queue.Empty:
                if not p.is_alive(): break
                continue
            if msg is None: break
            kind, a, b = msg
            if kind == 'r': self.q.put((a, b))
            else: self.done, self.total = a, b
        p.join(2)



class LinkCheckDialog(QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle('🔗 فحص الروابط'); self.resize(1250, 720)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.scope = QComboBox(); self.scope.setMinimumWidth(420)
        self.scope.addItem('🌐 كل المتجر', None)
        if win.store:
            for lab, tgt in win.all_sections():
                self.scope.addItem('📁 ' + lab, tgt)
        self.b_start = QPushButton('▶ بدء الفحص'); self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton('■ إيقاف'); self.b_stop.setProperty('kind', 'ghost'); self.b_stop.clicked.connect(self.stop)
        self.b_stop.setEnabled(False)
        self.filter = QComboBox(); self.filter.addItems(['عرض الكل', 'المعطلة فقط', 'غير المؤكدة فقط', 'السليمة فقط'])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(QLabel('القسم:')); top.addWidget(self.scope); top.addWidget(self.b_start); top.addWidget(self.b_stop)
        top.addStretch(); top.addWidget(QLabel('عرض:')); top.addWidget(self.filter)
        v.addLayout(top)
        self.prog = QProgressBar(); v.addWidget(self.prog)
        self.summary = QLabel('اضغط "بدء الفحص". الفحص يتم من الكمبيوتر عبر الإنترنت ولا يحتاج الجهاز.')
        v.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setIconSize(QSize(72, 40)); self.table.verticalHeader().setDefaultSectionSize(46)
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
        for t, fn, kind in [('⬇ تنزيل المحدد', self.download, ''), ('🌐 فتح الرابط', self.open_url, 'ghost'), ('📋 نسخ الرابط', self.copy_url, 'ghost'),
                            ('💾 تصدير تقرير CSV', self.export, 'ghost'),
                            ('🗑 حذف العناصر المحددة من المتجر', self.delete_selected, 'danger')]:
            b = QPushButton(t); b.setProperty('kind', kind); b.clicked.connect(fn); bot.addWidget(b)
        v.addLayout(bot)
        self.links, self.rows, self.res, self.th = [], {}, {}, None

    # ---------- scope
    def scoped_links(self):
        """walk the menu tree from the chosen section; game pages get their cover from the list entry"""
        from .core.xmb import ROOT_XML, ROOT_VIEW
        s = self.win.store
        tgt = self.scope.currentData()
        whole = tgt is None
        if whole:
            tgt = s.children(ROOT_XML, ROOT_VIEW)[0]['target']
        parent_icon = {tgt: None}
        stack, seen, files = [tgt], set(), set()
        while stack and len(seen) < 60000:
            tg = stack.pop()
            if tg in seen: continue
            seen.add(tg); files.add(tg[0])
            for c in s.children(*tg):
                if c['target'] and c['target'] not in parent_icon:
                    ic = c['pairs'].get('icon', '')
                    parent_icon[c['target']] = ic if (ic and not c['target'][0].startswith('USRDIR/PS3/XML/')) or not parent_icon.get(tg) else parent_icon[tg]
                    stack.append(c['target'])
        out = []
        for l in collect_links(s, None if whole else list(files)):
            key = (l['file'], l['view'])
            if key not in seen and not whole: continue
            if l['file'].startswith('USRDIR/PS3/XML/') and parent_icon.get(key):
                l['icon'] = parent_icon[key]
            out.append(l)
        return out

    # ---------- run
    def start(self):
        if not self.win.store:
            QMessageBox.warning(self, 'تنبيه', 'افتح المتجر أولاً'); return
        links = self.scoped_links()
        if links is None: return
        if not links:
            QMessageBox.information(self, 'فحص الروابط', 'لا توجد روابط تنزيل في هذا النطاق'); return
        self.links, self.res, self.rows = links, {}, {}
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0); self.table.setRowCount(len(links))
        for i, l in enumerate(links):
            for c, val in enumerate([ICON[None], l['title'], '', '', l['url'], '%s › %s' % (l['file'].replace('USRDIR/', ''), l['view'])]):
                self.table.setItem(i, c, QTableWidgetItem(val))
            ic = self.icon_for(l.get('icon'))
            if ic: self.table.item(i, 1).setIcon(ic)
            self.rows.setdefault(l['url'], []).append(i)
        self.table.setUpdatesEnabled(True)
        self.prog.setValue(0)
        self.cnt = {OK: 0, BROKEN: 0, UNSURE: 0}
        self.th = CheckThread(links)
        self.th.finished.connect(self.on_finished)
        self.timer = QTimer(self); self.timer.timeout.connect(self.drain); self.timer.start(400)
        self.b_start.setEnabled(False); self.b_stop.setEnabled(True)
        self.summary.setText('جاري فحص %d رابط فريد (%d عنصر)...' % (len(self.rows), len(links)))
        self.th.start()

    def stop(self):
        if self.th: self.th.cancel.set()

    def drain(self):
        """apply queued results in one batch (keeps the window responsive)"""
        batch = []
        try:
            while len(batch) < 2000: batch.append(self.th.q.get_nowait())
        except queue.Empty:
            pass
        if batch:
            self.table.setUpdatesEnabled(False)
            for u, r in batch: self.on_result(u, r)
            self.table.setUpdatesEnabled(True)
            if self.filter.currentIndex(): self.apply_filter()
        self.prog.setMaximum(max(self.th.total, 1)); self.prog.setValue(self.th.done)
        self.update_summary()

    def on_result(self, url, r):
        self.res[url] = r
        st, code, size, note = r
        self.cnt[st] += len(self.rows.get(url, []))
        for i in self.rows.get(url, []):
            self.table.item(i, 0).setText(ICON[st])
            self.table.item(i, 2).setText(human(size))
            self.table.item(i, 3).setText(note or (str(code) if code else ''))
            for c in range(6):
                self.table.item(i, c).setBackground(QColor(COLOR[st]))

    def counts(self):
        c = {OK: 0, BROKEN: 0, UNSURE: 0}
        for l in self.links:
            r = self.res.get(l['url'])
            if r: c[r[0]] += 1
        return c

    def update_summary(self):
        c = getattr(self, 'cnt', None) or self.counts()
        self.summary.setText('✅ يعمل: %d    ❌ معطل: %d    ⚠️ غير مؤكد: %d    — من %d عنصر'
                             % (c[OK], c[BROKEN], c[UNSURE], len(self.links)))

    def on_finished(self):
        self.timer.stop(); self.drain()
        self.b_start.setEnabled(True); self.b_stop.setEnabled(False); self.update_summary()
        self.summary.setText(self.summary.text() + '   ✔ انتهى الفحص')

    def apply_filter(self):
        want = {0: None, 1: BROKEN, 2: UNSURE, 3: OK}[self.filter.currentIndex()]
        for i, l in enumerate(self.links):
            r = self.res.get(l['url'])
            self.table.setRowHidden(i, bool(want) and (not r or r[0] != want))

    def icon_for(self, ps3path):
        if not ps3path: return None
        cache = self.__dict__.setdefault('_icons', {})
        if ps3path not in cache:
            p = self.win.store.abs_path(ps3path)
            cache[ps3path] = QIcon(p) if p and __import__('os').path.exists(p) else None
        return cache[ps3path]

    def download(self):
        rows = self.selected()
        if not rows:
            QMessageBox.information(self, 'تنبيه', 'حدد لعبة أو أكثر من الجدول'); return
        dm = self.win.downloads()
        for r in rows:
            l = self.links[r]
            dm.add(l['url'], l['title'], self.win.store.abs_path(l.get('icon', '')) if l.get('icon') else None)

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
        self.cnt = self.counts()
        self.win.rebuild_tree(); self.update_summary()
        self.win.statusBar().showMessage('تم الحذف — احفظ ثم انشر')

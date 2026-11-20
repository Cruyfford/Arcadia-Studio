"""🎬 Game videos page: short clips (20 s, 320x180, no sound) shown on the game page of the PS3 app.
Everything happens from the right-click menu, like the rest of the studio."""
import json
import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QGroupBox, QFormLayout,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QInputDialog,
                               QMessageBox, QDialog)

from .ui_common import context_menu, add, header, shortcut
from .core import previews as pv
from .core.publish import upload_archive, public_url

ST_OK, ST_MISS, ST_NONE, ST_ERR, ST_WORK = '✅ موجود', '— لا يوجد', '✖ لم يُعثر عليه', '⚠️ خطأ', '⏳ جاري...'
SYSTEMS = ['تلقائي من اسم القسم', 'PS3', 'PS2', 'PS1', 'PSP']
LINK_FILE = 'USRDIR/PREVIEWS.txt'


class FetchThread(QThread):
    row = Signal(str, str, str)        # key, status, note
    finished_all = Signal(int, int)

    def __init__(self, games, folder, creds, system, names, rawg_key=''):
        super().__init__(); self.games, self.folder, self.creds, self.system, self.names = games, folder, creds, system, names
        self.rawg_key = rawg_key
        self.cancel = threading.Event()

    def run(self):
        sources = []
        if self.rawg_key: sources.append(pv.Rawg(self.rawg_key))
        if self.creds[0] and self.creds[1]: sources.append(pv.ScreenScraper(*self.creds))
        cache = {}          # search name -> mp4 path (several regions share one clip)
        src_dir = os.path.join(self.folder, '_src'); os.makedirs(src_dir, exist_ok=True)
        ok = miss = 0
        for g in self.games:
            if self.cancel.is_set(): break
            key = g['key']; dest = os.path.join(self.folder, key + '.avp')
            if os.path.exists(dest): ok += 1; self.row.emit(key, ST_OK, ''); continue
            name = self.names.get(key) or g['search']
            system = self.system or pv.guess_system(g['path'])
            self.row.emit(key, ST_WORK, name)
            try:
                mp4 = cache.get((name, system))
                if not mp4:
                    url = src = None
                    for so in sources:
                        try:
                            url, matched = so.find_video(name, system)
                        except Exception:  # noqa  (one source failing must not stop the other)
                            url = None
                        if url: src = so; break
                    if not url:
                        miss += 1; self.row.emit(key, ST_NONE, name); continue
                    mp4 = os.path.join(src_dir, key + '.mp4')
                    src.download(url, mp4); cache[(name, system)] = mp4
                pv.to_avp(mp4, dest)
                ok += 1; self.row.emit(key, ST_OK, '')
            except Exception as e:  # noqa
                self.row.emit(key, ST_ERR, str(e)[:160])
        self.finished_all.emit(ok, miss)


class PlayDialog(QDialog):
    """plays an .avp the way the PS3 will show it"""
    def __init__(self, parent, path, title):
        super().__init__(parent); self.setWindowTitle('▶ ' + title)
        w, h, fps, self.frames = pv.read_avp(path)
        v = QVBoxLayout(self); self.lbl = QLabel(); self.lbl.setFixedSize(w * 2, h * 2); v.addWidget(self.lbl)
        v.addWidget(QLabel('%d×%d — %d إطار في الثانية — %.1f ثانية — %.1f MB' % (w, h, fps, len(self.frames) / fps, os.path.getsize(path) / 1048576)))
        self.i = 0; self.t = QTimer(self); self.t.timeout.connect(self.step); self.t.start(int(1000 / fps)); self.step()

    def step(self):
        p = QPixmap(); p.loadFromData(self.frames[self.i])
        self.lbl.setPixmap(p.scaled(self.lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.i = (self.i + 1) % len(self.frames)


class VideosPage(QWidget):
    def __init__(self, win):
        super().__init__(); self.win = win; st = win.settings
        self.games = []; self.status = {}; self.thread = None
        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 12)
        t = QLabel('🎬 فيديوهات الألعاب'); t.setStyleSheet('font-size:22px;font-weight:bold'); v.addWidget(t)
        sub = QLabel('مقاطع قصيرة (20 ثانية، بدون صوت) تظهر في صفحة اللعبة داخل تطبيق أركاديا على الجهاز.')
        sub.setStyleSheet('color:#9aa6d8'); v.addWidget(sub)

        top = QHBoxLayout()
        self.scope = QComboBox(); self.scope.setMinimumWidth(420); self.scope.currentIndexChanged.connect(self.reload)
        self.system = QComboBox(); self.system.addItems(SYSTEMS)
        self.filter = QComboBox(); self.filter.addItems(['الكل', 'بدون فيديو', 'لها فيديو', 'لم يُعثر عليها'])
        self.filter.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(QLabel('القسم:')); top.addWidget(self.scope, 1); top.addWidget(QLabel('الجهاز:')); top.addWidget(self.system)
        top.addWidget(QLabel('عرض:')); top.addWidget(self.filter)
        v.addLayout(top)

        g = QGroupBox('مصادر الفيديو — RAWG.io (مفتاح فوري) و/أو ScreenScraper.fr'); f = QFormLayout(g); f.setLabelAlignment(Qt.AlignRight)
        self.e = {}
        for k, lab, pw in (('rawg_key', 'مفتاح RAWG (من rawg.io/apidocs)', True),('ss_devid', 'Dev ID (من منتدى ScreenScraper)', False), ('ss_devpass', 'Dev Password', True),
                           ('ss_user', 'اسم المستخدم', False), ('ss_pass', 'كلمة المرور', True)):
            e = QLineEdit(st.value(k, '') or ''); e.setEchoMode(QLineEdit.Password if pw else QLineEdit.Normal)
            e.editingFinished.connect(lambda k=k, e=e: self.win.settings.setValue(k, e.text().strip()))
            self.e[k] = e; f.addRow(lab, e)
        v.addWidget(g)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['اللعبة', 'المفتاح', 'الحالة', 'القسم', 'ملاحظة'])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch); hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for c in (1, 2, 4): hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.cellDoubleClicked.connect(lambda r, c: self.play())
        v.addWidget(self.table, 1)
        self.info = QLabel(''); self.info.setStyleSheet('color:#7f8bc0'); v.addWidget(self.info)
        hint = QLabel('💡 كلك يمين على الجدول: جلب الفيديوهات، إضافة مقطع من ملف، تشغيل، رفع إلى archive.org، ربط المتجر — دبل كلك يشغّل الفيديو')
        hint.setStyleSheet('color:#7f8bc0'); hint.setWordWrap(True); v.addWidget(hint)
        context_menu(self.table, self.menu)
        shortcut(self.table, 'Delete', self.delete)
        shortcut(self.table, 'Space', self.play)

    # ---------------- data
    @property
    def folder(self):
        d = self.win.settings.value('prev_folder', '') or os.path.join(os.path.expanduser('~'), 'Documents', 'ArcadiaVideos')
        os.makedirs(d, exist_ok=True); return d

    def _names_path(self): return os.path.join(self.folder, 'names.json')

    def names(self):
        try: return json.load(open(self._names_path(), encoding='utf-8'))
        except Exception: return {}   # noqa

    def showEvent(self, e):
        super().showEvent(e); self.refresh_scopes()
        if not self.games: self.reload()

    def refresh_scopes(self):
        cur = self.scope.currentData() if self.scope.count() else None
        self.scope.blockSignals(True); self.scope.clear(); self.scope.addItem('🌐 كل المتجر', None)
        if self.win.store:
            for lab, tgt in self.win.all_sections(): self.scope.addItem('📁 ' + lab, tgt)
        i = self.scope.findData(cur); self.scope.setCurrentIndex(max(i, 0)); self.scope.blockSignals(False)

    def reload(self):
        if not self.win.store:
            self.info.setText('افتح مشروع المتجر أولاً'); return
        self.games = pv.collect_games(self.win.store, self.scope.currentData())
        self.table.setRowCount(len(self.games))
        for r, g in enumerate(self.games):
            have = os.path.exists(os.path.join(self.folder, g['key'] + '.avp'))
            st = self.status.get(g['key']) or (ST_OK if have else ST_MISS)
            for c, val in enumerate((g['title'], g['key'], st, g['path'].strip(' /'), '')):
                it = QTableWidgetItem(val); it.setData(Qt.UserRole, r); self.table.setItem(r, c, it)
        self.apply_filter(); self.count()

    def count(self):
        have = sum(1 for g in self.games if os.path.exists(os.path.join(self.folder, g['key'] + '.avp')))
        self.info.setText('%d لعبة — %d لها فيديو — المجلد: %s' % (len(self.games), have, self.folder))

    def apply_filter(self, *_):
        mode = self.filter.currentIndex()
        for r in range(self.table.rowCount()):
            st = self.table.item(r, 2).text()
            hide = (mode == 1 and st == ST_OK) or (mode == 2 and st != ST_OK) or (mode == 3 and st != ST_NONE)
            self.table.setRowHidden(r, hide)

    def row_of(self, key):
        for r, g in enumerate(self.games):
            if g['key'] == key: return r
        return -1

    def set_row(self, key, st, note):
        self.status[key] = st
        r = self.row_of(key)
        if r < 0: return
        self.table.item(r, 2).setText(st); self.table.item(r, 4).setText(note)
        if st == ST_WORK: self.table.scrollToItem(self.table.item(r, 0))

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.games[r] for r in rows if not self.table.isRowHidden(r)]

    # ---------------- menu
    def menu(self, m, pos):
        sel = self.selected()
        header(m, '🎬 %d محددة' % len(sel) if sel else '🎬 فيديوهات الألعاب')
        if self.thread and self.thread.isRunning():
            add(m, '⏹ إيقاف الجلب', self.stop)
        else:
            add(m, '⬇ جلب فيديوهات المحدد', lambda: self.fetch(sel), enabled=bool(sel))
            add(m, '⬇ جلب كل الناقص في القسم', lambda: self.fetch([g for g in self.games if not os.path.exists(os.path.join(self.folder, g['key'] + '.avp'))]))
        m.addSeparator()
        add(m, '🎞 إضافة مقطع من ملف فيديو...', self.add_file, enabled=len(sel) >= 1)
        add(m, '✏ البحث باسم آخر...', self.rename_search, enabled=len(sel) == 1)
        add(m, '▶ تشغيل الفيديو', self.play, enabled=len(sel) == 1, shortcut='Space')
        add(m, '🗑 حذف الفيديو', self.delete, enabled=bool(sel), shortcut='Delete')
        m.addSeparator()
        add(m, '☁ رفع الفيديوهات إلى archive.org', self.upload)
        add(m, '🔗 ربط المتجر برابط الفيديوهات', self.link_store)
        add(m, '📂 فتح مجلد الفيديوهات', self.open_folder)
        add(m, '📁 تغيير مجلد الفيديوهات...', self.change_folder)
        add(m, '🔄 تحديث القائمة', self.reload)

    def creds(self):
        c = tuple(self.e[k].text().strip() for k in ('ss_devid', 'ss_devpass', 'ss_user', 'ss_pass'))
        self.rawg = self.e['rawg_key'].text().strip()
        for k, e in self.e.items(): self.win.settings.setValue(k, e.text().strip())
        return c

    def fetch(self, games):
        if not games: return
        c = self.creds()
        if not self.rawg and not (c[0] and c[1]):
            QMessageBox.information(self, 'مصدر الفيديو', 'اكتب مفتاح RAWG (فوري ومجاني من rawg.io/apidocs)، '
                                    'أو بيانات مطوّر ScreenScraper إذا وصلتك.'); return
        sysname = None if self.system.currentIndex() == 0 else self.system.currentText()
        self.thread = FetchThread(games, self.folder, c, sysname, self.names(), self.rawg)
        self.thread.row.connect(self.set_row)
        self.thread.finished_all.connect(lambda ok, miss: (self.count(), self.win.statusBar().showMessage(
            '✔ الفيديوهات: %d جاهزة، %d لم يُعثر عليها' % (ok, miss), 10000)))
        self.thread.start()

    def stop(self):
        if self.thread: self.thread.cancel.set()

    def add_file(self):
        sel = self.selected()
        path, _ = QFileDialog.getOpenFileName(self, 'اختر مقطع فيديو', '', 'Video (*.mp4 *.mkv *.webm *.avi *.mov *.m4v *.flv *.wmv);;All (*)')
        if not path: return
        start, ok = QInputDialog.getDouble(self, 'بداية المقطع', 'من أي ثانية يبدأ المقطع؟', 0, 0, 36000, 1)
        if not ok: return
        folder = self.folder

        def job(prog):
            for i, g in enumerate(sel):
                pv.to_avp(path, os.path.join(folder, g['key'] + '.avp'), start=start); prog(i + 1, len(sel))
            return len(sel)
        self.win.run_bg(job, lambda n: ([self.set_row(g['key'], ST_OK, 'يدوي') for g in sel], self.count()), 'جاري تحويل المقطع...')

    def rename_search(self):
        g = self.selected()[0]
        names = self.names()
        name, ok = QInputDialog.getText(self, 'البحث باسم آخر', 'الاسم في ScreenScraper:', text=names.get(g['key'], g['search']))
        if not ok or not name.strip(): return
        names[g['key']] = name.strip(); json.dump(names, open(self._names_path(), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        self.fetch([g])

    def play(self):
        sel = self.selected()
        if len(sel) != 1: return
        p = os.path.join(self.folder, sel[0]['key'] + '.avp')
        if os.path.exists(p): PlayDialog(self, p, sel[0]['title']).exec()

    def delete(self):
        for g in self.selected():
            p = os.path.join(self.folder, g['key'] + '.avp')
            if os.path.exists(p): os.remove(p)
            self.set_row(g['key'], ST_MISS, '')
        self.count()

    def open_folder(self):
        f = self.folder
        if sys.platform.startswith('win'): os.startfile(f)  # noqa
        else: subprocess.Popen(['xdg-open', f])

    def change_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'مجلد الفيديوهات', self.folder)
        if d: self.win.settings.setValue('prev_folder', d); self.status = {}; self.reload()

    # ---------------- publishing
    def item_name(self):
        st = self.win.settings
        item = st.value('ia_prev_item', '') or ((st.value('ia_item', '') or 'arcadia-ps3') + '-videos')
        return item

    def upload(self):
        st = self.win.settings
        acc, sec = st.value('ia_access', ''), st.value('ia_secret', '')
        if not acc or not sec:
            QMessageBox.information(self, 'archive.org', 'اكتب مفاتيح archive.org في صفحة «الجهاز والنشر» أولاً.'); return
        item, ok = QInputDialog.getText(self, 'archive.org', 'اسم عنصر الفيديوهات على archive.org:', text=self.item_name())
        if not ok or not item.strip(): return
        item = item.strip(); st.setValue('ia_prev_item', item)
        folder = self.folder
        done_path = os.path.join(folder, 'uploaded.json')
        try: done = json.load(open(done_path))
        except Exception: done = {}   # noqa
        files = [f for f in sorted(os.listdir(folder)) if f.endswith('.avp')]
        todo = [f for f in files if done.get(f) != os.path.getsize(os.path.join(folder, f))]

        def job(prog):
            for i, f in enumerate(todo):
                upload_archive(item, acc, sec, os.path.join(folder, f), name=f, title='ARCADIA game videos')
                done[f] = os.path.getsize(os.path.join(folder, f))
                json.dump(done, open(done_path, 'w')); prog(i + 1, len(todo))
            return len(todo)
        self.win.run_bg(job, lambda n: QMessageBox.information(self, 'archive.org', 'تم رفع %d فيديو.\nالرابط: %s\n\nاستخدم «ربط المتجر برابط الفيديوهات» ثم ابنِ المتجر.'
                                                               % (n, public_url(item, ''))), 'جاري رفع الفيديوهات...')

    def link_store(self):
        if not self.win.store: return
        url, ok = QInputDialog.getText(self, 'رابط الفيديوهات', 'الرابط الذي يقرأ منه تطبيق أركاديا الفيديوهات:',
                                       text=public_url(self.item_name(), ''))
        if not ok or not url.strip(): return
        url = url.strip()
        if not url.endswith('/'): url += '/'
        self.win.store.set_text(LINK_FILE, url + '\n'); self.win.store.save()
        QMessageBox.information(self, 'تم', 'تم حفظ الرابط داخل المتجر (%s).\nابنِ المتجر وثبّته على الجهاز ليقرأه التطبيق.' % LINK_FILE)

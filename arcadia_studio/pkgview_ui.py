"""PKG viewer: open any PS3/PSP package (debug or retail), see its info and files, extract."""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTreeWidget,
                               QTreeWidgetItem, QFileDialog, QMessageBox, QProgressBar, QFormLayout, QWidget,
                               QHeaderView, QSplitter)

from .core.pkg import PkgReader, rebuild_pkg
import shutil
from .core.sfo import parse_sfo, set_sfo_string, LABELS, CATEGORIES
from PySide6.QtWidgets import QInputDialog
from .core.linkcheck import human


class PkgViewer(QDialog):
    def __init__(self, win, path=None):
        super().__init__(win); self.win = win; self.reader = None
        self.setWindowTitle('📦 عارض ومحرر PKG'); self.resize(1300, 820); self.setAcceptDrops(True)
        self.edit_dir = None
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
        for t, fn, k in [('✏ تعديل الحزمة', self.start_edit, ''), ('⬇ استخراج الكل', self.extract_all, 'ghost'),
                         ('⬇ استخراج المحدد', self.extract_sel, 'ghost'), ('📤 إرسال الحزمة للجهاز وتثبيتها', self.send, 'ghost')]:
            bb = QPushButton(t); bb.clicked.connect(fn)
            if k: bb.setProperty('kind', k)
            bot.addWidget(bb)
        v.addLayout(bot)
        # edit-mode toolbar (hidden until editing)
        self.edit_bar = QWidget(); eb = QHBoxLayout(self.edit_bar); eb.setContentsMargins(0, 0, 0, 0)
        for t, fn, k in [('✏ تغيير الاسم', self.edit_title, 'ghost'), ('🖼 تغيير الأيقونة', lambda: self.replace_image('ICON0.PNG', (320, 176)), 'ghost'),
                         ('🌄 تغيير الخلفية', lambda: self.replace_image('PIC1.PNG', (1920, 1080)), 'ghost'),
                         ('➕ إضافة / استبدال ملف', self.add_file, 'ghost'), ('🗑 حذف المحدد', self.delete_sel, 'danger'),
                         ('📂 فتح مجلد التعديل', lambda: self._open(self.edit_dir), 'ghost'), ('🔄 تحديث', self.refresh_edit, 'ghost'),
                         ('🛠 إعادة البناء إلى PKG', self.rebuild, '')]:
            bb = QPushButton(t); bb.clicked.connect(fn)
            if k: bb.setProperty('kind', k)
            eb.addWidget(bb)
        self.edit_bar.hide(); v.addWidget(self.edit_bar)
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
        self.edit_dir = None; self.edit_bar.hide(); self.setWindowTitle('📦 عارض ومحرر PKG')
        self.reader, self.ents = r, ents
        self.show_info(r.read_file, r.content_id, r.kind)
        self.fill_tree([(n, ds, fl & 0xFF == 4) for n, do, ds, fl in ents])
        files = [e for e in ents if e[3] & 0xFF != 4]
        self.lbl.setText('\u200f%s   •   %s   •   عدد الملفات: %d   •   الحجم: \u200e%s' % (os.path.basename(path), r.kind, len(files), human(os.path.getsize(path))))

    def show_info(self, read, cid, kind):
        while self.form.rowCount(): self.form.removeRow(0)
        sfo = parse_sfo(read('PARAM.SFO'))
        rows = [('Content ID', cid), ('نوع الحزمة', kind)]
        for k in ('TITLE', 'TITLE_ID', 'CATEGORY', 'APP_VER', 'VERSION', 'PS3_SYSTEM_VER', 'RESOLUTION'):
            if k in sfo:
                val = sfo[k]
                if k == 'CATEGORY': val = '%s (%s)' % (val, CATEGORIES.get(val, '؟'))
                rows.append((LABELS.get(k, k), str(val)))
        for k, val in rows:
            l = QLabel(val); l.setTextInteractionFlags(Qt.TextSelectableByMouse); l.setWordWrap(True)
            self.form.addRow(k + ':', l)
        for lbl, name in ((self.icon, 'ICON0.PNG'), (self.pic, 'PIC1.PNG')):
            data = read(name)
            pm = QPixmap()
            if data: pm.loadFromData(data)
            lbl.setPixmap(pm.scaled(lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else QPixmap())
            if pm.isNull(): lbl.setText('لا توجد %s' % name)

    def fill_tree(self, items):
        self.tree.clear(); nodes = {}
        for name, ds, isdir in sorted(items, key=lambda e: e[0].lower()):
            parts = name.split('/')
            parent = self.tree.invisibleRootItem()
            for i in range(len(parts)):
                key = '/'.join(parts[:i + 1])
                if key not in nodes:
                    is_dir = i < len(parts) - 1 or isdir
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

    # ---------- edit mode
    def start_edit(self):
        if not self.reader: return
        base = os.path.splitext(self.reader.path)[0] + '_edit'
        if os.path.exists(base) and QMessageBox.question(self, 'مجلد موجود',
                'يوجد مجلد تعديل سابق لهذه الحزمة.\nYes = متابعة التعديل السابق   No = فك الحزمة من جديد') == QMessageBox.No:
            shutil.rmtree(base, ignore_errors=True)
        r = PkgReader(self.reader.path)

        def job(prog):
            if not os.path.exists(base): r.extract(base, prog)
            return base
        self._run(job, self.enter_edit)

    def enter_edit(self, d):
        self.edit_dir = d; self.edit_bar.show()
        self.setWindowTitle('✏ تعديل: ' + os.path.basename(self.reader.path))
        self.refresh_edit()

    def refresh_edit(self):
        d = self.edit_dir
        if not d: return
        items = []
        for base, dirs, files in os.walk(d):
            rel = os.path.relpath(base, d).replace(os.sep, '/')
            for x in dirs: items.append(((x if rel == '.' else rel + '/' + x), 0, True))
            for x in files: items.append(((x if rel == '.' else rel + '/' + x), os.path.getsize(os.path.join(base, x)), False))
        self.fill_tree(items)

        def read(name):
            p = os.path.join(d, name)
            return open(p, 'rb').read() if os.path.exists(p) else None
        self.show_info(read, self.reader.content_id, 'وضع التعديل — ' + self.reader.kind)

    def _sel_dir(self):
        its = self.tree.selectedItems()
        if not its: return ''
        rel = its[0].data(0, Qt.UserRole)
        return rel if os.path.isdir(os.path.join(self.edit_dir, rel)) else os.path.dirname(rel)

    def edit_title(self):
        p = os.path.join(self.edit_dir, 'PARAM.SFO')
        if not os.path.exists(p):
            QMessageBox.warning(self, 'تنبيه', 'لا يوجد PARAM.SFO في هذه الحزمة'); return
        data = open(p, 'rb').read(); cur = parse_sfo(data).get('TITLE', '')
        t, ok = QInputDialog.getText(self, 'تغيير الاسم', 'اسم اللعبة كما يظهر في الجهاز:', text=cur)
        if ok and t.strip():
            open(p, 'wb').write(set_sfo_string(data, 'TITLE', t.strip())); self.refresh_edit()

    def replace_image(self, name, size):
        f, _ = QFileDialog.getOpenFileName(self, 'اختر صورة', '', 'Images (*.png *.jpg *.jpeg *.webp)')
        if not f: return
        from PIL import Image
        im = Image.open(f).convert('RGBA')
        im.thumbnail(size, Image.LANCZOS) if name == 'ICON0.PNG' else None
        if name == 'PIC1.PNG':
            im = im.resize(size, Image.LANCZOS).convert('RGB')
        else:
            canvas = Image.new('RGBA', size, (0, 0, 0, 0))
            canvas.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2), im); im = canvas
        im.save(os.path.join(self.edit_dir, name)); self.refresh_edit()

    def add_file(self):
        fs, _ = QFileDialog.getOpenFileNames(self, 'اختر ملفات لإضافتها')
        if not fs: return
        dest = os.path.join(self.edit_dir, self._sel_dir())
        for f in fs: shutil.copy(f, os.path.join(dest, os.path.basename(f)))
        self.refresh_edit()

    def delete_sel(self):
        rels = [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]
        if not rels or QMessageBox.question(self, 'حذف', 'حذف %d عنصر من الحزمة؟' % len(rels)) != QMessageBox.Yes: return
        for r in rels:
            p = os.path.join(self.edit_dir, r)
            if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p): os.remove(p)
        self.refresh_edit()

    def rebuild(self):
        src = self.reader.path
        out, _ = QFileDialog.getSaveFileName(self, 'حفظ الحزمة الجديدة', os.path.splitext(src)[0] + '_mod.pkg', 'PKG (*.pkg)')
        if not out: return
        if os.path.abspath(out) == os.path.abspath(src):
            QMessageBox.warning(self, 'تنبيه', 'اختر اسماً مختلفاً عن الحزمة الأصلية'); return
        d = self.edit_dir

        def done(r):
            n, size, mode = r
            msg = 'تم بناء الحزمة: %s\n%d ملف — %s' % (out, n, human(size))
            if self.reader.retail: msg += '\n\nملاحظة: الحزمة الأصلية Retail، والجديدة Debug (تُثبَّت على HEN/CFW).'
            if self.win.ps3_ip() and QMessageBox.question(self, 'تم', msg + '\n\nإرسالها للجهاز وتثبيتها الآن؟') == QMessageBox.Yes:
                self.win.send_pkg(out)
            else:
                QMessageBox.information(self, 'تم', msg)
        self._run(lambda prog: rebuild_pkg(src, d, out, prog), done)

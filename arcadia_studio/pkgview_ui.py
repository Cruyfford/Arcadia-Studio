"""PKG viewer: open any PS3/PSP package (debug or retail), see its info and files, extract."""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from .ui_common import context_menu, add, header, shortcut, ClickLabel
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
        self.lbl = QLabel('اسحب ملف PKG وأفلته هنا، أو كلك يمين ← فتح ملف PKG')
        self.lbl.setStyleSheet('color:#9fb4ff;font-size:15px;padding:4px')
        v.addWidget(self.lbl)
        sp = QSplitter()
        left = QWidget(); lv = QVBoxLayout(left)
        self.pic = ClickLabel(); self.pic.setFixedSize(420, 236); self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setStyleSheet('background:#0b0f1f;border-radius:12px;')
        self.icon = ClickLabel(); self.icon.setFixedSize(320, 176); self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet('background:#0b0f1f;border-radius:12px;')
        self.pic.setToolTip('كلك يمين: تغيير الخلفية'); self.icon.setToolTip('كلك يمين: تغيير الأيقونة')
        lv.addWidget(self.pic); lv.addWidget(self.icon, 0, Qt.AlignHCenter)
        self.form = QFormLayout(); self.form.setLabelAlignment(Qt.AlignRight); lv.addLayout(self.form); lv.addStretch()
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['الملف', 'الحجم', 'النوع'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(lambda it, c: self.need_edit(self.edit_file))
        sp.addWidget(self.tree); sp.addWidget(left); sp.setSizes([800, 460])
        v.addWidget(sp, 1)
        self.prog = QProgressBar(); self.prog.hide(); v.addWidget(self.prog)
        hint = QLabel('💡 كل الأدوات في القائمة: كلك يمين على أي ملف (فك التشفير، التعديل، Hex، الاستخراج، إعادة البناء...)')
        hint.setStyleSheet('color:#7f8bc0;padding:4px'); v.addWidget(hint)
        context_menu(self.tree, self.tree_menu)
        context_menu(self.pic, lambda m, p: self.img_menu(m, 'PIC1.PNG', (1920, 1080), '🌄 تغيير الخلفية PIC1'))
        context_menu(self.icon, lambda m, p: self.img_menu(m, 'ICON0.PNG', (320, 176), '🖼 تغيير الأيقونة ICON0'))
        self.pic.clicked.connect(lambda: self.need_edit(lambda: self.replace_image('PIC1.PNG', (1920, 1080))) if self.reader else self.pick())
        self.icon.clicked.connect(lambda: self.need_edit(lambda: self.replace_image('ICON0.PNG', (320, 176))) if self.reader else self.pick())
        shortcut(self.tree, 'Delete', lambda: self.need_edit(self.delete_sel))
        shortcut(self.tree, 'Return', lambda: self.need_edit(self.edit_file))
        shortcut(self, 'Ctrl+O', self.pick)
        self._after_edit = None
        if path: self.load(path)

    # ---------- right-click menus
    def tree_menu(self, m, pos):
        it = self.tree.itemAt(pos)
        if it and not it.isSelected():
            self.tree.clearSelection(); it.setSelected(True); self.tree.setCurrentItem(it)
        add(m, '📂 فتح ملف PKG...', self.pick, shortcut='Ctrl+O')
        if not self.reader: return
        sel = self.tree.selectedItems()
        rel = sel[0].data(0, Qt.UserRole) if sel else ''
        m.addSeparator()
        if sel:
            header(m, '📄 ' + (rel if len(sel) == 1 else '%d عناصر محددة' % len(sel)))
            add(m, '📝 فتح / تعديل الملف', lambda: self.need_edit(self.edit_file), len(sel) == 1, 'Return')
            add(m, '🔢 فتح في محرر Hex', lambda: self.need_edit(lambda: self.edit_file('hex')), len(sel) == 1)
            add(m, '🔓 فك تشفير المحدد', lambda: self.need_edit(self.decrypt_sel))
            sub = m.addMenu('🔤 أدوات التعريب')
            add(sub, '🔤 نصوص هذا الملف (استخراج وترجمة وكتابة)', lambda: self.need_edit(self.strings_tool), len(sel) == 1)
            add(sub, '🅰 إضافة الحروف العربية لهذا الخط', lambda: self.need_edit(self.font_tool),
                len(sel) == 1 and rel.lower().endswith(('.ttf', '.otf', '.ttc')))
            add(m, '⬇ استخراج المحدد...', self.extract_sel)
            m.addSeparator()
            add(m, '➕ إضافة / استبدال ملفات هنا...', lambda: self.need_edit(self.add_file))
            add(m, '🗑 حذف المحدد', lambda: self.need_edit(self.delete_sel), shortcut='Delete')
            m.addSeparator()
        add(m, '🔓 فك تشفير كل الحزمة', lambda: self.need_edit(self.decrypt_all))
        add(m, '🔑 مجلد الرخص RAP...', self.pick_rap)
        m.addSeparator()
        sub = m.addMenu('🎨 الاسم والصور')
        add(sub, '✏ تغيير اسم اللعبة', lambda: self.need_edit(self.edit_title))
        add(sub, '🖼 تغيير الأيقونة ICON0', lambda: self.need_edit(lambda: self.replace_image('ICON0.PNG', (320, 176))))
        add(sub, '🌄 تغيير الخلفية PIC1', lambda: self.need_edit(lambda: self.replace_image('PIC1.PNG', (1920, 1080))))
        add(m, '⬇ استخراج الكل...', self.extract_all)
        if self.edit_dir:
            add(m, '📂 فتح مجلد التعديل', lambda: self._open(self.edit_dir))
            add(m, '🔄 تحديث القائمة', self.refresh_edit)
        else:
            add(m, '✏ فك الحزمة للتعديل', self.start_edit)
        m.addSeparator()
        add(m, '🛠 إعادة البناء إلى PKG...', lambda: self.need_edit(self.rebuild))
        add(m, '📤 إرسال الحزمة للجهاز وتثبيتها', self.send)

    def img_menu(self, m, name, size, label):
        if not self.reader:
            add(m, '📂 فتح ملف PKG...', self.pick); return
        add(m, label + '...', lambda: self.need_edit(lambda: self.replace_image(name, size)))

    def need_edit(self, fn):
        """run fn in edit mode; unpack the package first if needed"""
        if not self.reader: return
        if self.edit_dir: return fn()
        self._after_edit = fn
        self._keep_sel = {it.data(0, Qt.UserRole) for it in self.tree.selectedItems()}
        self.start_edit()

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
        self.edit_dir = None; self._after_edit = None; self.setWindowTitle('📦 عارض ومحرر PKG')
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
        self.prog.show(); self.prog.setRange(0, 0); self.prog.setFormat('%p%')
        self.w = Worker(fn)
        self.w.progress.connect(lambda a, b: (self.prog.setRange(0, max(b, 1)), self.prog.setValue(a)))
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
        self.edit_dir = d
        self.setWindowTitle('✏ تعديل: ' + os.path.basename(self.reader.path))
        self.refresh_edit()
        keep = getattr(self, '_keep_sel', set()); self._keep_sel = set()
        if keep:
            from PySide6.QtWidgets import QTreeWidgetItemIterator
            x = QTreeWidgetItemIterator(self.tree)
            while x.value():
                if x.value().data(0, Qt.UserRole) in keep:
                    x.value().setSelected(True); self.tree.setCurrentItem(x.value())
                x += 1
        fn, self._after_edit = self._after_edit, None
        if fn: fn()

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

    def edit_file(self, force=None):
        its = self.tree.selectedItems()
        if not its: return
        if not self.edit_dir:
            return self.need_edit(lambda: self.edit_file(force))
        rel = its[0].data(0, Qt.UserRole); p = os.path.join(self.edit_dir, rel)
        if os.path.isdir(p): return
        from .file_editors import open_editor
        dlg = open_editor(self, p, force)
        if dlg: dlg.finished.connect(lambda *_: self.refresh_edit())

    # ---------- Arabization tools
    def _sel_file(self):
        its = self.tree.selectedItems()
        if not its or not self.edit_dir: return None
        p = os.path.join(self.edit_dir, its[0].data(0, Qt.UserRole))
        return p if os.path.isfile(p) else None

    def strings_tool(self):
        p = self._sel_file()
        if not p: return
        from .strings_ui import StringsDialog
        d = StringsDialog(self, p); d.show()

    def font_tool(self):
        p = self._sel_file()
        if not p: return
        from .core.fontmerge import merge_arabic
        import shutil as _sh
        bak = p + '.bak'

        def job(prog):
            if not os.path.exists(bak): _sh.copy(p, bak)
            return merge_arabic(bak, p)

        def done(n):
            self.refresh_edit()
            QMessageBox.information(self, 'تم', ('تمت إضافة %d حرف عربي للخط (مع أشكال الحروف المتصلة).\nالأصل محفوظ: %s\n\n'
                                    'النص العربي يُكتب في ملفات اللعبة بأداة «نصوص هذا الملف».' % (n, os.path.basename(bak)))
                                    if n else 'الخط يحتوي الحروف العربية مسبقاً.')
        self._run(job, done)

    # ---------- decryption (SELF/SPRX/EDAT/SDAT)
    def rap_dirs(self):
        d = self.win.settings.value('rap_dir', '')
        out = [d] if d else []
        if self.edit_dir: out.append(self.edit_dir)
        return out

    def pick_rap(self):
        d = QFileDialog.getExistingDirectory(self, 'اختر المجلد الذي فيه ملفات الرخص .rap', self.win.settings.value('rap_dir', ''))
        if d:
            self.win.settings.setValue('rap_dir', d)
            n = len([f for f in os.listdir(d) if f.lower().endswith('.rap')])
            QMessageBox.information(self, 'الرخص', 'تم. عدد ملفات RAP في المجلد: %d' % n)

    def _decrypt(self, paths):
        from .core.ps3crypto import decrypt_file, detect
        paths = [p for p in paths if os.path.isfile(p) and detect(p)]
        if not paths:
            QMessageBox.information(self, 'فك التشفير', 'لا توجد ملفات مشفّرة (SELF/SPRX/EDAT/SDAT) في التحديد'); return
        dirs = self.rap_dirs()

        def job(prog):
            ok, bad = [], []
            for i, p in enumerate(paths):
                try:
                    out, how, n = decrypt_file(p, rap_dirs=dirs)
                    ok.append('\u200f✅ \u200e%s\u200f ← \u200e%s\u200f (%s)' % (os.path.relpath(out, self.edit_dir), os.path.basename(p), how))
                except Exception as e:  # noqa
                    bad.append('\u200f❌ \u200e%s\u200f: %s' % (os.path.basename(p), e))
                prog(i + 1, len(paths))
            return ok, bad

        def done(r):
            ok, bad = r
            self.refresh_edit()
            msg = '\n'.join(ok + bad)
            msg += '\n\n\u200fالملفات المفكوكة تُحفظ بجانب الأصلية.'
            (QMessageBox.warning if bad and not ok else QMessageBox.information)(self, 'فك التشفير', msg[:3500])
        self._run(job, done)

    def decrypt_sel(self):
        if not self.edit_dir:
            QMessageBox.information(self, 'تنبيه', 'اضغط "تعديل الحزمة" أولاً'); return
        self._decrypt([os.path.join(self.edit_dir, it.data(0, Qt.UserRole)) for it in self.tree.selectedItems()])

    def decrypt_all(self):
        if not self.edit_dir:
            QMessageBox.information(self, 'تنبيه', 'اضغط "تعديل الحزمة" أولاً'); return
        ps = []
        for base, _, files in os.walk(self.edit_dir):
            ps += [os.path.join(base, f) for f in files]
        self._decrypt(ps)

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

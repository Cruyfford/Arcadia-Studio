"""Arcadia Studio — Windows manager for the ARCADIA (VIDEOSTORE-based) PS3 XMB store."""
import json
import os
import shutil
import sys
import time

from PySide6.QtCore import Qt, QThread, Signal, QSize, QRectF, QSettings
from PySide6.QtGui import QAction, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QColor, QLinearGradient, QPainterPath
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem, QWidget, QVBoxLayout,
                               QHBoxLayout, QFormLayout, QLineEdit, QLabel, QPushButton, QFileDialog, QComboBox,
                               QSplitter, QMessageBox, QProgressBar, QInputDialog, QToolBar, QGroupBox, QStyle)

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = 'arcadia_studio'

from arcadia_studio.core.pkg import PkgReader, build_pkg
from arcadia_studio.core.xmb import Store, ROOT_XML, ROOT_VIEW, STORE_PREFIX
from arcadia_studio.core.ps3 import PS3
from arcadia_studio.core.publish import upload_archive, public_url
import tempfile

HERE = os.path.dirname(os.path.abspath(getattr(sys, '_MEIPASS', os.path.dirname(__file__))))
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
if hasattr(sys, '_MEIPASS'):
    ASSETS = os.path.join(sys._MEIPASS, 'arcadia_studio', 'assets')

FOLDER = 'type:x-xmb/folder-pixmap'
ACTION = 'type:x-xmb/module-action'
WEBMAN_DL = 'http://127.0.0.1/download.ps3?url='

STYLE = """
* { font-size: 14px; }
QMainWindow, QWidget { background: #0d1120; color: #e8ecff; }
QToolBar { background: #121833; border: none; padding: 6px; spacing: 6px; }
QToolButton { background: #1b2350; border-radius: 10px; padding: 8px 14px; color: #fff; }
QToolButton:hover { background: #2f3fa0; }
QTreeWidget { background: #10152b; border: 1px solid #222a52; border-radius: 12px; padding: 6px; }
QTreeWidget::item { padding: 6px; border-radius: 8px; }
QTreeWidget::item:selected { background: #3a2fa8; }
QGroupBox { border: 1px solid #262f5e; border-radius: 14px; margin-top: 14px; padding: 12px; background: #121833; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top right; right: 16px; padding: 0 8px; color: #9fb4ff; }
QLineEdit, QComboBox { background: #0b0f1f; border: 1px solid #2b3570; border-radius: 9px; padding: 7px; color: #fff; }
QPushButton { background: #2f5bff; border: none; border-radius: 10px; padding: 9px 16px; color: #fff; font-weight: bold; }
QPushButton:hover { background: #4a73ff; }
QPushButton[kind="ghost"] { background: #1b2350; }
QPushButton[kind="danger"] { background: #b4233c; }
QProgressBar { border: 1px solid #2b3570; border-radius: 8px; text-align: center; background: #0b0f1f; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7c3aed, stop:1 #2f7bff); border-radius: 8px; }
QStatusBar { background: #121833; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a5bd6; border-radius: 5px; background: #0b0f1f; }
QCheckBox::indicator:checked { background: #2f5bff; border-color: #7aa0ff; }
QTabWidget::pane { border: 1px solid #262f5e; border-radius: 12px; padding: 10px; background: #121833; }
QTabBar::tab { background: #1b2350; padding: 8px 18px; border-radius: 8px; margin: 2px; }
QTabBar::tab:selected { background: #3a2fa8; }
QPlainTextEdit { background: #0b0f1f; border: 1px solid #2b3570; border-radius: 9px; }
"""


class Worker(QThread):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__(); self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(lambda a, b: self.progress.emit(int(a), int(b))))
        except Exception as e:  # noqa
            self.failed.emit(str(e))


class Preview(QWidget):
    """XMB-like preview of the selected folder."""

    def __init__(self, win):
        super().__init__(); self.win = win; self.nodes = []; self.sel = 0
        self.setMinimumSize(480, 270)

    def show_nodes(self, nodes, sel):
        self.nodes, self.sel = nodes, sel; self.update()

    def _pix(self, ps3path):
        p = self.win.store.abs_path(ps3path) if (self.win.store and ps3path) else None
        return QPixmap(p) if p and os.path.exists(p) else QPixmap()

    def paintEvent(self, ev):
        qp = QPainter(self); qp.setRenderHint(QPainter.Antialiasing); qp.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        bgp = QPixmap()
        if self.nodes and 0 <= self.sel < len(self.nodes):
            bgp = self._pix(self.nodes[self.sel]['pairs'].get('pic', ''))
        if bgp.isNull():
            g = QLinearGradient(0, 0, r.width(), r.height()); g.setColorAt(0, QColor('#0a0f24')); g.setColorAt(1, QColor('#2a1a5e'))
            qp.fillRect(r, g)
        else:
            qp.drawPixmap(r, bgp)
        if not self.nodes:
            qp.setPen(QColor('#9fb4ff')); qp.drawText(r, Qt.AlignCenter, 'اختر قسماً لعرض المعاينة'); return
        s = r.height() / 1080 * 1.5
        x_icon, x_text = int(r.width() * .26), int(r.width() * .26 + 110 * s)
        start = max(0, self.sel - 2)
        y = int(140 * s)
        for i, n in enumerate(self.nodes[start:start + 8], start):
            big = i == self.sel
            h = int((130 if big else 92) * s)
            pm = self._pix(n['pairs'].get('icon', ''))
            if not pm.isNull():
                pm = pm.scaled(int(h * 1.8), h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qp.drawPixmap(x_icon - pm.width() // 2, y, pm)
            f = QFont(self.win.ui_font, max(7, int((34 if big else 30) * s)))
            qp.setFont(f); qp.setPen(QColor(255, 255, 255, 255 if big else 200))
            qp.setLayoutDirection(Qt.LeftToRight)
            qp.drawText(x_text, y + int(h * .5), self.win.store.display(n['pairs'].get('title', n['key'])))
            if big and n['pairs'].get('info'):
                qp.setFont(QFont(self.win.ui_font, max(6, int(22 * s)))); qp.setPen(QColor('#c9d6ff'))
                qp.drawText(x_text, y + int(h * .5) + int(34 * s), self.win.store.display(n['pairs']['info']))
            y += h + int(14 * s)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Arcadia Studio — استوديو أركاديا')
        self.resize(1400, 860)
        self.store = None; self.template = None; self.cur = None
        self.settings = QSettings('Cruyfford', 'ArcadiaStudio')
        fid = QFontDatabase.addApplicationFont(os.path.join(ASSETS, 'IBMPlexSansArabic-SemiBold.ttf'))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        self.ui_font = fams[0] if fams else 'Segoe UI'
        QApplication.instance().setFont(QFont(self.ui_font, 10))

        tb = QToolBar(); tb.setMovable(False); tb.setIconSize(QSize(18, 18)); self.addToolBar(tb)
        for text, fn in [('📦 فتح PKG', self.open_pkg), ('📂 فتح مجلد', self.open_folder), ('💾 حفظ', self.save),
                         ('🛠 بناء PKG', self.build), ('⚡ تحديث سريع للجهاز', self.quick_sync),
                         ('📤 إرسال PKG للجهاز', self.send_pkg_pick), ('📡 إعداد PS3', self.ps3_setup),
                         ('🌍 نشر للجميع', self.publish), ('🔑 إعداد النشر', self.publish_setup),
                         ('🎮 محوّل الألعاب', self.open_converter), ('🧾 تصدير JSON', self.export_json)]:
            a = QAction(text, self); a.triggered.connect(fn); tb.addAction(a)

        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setIconSize(QSize(46, 26))
        self.tree.itemExpanded.connect(self.expand); self.tree.currentItemChanged.connect(self.select)

        # editor
        ed = QGroupBox('تعديل العنصر'); fl = QFormLayout(ed); fl.setLabelAlignment(Qt.AlignRight)
        self.e_title, self.e_info = QLineEdit(), QLineEdit()
        self.e_icon, self.e_pic, self.e_link = QLineEdit(), QLineEdit(), QLineEdit()
        self.e_method = QComboBox(); self.e_method.addItems(['webMAN — تنزيل في الخلفية (موصى به)', 'متصفح PS3 (الطريقة الأصلية)', 'متجر PSN (رابط pkg_src)'])
        self.icon_lbl, self.pic_lbl = QLabel(), QLabel()
        self.icon_lbl.setFixedSize(128, 72); self.pic_lbl.setFixedSize(128, 72)
        for l in (self.icon_lbl, self.pic_lbl): l.setStyleSheet('background:#0b0f1f;border-radius:8px;'); l.setAlignment(Qt.AlignCenter)
        bi = QPushButton('تغيير الأيقونة'); bi.setProperty('kind', 'ghost'); bi.clicked.connect(lambda: self.pick_image(self.e_icon, 'ICONS'))
        bp = QPushButton('تغيير الخلفية'); bp.setProperty('kind', 'ghost'); bp.clicked.connect(lambda: self.pick_image(self.e_pic, 'BG'))
        fl.addRow('العنوان', self.e_title); fl.addRow('الوصف', self.e_info)
        row = QHBoxLayout(); row.addWidget(self.icon_lbl); row.addWidget(bi); fl.addRow('الأيقونة', row)
        row2 = QHBoxLayout(); row2.addWidget(self.pic_lbl); row2.addWidget(bp); fl.addRow('خلفية القسم', row2)
        fl.addRow('رابط التنزيل', self.e_link); fl.addRow('طريقة التنزيل', self.e_method)
        b_apply = QPushButton('✔ تطبيق التعديل'); b_apply.clicked.connect(self.apply)
        fl.addRow(b_apply)

        acts = QHBoxLayout()
        for t, fn, kind in [('➕ لعبة', self.add_game, ''), ('📁 قسم جديد', self.add_section, ''), ('▲', lambda: self.move(-1), 'ghost'),
                            ('▼', lambda: self.move(1), 'ghost'), ('🗑 حذف', self.delete, 'danger')]:
            b = QPushButton(t); b.clicked.connect(fn)
            if kind: b.setProperty('kind', kind)
            acts.addWidget(b)

        self.preview = Preview(self)
        right = QWidget(); rl = QVBoxLayout(right); rl.addWidget(self.preview, 3); rl.addWidget(ed, 2); rl.addLayout(acts)
        sp = QSplitter(); sp.addWidget(self.tree); sp.addWidget(right); sp.setSizes([430, 970])
        self.setCentralWidget(sp)
        self.prog = QProgressBar(); self.prog.setMaximumWidth(320); self.prog.hide()
        self.statusBar().addPermanentWidget(self.prog)
        self.statusBar().showMessage('افتح ملف PKG للمتجر أو مجلد مستخرج للبدء')
        last = self.settings.value('last_ws')
        if last and os.path.exists(os.path.join(last, ROOT_XML)):
            self.load_ws(last)

    # ---------------- workspace
    def run_bg(self, fn, done, msg):
        self.prog.show(); self.prog.setValue(0); self.statusBar().showMessage(msg)
        self.w = Worker(fn)
        self.w.progress.connect(lambda a, b: (self.prog.setMaximum(max(b, 1)), self.prog.setValue(a)))
        self.w.done.connect(lambda r: (self.prog.hide(), done(r)))
        self.w.failed.connect(lambda e: (self.prog.hide(), QMessageBox.critical(self, 'خطأ', e)))
        self.w.start()

    def open_pkg(self):
        p, _ = QFileDialog.getOpenFileName(self, 'اختر ملف PKG', '', 'PS3 PKG (*.pkg)')
        if not p: return
        ws = os.path.splitext(p)[0] + '_workspace'
        if os.path.exists(ws) and QMessageBox.question(self, 'مجلد موجود', 'يوجد مجلد عمل سابق لهذا الملف. استبداله؟') == QMessageBox.Yes:
            shutil.rmtree(ws)

        def job(prog):
            if not os.path.exists(os.path.join(ws, ROOT_XML)):
                PkgReader(p).extract(ws, prog)
            json.dump({'template': p}, open(os.path.join(ws, '.arcadia_project.json'), 'w'))
            return ws
        self.run_bg(job, self.load_ws, 'جاري فك الحزمة...')

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'اختر مجلد المتجر المستخرج')
        if d: self.load_ws(d)

    def load_ws(self, ws):
        if not os.path.exists(os.path.join(ws, ROOT_XML)):
            QMessageBox.warning(self, 'تنبيه', 'هذا المجلد لا يحتوي على متجر VIDEOSTORE/ARCADIA'); return
        self.store = Store(ws); self.settings.setValue('last_ws', ws)
        pj = os.path.join(ws, '.arcadia_project.json')
        self.template = json.load(open(pj)).get('template') if os.path.exists(pj) else None
        self.rebuild_tree()
        self.statusBar().showMessage('تم فتح: ' + ws)

    def rebuild_tree(self):
        self.tree.clear()
        for n in self.store.children(ROOT_XML, ROOT_VIEW):
            it = self.make_item(n); self.tree.addTopLevelItem(it); it.setExpanded(True)

    def make_item(self, n):
        it = QTreeWidgetItem([self.store.display(n['pairs'].get('title', n['key'])) or n['key']])
        it.setData(0, Qt.UserRole, n)
        p = self.store.abs_path(n['pairs'].get('icon', ''))
        if p and os.path.exists(p): it.setIcon(0, QIcon(p))
        if n['target']: it.addChild(QTreeWidgetItem(['…']))
        return it

    def expand(self, it):
        n = it.data(0, Qt.UserRole)
        if it.childCount() == 1 and it.child(0).data(0, Qt.UserRole) is None and n and n['target']:
            it.takeChild(0)
            for c in self.store.children(*n['target']):
                it.addChild(self.make_item(c))

    def refresh_parent(self, it):
        par = it.parent() if it else None
        if par is None: return self.rebuild_tree()
        par.takeChildren(); par.addChild(QTreeWidgetItem(['…'])); par.setExpanded(False); par.setExpanded(True)

    # ---------------- selection / editor
    def select(self, it, _prev=None):
        if not it or it.data(0, Qt.UserRole) is None: return
        n = self.cur = it.data(0, Qt.UserRole); p = n['pairs']
        self.e_title.setText(self.store.display(p.get('title', ''))); self.e_info.setText(self.store.display(p.get('info', '')))
        self.e_icon.setText(p.get('icon', '')); self.e_pic.setText(p.get('pic', ''))
        link = p.get('pkg_src') or p.get('module_action', '')
        self.e_method.setCurrentIndex(2 if p.get('pkg_src') else (0 if link.startswith(WEBMAN_DL) else 1))
        self.e_link.setText(link[len(WEBMAN_DL):] if link.startswith(WEBMAN_DL) else link)
        self.show_img(self.icon_lbl, p.get('icon', '')); self.show_img(self.pic_lbl, p.get('pic', ''))
        sibs = self.store.children(n['file'], n['view'])
        idx = next((i for i, s in enumerate(sibs) if s['key'] == n['key']), 0)
        self.preview.show_nodes(sibs, idx)

    def show_img(self, lbl, ps3):
        p = self.store.abs_path(ps3) if ps3 else None
        pm = QPixmap(p) if p and os.path.exists(p) else QPixmap()
        lbl.setPixmap(pm.scaled(lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else QPixmap())
        if pm.isNull(): lbl.setText('—')

    def pick_image(self, edit, sub):
        if not self.store: return
        f, _ = QFileDialog.getOpenFileName(self, 'اختر صورة PNG', '', 'PNG (*.png)')
        if not f: return
        rel_dir = os.path.join('USRDIR', 'ARCADIA', sub)
        os.makedirs(os.path.join(self.store.root, rel_dir), exist_ok=True)
        name = os.path.basename(f).replace(' ', '_')
        shutil.copy(f, os.path.join(self.store.root, rel_dir, name))
        self.store.mark_changed(os.path.join(rel_dir, name))
        edit.setText(STORE_PREFIX + 'USRDIR/ARCADIA/%s/%s' % (sub, name))
        self.show_img(self.icon_lbl if edit is self.e_icon else self.pic_lbl, edit.text())

    def apply(self):
        n = self.cur
        if not n: return
        s, f, v, k = self.store, n['file'], n['view'], n['key']
        s.set_pair(f, v, k, 'title', self.e_title.text())
        s.set_pair(f, v, k, 'info', self.e_info.text())
        s.set_pair(f, v, k, 'icon', self.e_icon.text(), arabic=False)
        s.set_pair(f, v, k, 'pic', self.e_pic.text(), arabic=False)
        link = self.e_link.text().strip()
        if link and n['cls'] != FOLDER:
            self._set_link(f, v, k, link, self.e_method.currentIndex())
        self.statusBar().showMessage('تم التعديل — لا تنسَ الحفظ ثم بناء الحزمة')
        it = self.tree.currentItem(); it.setText(0, self.e_title.text())
        self.refresh_cur(it)

    def _set_link(self, f, v, k, link, method):
        s = self.store
        if method == 2:
            s.set_pair(f, v, k, 'pkg_src', link, False); s.set_pair(f, v, k, 'pkg_src_qa', link, False)
            return
        s.set_pair(f, v, k, 'module_name', 'webbrowser_plugin' if method == 0 else 'webrender_plugin', False)
        s.set_pair(f, v, k, 'module_action', (WEBMAN_DL + link) if method == 0 else link, False)
        s.set_pair(f, v, k, 'ingame', 'disable', False)

    def refresh_cur(self, it):
        n = it.data(0, Qt.UserRole)
        fresh = next((c for c in self.store.children(n['file'], n['view']) if c['key'] == n['key']), n)
        it.setData(0, Qt.UserRole, fresh); self.select(it)

    def _parent_view(self):
        it = self.tree.currentItem()
        if not it: return None, None
        n = it.data(0, Qt.UserRole)
        if n['target'] and n['cls'] == FOLDER and it.isExpanded():
            return it, n['target']      # add inside an open folder
        return it.parent() or it, (n['file'], n['view'])

    def all_sections(self, depth=3):
        """list of (label, (file, view)) for every folder, for the section picker"""
        out = []
        root = self.store.children(ROOT_XML, ROOT_VIEW)
        if not root or not root[0]['target']: return out
        out.append(('القائمة الرئيسية', root[0]['target']))

        def walk(target, prefix, d):
            if d > depth: return
            for c in self.store.children(*target):
                if c['cls'] == FOLDER and c['target'] and not c['target'][0].startswith('USRDIR/PS3/XML'):
                    kids = self.store.children(*c['target'])
                    if len(kids) < 3 and not any(k['cls'] == FOLDER for k in kids): continue
                    lab = prefix + self.store.display(c['pairs'].get('title', c['key']))
                    out.append((lab, c['target']))
                    walk(c['target'], lab + '  ›  ', d + 1)
        walk(root[0]['target'], '', 1)
        return out

    def ask(self, title, fields, with_section=True, default_target=None):
        dlg = QDialog(self); dlg.setWindowTitle(title); dlg.setMinimumWidth(560)
        fl = QFormLayout(dlg); edits = {}
        sec = None
        if with_section:
            sec = QComboBox(); secs = self.all_sections()
            for lab, tgt in secs: sec.addItem(lab, tgt)
            if default_target in [t for _, t in secs]:
                sec.setCurrentIndex([t for _, t in secs].index(default_target))
            fl.addRow('القسم', sec)
        for k, lab in fields:
            if k == 'method':
                e = QComboBox(); e.addItems(['webMAN — تنزيل في الخلفية (موصى به)', 'متصفح PS3', 'متجر PSN (pkg_src)'])
            else:
                e = QLineEdit()
            fl.addRow(lab, e); edits[k] = e
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText('إضافة'); bb.button(QDialogButtonBox.Cancel).setText('إلغاء')
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); fl.addRow(bb)
        if dlg.exec() != QDialog.Accepted: return None
        res = {k: (e.currentIndex() if isinstance(e, QComboBox) else e.text().strip()) for k, e in edits.items()}
        res['target'] = sec.currentData() if sec else None
        return res

    def default_target(self):
        it = self.tree.currentItem()
        if not it: return None
        n = it.data(0, Qt.UserRole)
        return n['target'] if (n['cls'] == FOLDER and n['target']) else (n['file'], n['view'])

    def add_game(self):
        if not self.store: return
        r = self.ask('إضافة لعبة', [('title', 'اسم اللعبة'), ('info', 'الوصف (الحجم مثلاً)'), ('link', 'رابط ملف PKG'),
                                    ('method', 'طريقة التنزيل')], default_target=self.default_target())
        if not r or not r['title'] or not r['target']: return
        f, v = r['target']
        bg = next((c['pairs'].get('pic') for c in self.store.children(f, v) if c['pairs'].get('pic')), '')
        key = self.store.add_node(f, v, 'game_%d' % int(time.time()),
                                  {'icon': STORE_PREFIX + 'USRDIR/XMB/PS3.png', 'title': r['title'], 'info': r['info'],
                                   **({'pic': bg} if bg else {})}, ACTION)
        if r['link']: self._set_link(f, v, key, r['link'], r['method'])
        self.after_add(f, v, key)

    def add_section(self):
        if not self.store: return
        r = self.ask('قسم جديد', [('title', 'اسم القسم'), ('info', 'وصف القسم')], default_target=self.default_target())
        if not r or not r['title'] or not r['target']: return
        f, v = r['target']
        vid = 'arc_sec_%d' % int(time.time())
        self.store.add_view(f, vid, r['title'])
        bg = next((c['pairs'].get('pic') for c in self.store.children(f, v) if c['pairs'].get('pic')), '')
        self.store.add_node(f, v, vid, {'icon': STORE_PREFIX + 'USRDIR/XMB/VIDEOGAMESSCZ.png', 'title': r['title'],
                                        'info': r['info'], **({'pic': bg} if bg else {})}, FOLDER, '#' + vid)
        self.after_add(f, v, vid)

    def after_add(self, f, v, key):
        """rebuild tree and select the new item"""
        self.rebuild_tree()

        def find(item):
            n = item.data(0, Qt.UserRole)
            if n and n['target'] == (f, v):
                item.setExpanded(True); self.expand(item)
                for i in range(item.childCount()):
                    c = item.child(i)
                    if c.data(0, Qt.UserRole) and c.data(0, Qt.UserRole)['key'] == key:
                        self.tree.setCurrentItem(c); self.tree.scrollToItem(c); return True
                return False
            if n and n['target'] and n['cls'] == FOLDER:
                was = item.isExpanded(); item.setExpanded(True); self.expand(item)
                for i in range(item.childCount()):
                    if find(item.child(i)): return True
                item.setExpanded(was)
            return False
        for i in range(self.tree.topLevelItemCount()):
            if find(self.tree.topLevelItem(i)): break
        self.statusBar().showMessage('تمت الإضافة — احفظ ثم ابنِ الحزمة')

    def reload_view(self, it):
        if it is None or (it.parent() is None and not it.isExpanded()):
            return self.rebuild_tree()
        it.takeChildren(); it.addChild(QTreeWidgetItem(['…'])); it.setExpanded(False); it.setExpanded(True)

    def move(self, d):
        it = self.tree.currentItem()
        if not it or not self.store: return
        n = it.data(0, Qt.UserRole); self.store.move_node(n['file'], n['view'], n['key'], d)
        par = it.parent()
        if par is None:
            i = self.tree.indexOfTopLevelItem(it); j = i + d
            if 0 <= j < self.tree.topLevelItemCount():
                self.tree.takeTopLevelItem(i); self.tree.insertTopLevelItem(j, it); self.tree.setCurrentItem(it)
        else:
            i = par.indexOfChild(it); j = i + d
            if 0 <= j < par.childCount():
                par.takeChild(i); par.insertChild(j, it); self.tree.setCurrentItem(it)

    def delete(self):
        it = self.tree.currentItem()
        if not it or not self.store: return
        n = it.data(0, Qt.UserRole)
        if QMessageBox.question(self, 'حذف', 'حذف "%s" من القائمة؟' % it.text(0)) != QMessageBox.Yes: return
        self.store.remove_node(n['file'], n['view'], n['key'])
        (it.parent() or self.tree.invisibleRootItem()).removeChild(it)

    # ---------------- output
    def save(self):
        if self.store:
            self.store.save(); self.statusBar().showMessage('تم الحفظ')

    def build(self):
        if not self.store: return
        self.save()
        tpl = self.template
        if not tpl or not os.path.exists(tpl):
            tpl, _ = QFileDialog.getOpenFileName(self, 'اختر الحزمة الأصلية (قالب الرأس)', '', 'PKG (*.pkg)')
            if not tpl: return
            self.template = tpl
        out, _ = QFileDialog.getSaveFileName(self, 'حفظ الحزمة', 'ARCADIA_AR.pkg', 'PKG (*.pkg)')
        if not out: return
        ws = self.store.root

        def job(prog):
            return build_pkg(tpl, ws, out, prog)
        def built(r):
            msg = 'تم بناء الحزمة (%d ملف، %.1f ميغا)\n%s' % (r[0], r[1] / 1e6, out)
            if self.ps3_ip() and QMessageBox.question(self, 'تم', msg + '\n\nإرسالها للجهاز وتثبيتها الآن؟') == QMessageBox.Yes:
                self.send_pkg(out)
            else:
                QMessageBox.information(self, 'تم', msg)
        self.run_bg(job, built, 'جاري بناء الحزمة...')

    def open_converter(self):
        from arcadia_studio.converter import ConverterDialog
        self.conv = ConverterDialog(self); self.conv.show()

    # ---------------- publish online for all users
    def publish_setup(self):
        dlg = QDialog(self); dlg.setWindowTitle('إعداد النشر على archive.org'); dlg.setMinimumWidth(620)
        fl = QFormLayout(dlg)
        info = QLabel('سجّل حساباً مجانياً في archive.org ثم خذ المفاتيح من:\nhttps://archive.org/account/s3.php\n'
                      'اسم العنصر (Item) يكون ثابتاً ومميزاً، مثل: arcadia-ps3-store-cruyfford')
        info.setWordWrap(True); fl.addRow(info)
        e_item, e_acc, e_sec = QLineEdit(self.settings.value('ia_item', '')), QLineEdit(self.settings.value('ia_access', '')), QLineEdit(self.settings.value('ia_secret', ''))
        e_sec.setEchoMode(QLineEdit.Password)
        fl.addRow('اسم العنصر', e_item); fl.addRow('Access Key', e_acc); fl.addRow('Secret Key', e_sec)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText('حفظ'); bb.button(QDialogButtonBox.Cancel).setText('إلغاء')
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); fl.addRow(bb)
        if dlg.exec() != QDialog.Accepted: return False
        for k, e in (('ia_item', e_item), ('ia_access', e_acc), ('ia_secret', e_sec)):
            self.settings.setValue(k, e.text().strip())
        if self.store and e_item.text().strip():
            self.store.set_update(public_url(e_item.text().strip()), self.store.get_version())
            self.save()
        return True

    def publish(self):
        if not self.store: return
        item, acc, sec = (self.settings.value(k, '') for k in ('ia_item', 'ia_access', 'ia_secret'))
        if not (item and acc and sec):
            if not self.publish_setup(): return
            item, acc, sec = (self.settings.value(k, '') for k in ('ia_item', 'ia_access', 'ia_secret'))
        tpl = self.template
        if not tpl or not os.path.exists(tpl):
            tpl, _ = QFileDialog.getOpenFileName(self, 'اختر الحزمة الأصلية (قالب الرأس)', '', 'PKG (*.pkg)')
            if not tpl: return
            self.template = tpl
        ver = self.store.get_version() + 1
        if QMessageBox.question(self, 'نشر للجميع', 'سيتم بناء النسخة رقم %d ورفعها إلى:\n%s\n\n'
                                'كل من ثبّت أركاديا يقدر يحدّث لها من "تحديث أركاديا". متابعة؟' % (ver, public_url(item))) != QMessageBox.Yes:
            return
        self.store.set_update(public_url(item), ver)
        self.save()
        ws = self.store.root
        out = os.path.join(tempfile.gettempdir(), 'ARCADIA_latest.pkg')

        def job(prog):
            build_pkg(tpl, ws, out, lambda a, b: prog(a, b * 2))
            return upload_archive(item, acc, sec, out, progress=lambda a, b: prog(b + a, b * 2))

        def done(url):
            self.store.clear_changed()
            QMessageBox.information(self, 'تم النشر', 'تم نشر النسخة %d بنجاح.\n\nرابط التحديث الثابت:\n%s\n\n'
                                    'ملاحظة: archive.org قد يحتاج بضع دقائق قبل أن يظهر الملف للجميع.\n'
                                    'أول مرة فقط: وزّع هذه الحزمة على الناس، وبعدها كل تحديث يوصلهم من داخل المتجر.' % (ver, url))
            shutil.copy(out, os.path.join(os.path.dirname(ws), 'ARCADIA_v%d.pkg' % ver))
        self.run_bg(job, done, 'جاري بناء النسخة %d ونشرها...' % ver)

    # ---------------- PS3 link (webMAN FTP + web commands)
    def ps3_ip(self):
        return self.settings.value('ps3_ip', '') or ''

    def ps3_setup(self):
        ip, ok = QInputDialog.getText(self, 'إعداد PS3', 'عنوان IP للجهاز (تجده في webMAN أو الإعدادات ← الشبكة ← حالة الاتصال):',
                                      text=self.ps3_ip())
        if not ok or not ip.strip(): return
        self.settings.setValue('ps3_ip', ip.strip())
        self.run_bg(lambda prog: PS3(ip).test(), self._show_test, 'جاري اختبار الاتصال بالجهاز...')

    def _show_test(self, r):
        ftp_ok, web_ok = r
        t = 'FTP: %s\nwebMAN: %s' % ('✅ متصل' if ftp_ok else '❌ غير متصل', '✅ متصل' if web_ok else '❌ غير متصل')
        if not ftp_ok:
            t += '\n\nتأكد أن الجهاز يعمل، وwebMAN مثبت ومفعّل، وأن الكمبيوتر والجهاز على نفس الشبكة.'
        QMessageBox.information(self, 'نتيجة الاختبار', t)

    def need_ip(self):
        if not self.ps3_ip():
            self.ps3_setup()
        return self.ps3_ip()

    def quick_sync(self):
        if not self.store or not self.need_ip(): return
        self.save()
        files = sorted(self.store.changed)
        if not files:
            QMessageBox.information(self, 'تحديث سريع', 'لا توجد تعديلات جديدة لإرسالها.'); return
        ps3 = PS3(self.ps3_ip()); root = self.store.root

        def job(prog):
            n = ps3.sync_files(root, files, prog); ps3.notify('ARCADIA updated'); return n

        def done(n):
            self.store.clear_changed()
            QMessageBox.information(self, 'تم', 'تم إرسال %d ملف مباشرة إلى المتجر في الجهاز.\n'
                                    'اخرج من قسم المتجر وادخله من جديد لتظهر التعديلات (أو أعد تشغيل الجهاز).' % n)
        self.run_bg(job, done, 'جاري إرسال التعديلات إلى الجهاز...')

    def send_pkg_pick(self):
        p, _ = QFileDialog.getOpenFileName(self, 'اختر ملف PKG لإرساله', '', 'PKG (*.pkg)')
        if p: self.send_pkg(p)

    def send_pkg(self, path):
        if not self.need_ip(): return
        ps3 = PS3(self.ps3_ip())

        def job(prog):
            return ps3.send_pkg(path, install=True, progress=prog)

        def done(remote):
            if self.store: self.store.clear_changed()
            QMessageBox.information(self, 'تم', 'تم رفع الحزمة إلى %s وطلب تثبيتها عبر webMAN.\n'
                                    'إذا لم يبدأ التثبيت تلقائياً: الجهاز ← Install Package Files ← packages.' % remote)
        self.run_bg(job, done, 'جاري رفع الحزمة إلى الجهاز...')

    def export_json(self):
        if not self.store: return
        out, _ = QFileDialog.getSaveFileName(self, 'تصدير الكتالوج', 'arcadia_catalog.json', 'JSON (*.json)')
        if not out: return
        json.dump(self.store.export_catalog(), open(out, 'w', encoding='utf-8'), ensure_ascii=False)
        self.statusBar().showMessage('تم تصدير الكتالوج: ' + out)


def main():
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyleSheet(STYLE)
    w = Main(); w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

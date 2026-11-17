"""PS3 file manager page (webMAN FTP): browse, drag & drop upload, download, delete, rename, new folder."""
import os
import posixpath

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QTreeWidget, QTreeWidgetItem,
                               QHeaderView, QFileDialog, QInputDialog, QMessageBox, QStyle, QComboBox)

from .core.ps3 import PS3
from .ui_common import context_menu, add, header, shortcut

PLACES = [('🏠 الجذر', '/'), ('💽 القرص dev_hdd0', '/dev_hdd0'), ('🎮 الألعاب game', '/dev_hdd0/game'),
          ('📦 packages', '/dev_hdd0/packages'), ('🕹 PS3ISO', '/dev_hdd0/PS3ISO'), ('💿 PS2ISO', '/dev_hdd0/PS2ISO'),
          ('📀 PSXISO', '/dev_hdd0/PSXISO'), ('🏪 متجر أركاديا', '/dev_hdd0/game/VIDESTORE'),
          ('🔌 فلاشة usb000', '/dev_usb000'), ('🗂 tmp', '/dev_hdd0/tmp')]


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB': return ('%.1f %s' % (n, u)) if u != 'B' else '%d B' % n
        n /= 1024.0


class FtpPage(QWidget):
    def __init__(self, win):
        super().__init__(); self.win = win; self.cwd = '/dev_hdd0'
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.places = QComboBox()
        for lab, p in PLACES: self.places.addItem(lab, p)
        self.places.activated.connect(lambda i: self.go(self.places.itemData(i)))
        self.path = QLineEdit(self.cwd); self.path.returnPressed.connect(lambda: self.go(self.path.text().strip() or '/'))
        top.addWidget(QLabel('المسار:')); top.addWidget(self.path, 1); top.addWidget(self.places)
        v.addLayout(top)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['الاسم', 'الحجم', 'التاريخ'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection); self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(self.open_item)
        self.tree.setAcceptDrops(False)
        v.addWidget(self.tree, 1)
        self.st = QLabel('اكتب IP الجهاز في «الجهاز والنشر» ثم اضغط F5'); self.st.setStyleSheet('color:#7f8bc0')
        v.addWidget(self.st)
        hint = QLabel('💡 دبل كليك للدخول • Backspace للرجوع • اسحب ملفات أو مجلدات من الكمبيوتر وأفلتها هنا للرفع • كلك يمين لكل الأدوات')
        hint.setStyleSheet('color:#7f8bc0'); v.addWidget(hint)
        self.setAcceptDrops(True)
        context_menu(self.tree, self.menu)
        shortcut(self, 'F5', self.refresh); shortcut(self.tree, 'Backspace', self.up)
        shortcut(self.tree, 'Delete', self.delete); shortcut(self.tree, 'F2', self.rename)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon); self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        self.loaded = False

    def ps3(self):
        ip = self.win.ps3_ip()
        if not ip:
            self.win.need_ip(); ip = self.win.ps3_ip()
        return PS3(ip) if ip else None

    def showEvent(self, e):
        super().showEvent(e)
        if not self.loaded and self.win.ps3_ip(): self.refresh()

    # ---------- navigation
    def go(self, path):
        self.cwd = posixpath.normpath(path) if path != '/' else '/'
        self.path.setText(self.cwd); self.refresh()

    def up(self):
        if self.cwd != '/': self.go(posixpath.dirname(self.cwd) or '/')

    def refresh(self):
        ps3 = self.ps3()
        if not ps3: return
        cwd = self.cwd
        self.st.setText('⏳ جاري القراءة...')
        self.win.run_bg(lambda prog: ps3.listdir(cwd), lambda items: self.fill(cwd, items), 'جاري قراءة ملفات الجهاز...')

    def fill(self, cwd, items):
        if cwd != self.cwd: return          # an older listing finished after we moved on
        self.loaded = True; self.tree.clear()
        if cwd != '/':
            it = QTreeWidgetItem(['..', '', '']); it.setIcon(0, self.dir_icon); it.setData(0, Qt.UserRole, {'name': '..', 'dir': True, 'size': 0})
            self.tree.addTopLevelItem(it)
        tot = 0
        for e in items:
            it = QTreeWidgetItem([e['name'], '' if e['dir'] else human(e['size']), e['mtime']])
            it.setIcon(0, self.dir_icon if e['dir'] else self.file_icon); it.setData(0, Qt.UserRole, e)
            it.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it); tot += e['size']
        self.st.setText('📂 %s — %d عنصر — %s' % (cwd, len(items), human(tot)))

    def open_item(self, it, _c=0):
        e = it.data(0, Qt.UserRole)
        if not e: return
        if e['name'] == '..': return self.up()
        if e['dir']: return self.go(posixpath.join(self.cwd, e['name']))
        if e['name'].lower().endswith('.pkg') and QMessageBox.question(self, 'تثبيت', 'تثبيت الحزمة %s على الجهاز؟' % e['name']) == QMessageBox.Yes:
            ps3 = self.ps3(); rp = posixpath.join(self.cwd, e['name'])
            self.win.run_bg(lambda prog: ps3.install_pkg(rp), lambda r: self.win.statusBar().showMessage('📦 طُلب التثبيت عبر webMAN', 8000), 'جاري طلب التثبيت...')

    def sel(self):
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems() if it.data(0, Qt.UserRole) and it.data(0, Qt.UserRole)['name'] != '..']

    # ---------- menu
    def menu(self, m, pos):
        it = self.tree.itemAt(pos)
        if it and not it.isSelected(): self.tree.clearSelection(); it.setSelected(True)
        s = self.sel()
        if s:
            header(m, s[0]['name'] if len(s) == 1 else '%d عناصر' % len(s))
            if len(s) == 1 and s[0]['dir']: add(m, '📂 فتح', lambda: self.go(posixpath.join(self.cwd, s[0]['name'])), shortcut='Return')
            add(m, '⬇ تنزيل إلى الكمبيوتر...', self.download)
            if len(s) == 1 and s[0]['name'].lower().endswith('.pkg'): add(m, '📦 تثبيت الحزمة على الجهاز', lambda: self.open_item(it))
            add(m, '✏ إعادة تسمية', self.rename, len(s) == 1, 'F2')
            add(m, '🗑 حذف', self.delete, shortcut='Delete')
            m.addSeparator()
        add(m, '⬆ رفع ملفات إلى هنا...', self.upload_files)
        add(m, '⬆ رفع مجلد إلى هنا...', self.upload_folder)
        add(m, '📁 مجلد جديد...', self.mkdir)
        m.addSeparator()
        add(m, '⬅ رجوع للمجلد الأعلى', self.up, self.cwd != '/', 'Backspace')
        add(m, '🔄 تحديث', self.refresh, shortcut='F5')
        add(m, '📋 نسخ المسار', lambda: __import__('PySide6.QtWidgets', fromlist=['QApplication']).QApplication.clipboard().setText(
            posixpath.join(self.cwd, s[0]['name']) if s else self.cwd))

    # ---------- actions
    def download(self):
        s = self.sel(); ps3 = self.ps3()
        if not s or not ps3: return
        d = QFileDialog.getExistingDirectory(self, 'حفظ في', self.win.settings.value('ftp_dl', os.path.expanduser('~')))
        if not d: return
        self.win.settings.setValue('ftp_dl', d)
        cwd = self.cwd

        def job(prog):
            n = 0
            for e in s:
                rp = posixpath.join(cwd, e['name'])
                if e['dir']: n += ps3.download_tree(rp, os.path.join(d, e['name']), prog)
                else: ps3.download(rp, os.path.join(d, e['name']), prog); n += 1
            return n
        self.win.run_bg(job, lambda n: self.win.statusBar().showMessage('✔ تم تنزيل %d ملف إلى %s' % (n, d), 10000), 'جاري التنزيل من الجهاز...')

    def upload_paths(self, paths):
        ps3 = self.ps3()
        if not ps3 or not paths: return
        cwd = self.cwd

        def job(prog):
            return sum(ps3.upload_tree(p, cwd, prog) for p in paths)
        self.win.run_bg(job, lambda n: (self.win.statusBar().showMessage('✔ تم رفع %d ملف' % n, 10000), self.refresh()), 'جاري الرفع إلى الجهاز...')

    def upload_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, 'اختر ملفات للرفع')
        self.upload_paths(fs)

    def upload_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'اختر مجلداً للرفع')
        if d: self.upload_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        self.upload_paths([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()])

    def delete(self):
        s = self.sel(); ps3 = self.ps3()
        if not s or not ps3: return
        names = '\n'.join(e['name'] for e in s[:10]) + ('\n...' if len(s) > 10 else '')
        if QMessageBox.question(self, 'حذف من الجهاز', 'حذف نهائي من الجهاز؟\n\n' + names) != QMessageBox.Yes: return
        cwd = self.cwd

        def job(prog):
            for i, e in enumerate(s):
                ps3.delete(posixpath.join(cwd, e['name']), e['dir']); prog(i + 1, len(s))
            return len(s)
        self.win.run_bg(job, lambda n: self.refresh(), 'جاري الحذف...')

    def rename(self):
        s = self.sel(); ps3 = self.ps3()
        if len(s) != 1 or not ps3: return
        new, ok = QInputDialog.getText(self, 'إعادة تسمية', 'الاسم الجديد:', text=s[0]['name'])
        if not ok or not new.strip() or new == s[0]['name']: return
        a, b = posixpath.join(self.cwd, s[0]['name']), posixpath.join(self.cwd, new.strip())
        self.win.run_bg(lambda prog: ps3.rename(a, b), lambda r: self.refresh(), 'جاري إعادة التسمية...')

    def mkdir(self):
        ps3 = self.ps3()
        if not ps3: return
        name, ok = QInputDialog.getText(self, 'مجلد جديد', 'اسم المجلد:')
        if not ok or not name.strip(): return
        p = posixpath.join(self.cwd, name.strip())
        self.win.run_bg(lambda prog: ps3.mkdir(p), lambda r: self.refresh(), 'جاري إنشاء المجلد...')

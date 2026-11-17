"""Shared UI helpers: right-click menus, clickable pictures, embedding dialogs as pages."""
from PySide6.QtCore import Qt, Signal, QObject, QEvent
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QLabel, QAbstractScrollArea

MENU_STYLE = """
QMenu { background: #141a36; border: 1px solid #2f3a78; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 26px 8px 18px; border-radius: 8px; color: #e8ecff; }
QMenu::item:selected { background: #3a2fa8; }
QMenu::item:disabled { color: #6f7aa8; }
QMenu::separator { height: 1px; background: #262f5e; margin: 5px 10px; }
QMenuBar { background: #121833; padding: 4px; }
QMenuBar::item { padding: 6px 14px; border-radius: 8px; }
QMenuBar::item:selected { background: #2f3fa0; }
"""


def context_menu(widget, build):
    """build(menu, pos) fills the menu; nothing is shown if it stays empty."""
    widget.setContextMenuPolicy(Qt.CustomContextMenu)

    def show(pos):
        m = QMenu(widget)
        build(m, pos)
        if not m.isEmpty():
            host = widget.viewport() if isinstance(widget, QAbstractScrollArea) else widget
            m.exec(host.mapToGlobal(pos))
    widget.customContextMenuRequested.connect(show)


def add(menu, text, fn=None, enabled=True, shortcut=None):
    a = menu.addAction(text)
    if fn: a.triggered.connect(lambda *_: fn())
    a.setEnabled(bool(enabled))
    if shortcut: a.setShortcut(QKeySequence(shortcut)); a.setShortcutVisibleInContextMenu(True)
    return a


def header(menu, text):
    a = menu.addAction(text); a.setEnabled(False); menu.addSeparator(); return a


def shortcut(widget, keys, fn):
    """keyboard shortcut active while the widget (or its children) has focus"""
    a = QAction(widget); a.setShortcut(QKeySequence(keys)); a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
    a.triggered.connect(lambda *_: fn()); widget.addAction(a); return a


class ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton: self.clicked.emit()
        super().mouseReleaseEvent(e)


class _NoEscape(QObject):
    def eventFilter(self, obj, ev):
        return ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape


_noesc = None


def as_page(dlg):
    """turn a QDialog into a normal page widget (no window, Esc doesn't hide it)"""
    global _noesc
    if _noesc is None: _noesc = _NoEscape()
    dlg.setWindowFlags(Qt.Widget)
    dlg.installEventFilter(_noesc)
    return dlg

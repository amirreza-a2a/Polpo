# ============================================================
#  interfaces/desktop/qt_compat.py
#  Desktop Qt Compatibility Module (PySide6 / PyQt6)
# ============================================================

try:
    from PySide6.QtCore import (
        QObject,
        Signal,
        Slot,
        Property,
        QModelIndex,
        Qt,
        QCoreApplication,
        QThread,
        QUrl,
        QAbstractListModel,
        QEvent,
        QTimer,
    )
    from PySide6.QtGui import (
        QGuiApplication,
        QDesktopServices,
        QTextDocument,
        QTextCursor,
        QSyntaxHighlighter,
        QTextCharFormat,
        QColor,
        QFont,
    )
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    QT_BINDING = "PySide6"
except ImportError:
    from PyQt6.QtCore import (
        QObject,
        pyqtSignal as Signal,
        pyqtSlot as Slot,
        pyqtProperty as Property,
        QModelIndex,
        Qt,
        QCoreApplication,
        QThread,
        QUrl,
        QAbstractListModel,
        QEvent,
        QTimer,
    )
    from PyQt6.QtGui import (
        QGuiApplication,
        QDesktopServices,
        QTextDocument,
        QTextCursor,
        QSyntaxHighlighter,
        QTextCharFormat,
        QColor,
        QFont,
    )
    from PyQt6.QtQml import QQmlApplicationEngine
    from PyQt6.QtQuick import QQuickItem
    QT_BINDING = "PyQt6"

__all__ = [
    "QObject",
    "Signal",
    "Slot",
    "Property",
    "QModelIndex",
    "Qt",
    "QCoreApplication",
    "QThread",
    "QUrl",
    "QAbstractListModel",
    "QEvent",
    "QTimer",
    "QGuiApplication",
    "QDesktopServices",
    "QQmlApplicationEngine",
    "QQuickItem",
    "QTextDocument",
    "QTextCursor",
    "QSyntaxHighlighter",
    "QTextCharFormat",
    "QColor",
    "QFont",
    "QT_BINDING",
]

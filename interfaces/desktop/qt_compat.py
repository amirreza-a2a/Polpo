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
        QByteArray,
        QSize,
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
        QImage,
        QPainter,
    )
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
    from PySide6.QtQuick import QQuickItem, QQuickImageProvider
    from PySide6.QtSvg import QSvgRenderer
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
        QByteArray,
        QSize,
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
        QImage,
        QPainter,
    )
    from PyQt6.QtQml import QQmlApplicationEngine, QQmlComponent
    from PyQt6.QtQuick import QQuickItem, QQuickImageProvider
    from PyQt6.QtSvg import QSvgRenderer
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
    "QByteArray",
    "QSize",
    "QAbstractListModel",
    "QEvent",
    "QTimer",
    "QGuiApplication",
    "QDesktopServices",
    "QQmlApplicationEngine",
    "QQmlComponent",
    "QQuickItem",
    "QQuickImageProvider",
    "QSvgRenderer",
    "QImage",
    "QPainter",
    "QTextDocument",
    "QTextCursor",
    "QSyntaxHighlighter",
    "QTextCharFormat",
    "QColor",
    "QFont",
    "QT_BINDING",
]

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
    )
    from PySide6.QtGui import QGuiApplication, QDesktopServices
    from PySide6.QtQml import QQmlApplicationEngine
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
    )
    from PyQt6.QtGui import QGuiApplication, QDesktopServices
    from PyQt6.QtQml import QQmlApplicationEngine
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
    "QGuiApplication",
    "QDesktopServices",
    "QQmlApplicationEngine",
    "QT_BINDING",
]

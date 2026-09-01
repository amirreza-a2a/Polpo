# ============================================================
#  interfaces/desktop/controllers/quick_convert_controller.py
#  Desktop Presentation Controller for Single-Image Quick Convert
# ============================================================

import threading
from pathlib import Path
from typing import Optional, Dict, Any

from interfaces.desktop.qt_compat import (
    QObject,
    Slot,
    Signal,
    Property,
    QUrl,
    QGuiApplication,
)
from application.dto.quick_convert_dto import QuickConvertCommand
from application.services.quick_convert import QuickConvertService
from application.sanitizer import sanitize_error_message


def _to_local_path(file_path: str) -> Path:
    """Safely normalizes local file path or file:// URI across Windows, macOS, and Linux."""
    if file_path.startswith("file:"):
        local_str = QUrl(file_path).toLocalFile()
        if local_str:
            return Path(local_str)
        cleaned = file_path[7:] if file_path.startswith("file://") else file_path[5:]
        return Path(cleaned)
    return Path(file_path)


class QuickConvertController(QObject):
    """
    Presentation controller for single-image drag-and-drop OCR markdown extraction.
    Executes AI requests asynchronously on a background worker thread so the GUI thread never blocks.
    """

    conversion_started = Signal()
    conversion_completed = Signal(str, str)  # markdown_content, api_label
    error_occurred = Signal(str)
    busy_changed = Signal()

    def __init__(self, quick_convert_service: QuickConvertService, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.quick_convert_service = quick_convert_service
        self._is_busy = False

    def _get_is_busy(self) -> bool:
        return self._is_busy

    isBusy = Property(bool, _get_is_busy, notify=busy_changed)

    @Slot(str, str)
    @Slot(str)
    def convert_image(self, file_path: str, prompt_text: str = "") -> None:
        """
        Asynchronously converts an image file directly to clean Markdown.
        Never blocks the Qt GUI thread.
        """
        if self._is_busy:
            self.error_occurred.emit("A conversion is already in progress.")
            return

        try:
            path = _to_local_path(file_path)
            if not path.exists() or not path.is_file():
                err = f"Image file not found: '{file_path}'"
                self.error_occurred.emit(err)
                return

            ext = path.suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                self.error_occurred.emit(f"Unsupported image format '{ext}'. Supported: PNG, JPG, JPEG, WebP.")
                return

            mime_type = "image/png" if ext == ".png" else "image/jpeg"
            if ext == ".webp":
                mime_type = "image/webp"

            file_bytes = path.read_bytes()
            cmd = QuickConvertCommand(
                user_id=1,
                image_bytes=file_bytes,
                prompt_text=prompt_text.strip() if prompt_text and prompt_text.strip() else None,
                mime_type=mime_type,
            )

            self._is_busy = True
            self.busy_changed.emit()
            self.conversion_started.emit()

            def _worker():
                try:
                    dto = self.quick_convert_service.convert_image(cmd)
                    self._is_busy = False
                    self.busy_changed.emit()
                    self.conversion_completed.emit(dto.markdown_content, dto.used_api_label or "")
                except Exception as err:
                    self._is_busy = False
                    self.busy_changed.emit()
                    sanitized = sanitize_error_message(str(err))
                    self.error_occurred.emit(sanitized)

            worker_thread = threading.Thread(target=_worker, name="QuickConvertWorker", daemon=True)
            worker_thread.start()

        except Exception as err:
            self._is_busy = False
            self.busy_changed.emit()
            sanitized = sanitize_error_message(str(err))
            self.error_occurred.emit(sanitized)

    @Slot(str, result=bool)
    def copy_to_clipboard(self, text: str) -> bool:
        """Copies markdown text to the OS clipboard in a cross-platform manner."""
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                return True
            return False
        except Exception as err:
            self.error_occurred.emit(f"Failed to copy to clipboard: {err}")
            return False

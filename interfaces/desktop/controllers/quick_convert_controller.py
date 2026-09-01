# ============================================================
#  interfaces/desktop/controllers/quick_convert_controller.py
#  Desktop Presentation Controller for Single-Image Quick Convert
# ============================================================

from pathlib import Path
from typing import Optional, Dict, Any
from interfaces.desktop.qt_compat import QObject, Slot, Signal
from application.dto.quick_convert_dto import QuickConvertCommand
from application.services.quick_convert import QuickConvertService
from application.sanitizer import sanitize_error_message


class QuickConvertController(QObject):
    """
    Presentation controller for single-image drag-and-drop OCR markdown extraction.
    """

    conversion_started = Signal()
    conversion_completed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, quick_convert_service: QuickConvertService, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.quick_convert_service = quick_convert_service

    @Slot(str, str, result="QVariantMap")
    @Slot(str, result="QVariantMap")
    def convert_image(self, file_path: str, prompt_text: str = "") -> Dict[str, Any]:
        """
        Converts an image file directly to clean Markdown.
        """
        try:
            self.conversion_started.emit()
            path = Path(file_path.replace("file://", ""))
            if not path.exists() or not path.is_file():
                err = f"Image file not found: '{file_path}'"
                self.error_occurred.emit(err)
                return {}

            ext = path.suffix.lower()
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
            dto = self.quick_convert_service.convert_image(cmd)
            self.conversion_completed.emit(dto.markdown_content)
            return {
                "markdown_content": dto.markdown_content,
                "used_api_label": dto.used_api_label or "",
                "pages_consumed": dto.pages_consumed,
            }
        except Exception as err:
            sanitized = sanitize_error_message(str(err))
            self.error_occurred.emit(sanitized)
            return {}

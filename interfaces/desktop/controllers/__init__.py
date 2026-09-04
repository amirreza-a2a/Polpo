# ============================================================
#  interfaces/desktop/controllers/__init__.py
# ============================================================

from interfaces.desktop.controllers.job_controller import JobController
from interfaces.desktop.controllers.api_key_controller import ApiKeyController
from interfaces.desktop.controllers.prompt_controller import PromptController
from interfaces.desktop.controllers.settings_controller import SettingsController
from interfaces.desktop.controllers.quick_convert_controller import QuickConvertController
from interfaces.desktop.controllers.document_viewer_controller import DocumentViewerController

__all__ = [
    "JobController",
    "ApiKeyController",
    "PromptController",
    "SettingsController",
    "QuickConvertController",
    "DocumentViewerController",
]

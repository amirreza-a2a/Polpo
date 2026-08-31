# ============================================================
#  tests/characterization/conftest_base.py
# ============================================================

import sys
import os
import types
from unittest.mock import MagicMock, AsyncMock

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ─── Mocking external dependencies in dev env ────────────────
if "pymysql" not in sys.modules:
    mock_pymysql = types.ModuleType("pymysql")
    mock_pymysql_cursors = types.ModuleType("pymysql.cursors")
    mock_pymysql_cursors.DictCursor = object
    mock_pymysql.cursors = mock_pymysql_cursors
    mock_pymysql.connect = MagicMock()
    sys.modules["pymysql"] = mock_pymysql
    sys.modules["pymysql.cursors"] = mock_pymysql_cursors

if "telegram" not in sys.modules:
    mock_tg = types.ModuleType("telegram")
    mock_tg.Update = MagicMock
    mock_tg.Bot = MagicMock
    mock_tg.InlineKeyboardButton = MagicMock
    mock_tg.InlineKeyboardMarkup = MagicMock
    sys.modules["telegram"] = mock_tg

if "telegram.ext" not in sys.modules:
    mock_tg_ext = types.ModuleType("telegram.ext")
    mock_tg_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    mock_tg_ext.Application = MagicMock()
    mock_tg_ext.CommandHandler = MagicMock
    mock_tg_ext.MessageHandler = MagicMock
    mock_tg_ext.CallbackQueryHandler = MagicMock
    mock_tg_ext.ConversationHandler = MagicMock
    mock_tg_ext.filters = MagicMock()
    sys.modules["telegram.ext"] = mock_tg_ext

if "PIL" not in sys.modules:
    mock_pil = types.ModuleType("PIL")
    mock_pil_img = types.ModuleType("PIL.Image")
    class ImageClass:
        pass
    mock_pil_img.Image = ImageClass
    mock_pil_img.open = MagicMock()
    mock_pil.Image = mock_pil_img
    sys.modules["PIL"] = mock_pil
    sys.modules["PIL.Image"] = mock_pil_img

if "fitz" not in sys.modules:
    mock_fitz = types.ModuleType("fitz")
    mock_fitz.open = MagicMock()
    sys.modules["fitz"] = mock_fitz

if "google" not in sys.modules:
    mock_google = types.ModuleType("google")
    mock_google_genai = types.ModuleType("google.genai")
    mock_google_genai.Client = MagicMock
    mock_google.genai = mock_google_genai
    sys.modules["google"] = mock_google
    sys.modules["google.genai"] = mock_google_genai

if "openai" not in sys.modules:
    mock_openai = types.ModuleType("openai")
    mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = mock_openai

# Import handlers so patching targets resolve cleanly
import handlers.common
import handlers.pdf
import handlers.user
import handlers.admin
import handlers.quick_convert
import handlers.pipeline2


class MockDatabase:
    """In-memory dictionary-backed store simulating MySQL tables for characterization."""
    def __init__(self):
        self.users = {}
        self.jobs = {}
        self.pipeline2_jobs = {}
        self.prompts = {
            1: {"id": 1, "title": "پیش‌فرض", "prompt_text": "Default Prompt", "is_active": 1, "is_default": 1},
            2: {"id": 2, "title": "تخصصی", "prompt_text": "Special Prompt", "is_active": 1, "is_default": 0},
        }
        self.pipeline2_prompts = {
            1: {"id": 1, "title": "P2 Default", "prompt_text": "P2 Default Text", "is_active": 1, "is_default": 1},
        }
        self.private_apis = {}
        self.public_apis = {}
        self.bot_settings = {"public_apis_active": 1}
        self._next_id = 100

    def next_id(self):
        self._next_id += 1
        return self._next_id


class MockTelegramContext:
    """Mock telegram.ext ContextTypes.DEFAULT_TYPE."""
    def __init__(self):
        self.bot_data = {}
        self.user_data = {}
        self.chat_data = {}
        self.bot = AsyncMock()


class MockTelegramUpdate:
    """Mock telegram.Update."""
    def __init__(self, user_id=12345, text=None, callback_data=None):
        self.effective_user = MagicMock()
        self.effective_user.id = user_id
        self.effective_user.first_name = "TestUser"
        self.effective_user.username = "testuser"
        self.effective_chat = MagicMock()
        self.effective_chat.id = user_id

        self.message = None
        self.callback_query = None

        if text is not None:
            self.message = AsyncMock()
            self.message.text = text
            self.message.reply_text = AsyncMock()
            self.message.reply_document = AsyncMock()

        if callback_data is not None:
            self.callback_query = AsyncMock()
            self.callback_query.data = callback_data
            self.callback_query.message = AsyncMock()
            self.callback_query.message.edit_text = AsyncMock()
            self.callback_query.answer = AsyncMock()

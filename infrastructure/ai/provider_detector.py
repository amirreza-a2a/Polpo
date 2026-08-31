# ============================================================
#  infrastructure/ai/provider_detector.py
#  Provider & Model Auto-Detection and Configuration Defaults
# ============================================================

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("polpot.ai.detector")

_DEFAULT_BASE_URL = {
    "google": None,
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_DEFAULT_MODEL = {
    "google": "gemini-3.5-flash",
    "openai": "gpt-4o",
    "openrouter": "openai/gpt-4o",
}


def get_default_base_url(provider: str) -> Optional[str]:
    """دریافت Base URL پیش‌فرض برای ارائه‌دهنده."""
    return _DEFAULT_BASE_URL.get(provider.lower()) if provider else None


def get_default_model(provider: str) -> str:
    """دریافت مدل پیش‌فرض برای ارائه‌دهنده."""
    return _DEFAULT_MODEL.get(provider.lower(), "gemini-3.5-flash") if provider else "gemini-3.5-flash"


def detect_provider_and_models(api_key: str, base_url: Optional[str] = None) -> Tuple[Optional[str], List[str]]:
    """
    تشخیص خودکار ارائه‌دهنده هوش مصنوعی و فهرست مدل‌های در دسترس با کلید ارائه‌شده.
    خروجی: (نام_ارائه‌دهنده, لیست_مدل‌ها)
    """
    if not api_key:
        return None, []

    # 1. تست Google Gemini در صورت نبود base_url
    if not base_url:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            models_list = list(client.models.list())
            model_names = []
            for m in models_list:
                name = m.name.replace("models/", "")
                if "gemini" in name.lower():
                    model_names.append(name)
            if not model_names:
                model_names = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-1.5-pro"]
            return "google", model_names
        except Exception as e:
            logger.debug(f"Google discovery attempt failed: {e}")

    # 2. تست OpenAI و ارائه‌دهندگان سازگار (مانند OpenRouter)
    try:
        import openai
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        models = client.models.list()
        model_names = [m.id for m in models.data]

        if model_names:
            if base_url and "openrouter" in base_url.lower():
                return "openrouter", model_names
            if base_url:
                return "openai", model_names
            gpt_models = [m for m in model_names if "gpt" in m.lower()]
            if gpt_models:
                return "openai", gpt_models
            return "openai", model_names
    except Exception as e:
        logger.debug(f"OpenAI discovery attempt failed: {e}")

    # 3. تست ثانویه OpenRouter پیش‌فرض در صورت نبود base_url
    if not base_url:
        try:
            import openai
            client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            models = client.models.list()
            model_names = [m.id for m in models.data]
            if model_names:
                return "openrouter", model_names
        except Exception as e:
            logger.debug(f"OpenRouter discovery attempt failed: {e}")

    return None, []

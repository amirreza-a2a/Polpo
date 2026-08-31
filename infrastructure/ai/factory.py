# ============================================================
#  infrastructure/ai/factory.py
# ============================================================

from typing import Optional
from application.ports.ai_provider import AIProviderPort
from infrastructure.ai.google_adapter import GoogleAdapter
from infrastructure.ai.openai_adapter import OpenAIAdapter


def create_ai_adapter(
    provider: str,
    api_key: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 120.0,
) -> AIProviderPort:
    """
    Factory function جهت ساخت پیاده‌سازی مناسب از AIProviderPort بر اساس provider و پیکربندی داده‌شده.
    """
    provider_clean = (provider or "").lower().strip()

    if provider_clean == "google":
        return GoogleAdapter(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            timeout=timeout,
        )

    if provider_clean in ("openai", "openrouter") or base_url:
        return OpenAIAdapter(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            provider_name=provider_clean if provider_clean in ("openai", "openrouter") else "openai",
            timeout=timeout,
        )

    # پیش‌فرض در صورت عدم تطابق صریح
    return GoogleAdapter(
        api_key=api_key,
        default_model=model,
        base_url=base_url,
        timeout=timeout,
    )

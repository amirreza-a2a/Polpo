# ============================================================
#  services/api_manager.py  –  مدیریت زنجیره API و سوئیچ
# ============================================================

import json
from database.models import (
    get_user_private_apis,
    get_next_available_public_api,
    get_user,
    increment_public_api_pages,
)

# Base URL پیش‌فرض هر provider
_DEFAULT_BASE_URL = {
    "google":     None,                          # SDK خودش مدیریت می‌کند
    "openai":     "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# مدل پیش‌فرض هر provider
_DEFAULT_MODEL = {
    "google":     "gemini-2.5-flash",
    "openai":     "gpt-4o",
    "openrouter": "openai/gpt-4o",
}


def get_default_base_url(provider: str) -> str | None:
    return _DEFAULT_BASE_URL.get(provider)


def get_default_model(provider: str) -> str:
    return _DEFAULT_MODEL.get(provider, "gemini-2.5-flash")


def build_api_chain(user_id: int, use_public: bool) -> list:
    """
    زنجیره API را برای یک جاب می‌سازد.
    خروجی: لیستی از دیکشنری با selected_model و base_url
    """
    chain = []

    # ─── API های خصوصی ────────────────────────────────────
    private_apis = get_user_private_apis(user_id)
    for api in private_apis:
        # مدل: اول selected_model، اگر نبود اولین مدل از لیست، اگر نبود default
        model = (
            api.get("selected_model")
            or (api["supported_models"] or [None])[0]
            or get_default_model(api["provider"])
        )
        # base_url: اول مقدار ذخیره‌شده، اگر نبود پیش‌فرض
        base_url = api.get("base_url") or get_default_base_url(api["provider"])

        chain.append({
            "type":           "private",
            "id":             api["id"],
            "api_key":        api["api_key"],
            "provider":       api["provider"],
            "models":         api["supported_models"],
            "selected_model": model,
            "base_url":       base_url,
            "label":          api["label"],
        })

    # ─── API عمومی ────────────────────────────────────────
    if use_public:
        public_api = get_next_available_public_api()
        if public_api:
            model = (public_api["supported_models"] or [None])[0] or get_default_model(public_api["provider"])
            chain.append({
                "type":           "public",
                "id":             public_api["id"],
                "api_key":        public_api["api_key"],
                "provider":       public_api["provider"],
                "models":         public_api["supported_models"],
                "selected_model": model,
                "base_url":       get_default_base_url(public_api["provider"]),
                "label":          "API عمومی",
            })

    return chain


def get_current_api(job: dict) -> dict | None:
    """API فعلی جاب را برمی‌گرداند."""
    chain = job["api_chain"]
    idx   = job["current_api_index"]
    if not chain or idx >= len(chain):
        return None
    return chain[idx]


def switch_to_next_api(job: dict, reason: str, at_page: int) -> dict | None:
    chain       = job["api_chain"]
    current_idx = job["current_api_index"]
    switch_log  = job.get("api_switch_log") or []

    if current_idx < len(chain):
        switch_log.append({
            "switched_at_page": at_page,
            "from": f"{chain[current_idx]['type']}_{chain[current_idx]['id']}",
            "reason": reason,
        })

    next_idx = current_idx + 1

    while next_idx < len(chain):
        candidate = chain[next_idx]

        if candidate["type"] == "public":
            fresh = get_next_available_public_api()
            if fresh is None:
                next_idx += 1
                continue
            model = (fresh["supported_models"] or [None])[0] or get_default_model(fresh["provider"])
            chain[next_idx]["api_key"]        = fresh["api_key"]
            chain[next_idx]["id"]             = fresh["id"]
            chain[next_idx]["models"]         = fresh["supported_models"]
            chain[next_idx]["selected_model"] = model
            chain[next_idx]["base_url"]       = get_default_base_url(fresh["provider"])

        switch_log[-1]["to"] = f"{candidate['type']}_{candidate['id']}"
        job["current_api_index"] = next_idx
        job["api_switch_log"]    = switch_log
        job["api_chain"]         = chain
        return candidate

    # آخرین تلاش: public جدید
    fresh = get_next_available_public_api()
    if fresh:
        model = (fresh["supported_models"] or [None])[0] or get_default_model(fresh["provider"])
        new_entry = {
            "type":           "public",
            "id":             fresh["id"],
            "api_key":        fresh["api_key"],
            "provider":       fresh["provider"],
            "models":         fresh["supported_models"],
            "selected_model": model,
            "base_url":       get_default_base_url(fresh["provider"]),
            "label":          "API عمومی",
        }
        chain.append(new_entry)
        switch_log[-1]["to"] = f"public_{fresh['id']}"
        job["current_api_index"] = len(chain) - 1
        job["api_switch_log"]    = switch_log
        job["api_chain"]         = chain
        return new_entry

    job["api_switch_log"] = switch_log
    return None


def report_pages_used(api_entry: dict, count: int):
    if api_entry and api_entry["type"] == "public":
        increment_public_api_pages(api_entry["id"], count)


def detect_provider_and_models(api_key: str, base_url: str = None) -> tuple[str, list]:
    """
    Provider و مدل‌های موجود را تشخیص می‌دهد.
    base_url اختیاری است - اگر داده شود برای OpenAI-compatible APIها استفاده می‌شود.
    """
    # ─── تست Google / Gemini ──────────────────────────────
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
                model_names = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
            return "google", model_names
        except Exception as e:
            print(f"Google test failed: {e}")

    # ─── تست OpenAI / OpenRouter / Custom ────────────────
    try:
        import openai
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        models = client.models.list()
        model_names = [m.id for m in models.data]

        if model_names:
            # تشخیص provider از base_url یا نام مدل‌ها
            if base_url and "openrouter" in base_url:
                return "openrouter", model_names
            if base_url:
                return "openai", model_names
            # بدون base_url ← OpenAI اصلی
            gpt_models = [m for m in model_names if "gpt" in m.lower()]
            if gpt_models:
                return "openai", gpt_models
    except Exception as e:
        print(f"OpenAI test failed: {e}")

    # ─── تست OpenRouter (بدون base_url صریح) ─────────────
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
            print(f"OpenRouter test failed: {e}")

    return None, []
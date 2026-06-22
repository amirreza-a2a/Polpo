# ============================================================
#  services/api_manager.py  –  مدیریت زنجیره API و سوئیچ
# ============================================================

from database.models import (
    get_user_private_apis,
    get_all_available_public_apis,
    get_public_api_by_id,
    get_user,
    increment_public_api_pages,
)

_DEFAULT_BASE_URL = {
    "google":     None,
    "openai":     "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_DEFAULT_MODEL = {
    "google":     "gemini-3.5-flash",
    "openai":     "gpt-4o",
    "openrouter": "openai/gpt-4o",
}


def get_default_base_url(provider: str) -> str | None:
    return _DEFAULT_BASE_URL.get(provider)


def get_default_model(provider: str) -> str:
    return _DEFAULT_MODEL.get(provider, "gemini-3.5-flash")


def _build_chain_entry(api: dict, entry_type: str, default_label: str = None) -> dict:
    model = (
        api.get("selected_model")
        or (api.get("supported_models") or [None])[0]
        or get_default_model(api["provider"])
    )
    base_url = api.get("base_url") or get_default_base_url(api["provider"])
    return {
        "type":           entry_type,
        "id":             api["id"],
        "api_key":        api["api_key"],
        "provider":       api["provider"],
        "models":         api.get("supported_models"),
        "selected_model": model,
        "base_url":       base_url,
        "label":          api.get("label") or default_label,
    }


def build_api_chain(user_id: int,
                     include_private: bool,
                     include_public: bool) -> list:
    """
    زنجیره API را بر اساس انتخاب صریح کاربر می‌سازد.

    include_private=True, include_public=False  → فقط خصوصی، بدون fallback
    include_private=True, include_public=True   → خصوصی + همه‌ی عمومی‌ها به ترتیب priority
    include_private=False, include_public=True  → فقط عمومی (خصوصی اصلاً وارد چین نمی‌شود)
    """
    chain = []

    if include_private:
        for api in get_user_private_apis(user_id):
            chain.append(_build_chain_entry(api, "private"))

    if include_public:
        for pub in get_all_available_public_apis():
            chain.append(_build_chain_entry(pub, "public", default_label="API عمومی"))

    return chain


def get_current_api(job: dict) -> dict | None:
    chain = job["api_chain"]
    idx   = job["current_api_index"]
    if not chain or idx >= len(chain):
        return None
    return chain[idx]


def switch_to_next_api(job: dict, reason: str, at_page: int) -> dict | None:
    """
    به اسلات بعدیِ از پیش موجود در چین سوئیچ می‌کند.
    هیچ API جدیدی که قبلاً در چین نبوده اضافه نمی‌شود — چین کاملاً
    بازتاب‌دهنده‌ی انتخاب کاربر است (build_api_chain).
    """
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
            # اعتبارسنجی همین اسلات مشخص (نه گرفتن یک public دلخواه دیگر)
            fresh = get_public_api_by_id(candidate["id"])
            still_available = (
                fresh is not None
                and fresh.get("is_active")
                and fresh["pages_used_today"] < fresh["daily_page_limit"]
            )
            if not still_available:
                next_idx += 1
                continue
            # sync کردن کلید/مدل در صورت تغییر دستی توسط ادمین
            chain[next_idx]["api_key"] = fresh["api_key"]

        switch_log[-1]["to"] = f"{candidate['type']}_{candidate['id']}"
        job["current_api_index"] = next_idx
        job["api_switch_log"]    = switch_log
        job["api_chain"]         = chain
        return candidate

    # چین تمام شد — هیچ API دیگری که کاربر مجاز دانسته باقی نمانده
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
            print(f"Google test failed: {e}")

    try:
        import openai
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        models = client.models.list()
        model_names = [m.id for m in models.data]

        if model_names:
            if base_url and "openrouter" in base_url:
                return "openrouter", model_names
            if base_url:
                return "openai", model_names
            gpt_models = [m for m in model_names if "gpt" in m.lower()]
            if gpt_models:
                return "openai", gpt_models
    except Exception as e:
        print(f"OpenAI test failed: {e}")

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
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


def build_api_chain(user_id: int, use_public: bool) -> list:
    """
    زنجیره API را برای یک جاب می‌سازد.
    
    خروجی: لیستی از دیکشنری مثل:
    [
      {"type": "private", "id": 3, "api_key": "...", "provider": "google",
       "models": [...], "label": "کلید اصلی"},
      {"type": "public",  "id": 1, "api_key": "...", "provider": "google",
       "models": [...], "label": "عمومی"},
    ]
    """
    chain = []

    # ─── API های خصوصی (مرتب بر اساس priority) ────────────
    private_apis = get_user_private_apis(user_id)
    for api in private_apis:
        chain.append({
            "type":     "private",
            "id":       api["id"],
            "api_key":  api["api_key"],
            "provider": api["provider"],
            "models":   api["supported_models"],
            "label":    api["label"],
        })

    # ─── API عمومی (اگر کاربر خواسته باشد) ────────────────
    if use_public:
        public_api = get_next_available_public_api()
        if public_api:
            chain.append({
                "type":      "public",
                "id":        public_api["id"],
                "api_key":   public_api["api_key"],
                "provider":  public_api["provider"],
                "models":    public_api["supported_models"],
                "label":     "API عمومی",
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

    # لاگ سوئیچ
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
            # یک API عمومی تازه با ظرفیت پیدا کن
            fresh = get_next_available_public_api()
            if fresh is None:
                next_idx += 1
                continue
            # اطلاعات تازه را جایگزین کن
            chain[next_idx]["api_key"] = fresh["api_key"]
            chain[next_idx]["id"]      = fresh["id"]
            chain[next_idx]["models"]  = fresh["supported_models"]

        switch_log[-1]["to"] = f"{candidate['type']}_{candidate['id']}"
        job["current_api_index"] = next_idx
        job["api_switch_log"]    = switch_log
        job["api_chain"]         = chain
        return candidate

    # ─── هیچ API در chain نماند، آخرین تلاش: public جدید ───
    fresh = get_next_available_public_api()
    if fresh:
        new_entry = {
            "type":     "public",
            "id":       fresh["id"],
            "api_key":  fresh["api_key"],
            "provider": fresh["provider"],
            "models":   fresh["supported_models"],
            "label":    "API عمومی",
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
    """مصرف صفحه را برای API عمومی ثبت می‌کند."""
    if api_entry and api_entry["type"] == "public":
        increment_public_api_pages(api_entry["id"], count)



def detect_provider_and_models(api_key: str) -> tuple[str, list]:
    # ─── تست Google / Gemini ───────────────────────────────
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        # یک درخواست ساده برای تست اعتبار کلید
        models_list = list(client.models.list())
        model_names = []
        for m in models_list:
            name = m.name.replace("models/", "")
            # فقط مدل‌های gemini که قابل استفاده هستند
            if "gemini" in name.lower():
                model_names.append(name)
        
        if not model_names:
            # اگر لیست خالی بود، مدل‌های پیش‌فرض
            model_names = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
        
        return "google", model_names
    except Exception as e:
        print(f"Google test failed: {e}")

    # ─── تست OpenAI ────────────────────────────────────────
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        models = client.models.list()
        model_names = [m.id for m in models.data if "gpt" in m.id.lower()]
        if model_names:
            return "openai", model_names
    except Exception as e:
        print(f"OpenAI test failed: {e}")

    # ─── تست OpenRouter ────────────────────────────────────
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
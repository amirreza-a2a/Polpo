# ============================================================
#  infrastructure/security/token_service.py
# ============================================================

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, Optional, Tuple
from application.ports.token_service import ITokenService
from config import BOT_TOKEN


class SecureTokenService(ITokenService):
    """
    پیاده‌سازی امن امضای توکن‌های Bearer، کلیدهای ماشین (Machine API Key)
    و کدهای تبادل تک‌بار مصرف با استفاده از HMAC-SHA256 استاندارد.
    """

    def __init__(self, secret_key: Optional[str] = None):
        raw_secret = secret_key or os.getenv("AUTH_SECRET_KEY") or BOT_TOKEN
        if not raw_secret or not str(raw_secret).strip():
            raise ValueError(
                "Security fatal: No authentication signing secret configured. "
                "Set AUTH_SECRET_KEY or BOT_TOKEN in environment."
            )
        self.secret_key = str(raw_secret).encode("utf-8")
        # نگهداری کدهای تبادل یک‌بار مصرف: code -> (user_id, expire_timestamp)
        self._exchange_codes: Dict[str, Tuple[int, float]] = {}

    def create_access_token(self, user_id: int, expires_minutes: int = 60 * 24 * 7) -> str:
        expire_at = int(time.time()) + (expires_minutes * 60)
        payload = {"sub": user_id, "exp": expire_at, "iat": int(time.time()), "type": "bearer"}
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

        sig = hmac.new(self.secret_key, f"bearer:{payload_b64}".encode("utf-8"), hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

        return f"{payload_b64}.{sig_b64}"

    def verify_access_token(self, token: str) -> Optional[int]:
        try:
            parts = token.strip().split(".")
            if len(parts) != 2:
                return None

            payload_b64, sig_b64 = parts
            expected_sig = hmac.new(self.secret_key, f"bearer:{payload_b64}".encode("utf-8"), hashlib.sha256).digest()
            expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

            if not hmac.compare_digest(sig_b64, expected_sig_b64):
                return None

            padded_b64 = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded_b64).decode("utf-8"))

            if payload.get("type") != "bearer":
                return None

            if payload.get("exp", 0) < time.time():
                return None

            return int(payload.get("sub"))
        except Exception:
            return None

    def create_machine_api_key(self, user_id: int) -> str:
        """
        تولید کلید ماشین مستقل با امضای دامنه machine_key.
        ساختار: polpot_key_<user_id>_<signature>
        """
        msg = f"machine_key:{user_id}".encode("utf-8")
        sig = hmac.new(self.secret_key, msg, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
        return f"polpot_key_{user_id}_{sig_b64}"

    def verify_machine_api_key(self, api_key: str) -> Optional[int]:
        """
        اعتبارسنجی مستقل کلید دسترسی ماشین.
        """
        try:
            parts = api_key.strip().split("_", 3)
            if len(parts) != 4 or parts[0] != "polpot" or parts[1] != "key":
                return None

            user_id = int(parts[2])
            sig_b64 = parts[3]

            msg = f"machine_key:{user_id}".encode("utf-8")
            expected_sig = hmac.new(self.secret_key, msg, hashlib.sha256).digest()
            expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

            if not hmac.compare_digest(sig_b64, expected_sig_b64):
                return None

            return user_id
        except Exception:
            return None

    def create_one_time_exchange_code(self, user_id: int, expires_seconds: int = 300) -> str:
        now = time.time()
        self._exchange_codes = {k: v for k, v in self._exchange_codes.items() if v[1] > now}

        code = f"ex_{secrets.token_urlsafe(16)}"
        self._exchange_codes[code] = (user_id, now + expires_seconds)
        return code

    def exchange_code_for_user_id(self, code: str) -> Optional[int]:
        now = time.time()
        record = self._exchange_codes.pop(code, None)
        if not record:
            return None
        user_id, expire_at = record
        if now > expire_at:
            return None
        return user_id

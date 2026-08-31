# ============================================================
#  core/policies/quota_policy.py
# ============================================================

from datetime import date
from core.entities.user import User, QuotaAllocation


class QuotaPolicy:
    """
    سیاست‌های محاسبه، بررسی و ریست سهمیه روزانه کاربر در لایه دامنه.
    """

    @classmethod
    def should_reset_quota(cls, last_active_date: date, today: date = None) -> bool:
        """بررسی اینکه آیا تاریخ جاری نسبت به آخرین فعالیت تغییر کرده است."""
        if last_active_date is None:
            return False
        if today is None:
            today = date.today()
        return today > last_active_date

    @classmethod
    def can_consume(cls, quota: QuotaAllocation, pages: int = 1) -> bool:
        """بررسی اینکه آیا سهمیه باقیمانده پاسخگوی درخواست است."""
        return (quota.daily_pages_used + pages) <= quota.daily_limit

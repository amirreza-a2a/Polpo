# ============================================================
#  infrastructure/notifier/event_notifier.py
# ============================================================

import asyncio
import logging
from collections import defaultdict
from typing import Dict, Set
from application.ports.notifier import IProgressNotifier

logger = logging.getLogger("polpot.notifier")


class InMemoryEventNotifier(IProgressNotifier):
    """
    اطلاع‌رسان پیشرفت کارها در حافظه با قابلیت انتشار رویداد به شنوندگان SSE و کلاینت‌ها.
    """

    def __init__(self):
        self._listeners: Dict[int, Set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, job_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners[job_id].add(q)
        return q

    def unsubscribe(self, job_id: int, q: asyncio.Queue) -> None:
        if job_id in self._listeners and q in self._listeners[job_id]:
            self._listeners[job_id].remove(q)
            if not self._listeners[job_id]:
                del self._listeners[job_id]

    def _broadcast(self, job_id: int, event_type: str, data: dict) -> None:
        payload = {"event": event_type, "data": data}
        if job_id in self._listeners:
            for q in list(self._listeners[job_id]):
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass

    def notify_progress(self, user_id: int, job_id: int, processed_pages: int, total_pages: int) -> None:
        logger.debug(f"[Job {job_id}] Progress: {processed_pages}/{total_pages} (User {user_id})")
        self._broadcast(job_id, "progress", {
            "job_id": job_id,
            "user_id": user_id,
            "processed_pages": processed_pages,
            "total_pages": total_pages,
            "status": "processing",
        })

    def notify_api_switch(
        self,
        user_id: int,
        job_id: int,
        old_label: str,
        new_label: str,
        reason: str,
        page: int,
    ) -> None:
        logger.info(f"[Job {job_id}] API switched from '{old_label}' to '{new_label}' at page {page} (reason: {reason})")
        self._broadcast(job_id, "api_switch", {
            "job_id": job_id,
            "user_id": user_id,
            "from_api": old_label,
            "to_api": new_label,
            "reason": reason,
            "page": page,
        })

    def notify_job_completed(self, user_id: int, job_id: int, output_handle_uri: str) -> None:
        logger.info(f"[Job {job_id}] Completed successfully. Artifact: {output_handle_uri}")
        self._broadcast(job_id, "completed", {
            "job_id": job_id,
            "user_id": user_id,
            "status": "done",
            "artifact_uri": output_handle_uri,
        })

    def notify_job_failed(self, user_id: int, job_id: int, error_message: str) -> None:
        logger.error(f"[Job {job_id}] Failed: {error_message}")
        self._broadcast(job_id, "failed", {
            "job_id": job_id,
            "user_id": user_id,
            "status": "failed",
            "error_message": error_message,
        })

# ============================================================
#  interfaces/desktop/actions/region_actions.py
#  Visual Region Action Descriptors and Presentation Provider
# ============================================================

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RegionActionDescriptor:
    """
    Immutable value-style descriptor for a visual region context action.
    Pure Python presentation model with zero dependencies on Qt, SQLite, or DB.
    """
    action_id: str
    label: str
    group: str
    order: int
    is_enabled: bool = True
    disabled_reason: str = ""
    is_visible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """QML QVariantMap friendly conversion."""
        return {
            "action_id": self.action_id,
            "label": self.label,
            "group": self.group,
            "order": self.order,
            "is_enabled": self.is_enabled,
            "disabled_reason": self.disabled_reason,
            "is_visible": self.is_visible,
        }


class RegionActionProvider:
    """
    Presentation-scoped action builder for visual region context actions.
    Deterministically builds the 9 canonical region actions with strict
    separation between visibility and enablement policies.
    """

    @staticmethod
    def build_actions(
        job_id: int,
        region: Dict[str, Any],
        can_insert_markdown: bool = False,
        is_duplicated_in_markdown: bool = False,
    ) -> List[RegionActionDescriptor]:
        """
        Generates the canonical 9-action inventory for a target visual region.

        Args:
            job_id: Target job identifier. Must be positive.
            region: Dictionary representing the visual region.
            can_insert_markdown: Whether markdown editor is available and matching current job.
            is_duplicated_in_markdown: Whether a canonical token for this region is already in markdown.

        Returns:
            Deterministic ordered list of RegionActionDescriptors.
        """
        if job_id <= 0 or not region:
            return []

        origin = str(region.get("origin") or "")
        review_status = str(region.get("review_status") or "").lower()
        sync_status = str(region.get("sync_status") or "").lower()
        artifact_uri = region.get("active_artifact_uri")
        has_artifact = bool(artifact_uri and str(artifact_uri).strip())
        is_modified = bool(region.get("is_modified", False))

        is_synced = sync_status in ("synced", "synchronized")
        is_sync_failed = sync_status == "sync_failed"
        is_ai_detected = origin == "ai_detected"
        is_unreviewed = review_status == "unreviewed"

        # 1. insert_markdown (markdown, 10)
        insert_enabled = True
        insert_disabled_reason = ""
        if not is_synced:
            insert_enabled = False
            insert_disabled_reason = "Region is not synchronized"
        elif not has_artifact:
            insert_enabled = False
            insert_disabled_reason = "Artifact image does not exist"
        elif not can_insert_markdown:
            insert_enabled = False
            insert_disabled_reason = "Markdown editor is not available for this job"
        elif is_duplicated_in_markdown:
            insert_enabled = False
            insert_disabled_reason = "Token already exists in Markdown editor"

        act_insert_markdown = RegionActionDescriptor(
            action_id="insert_markdown",
            label="Insert into Markdown",
            group="markdown",
            order=10,
            is_enabled=insert_enabled,
            disabled_reason=insert_disabled_reason,
            is_visible=True,
        )

        # 2. copy_token (markdown, 20)
        token_enabled = True
        token_disabled_reason = ""
        if not is_synced:
            token_enabled = False
            token_disabled_reason = "Region is not synchronized"
        elif not has_artifact:
            token_enabled = False
            token_disabled_reason = "Artifact image does not exist"

        act_copy_token = RegionActionDescriptor(
            action_id="copy_token",
            label="Copy Markdown Token",
            group="markdown",
            order=20,
            is_enabled=token_enabled,
            disabled_reason=token_disabled_reason,
            is_visible=True,
        )

        # 3. copy_path (clipboard, 30)
        path_enabled = True
        path_disabled_reason = ""
        if not is_synced:
            path_enabled = False
            path_disabled_reason = "Region is not synchronized"
        elif not has_artifact:
            path_enabled = False
            path_disabled_reason = "Artifact image does not exist"

        act_copy_path = RegionActionDescriptor(
            action_id="copy_path",
            label="Copy Image Path",
            group="clipboard",
            order=30,
            is_enabled=path_enabled,
            disabled_reason=path_disabled_reason,
            is_visible=True,
        )

        # 4. copy_bbox (clipboard, 40)
        act_copy_bbox = RegionActionDescriptor(
            action_id="copy_bbox",
            label="Copy Bounding Box",
            group="clipboard",
            order=40,
            is_enabled=True,
            disabled_reason="",
            is_visible=True,
        )

        # 5. copy_id (clipboard, 50)
        act_copy_id = RegionActionDescriptor(
            action_id="copy_id",
            label="Copy Region ID",
            group="clipboard",
            order=50,
            is_enabled=True,
            disabled_reason="",
            is_visible=True,
        )

        # 6. accept_region (review, 60)
        accept_visible = is_ai_detected
        accept_enabled = False
        accept_disabled_reason = ""
        if accept_visible:
            if is_unreviewed:
                accept_enabled = True
            else:
                accept_enabled = False
                if review_status == "accepted":
                    accept_disabled_reason = "Region has already been accepted"
                else:
                    accept_disabled_reason = f"Region is already {review_status}"

        act_accept_region = RegionActionDescriptor(
            action_id="accept_region",
            label="Accept Region",
            group="review",
            order=60,
            is_enabled=accept_enabled,
            disabled_reason=accept_disabled_reason,
            is_visible=accept_visible,
        )

        # 7. reset_to_ai (review, 70)
        reset_visible = is_ai_detected
        reset_enabled = False
        reset_disabled_reason = ""
        if reset_visible:
            if is_modified:
                reset_enabled = True
            else:
                reset_enabled = False
                reset_disabled_reason = "Region geometry has not been modified"

        act_reset_to_ai = RegionActionDescriptor(
            action_id="reset_to_ai",
            label="Reset to AI",
            group="review",
            order=70,
            is_enabled=reset_enabled,
            disabled_reason=reset_disabled_reason,
            is_visible=reset_visible,
        )

        # 8. delete_region (review, 80)
        act_delete_region = RegionActionDescriptor(
            action_id="delete_region",
            label="Delete Region",
            group="review",
            order=80,
            is_enabled=True,
            disabled_reason="",
            is_visible=True,
        )

        # 9. retry_sync (diagnostics, 90)
        retry_visible = is_sync_failed
        act_retry_sync = RegionActionDescriptor(
            action_id="retry_sync",
            label="Retry Sync",
            group="diagnostics",
            order=90,
            is_enabled=retry_visible,
            disabled_reason="",
            is_visible=retry_visible,
        )

        return [
            act_insert_markdown,
            act_copy_token,
            act_copy_path,
            act_copy_bbox,
            act_copy_id,
            act_accept_region,
            act_reset_to_ai,
            act_delete_region,
            act_retry_sync,
        ]

# ============================================================
#  tests/unit/test_region_actions.py
#  Unit Tests for Visual Region Action Descriptor & Provider (TICK-P04A)
# ============================================================

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from interfaces.desktop.actions.region_actions import (
    RegionActionDescriptor,
    RegionActionProvider,
)


def test_action_descriptor_frozen_value_semantics():
    """Verifies that RegionActionDescriptor is immutable, value-typed, and serializes cleanly."""
    desc = RegionActionDescriptor(
        action_id="test_act",
        label="Test Action",
        group="markdown",
        order=10,
        is_enabled=True,
        disabled_reason="",
        is_visible=True,
    )

    with pytest.raises((FrozenInstanceError, AttributeError)):
        desc.is_enabled = False  # type: ignore

    d = desc.to_dict()
    assert d == {
        "action_id": "test_act",
        "label": "Test Action",
        "group": "markdown",
        "order": 10,
        "is_enabled": True,
        "disabled_reason": "",
        "is_visible": True,
    }


def test_provider_deterministic_ordering_and_inventory():
    """Verifies exact 9-action canonical inventory, deterministic ordering, and groups."""
    region = {
        "region_id": "01234567-89ab-4cde-8f01-23456789abcd",
        "job_id": 42,
        "origin": "ai_detected",
        "review_status": "unreviewed",
        "sync_status": "synced",
        "active_artifact_uri": "artifacts/crop_42.png",
        "is_modified": False,
    }

    actions = RegionActionProvider.build_actions(
        job_id=42,
        region=region,
        can_insert_markdown=True,
        is_duplicated_in_markdown=False,
    )

    assert len(actions) == 9

    expected = [
        ("insert_markdown", "markdown", 10),
        ("copy_token", "markdown", 20),
        ("copy_path", "clipboard", 30),
        ("copy_bbox", "clipboard", 40),
        ("copy_id", "clipboard", 50),
        ("accept_region", "review", 60),
        ("reset_to_ai", "review", 70),
        ("delete_region", "review", 80),
        ("retry_sync", "diagnostics", 90),
    ]

    for act, (expected_id, expected_group, expected_order) in zip(actions, expected):
        assert act.action_id == expected_id
        assert act.group == expected_group
        assert act.order == expected_order


def test_provider_invalid_job_or_empty_region():
    """Verifies that empty region or non-positive job_id deterministically yields empty action list."""
    valid_region = {"region_id": "abc", "origin": "ai_detected"}

    assert RegionActionProvider.build_actions(job_id=0, region=valid_region) == []
    assert RegionActionProvider.build_actions(job_id=-1, region=valid_region) == []
    assert RegionActionProvider.build_actions(job_id=1, region={}) == []


def test_provider_review_actions_ai_detected_states():
    """Verifies visibility and enablement for accept_region and reset_to_ai on ai_detected regions."""
    # 1. Unreviewed & Unmodified
    r1 = {
        "region_id": "r1",
        "origin": "ai_detected",
        "review_status": "unreviewed",
        "is_modified": False,
        "sync_status": "synced",
        "active_artifact_uri": "crop.png",
    }
    acts1 = {a.action_id: a for a in RegionActionProvider.build_actions(1, r1)}
    assert acts1["accept_region"].is_visible is True
    assert acts1["accept_region"].is_enabled is True
    assert acts1["accept_region"].disabled_reason == ""

    assert acts1["reset_to_ai"].is_visible is True
    assert acts1["reset_to_ai"].is_enabled is False
    assert "not been modified" in acts1["reset_to_ai"].disabled_reason

    # 2. Accepted & Modified
    r2 = {
        "region_id": "r2",
        "origin": "ai_detected",
        "review_status": "accepted",
        "is_modified": True,
        "sync_status": "synced",
        "active_artifact_uri": "crop.png",
    }
    acts2 = {a.action_id: a for a in RegionActionProvider.build_actions(1, r2)}
    assert acts2["accept_region"].is_visible is True
    assert acts2["accept_region"].is_enabled is False
    assert "already been accepted" in acts2["accept_region"].disabled_reason

    assert acts2["reset_to_ai"].is_visible is True
    assert acts2["reset_to_ai"].is_enabled is True
    assert acts2["reset_to_ai"].disabled_reason == ""


def test_provider_manual_region_hides_ai_review_actions():
    """Verifies that manual regions hide accept_region and reset_to_ai entirely."""
    manual_reg = {
        "region_id": "man1",
        "origin": "user_manual",
        "review_status": "unreviewed",
        "is_modified": True,
        "sync_status": "synced",
        "active_artifact_uri": "crop.png",
    }
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, manual_reg)}
    assert acts["accept_region"].is_visible is False
    assert acts["accept_region"].is_enabled is False
    assert acts["reset_to_ai"].is_visible is False
    assert acts["reset_to_ai"].is_enabled is False
    assert acts["delete_region"].is_visible is True
    assert acts["delete_region"].is_enabled is True


def test_provider_diagnostics_retry_sync_visibility():
    """Verifies that retry_sync is visible ONLY when sync_status == 'sync_failed'."""
    failed_reg = {
        "region_id": "f1",
        "origin": "ai_detected",
        "sync_status": "sync_failed",
    }
    acts_failed = {a.action_id: a for a in RegionActionProvider.build_actions(1, failed_reg)}
    assert acts_failed["retry_sync"].is_visible is True
    assert acts_failed["retry_sync"].is_enabled is True

    synced_reg = {
        "region_id": "s1",
        "origin": "ai_detected",
        "sync_status": "synced",
    }
    acts_synced = {a.action_id: a for a in RegionActionProvider.build_actions(1, synced_reg)}
    assert acts_synced["retry_sync"].is_visible is False
    assert acts_synced["retry_sync"].is_enabled is False

    pending_reg = {
        "region_id": "p1",
        "origin": "ai_detected",
        "sync_status": "pending",
    }
    acts_pending = {a.action_id: a for a in RegionActionProvider.build_actions(1, pending_reg)}
    assert acts_pending["retry_sync"].is_visible is False


def test_provider_markdown_insertion_enablement_matrix():
    """Verifies all combinations of sync_status, artifact existence, editor match, and duplicates."""
    base_reg = {
        "region_id": "reg1",
        "origin": "ai_detected",
        "sync_status": "synced",
        "active_artifact_uri": "crops/crop.png",
    }

    # Happy path
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, base_reg, can_insert_markdown=True, is_duplicated_in_markdown=False)}
    assert acts["insert_markdown"].is_visible is True
    assert acts["insert_markdown"].is_enabled is True
    assert acts["insert_markdown"].disabled_reason == ""

    # Unsynced
    unsynced_reg = dict(base_reg, sync_status="dirty")
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, unsynced_reg, can_insert_markdown=True, is_duplicated_in_markdown=False)}
    assert acts["insert_markdown"].is_enabled is False
    assert "not synchronized" in acts["insert_markdown"].disabled_reason

    # Missing artifact
    missing_art_reg = dict(base_reg, active_artifact_uri=None)
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, missing_art_reg, can_insert_markdown=True, is_duplicated_in_markdown=False)}
    assert acts["insert_markdown"].is_enabled is False
    assert "Artifact image does not exist" in acts["insert_markdown"].disabled_reason

    # Editor unavailable
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, base_reg, can_insert_markdown=False, is_duplicated_in_markdown=False)}
    assert acts["insert_markdown"].is_enabled is False
    assert "Markdown editor is not available" in acts["insert_markdown"].disabled_reason

    # Duplicated in buffer
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, base_reg, can_insert_markdown=True, is_duplicated_in_markdown=True)}
    assert acts["insert_markdown"].is_enabled is False
    assert "already exists in Markdown editor" in acts["insert_markdown"].disabled_reason


def test_provider_clipboard_actions_enablement():
    """Verifies enablement rules for copy_token, copy_path, copy_bbox, and copy_id."""
    synced_reg = {
        "region_id": "r1",
        "origin": "ai_detected",
        "sync_status": "synced",
        "active_artifact_uri": "crops/crop1.png",
    }
    acts = {a.action_id: a for a in RegionActionProvider.build_actions(1, synced_reg)}
    assert acts["copy_token"].is_enabled is True
    assert acts["copy_path"].is_enabled is True
    assert acts["copy_bbox"].is_enabled is True
    assert acts["copy_id"].is_enabled is True

    unsynced_reg = {
        "region_id": "r2",
        "origin": "ai_detected",
        "sync_status": "pending",
        "active_artifact_uri": "",
    }
    acts_unsynced = {a.action_id: a for a in RegionActionProvider.build_actions(1, unsynced_reg)}
    assert acts_unsynced["copy_token"].is_enabled is False
    assert acts_unsynced["copy_path"].is_enabled is False
    assert acts_unsynced["copy_bbox"].is_enabled is True
    assert acts_unsynced["copy_id"].is_enabled is True


def test_architecture_action_model_zero_qt_zero_persistence():
    """AST invariant test verifying zero Qt and zero persistence imports in actions/."""
    actions_dir = Path(__file__).parent.parent.parent / "interfaces" / "desktop" / "actions"
    forbidden_qt = {"PySide6", "PyQt6", "PyQt5", "PySide2", "interfaces.desktop.qt_compat"}
    forbidden_db = {"sqlite3", "sqlalchemy", "pymysql", "keyring"}

    for fpath in actions_dir.glob("*.py"):
        tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    for forbidden in forbidden_qt | forbidden_db:
                        assert name.name != forbidden and not name.name.startswith(forbidden + "."), (
                            f"Forbidden import '{name.name}' in {fpath}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in forbidden_qt | forbidden_db:
                    assert mod != forbidden and not mod.startswith(forbidden + "."), (
                        f"Forbidden from-import '{mod}' in {fpath}"
                    )

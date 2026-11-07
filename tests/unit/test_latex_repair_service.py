# ============================================================
#  tests/unit/test_latex_repair_service.py
#  Unit Tests for LaTeXRepairService & Repair Contracts (LFR-02)
# ============================================================

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from application.dto.latex_repair_dto import LaTeXRepairProposal, LaTeXRepairRequest
from application.ports.ai_executor import IAIExecutionService
from application.ports.diagram_renderer import (
    DiagramRenderError,
    DiagramRenderRequest,
    DiagramRenderResult,
    DiagramSyntaxError,
    IDiagramRenderer,
)
from application.ports.math_renderer import (
    IMathRenderer,
    MathRenderError,
    MathRenderRequest,
    MathRenderResult,
    MathSyntaxError,
)
from application.ports.unit_of_work import IUnitOfWorkFactory
from application.services.latex_repair_service import LaTeXRepairService
from core.entities.api_slot import ApiSlot, CredentialRef


class MockUow:
    def __init__(self, slots: list[ApiSlot]):
        self.apis = MagicMock()
        self.apis.list_all.return_value = slots

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def make_dummy_slot(label: str = "TestSlot") -> ApiSlot:
    return ApiSlot(
        id=1,
        provider="google",
        label=label,
        slot_type="byok",
        selected_model="gemini-3.5-flash-lite",
    )


def test_parse_and_validate_ai_response_valid_json():
    service = LaTeXRepairService(MagicMock(), MagicMock())
    raw = json.dumps({
        "schema_version": 1,
        "repaired_source": "\\draw (0,0) -- (2,2);",
        "change_summary": "Added missing semicolon at end of path",
        "preserved_invariants": ["coordinates", "variables"],
    })

    data, err = service.parse_and_validate_ai_response(raw, "\\draw (0,0) -- (2,2)")
    assert err is None
    assert data is not None
    assert data["repaired_source"] == "\\draw (0,0) -- (2,2);"
    assert data["change_summary"] == "Added missing semicolon at end of path"
    assert "coordinates" in data["preserved_invariants"]


def test_parse_and_validate_ai_response_strips_markdown_code_fences():
    service = LaTeXRepairService(MagicMock(), MagicMock())
    payload = {
        "schema_version": 1,
        "repaired_source": "E = m c^2",
        "change_summary": "Removed stray backslash",
        "preserved_invariants": ["variables"],
    }
    raw = f"Here is the repair:\n```json\n{json.dumps(payload)}\n```\nEnjoy!"

    data, err = service.parse_and_validate_ai_response(raw, "E = \\m c^2")
    assert err is None
    assert data is not None
    assert data["repaired_source"] == "E = m c^2"


def test_parse_and_validate_ai_response_rejects_missing_fields():
    service = LaTeXRepairService(MagicMock(), MagicMock())
    raw = json.dumps({"schema_version": 1, "change_summary": "No repaired source"})

    data, err = service.parse_and_validate_ai_response(raw, "\\bad")
    assert data is None
    assert "repaired_source" in err


def test_parse_and_validate_ai_response_rejects_forbidden_wrappers():
    service = LaTeXRepairService(MagicMock(), MagicMock())
    forbidden_payloads = [
        "\\documentclass{standalone}\n\\begin{document}\\end{document}",
        "\\usepackage{tikz}\n\\draw (0,0);",
        "\\write18{rm -rf /}",
        "\\immediate\\write18{ls}",
        "\\input|ls",
    ]

    for candidate in forbidden_payloads:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": candidate,
            "change_summary": "Unsafe attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, "\\draw (0,0);")
        assert data is None, f"Should have rejected candidate: {candidate}"
        assert "forbidden" in err


def test_parse_and_validate_ai_response_rejects_full_document_replacement():
    service = LaTeXRepairService(MagicMock(), MagicMock())
    raw = json.dumps({
        "schema_version": 1,
        "repaired_source": "# Whole Document Title\n\nParagraph 1\n\n$$\\alpha$$",
        "change_summary": "Replaced everything",
        "preserved_invariants": [],
    })
    data, err = service.parse_and_validate_ai_response(raw, "\\begin{tikzpicture}\\end{tikzpicture}")
    assert data is None
    assert "full document replacement" in err


def test_repair_latex_no_active_slots():
    uow_factory = MagicMock()
    uow_factory.create.return_value = MockUow([])
    ai_exec = MagicMock()

    service = LaTeXRepairService(uow_factory, ai_exec)
    req = LaTeXRepairRequest(
        job_id=1,
        canonical_version=1,
        node_id="diagram_1",
        original_source="\\draw (0,0);",
        renderer_type="diagram",
        sanitized_diagnostics="Syntax error",
    )

    prop = service.repair_latex(req)
    assert not prop.validation_passed
    assert "No active AI provider slot configured" in prop.validation_error


def test_repair_latex_successful_diagram_repair():
    uow_factory = MagicMock()
    uow_factory.create.return_value = MockUow([make_dummy_slot()])
    ai_exec = MagicMock()
    ai_exec.execute_text_with_fallback.return_value = (
        json.dumps({
            "schema_version": 1,
            "repaired_source": "\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}",
            "change_summary": "Closed tikzpicture environment",
            "preserved_invariants": ["coordinates"],
        }),
        make_dummy_slot("GoogleGemini"),
    )

    diag_renderer = MagicMock(spec=IDiagramRenderer)
    diag_renderer.render.return_value = DiagramRenderResult(
        hash="validhash",
        svg_xml="<svg>test</svg>",
        width=100.0,
        height=100.0,
        status="rendered",
    )

    service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=ai_exec,
        diagram_renderer=diag_renderer,
    )

    req = LaTeXRepairRequest(
        job_id=1,
        canonical_version=2,
        draft_revision=3,
        node_id="diagram_node_1",
        original_source="\\begin{tikzpicture}\\draw (0,0) -- (1,1);",
        renderer_type="diagram",
        sanitized_diagnostics="! LaTeX Error: \\begin{tikzpicture} on input line 5 ended by \\end{document}.",
    )

    prop = service.repair_latex(req)
    assert prop.validation_passed
    assert prop.validation_error is None
    assert prop.repaired_source == "\\begin{tikzpicture}\\draw (0,0) -- (1,1);\\end{tikzpicture}"
    assert prop.rendered_preview_xml == "<svg>test</svg>"
    assert prop.rendered_preview_width == 100.0
    assert prop.rendered_preview_height == 100.0
    assert prop.job_id == 1
    assert prop.canonical_version == 2
    assert prop.draft_revision == 3
    assert prop.provider_label == "GoogleGemini"
    assert prop.model_name == "gemini-3.5-flash-lite"
    assert prop.retry_count == 0


def test_repair_latex_successful_math_repair():
    uow_factory = MagicMock()
    uow_factory.create.return_value = MockUow([make_dummy_slot()])
    ai_exec = MagicMock()
    ai_exec.execute_text_with_fallback.return_value = (
        json.dumps({
            "schema_version": 1,
            "repaired_source": "\\frac{a}{b}",
            "change_summary": "Fixed unbalanced brace in fraction",
            "preserved_invariants": ["variables"],
        }),
        make_dummy_slot(),
    )

    math_renderer = MagicMock(spec=IMathRenderer)
    math_renderer.render.return_value = MathRenderResult(
        hash="mathhash",
        svg_xml="<svg>math</svg>",
        width="2ex",
        height="1ex",
        vertical_align="0ex",
    )

    service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=ai_exec,
        math_renderer=math_renderer,
    )

    req = LaTeXRepairRequest(
        job_id=2,
        canonical_version=1,
        node_id="math_1",
        original_source="\\frac{a}{b",
        renderer_type="math_display",
        sanitized_diagnostics="Math syntax error: Missing close brace",
    )

    prop = service.repair_latex(req)
    assert prop.validation_passed
    assert prop.repaired_source == "\\frac{a}{b}"
    assert prop.rendered_preview_xml == "<svg>math</svg>"


def test_repair_latex_compiler_rejection_triggers_bounded_retry():
    uow_factory = MagicMock()
    uow_factory.create.return_value = MockUow([make_dummy_slot()])
    ai_exec = MagicMock()

    attempt_1_reply = json.dumps({
        "schema_version": 1,
        "repaired_source": "\\draw (0,0) -> (1,1);",  # Still invalid (TikZ uses -- not ->)
        "change_summary": "Changed to arrow",
        "preserved_invariants": [],
    })
    attempt_2_reply = json.dumps({
        "schema_version": 1,
        "repaired_source": "\\draw [->] (0,0) -- (1,1);",  # Valid TikZ syntax
        "change_summary": "Used proper [->] option with -- path",
        "preserved_invariants": ["coordinates"],
    })
    ai_exec.execute_text_with_fallback.side_effect = [
        (attempt_1_reply, make_dummy_slot()),
        (attempt_2_reply, make_dummy_slot()),
    ]

    diag_renderer = MagicMock(spec=IDiagramRenderer)
    diag_renderer.render.side_effect = [
        DiagramSyntaxError("syntax", "Package tikz Error: Cannot parse -> path."),
        DiagramRenderResult("hash2", "<svg>arrow</svg>", 50.0, 50.0, "rendered"),
    ]

    service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=ai_exec,
        diagram_renderer=diag_renderer,
    )

    req = LaTeXRepairRequest(
        job_id=3,
        canonical_version=1,
        node_id="diagram_retry",
        original_source="\\draw (0,0) -> (1,1)",
        renderer_type="diagram",
        sanitized_diagnostics="Cannot parse path",
    )

    prop = service.repair_latex(req, max_retries=1)
    assert prop.validation_passed
    assert prop.repaired_source == "\\draw [->] (0,0) -- (1,1);"
    assert prop.retry_count == 1
    assert ai_exec.execute_text_with_fallback.call_count == 2
    # Check that follow-up prompt contained previous compilation failure
    second_call_prompt = ai_exec.execute_text_with_fallback.call_args_list[1].kwargs["prompt"]
    assert "PREVIOUS ATTEMPT COMPILATION FAILURE" in second_call_prompt


def test_repair_latex_compiler_rejection_exhausts_retries():
    uow_factory = MagicMock()
    uow_factory.create.return_value = MockUow([make_dummy_slot()])
    ai_exec = MagicMock()
    bad_reply = json.dumps({
        "schema_version": 1,
        "repaired_source": "\\badcmd",
        "change_summary": "Bad cmd",
        "preserved_invariants": [],
    })
    ai_exec.execute_text_with_fallback.return_value = (bad_reply, make_dummy_slot())

    diag_renderer = MagicMock(spec=IDiagramRenderer)
    diag_renderer.render.side_effect = DiagramSyntaxError("syntax", "Undefined control sequence \\badcmd")

    service = LaTeXRepairService(
        uow_factory=uow_factory,
        ai_executor=ai_exec,
        diagram_renderer=diag_renderer,
    )

    req = LaTeXRepairRequest(
        job_id=4,
        canonical_version=1,
        node_id="diagram_fail",
        original_source="\\badcmd",
        renderer_type="diagram",
        sanitized_diagnostics="Undefined control sequence",
    )

    prop = service.repair_latex(req, max_retries=1)
    assert not prop.validation_passed
    assert "Undefined control sequence \\badcmd" in prop.validation_error
    assert ai_exec.execute_text_with_fallback.call_count == 2  # Attempt 0 and Retry 1

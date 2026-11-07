# ============================================================
#  tests/acceptance/test_rendering_acceptance.py
#  Real-World Rendering Acceptance Tests (Ubuntu 24 Baseline)
# ============================================================

from __future__ import annotations

import os
import pytest

from scripts.acceptance.run_rendering_acceptance import (
    TestStatus,
    run_desktop_preview,
    run_diagram_render_live,
    run_gemini_live,
    run_mathjax_live,
    run_quarto_pdf_live,
    run_runtime_preflight,
)


@pytest.mark.integration
def test_runtime_preflight_acceptance():
    """Probes host toolchain, compilers, fonts, and runtimes without mocks."""
    res = run_runtime_preflight()
    assert res.status == TestStatus.PASS, f"Preflight failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.passed_tests == res.total_tests


@pytest.mark.integration
def test_mathjax_headless_acceptance():
    """Verifies real headless MathJax daemon worker, client, and SVG cache."""
    res = run_mathjax_live()
    assert res.status == TestStatus.PASS, f"MathJax live failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_diagram_standalone_tex_acceptance():
    """Verifies real StandaloneTeXDiagramRenderer on TikZ, PGFPlots, CircuitTikZ, and TikZ-CD."""
    res = run_diagram_render_live()
    assert res.status == TestStatus.PASS, f"Diagram live failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_quarto_pdf_export_acceptance():
    """Verifies real Quarto CLI and XeLaTeX full-document PDF export with Persian RTL and vector diagrams."""
    res = run_quarto_pdf_live()
    assert res.status == TestStatus.PASS, f"Quarto PDF export failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_desktop_preview_acceptance():
    """Verifies real native Qt/QML image providers for math and diagram SVGs across themes."""
    res = run_desktop_preview()
    assert res.status == TestStatus.PASS, f"Desktop preview failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_gemini_live_acceptance_opt_in_gate():
    """Verifies that live AI tests are safely skipped by default unless explicitly opted in."""
    opt_in = os.environ.get("POLPO_ACCEPTANCE_LIVE_AI", "").lower() in ("1", "true", "yes")
    res = run_gemini_live(opt_in=opt_in)
    if not opt_in:
        assert res.status == TestStatus.SKIPPED_WITH_REASON
        assert res.skipped_tests == 1
    else:
        # In test runners where conftest installs an in-memory keyring, the status
        # is BLOCKED due to no keys in the test keyring; on live CLI it is PASS.
        assert res.status in (TestStatus.PASS, TestStatus.BLOCKED)
        assert res.failed_tests == 0

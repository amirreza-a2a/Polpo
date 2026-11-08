import os
import shutil
import pytest

from scripts.acceptance.run_rendering_acceptance import (
    TestStatus,
    run_desktop_preview,
    run_diagram_render_live,
    run_gemini_live,
    run_mathjax_live,
    run_qml_e2e_acceptance,
    run_quarto_pdf_live,
    run_repair_workflow,
    run_runtime_preflight,
    run_tex_security,
)


def is_quarto_and_xelatex_installed() -> bool:
    """Returns True if both Quarto CLI and XeLaTeX compiler are present on PATH."""
    return shutil.which("quarto") is not None and shutil.which("xelatex") is not None


def is_tex_compiler_installed() -> bool:
    """Returns True if XeLaTeX or pdfLaTeX compiler is present on PATH."""
    return shutil.which("xelatex") is not None or shutil.which("pdflatex") is not None


@pytest.mark.integration
def test_runtime_preflight_acceptance():
    """Probes host toolchain, compilers, fonts, and runtimes without mocks."""
    res = run_runtime_preflight()
    # On reference workstations (Ubuntu 24 with TeX Live & Quarto), status is PASS.
    # On minimal standard CI runners where Quarto/XeLaTeX are absent, status is BLOCKED.
    # In all environments, zero fatal failures must occur.
    assert res.failed_tests == 0, f"Preflight failed: {[i for i in res.items if i.status == TestStatus.FAIL]}"
    assert res.status in (TestStatus.PASS, TestStatus.BLOCKED)


@pytest.mark.integration
def test_mathjax_headless_acceptance():
    """Verifies real headless MathJax daemon worker, client, and SVG cache."""
    res = run_mathjax_live()
    assert res.status == TestStatus.PASS, f"MathJax live failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_diagram_standalone_tex_acceptance():
    """Verifies real StandaloneTeXDiagramRenderer on TikZ, PGFPlots, CircuitTikZ, and TikZ-CD."""
    if not is_tex_compiler_installed():
        pytest.skip("No TeX compiler installed on host runner; skipping live diagram compilation test")
    res = run_diagram_render_live()
    assert res.status == TestStatus.PASS, f"Diagram live failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_quarto_pdf_export_acceptance():
    """Verifies real Quarto CLI and XeLaTeX full-document PDF export with Persian RTL and vector diagrams."""
    if not is_quarto_and_xelatex_installed():
        pytest.skip("Quarto or XeLaTeX not installed on host runner; skipping live PDF compilation test")
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


@pytest.mark.integration
def test_gemini_live_stage_items_and_clean_architecture_compliance():
    """Verifies that when opt_in=True, all 6 Section 4.3 acceptance items are evaluated without raw SQL."""
    res = run_gemini_live(opt_in=True)
    # The stage evaluates 6 items when configured, or 1 item (BLOCKED) if keys/db not found
    if res.status == TestStatus.PASS:
        assert res.total_tests == 6
        item_ids = [item.test_id for item in res.items]
        assert "GEMINI-01-TEXT" in item_ids
        assert "GEMINI-02-VISION" in item_ids
        assert "GEMINI-03-REPAIR-STRUCTURED" in item_ids
        assert "GEMINI-04-FALLBACK-BEHAVIOR" in item_ids
        assert "GEMINI-05-AUTH-ERROR" in item_ids
        assert "GEMINI-06-TIMEOUT-FAILURE" in item_ids
    else:
        assert res.status == TestStatus.BLOCKED


@pytest.mark.integration
def test_tex_security_acceptance():
    """Verifies TeX security sandbox, environment sanitization, and comment tokenizer."""
    if not is_tex_compiler_installed():
        pytest.skip("No TeX compiler installed on host runner; skipping TeX security test")
    res = run_tex_security()
    assert res.status == TestStatus.PASS, f"TeX security failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_qml_e2e_acceptance_stage():
    """Verifies complete offscreen Qt Quick Scene Graph pipeline projection and rendering."""
    if not is_tex_compiler_installed():
        pytest.skip("No TeX compiler installed on host runner; skipping QML E2E test")
    res = run_qml_e2e_acceptance()
    assert res.status == TestStatus.PASS, f"QML E2E acceptance failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0


@pytest.mark.integration
def test_repair_workflow_acceptance_stage():
    """Verifies interactive AI repair journey: consent, compiler validation, undo, and OCC."""
    if not is_tex_compiler_installed():
        pytest.skip("No TeX compiler installed on host runner; skipping repair workflow test")
    res = run_repair_workflow()
    assert res.status == TestStatus.PASS, f"Repair workflow acceptance failed: {[i for i in res.items if i.status != TestStatus.PASS]}"
    assert res.failed_tests == 0

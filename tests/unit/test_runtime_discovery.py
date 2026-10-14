# ============================================================
#  tests/unit/test_runtime_discovery.py
#  Unit tests for Quarto and TeX Runtime Discovery & Diagnostics
#  Phase 13 — Ticket #98
# ============================================================

from unittest.mock import MagicMock, patch

from application.dto.pdf_readiness_dto import EngineReadinessDTO
from application.ports.pdf_export_port import IPdfRuntimeDiscovery
from infrastructure.pdf.runtime_discovery import QuartoRuntimeDiscovery


def test_port_and_dto_contracts():
    """Verify DTO properties and port abstract base class."""
    dto_ready = EngineReadinessDTO(quarto_available=True, xelatex_available=True)
    assert dto_ready.is_export_ready is True

    dto_not_ready = EngineReadinessDTO(quarto_available=True, xelatex_available=False)
    assert dto_not_ready.is_export_ready is False

    assert issubclass(QuartoRuntimeDiscovery, IPdfRuntimeDiscovery)


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_quarto_found_on_path(mock_run, mock_which):
    """Mocks successful Quarto 1.9.38 detection on system PATH."""
    def which_side_effect(binary):
        if binary in ("quarto", "quarto.cmd", "quarto.exe"):
            return "/usr/local/bin/quarto"
        if binary in ("xelatex", "xelatex.exe"):
            return "/usr/bin/xelatex"
        return None

    mock_which.side_effect = which_side_effect

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.9.38\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness()

    assert dto.quarto_available is True
    assert dto.quarto_version == "1.9.38"
    assert dto.quarto_path == "/usr/local/bin/quarto"
    assert dto.xelatex_available is True
    assert dto.is_export_ready is True
    assert len(dto.missing_prerequisites) == 0


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_user_configured_path_precedence(mock_run, mock_which):
    """Verifies user-configured path overrides system PATH."""
    mock_which.return_value = "/system/bin/quarto"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.5.0\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness(configured_quarto_path="/custom/path/quarto")

    assert dto.quarto_available is True
    assert dto.quarto_path == "/custom/path/quarto"
    # mock_run should have been invoked with custom path
    called_cmd = mock_run.call_args_list[0][0][0]
    assert called_cmd[0] == "/custom/path/quarto"


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
def test_discovery_quarto_absent(mock_which):
    """When Quarto is absent, returns graceful DTO with platform guidance."""
    mock_which.return_value = None

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness()

    assert dto.quarto_available is False
    assert dto.is_export_ready is False
    assert "quarto" in dto.missing_prerequisites
    assert len(dto.diagnostic_instructions) > 0


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_quarto_version_too_low(mock_run, mock_which):
    """Rejects Quarto version below 1.4.0 with diagnostic warning."""
    mock_which.return_value = "/usr/bin/quarto"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.3.450\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness()

    assert dto.quarto_available is False
    assert dto.quarto_version == "1.3.450"
    assert "quarto (version >= 1.4.0 required)" in dto.missing_prerequisites
    assert "1.4.0" in dto.diagnostic_instructions


@patch("infrastructure.pdf.runtime_discovery.platform.system")
@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_windows_shim_resolution(mock_run, mock_which, mock_system):
    """Mocks Windows platform, verifies .cmd resolution and CREATE_NO_WINDOW flag."""
    mock_system.return_value = "Windows"

    def which_side_effect(binary):
        if binary in ("quarto.cmd", "quarto"):
            return "C:\\Program Files\\Quarto\\bin\\quarto.cmd"
        if binary in ("xelatex.exe", "xelatex"):
            return "C:\\Users\\User\\AppData\\Roaming\\TinyTeX\\bin\\windows\\xelatex.exe"
        return None

    mock_which.side_effect = which_side_effect

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.6.10\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness()

    assert dto.quarto_available is True
    assert dto.quarto_path == "C:\\Program Files\\Quarto\\bin\\quarto.cmd"

    # Verify subprocess called with creationflags=0x08000000 (CREATE_NO_WINDOW)
    kwargs = mock_run.call_args.kwargs
    assert kwargs.get("creationflags") == 0x08000000


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_tinytex_vs_system_tex_detection(mock_run, mock_which):
    """Verifies discrimination between TinyTeX and system TeX distributions."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.5.0\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()

    # Case 1: TinyTeX path
    mock_which.side_effect = lambda b: (
        "/usr/local/bin/quarto" if "quarto" in b else
        "/home/user/.TinyTeX/bin/x86_64-linux/xelatex" if "xelatex" in b else None
    )
    dto_tiny = discovery.probe_readiness()
    assert dto_tiny.xelatex_available is True
    assert dto_tiny.is_tinytex is True
    assert dto_tiny.tex_distribution_name == "TinyTeX"

    # Case 2: System TeX Live path
    mock_which.side_effect = lambda b: (
        "/usr/local/bin/quarto" if "quarto" in b else
        "/usr/bin/xelatex" if "xelatex" in b else None
    )
    dto_system = discovery.probe_readiness()
    assert dto_system.xelatex_available is True
    assert dto_system.is_tinytex is False
    assert "TeX Live" in dto_system.tex_distribution_name


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_user_configured_path_execution_failure(mock_run, mock_which):
    """Verifies that an unexecutable or non-existent configured path fails gracefully."""
    mock_which.return_value = None
    mock_run.side_effect = FileNotFoundError("Executable not found")

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness(configured_quarto_path="/invalid/quarto")

    assert dto.quarto_available is False
    assert "quarto" in dto.missing_prerequisites
    assert "Failed to execute Quarto" in dto.diagnostic_instructions


@patch("infrastructure.pdf.runtime_discovery.shutil.which")
@patch("infrastructure.pdf.runtime_discovery.subprocess.run")
def test_discovery_configured_path_whitespace_fallback(mock_run, mock_which):
    """Verifies empty or whitespace configured path falls back to system PATH."""
    mock_which.return_value = "/usr/bin/quarto"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.5.10\n"
    mock_run.return_value = mock_proc

    discovery = QuartoRuntimeDiscovery()
    dto = discovery.probe_readiness(configured_quarto_path="   ")

    assert dto.quarto_available is True
    assert dto.quarto_path == "/usr/bin/quarto"


def test_discovery_quarto_version_parsing_variations():
    """Verifies that 2-element semver like 1.4 or 1.4-rc1 are accepted and padded correctly."""
    from infrastructure.pdf.runtime_discovery import _parse_version_tuple
    assert _parse_version_tuple("1.4") == (1, 4, 0)
    assert _parse_version_tuple("1.4-rc1") == (1, 4, 0)
    assert _parse_version_tuple("1.4.0") == (1, 4, 0)
    assert _parse_version_tuple("1.5.2") == (1, 5, 2)
    assert _parse_version_tuple("2") == (2, 0, 0)
    assert _parse_version_tuple("1.3") == (1, 3, 0)

    # 1.4 should be >= (1, 4, 0)
    assert _parse_version_tuple("1.4") >= (1, 4, 0)
    # 1.3 should be < (1, 4, 0)
    assert _parse_version_tuple("1.3") < (1, 4, 0)

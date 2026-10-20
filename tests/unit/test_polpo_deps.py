"""Unit tests for scripts/polpo_linux_deps.sh."""

import os
from pathlib import Path
import subprocess
import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "polpo_linux_deps.sh"


def run_deps_script(args: list[str], env: dict[str, str] | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Helper to run polpo_linux_deps.sh in a controlled environment."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [str(SCRIPT_PATH)] + args,
        capture_output=True,
        text=True,
        env=run_env,
        input=input_text,
    )


def test_help_flag():
    """--help should print usage and exit 0."""
    res = run_deps_script(["--help"])
    assert res.returncode == 0
    assert "Usage: polpo-deps" in res.stdout
    assert "check" in res.stdout
    assert "install-gui" in res.stdout
    assert "pdf" in res.stdout


def test_detect_debian_family(tmp_path: Path):
    """Debian/Ubuntu os-release mapping."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\nID_LIKE=debian\n')

    # Run check pointing to this os-release
    res = run_deps_script(["check", "--os-release", str(os_rel)])
    assert "Distribution : Ubuntu 24.04 LTS" in res.stdout
    assert "Distro family: debian" in res.stdout


def test_detect_fedora_family(tmp_path: Path):
    """Fedora os-release mapping."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=fedora\nVERSION_ID="40"\nPRETTY_NAME="Fedora Linux 40 (Workstation Edition)"\n')

    res = run_deps_script(["check", "--os-release", str(os_rel)])
    assert "Distribution : Fedora Linux 40" in res.stdout
    assert "Distro family: fedora" in res.stdout
    assert "mesa-libGL" in res.stdout or "mesa-libEGL" in res.stdout


def test_detect_arch_family(tmp_path: Path):
    """Arch Linux os-release mapping."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=arch\nPRETTY_NAME="Arch Linux"\n')

    res = run_deps_script(["check", "--os-release", str(os_rel)])
    assert "Distribution : Arch Linux" in res.stdout
    assert "Distro family: arch" in res.stdout
    assert "libglvnd" in res.stdout


def test_detect_suse_family(tmp_path: Path):
    """openSUSE os-release mapping."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\nPRETTY_NAME="openSUSE Tumbleweed"\n')

    res = run_deps_script(["check", "--os-release", str(os_rel)])
    assert "Distribution : openSUSE Tumbleweed" in res.stdout
    assert "Distro family: suse" in res.stdout
    assert "libGL1" in res.stdout


def test_detect_unknown_distro_fallback(tmp_path: Path):
    """Unknown Linux distribution falls back to shared library probing."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=customos\nPRETTY_NAME="Custom In-House Linux"\n')

    res = run_deps_script(["check", "--os-release", str(os_rel)])
    assert "Distribution : Custom In-House Linux" in res.stdout
    assert "Distro family: unknown" in res.stdout
    assert "Checking required shared libraries" in res.stdout


def test_install_gui_dry_run_fedora(tmp_path: Path):
    """install-gui with --dry-run prints expected dnf command without running it."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=fedora\nPRETTY_NAME="Fedora 40"\n')

    # Mock rpm query so packages appear missing
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    rpm_mock = mock_bin / "rpm"
    rpm_mock.write_text("#!/bin/sh\nexit 1\n")
    rpm_mock.chmod(0o755)

    fake_env = {"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"}
    res = run_deps_script(["install-gui", "--dry-run", "--yes", "--os-release", str(os_rel)], env=fake_env)

    assert res.returncode == 0
    assert "Command to be executed:" in res.stdout
    assert "sudo dnf install -y mesa-libGL mesa-libEGL libxkbcommon-x11 dbus-libs" in res.stdout
    assert "Dry-run mode: skipping execution." in res.stdout


def test_install_gui_consent_declined(tmp_path: Path):
    """install-gui prompts for user confirmation and aborts if declined."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04"\n')

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    dpkg_mock = mock_bin / "dpkg-query"
    dpkg_mock.write_text("#!/bin/sh\nexit 1\n")
    dpkg_mock.chmod(0o755)

    fake_env = {"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"}
    res = run_deps_script(["install-gui", "--os-release", str(os_rel)], env=fake_env, input_text="n\n")

    assert res.returncode == 1
    assert "Installation cancelled by user." in res.stdout


def test_pdf_check_quarto_and_xelatex_missing(tmp_path: Path):
    """pdf check reports missing Quarto and XeLaTeX with actionable instructions."""
    # Ensure system binaries (bash, grep, cut) are available, but quarto and xelatex are absent
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    for tool in ["bash", "sh", "uname", "grep", "cut", "head", "cat", "echo", "tr"]:
        src = Path(f"/bin/{tool}")
        if not src.exists():
            src = Path(f"/usr/bin/{tool}")
        if src.exists():
            (mock_bin / tool).symlink_to(src)

    fake_env = {"PATH": str(mock_bin)}

    res = run_deps_script(["pdf"], env=fake_env)
    assert res.returncode == 1
    assert "[MISSING] Quarto CLI" in res.stdout
    assert "[MISSING] XeLaTeX compiler" in res.stdout
    assert "https://quarto.org/docs/get-started/" in res.stdout
    assert "tinytex" in res.stdout.lower()
    assert "Do NOT install 'texlive-full'" in res.stdout


def test_pdf_check_quarto_version_too_low(tmp_path: Path):
    """pdf check warns if Quarto is installed but below 1.4.0."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()

    quarto_mock = mock_bin / "quarto"
    quarto_mock.write_text("#!/bin/sh\necho '1.3.450'\n")
    quarto_mock.chmod(0o755)

    xelatex_mock = mock_bin / "xelatex"
    xelatex_mock.write_text("#!/bin/sh\necho 'XeTeX 3.141592653'\n")
    xelatex_mock.chmod(0o755)

    fake_env = {"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"}
    res = run_deps_script(["pdf"], env=fake_env)

    assert res.returncode == 1
    assert "[WARN]    Quarto CLI: version 1.3.450 installed, but Polpo requires >= 1.4.0" in res.stdout
    assert "[OK]      XeLaTeX compiler:" in res.stdout


def test_pdf_install_dry_run_shows_tex_command(tmp_path: Path):
    """install-pdf with --dry-run prints distro TeX install command with storage notes."""
    os_rel = tmp_path / "os-release"
    os_rel.write_text('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04"\n')

    res = run_deps_script(["install-pdf", "--dry-run", "--yes", "--os-release", str(os_rel)])
    assert res.returncode == 0
    assert "sudo apt-get update && sudo apt-get install -y texlive-xetex texlive-latex-extra" in res.stdout
    assert "500MB to 1GB" in res.stdout
    assert "Dry-run mode: skipping execution." in res.stdout

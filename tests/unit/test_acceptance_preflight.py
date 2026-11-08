# ============================================================
#  tests/unit/test_acceptance_preflight.py
#  Unit Tests for Acceptance Harness Preflight & Status Logic
# ============================================================

from __future__ import annotations

import pytest

from scripts.acceptance.run_rendering_acceptance import (
    StageResult,
    TestItemResult,
    TestStatus,
    classify_os_target,
    compute_overall_status,
)


def test_classify_os_target_ubuntu_24_04_pass():
    os_release = """
ID=ubuntu
VERSION_ID="24.04"
PRETTY_NAME="Ubuntu 24.04.5 LTS"
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.PASS
    assert err is None
    assert details["ID"] == "ubuntu"
    assert details["VERSION_ID"] == "24.04"


def test_classify_os_target_ubuntu_22_04_blocked():
    os_release = """
ID=ubuntu
VERSION_ID="22.04"
PRETTY_NAME="Ubuntu 22.04.4 LTS"
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "22.04" in err
    assert "Ubuntu 24.04 required" in err


def test_classify_os_target_ubuntu_24_10_blocked():
    os_release = """
ID=ubuntu
VERSION_ID="24.10"
PRETTY_NAME="Ubuntu 24.10"
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "24.10" in err


def test_classify_os_target_debian_12_blocked():
    os_release = """
ID=debian
VERSION_ID="12"
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "debian" in err


def test_classify_os_target_fedora_blocked():
    os_release = """
ID=fedora
VERSION_ID="40"
PRETTY_NAME="Fedora Linux 40 (Workstation Edition)"
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "fedora" in err


def test_classify_os_target_missing_os_release():
    status, err, details = classify_os_target(None, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "not found" in err.lower()


def test_classify_os_target_malformed_version():
    os_release = """
ID=ubuntu
VERSION_ID=""
"""
    status, err, details = classify_os_target(os_release, machine="x86_64", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "unsupported" in err.lower() or "malformed" in err.lower()


def test_classify_os_target_unsupported_architecture():
    os_release = """
ID=ubuntu
VERSION_ID="24.04"
"""
    status, err, details = classify_os_target(os_release, machine="armv7l", system="Linux")
    assert status == TestStatus.BLOCKED
    assert "armv7l" in err
    assert "x86_64" in err


def test_ubuntu_24_04_missing_mandatory_xelatex_package_yields_fail(monkeypatch):
    """Item 9: Valid Ubuntu 24.04 target but missing mandatory XeLaTeX package must yield FAIL."""
    from scripts.acceptance.run_rendering_acceptance import run_runtime_preflight
    import shutil
    import subprocess

    # Mock /etc/os-release to represent valid Ubuntu 24.04
    os_release_text = "ID=ubuntu\nVERSION_ID=\"24.04\"\nPRETTY_NAME=\"Ubuntu 24.04.5 LTS\"\n"
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda self, *args, **kwargs: os_release_text if "os-release" in str(self) else "",
    )
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("platform.system", lambda: "Linux")

    # Mock shutil.which so kpsewhich is found regardless of host runner environment
    orig_which = shutil.which
    def mock_which(cmd, *args, **kwargs):
        if cmd == "kpsewhich":
            return "/usr/bin/kpsewhich"
        return orig_which(cmd, *args, **kwargs)

    monkeypatch.setattr("shutil.which", mock_which)

    # Mock kpsewhich so circuitikz.sty is missing
    orig_run = subprocess.run
    def mock_run(cmd, *args, **kwargs):
        if "kpsewhich" in str(cmd[0]):
            pkg = cmd[1]
            if pkg == "circuitikz.sty":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=f"/usr/share/texlive/texmf-dist/{pkg}", stderr="")
        return orig_run(cmd, *args, **kwargs)

    monkeypatch.setattr("subprocess.run", mock_run)

    stage = run_runtime_preflight()
    pkg_item = next(i for i in stage.items if i.test_id == "PREFLIGHT-06-TEX-PACKAGES")
    assert pkg_item.status == TestStatus.FAIL
    assert "circuitikz.sty" in pkg_item.error_message
    assert stage.failed_tests >= 1
    assert stage.status == TestStatus.FAIL


def test_compute_overall_status_all_pass():
    stages = {
        "runtime-preflight": StageResult("runtime-preflight", status=TestStatus.PASS),
        "mathjax-live": StageResult("mathjax-live", status=TestStatus.PASS),
        "gemini-live": StageResult("gemini-live", status=TestStatus.SKIPPED_WITH_REASON),
    }
    assert compute_overall_status(stages) == TestStatus.PASS


def test_compute_overall_status_mandatory_blocked_yields_blocked():
    stages = {
        "runtime-preflight": StageResult("runtime-preflight", status=TestStatus.BLOCKED),
        "mathjax-live": StageResult("mathjax-live", status=TestStatus.PASS),
    }
    assert compute_overall_status(stages) == TestStatus.BLOCKED


def test_compute_overall_status_any_fail_yields_fail():
    stages = {
        "runtime-preflight": StageResult("runtime-preflight", status=TestStatus.PASS),
        "mathjax-live": StageResult("mathjax-live", status=TestStatus.FAIL),
        "diagram-render-live": StageResult("diagram-render-live", status=TestStatus.BLOCKED),
    }
    assert compute_overall_status(stages) == TestStatus.FAIL

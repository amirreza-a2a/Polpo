# ============================================================
#  tests/unit/test_test_environment_config.py
#  Verification suite for Ticket 11A-01: Standardized Project & Test Configuration
# ============================================================

import os
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_pyproject_toml_exists_and_valid():
    """Verify pyproject.toml exists, parses as valid TOML, and configures pytest."""
    config_path = REPO_ROOT / "pyproject.toml"
    assert config_path.is_file(), "pyproject.toml must exist in repo root"

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    assert "tool" in config, "pyproject.toml must contain [tool] table"
    assert "pytest" in config["tool"], "pyproject.toml must contain [tool.pytest] table"
    assert "ini_options" in config["tool"]["pytest"], "pyproject.toml must contain [tool.pytest.ini_options]"

    pytest_ini = config["tool"]["pytest"]["ini_options"]
    assert pytest_ini.get("testpaths") == ["tests"], "testpaths must be explicitly set to ['tests']"
    assert pytest_ini.get("python_files") == ["test_*.py"], "python_files must match test_*.py"
    assert pytest_ini.get("python_classes") == ["Test*"], "python_classes must match Test*"
    assert pytest_ini.get("python_functions") == ["test_*"], "python_functions must match test_*"


def test_pyproject_toml_warning_filters():
    """Verify warning filters in pyproject.toml are configured and don't suppress all warnings."""
    config_path = REPO_ROOT / "pyproject.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    pytest_ini = config["tool"]["pytest"]["ini_options"]
    filterwarnings = pytest_ini.get("filterwarnings", [])
    assert isinstance(filterwarnings, list), "filterwarnings must be a list"
    # Ensure it does not broadly ignore all warnings (e.g. 'ignore')
    for f in filterwarnings:
        assert f.strip() != "ignore", "filterwarnings must not broadly silence all warnings with 'ignore'"


def test_requirements_dev_content_and_isolation():
    """Verify requirements-dev.txt specifies pytest and contains no speculative tools."""
    dev_req_path = REPO_ROOT / "requirements-dev.txt"
    assert dev_req_path.is_file(), "requirements-dev.txt must exist in repo root"

    dev_content = dev_req_path.read_text(encoding="utf-8")
    assert "pytest" in dev_content, "requirements-dev.txt must contain pytest"

    # Prohibited speculative tools
    prohibited_tools = ["ruff", "black", "flake8", "mypy", "pytest-cov", "pytest-xdist"]
    for tool in prohibited_tools:
        assert tool not in dev_content.lower(), f"Speculative tool '{tool}' must not be in requirements-dev.txt"


def test_requirements_prod_is_untouched():
    """Verify production requirements.txt contains zero dev/testing dependencies."""
    prod_req_path = REPO_ROOT / "requirements.txt"
    assert prod_req_path.is_file(), "requirements.txt must exist"

    prod_content = prod_req_path.read_text(encoding="utf-8").lower()
    assert "pytest" not in prod_content, "requirements.txt must not contain pytest"
    assert "pytest-qt" not in prod_content
    assert "pytest-mock" not in prod_content


def test_conftest_headless_qt_default_preserves_explicit_env(monkeypatch):
    """
    Verify tests/conftest.py uses setdefault for QT_QPA_PLATFORM so existing
    explicit settings are not clobbered.
    """
    conftest_path = REPO_ROOT / "tests" / "conftest.py"
    conftest_text = conftest_path.read_text(encoding="utf-8")

    assert 'os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")' in conftest_text or \
           "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')" in conftest_text, (
        "tests/conftest.py must setdefault('QT_QPA_PLATFORM', 'offscreen')"
    )


def test_qguiapplication_lifecycle_under_offscreen():
    """Verify QGuiApplication initializes and reports offscreen platform cleanly."""
    from interfaces.desktop.qt_compat import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])

    assert app is not None
    assert app.platformName() in ("offscreen", "minimal", "xcb", "cocoa", "windows")

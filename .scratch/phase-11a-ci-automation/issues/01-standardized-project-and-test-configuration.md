# 01: Standardized Project & Test Configuration

**What to build:** Establish a canonical root `pyproject.toml` declaring standardized `pytest` configuration (test discovery paths, filterwarnings, and strict markers) and a clean `requirements-dev.txt` for development and CI testing tools. Update `tests/conftest.py` so that headless Qt execution is the default (`QT_QPA_PLATFORM=offscreen`) without overriding explicit configurations or breaking existing `QGuiApplication` lifecycle tests. This ensures local developer environments and automated CI runners share identical discovery and execution rules without modifying production dependencies.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Create a minimal, standard `pyproject.toml` at the repository root containing `[tool.pytest.ini_options]`:
  - `testpaths = ["tests"]`
  - `python_files = ["test_*.py"]`
  - `python_classes = ["Test*"]`
  - `python_functions = ["test_*"]`
  - explicit filterwarnings rules to suppress known external deprecation warnings while keeping errors visible.
- Create `requirements-dev.txt` containing explicit development and testing dependencies (`pytest>=8.3.0,<10.0.0`), strictly isolated from production runtime dependencies.
- Update `tests/conftest.py` to ensure `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` is initialized safely during test-harness initialization, only setting the variable if not already present in the environment.

## Non-Goals

- Adding unrequested linters or test plugins (`ruff`, `black`, `flake8`, `mypy`, `pytest-cov`, `pytest-xdist`).
- Modifying `requirements.txt` — production dependencies must remain strictly production-only.
- Modifying production application logic, services, entities, or `composition.py`.
- Altering existing test assertion logic or test semantics.

## Files Likely Impacted

- Create: `pyproject.toml`
- Create: `requirements-dev.txt`
- Modify: `tests/conftest.py`

## Acceptance Criteria

- [ ] A root `pyproject.toml` exists and defines `[tool.pytest.ini_options]` with explicit `testpaths = ["tests"]`.
- [ ] Invoking `pytest` from the repository root with no arguments automatically discovers and executes the full test suite (922+ tests) across `tests/unit` and `tests/characterization`.
- [ ] `requirements-dev.txt` exists and specifies `pytest>=8.3.0`, cleanly decoupled from production `requirements.txt`.
- [ ] `requirements.txt` remains strictly untouched with zero development packages added.
- [ ] `tests/conftest.py` uses `setdefault` so that any pre-existing `QT_QPA_PLATFORM` environment setting is not overridden.
- [ ] Existing `QGuiApplication` lifecycle tests (including those explicitly passing `["-platform", "offscreen"]` or checking `QGuiApplication.instance()`) continue to pass without duplicate instance or conflict errors.
- [ ] No test behavior is changed other than ensuring headless execution where no platform was previously set.
- [ ] All 922 existing tests pass with zero regressions.
- [ ] `git diff --check` passes cleanly with zero whitespace or line-ending errors.

## Verification / Tests Required

- Execute `pytest` (without path arguments) from repo root and confirm 922 tests pass.
- Execute `pytest tests/unit/test_desktop_bootstrap_integration.py` to confirm headless Qt bootstrapping works as expected.
- Execute `QT_QPA_PLATFORM=minimal pytest tests/unit/test_desktop_bridge.py` to confirm pre-existing environment variables are respected.
- Inspect `requirements.txt` to confirm no modifications were made.

## Cross-Platform Implications

- `pyproject.toml` and `pytest` discovery work identically across POSIX and Windows.
- The `QT_QPA_PLATFORM=offscreen` default in `tests/conftest.py` ensures tests run consistently on Linux without X11 and on Windows without opening interactive window chrome.

## Failure & Rollback Considerations

- If any pytest configuration setting causes test discovery failures or changes existing fixture behavior, remove the conflicting option and verify against `pytest -v` baseline. Rollback is a simple git checkout of `tests/conftest.py` and removal of untracked files.

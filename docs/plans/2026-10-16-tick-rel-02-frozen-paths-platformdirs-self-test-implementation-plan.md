# Implementation Plan: TICK-REL-02 — Frozen-Safe Resource Resolution, Cross-Platform Paths, Startup Failure Handling, and CLI Self-Test

**Date:** 2026-10-16
**Status:** In Progress
**Objective:** Deliver TICK-REL-02 in accordance with project architecture rules (`AGENTS.md`) and pre-implementation audit findings.

---

## 1. Architectural Strategy & Scope

1. **Frozen-Safe Resource Resolution:**
   - Use `infrastructure/paths.py::get_runtime_resource_path()` across all runtime resource lookups.
   - Fix bypassed lookups in `interfaces/desktop/app.py` (`Main.qml`), `infrastructure/pdf/staging_workspace.py` (`resources/templates`), `infrastructure/persistence/sqlite/migration_runner.py` (`infrastructure/persistence/sqlite/migrations`), and `infrastructure/logging/setup.py`.
   - Ensure resolution is working-directory independent and supports both source checkouts and PyInstaller bundles (`sys.frozen`, `sys._MEIPASS`).

2. **Cross-Platform User Data & Log Directories:**
   - Centralize path resolution in `infrastructure/paths.py` via `platformdirs` (`appauthor=False`, slug `polpot`).
   - Honor `$XDG_DATA_HOME` when explicitly set.
   - Maintain compatibility slugs: data slug `polpot`, keyring slug `polpot_desktop`, application name `Polpo`.
   - Implement safe, idempotent legacy data discovery and migration (`~/.local/share/polpot` to platform data dir) with WAL checkpoint truncate and SQLite backup API.
   - Stop and preserve both copies without overwriting if conflicts are detected.
   - Place rotating application log files in `<user_data_dir>/logs/polpot.log` with bounded rotation and secret redaction.

3. **Fail-Fast QML Startup:**
   - Update `interfaces/desktop/app.py`: check `qml_path.is_file()` and `engine.rootObjects()` after loading.
   - On fatal failure, clean up initialized container resources and return non-zero exit code (`1`) instead of entering `app.exec()`.

4. **CLI Modes (`--version` and `--self-test`):**
   - `--version`: print `Polpo {__version__}` from `core/version.py`, exit `0` without GUI or DB initialization.
   - `--self-test`: non-interactive diagnostic runner verifying core metadata, packaged assets, writable user data/log dirs, runtime discovery (bundled vs system Pandoc/Node), persistence smoke test in isolated temp dir, and headless QML instantiation. Exit `0` on success, `1` on failure.

---

## 2. Execution Steps

1. Update `infrastructure/paths.py` with `get_user_data_dir`, `get_user_log_dir`, `get_legacy_user_data_dir`, and `migrate_legacy_user_data_if_needed`.
2. Update `infrastructure/pdf/staging_workspace.py` to resolve templates via `get_runtime_resource_path("resources/templates")`.
3. Update `infrastructure/persistence/sqlite/migration_runner.py` to resolve default migrations dir via `get_runtime_resource_path("infrastructure/persistence/sqlite/migrations")`.
4. Update `infrastructure/logging/setup.py` to default `log_dir` to `get_user_log_dir()` and extend secret redaction.
5. Update `interfaces/desktop/composition.py` to use `get_user_data_dir()` and trigger legacy migration before SQLite initialization.
6. Create `interfaces/desktop/self_test.py` with complete diagnostic checks.
7. Update `interfaces/desktop/app.py` with CLI argument handling (`--version`, `--self-test`), fail-fast QML load checks, and proper logging initialization.
8. Add comprehensive unit tests in `tests/unit/test_runtime_paths.py`, `tests/unit/test_desktop_self_test.py`, `tests/unit/test_desktop_bootstrap_integration.py`, and `tests/unit/test_legacy_data_migration.py`.
9. Verify all quality gates, architecture tests, and full test suite.

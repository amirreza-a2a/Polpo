# 09: Desktop Presentation Integration, Setup Assistant & Recovery Dialog

**What to build:** Extend `ExportController` in `interfaces/desktop/controllers/export_controller.py` and implement `PdfSetupController` in `interfaces/desktop/controllers/pdf_setup_controller.py`. Integrates PDF export into the desktop UI: binds `export_type == "pdf"`, enforces the dirty-editor OCC save guard, offloads compilation and recovery to background worker threads, provides in-app Setup & Diagnostics for missing runtimes, displays grouped-consent package recovery dialogs, and surfaces non-fatal cleanup warnings.

**Blocked by:** 07 (Application Export Orchestration Service & Canonical OCC Integration), 08 (Consented Asynchronous TinyTeX Package Recovery & Offline Diagnostics).

**Status:** ready-for-agent

## Scope

- Extend `interfaces/desktop/controllers/export_controller.py`:
  - **PDF Export Support:** Extends `start_export(job_id, export_type, destination_path, version)` to handle `export_type == "pdf"`.
  - **Save-Before-Export Gate:** Reuses `_is_editor_dirty_for_job(job_id)`. Emits `saveBeforeExportRequired(job_id, "pdf", version)` if active editor holds unsaved changes.
  - **Thread Pool Execution:** Submits `QuartoPdfExportService.export_pdf()` to `_executor` (`ThreadPoolExecutor`). Qt GUI thread remains responsive at 60 FPS.
  - **Overwrite Confirmation:** Emits `overwriteRequired(job_id, "pdf", dest, version)` when `DestinationAlreadyExistsError` is caught. When confirmed, re-executes with `overwrite=True`.
  - **Signal Emission:**
    - On success: emits `exportCompleted(job_id, destination_path)`.
    - On non-fatal cleanup warnings: emits `exportWarning(job_id, warning_message)`.
    - On fatal error: sanitizes error message and emits `exportFailed(job_id, error_message)`.
  - **Missing Package Interception:** If `MissingLatexPackageError` is caught, invokes `PackageRecoveryService.create_recovery_plan()`. Emits `packageRecoveryRequired(job_id, missing_packages, can_auto_recover, instructions)`.
- Implement `PdfSetupController` in `interfaces/desktop/controllers/pdf_setup_controller.py`:
  - QObject exposing readiness properties to QML: `quartoAvailable`, `quartoVersion`, `xelatexAvailable`, `isExportReady`, `texDistribution`, `setupInstructions`, `customQuartoPath`.
  - Slots:
    - `probeEnvironment()`: asynchronous re-probe of Quarto/TeX runtimes.
    - `setCustomQuartoPath(path: str)`: updates `AppSettings` and re-probes.
    - `installMissingPackages(packages: List[str])`: submits consented recovery to background executor.
- QML UI Integration in `interfaces/desktop/qml/`:
  - Add PDF option to `ExportDialog.qml`.
  - Create `PdfSetupAssistantDialog.qml`: displays Quarto/TeX readiness, installation guides with copyable commands, and custom path browse button.
  - Create `MissingPackageRecoveryDialog.qml`: displays missing package set, single "Install Dependencies" button (if TinyTeX), or copyable shell commands (if system TeX).

## Non-Goals

- Executing direct SQL in controllers (forbidden by Clean Architecture Rule 5).
- Spawning child processes directly from controllers (forbidden by Clean Architecture Rule 6.3).
- Real-time diagram rendering inside the editor preview (prohibited by ADR-003 D03).

## Files Likely Impacted

- Modify: `interfaces/desktop/controllers/export_controller.py`
- Create: `interfaces/desktop/controllers/pdf_setup_controller.py`
- Modify: `interfaces/desktop/qml/dialogs/ExportDialog.qml`
- Create: `interfaces/desktop/qml/dialogs/PdfSetupAssistantDialog.qml`
- Create: `interfaces/desktop/qml/dialogs/MissingPackageRecoveryDialog.qml`
- Create: `tests/unit/test_export_controller_pdf.py`
- Create: `tests/unit/test_pdf_setup_controller.py`

## Public Interfaces / Contracts

```python
class ExportController(QObject):
    # Existing signals: exportingChanged, errorChanged, exportCompleted, exportFailed,
    # overwriteRequired, saveBeforeExportRequired, readyForDestination

    # New signals:
    exportWarning = Signal(int, str)  # (job_id, warning_message)
    packageRecoveryRequired = Signal(int, list, bool, str)  # (job_id, pkgs, can_auto, instructions)


class PdfSetupController(QObject):
    readinessChanged = Signal()
    recoveryStatusChanged = Signal(bool, str)

    @Property(bool, notify=readinessChanged)
    def isExportReady(self) -> bool: ...

    @Property(str, notify=readinessChanged)
    def setupInstructions(self) -> str: ...

    @Slot(str)
    def setCustomQuartoPath(self, path: str) -> None: ...

    @Slot(list)
    def installMissingPackages(self, packages: List[str]) -> None: ...
```

## Architectural Invariants

- **GUI Thread Freedom (AGENTS.md Rule 12):** Neither Quarto compilation, XeLaTeX rendering, nor package installation may ever execute on the Qt GUI thread.
- **Thin Presentation Adapters (AGENTS.md Rule 5):** Presentation controllers translate user input into typed DTOs/commands and subscribe to application events; they contain zero compilation logic or shell scripts.
- **Single Source of Truth:** `AppSettings` persists custom paths; SQLite persists document version state.

## Acceptance Criteria

- [ ] `ExportController.start_export(job_id, "pdf", ...)` triggers background PDF compilation via `QuartoPdfExportService`.
- [ ] If editor is dirty, emits `saveBeforeExportRequired` and postpones PDF export until version is committed.
- [ ] Existing destination file triggers `overwriteRequired` before performing atomic overwrite.
- [ ] Missing Quarto prerequisites route to `PdfSetupAssistantDialog` with copyable instructions.
- [ ] Users can set a custom Quarto executable path in settings, which immediately triggers a capability probe.
- [ ] Missing package errors trigger `MissingPackageRecoveryDialog`. If TinyTeX, user confirmation launches asynchronous package installation off the GUI thread.
- [ ] If system TeX is active, displays copyable manual terminal command without offering automated installation.
- [ ] Successful export with cleanup warnings displays non-fatal toast/banner without marking export failed.
- [ ] Qt GUI remains interactive and responsive at 60 FPS throughout long-running export operations.
- [ ] All unit tests pass in `tests/unit/test_export_controller_pdf.py` and `tests/unit/test_pdf_setup_controller.py`.

## Verification / Tests Required

- `test_export_controller_pdf_happy_path()`: submits PDF export, verifies worker thread dispatch and `exportCompleted`.
- `test_export_controller_pdf_save_gate()`: verifies `saveBeforeExportRequired` emitted when editor is dirty.
- `test_export_controller_pdf_overwrite_flow()`: verifies `overwriteRequired` emitted and resumed upon confirmation.
- `test_export_controller_missing_package_signal()`: asserts `packageRecoveryRequired` signal with package payload.
- `test_pdf_setup_controller_custom_path_probing()`: updates custom path, verifies readiness signal emission.

## Commit Boundary

Single commit: `feat(desktop): integrate pdf export, setup assistant, and package recovery into desktop presentation`

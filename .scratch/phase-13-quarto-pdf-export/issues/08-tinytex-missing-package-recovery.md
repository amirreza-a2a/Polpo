# 08: Consented Asynchronous TinyTeX Package Recovery & Offline Diagnostics

**What to build:** Implement `TinyTexManager` in `infrastructure/pdf/tinytex_manager.py` and `PackageRecoveryService` in `application/services/package_recovery_service.py`. Implements the missing LaTeX package recovery workflow according to ADR-003 D04: provides structured diagnostics for missing packages, enforces explicit user consent for TinyTeX package installation (`tlmgr install`), executes recovery asynchronously off the GUI thread with cancellation support, re-validates TeX readiness post-install, rejects automatic elevation (`sudo`) for OS-managed distributions, and handles offline failures cleanly.

**Blocked by:** 02 (Quarto and TeX Runtime Discovery, Readiness Probing & In-App Diagnostics), 03 (Subprocess Supervisor, Environment Sanitization & Process Tree Cancellation), 05 (Quarto PDF Infrastructure Adapter & TeX Log Sanitizer).

**Status:** ready-for-agent

## Scope

- Define package recovery DTOs in `application/dto/package_recovery_dto.py`:
  - `PackageRecoveryRequest(packages: Tuple[str, ...], is_cancelled: Optional[Callable[[], bool]] = None)`
  - `PackageRecoveryResultDTO(success: bool, installed_packages: Tuple[str, ...], failed_packages: Tuple[str, ...], error_message: str = "")`
- Implement `TinyTexManager` in `infrastructure/pdf/tinytex_manager.py`:
  - **Executable Resolution:** Locates `tlmgr` / `quarto tools` associated with Quarto-managed TinyTeX.
  - **Single Grouped Command:** Spawns `quarto tools tlmgr install <pkg1> <pkg2> ...` via `SubprocessSupervisor` (Ticket 03).
  - **Network / Offline Detection:** Detects network errors (e.g. `tlmgr: CTAN unreachable`, DNS resolution failure). If offline, fails cleanly with `OfflineRecoveryError` without infinite retries.
  - **Timeout & Cancellation:** Bound by timeout (default 120s) and cooperatively cancellable.
  - **Post-Install Verification:** Re-probes TeX environment via `QuartoRuntimeDiscovery` (Ticket 02) to verify packages are readable before declaring recovery successful.
- Implement `PackageRecoveryService` in `application/services/package_recovery_service.py`:
  - **Distribution Discrimination (ADR-003 D04):**
    - If active distribution is TinyTeX: exposes `can_auto_recover = True`. Authorizes recovery only when user consent is provided for the exact identified package set.
    - If active distribution is system TeX Live (Debian/Ubuntu/Fedora) or MiKTeX: exposes `can_auto_recover = False`. Polpo **never** invokes `sudo` or OS package managers. Generates platform-appropriate manual commands (e.g. `sudo apt install texlive-latex-extra texlive-pictures`).
  - **Zero Silent Installation:** Enforces that package installation NEVER executes during standard `render_pdf()` calls.

## Non-Goals

- Invoking operating system package managers (`apt`, `dnf`, `pacman`) or `sudo`.
- Installing full TeX distributions from scratch (Polpo requires external installation per D01).
- Presenting Qt dialogs (handled by `ExportController` in Ticket 09).

## Files Likely Impacted

- Create: `application/dto/package_recovery_dto.py`
- Create: `application/services/package_recovery_service.py`
- Create: `infrastructure/pdf/tinytex_manager.py`
- Create: `tests/unit/test_package_recovery_service.py`
- Create: `tests/unit/test_tinytex_manager.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class PackageRecoveryPlanDTO:
    missing_packages: Tuple[str, ...]
    can_auto_recover: bool
    manual_command: str = ""
    diagnostic_summary: str = ""


class PackageRecoveryService:
    def __init__(
        self,
        discovery: IPdfRuntimeDiscovery,
        tinytex_manager: TinyTexManager,
    ):
        ...

    def create_recovery_plan(self, missing_packages: Sequence[str]) -> PackageRecoveryPlanDTO:
        """Determines recovery capabilities based on detected TeX distribution."""
        ...

    def execute_recovery(
        self, request: PackageRecoveryRequest
    ) -> PackageRecoveryResultDTO:
        """Executes consented package installation for TinyTeX."""
        ...
```

## Architectural Invariants

- **Explicit Consent (ADR-003 D04):** Package installation requires explicit user authorization of the specific package set. Silent background downloads are strictly prohibited.
- **No Privilege Elevation:** Polpo Desktop must never execute commands requiring root or administrative privileges (`sudo`).
- **Offline Safety:** Network or CTAN unreachability must fail fast with actionable guidance.

## Acceptance Criteria

- [ ] When TinyTeX is detected, `create_recovery_plan` returns `can_auto_recover=True`.
- [ ] When system TeX Live is detected, returns `can_auto_recover=False` and provides copyable manual commands (e.g. `sudo apt install ...`).
- [ ] TinyTeX package installation runs through `SubprocessSupervisor` off the GUI thread.
- [ ] Grouped installation command passes only the explicitly consented package names.
- [ ] Offline status or CTAN unreachability fails fast, returning clean diagnostic messages without hanging.
- [ ] Post-recovery probing confirms package installation before returning success.
- [ ] Subprocess cancellation cleanly terminates `tlmgr` execution.
- [ ] All unit tests pass in `tests/unit/test_package_recovery_service.py` and `tests/unit/test_tinytex_manager.py`.

## Verification / Tests Required

- `test_plan_tinytex_allows_auto_recover()`: asserts `can_auto_recover=True` for TinyTeX.
- `test_plan_system_tex_provides_manual_instructions()`: asserts `can_auto_recover=False` and returns manual apt command for Linux.
- `test_execute_recovery_success()`: mocks successful `tlmgr install` and post-install revalidation.
- `test_execute_recovery_offline_fails_fast()`: mocks network error, asserts `OfflineRecoveryError` diagnostic.
- `test_execute_recovery_cancellation()`: triggers cancellation callback; asserts process tree killed cleanly.

## Commit Boundary

Single commit: `feat(export): implement consented tinytex package recovery service and offline diagnostics`

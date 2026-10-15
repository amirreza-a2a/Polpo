# 02: Quarto and TeX Runtime Discovery, Readiness Probing & In-App Diagnostics

**What to build:** Implement Quarto CLI and XeLaTeX discovery, capability probing, and diagnostics in `infrastructure/pdf/runtime_discovery.py`, along with port and DTO definitions in `application/ports/pdf_export_port.py` and `application/dto/pdf_readiness_dto.py`. Probes system `PATH` and optional user-configured executable paths in `AppSettings`, validates minimum Quarto capability ($\ge 1.4.0$), detects XeLaTeX and TeX distribution type (TinyTeX vs system TeX Live/MiKTeX), and formats platform-appropriate setup instructions.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Define readiness DTOs in `application/dto/pdf_readiness_dto.py`:
  - `EngineReadinessDTO(quarto_available: bool, quarto_version: str, quarto_path: Optional[str], xelatex_available: bool, is_tinytex: bool, tex_distribution_name: str, missing_prerequisites: Tuple[str, ...], diagnostic_instructions: str)`
- Define port interface `IPdfRuntimeDiscovery` in `application/ports/pdf_export_port.py`:
  - `probe_readiness(configured_quarto_path: Optional[str] = None) -> EngineReadinessDTO`
- Implement `QuartoRuntimeDiscovery` in `infrastructure/pdf/runtime_discovery.py`:
  - **Executable Resolution:** Checks user-configured path from `AppSettings` first, then system `PATH`. On Windows, handles both `quarto.cmd` and `quarto.exe` shims.
  - **Capability Probing:** Executes `quarto --version` using `CREATE_NO_WINDOW` (`0x08000000`) on Windows to eliminate console flashing. Validates version satisfies $\ge 1.4.0$.
  - **TeX Discovery:** Executes `quarto check pdf` or lightweight probing to check for `xelatex`.
  - **Distribution Identification:** Identifies whether TeX is managed by TinyTeX (via `quarto install tinytex` location) or an operating system distribution (e.g. Debian/Ubuntu TeX Live, MiKTeX).
  - **Diagnostic Generation:** Formats clear, platform-appropriate setup instructions (e.g. `winget install --id Posit.Quarto` on Windows; official `.deb` instructions on Linux).
  - **Graceful Degradation (ADR-003 D01):** Absence of Quarto or XeLaTeX returns `quarto_available=False` without throwing unhandled exceptions or crashing the application.

## Non-Goals

- Installing or updating Quarto automatically (prohibited by ADR-003 D01).
- PDF document compilation (deferred to Ticket 05).
- Package installation recovery (deferred to Ticket 08).
- QML view rendering (deferred to Ticket 09).

## Files Likely Impacted

- Create: `application/dto/pdf_readiness_dto.py`
- Modify/Create: `application/ports/pdf_export_port.py`
- Create: `infrastructure/pdf/runtime_discovery.py`
- Create: `tests/unit/test_runtime_discovery.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class EngineReadinessDTO:
    quarto_available: bool
    quarto_version: str = ""
    quarto_path: Optional[str] = None
    xelatex_available: bool = False
    is_tinytex: bool = False
    tex_distribution_name: str = ""
    missing_prerequisites: Tuple[str, ...] = ()
    diagnostic_instructions: str = ""

    @property
    def is_export_ready(self) -> bool:
        return self.quarto_available and self.xelatex_available


class IPdfRuntimeDiscovery(ABC):
    @abstractmethod
    def probe_readiness(self, configured_quarto_path: Optional[str] = None) -> EngineReadinessDTO:
        """Probes the host environment for Quarto and XeLaTeX runtimes."""
        raise NotImplementedError
```

## Architectural Invariants

- **Clean Architecture:** `application/dto/` and `application/ports/` contain zero infrastructure or subprocess logic.
- **No Console Flashing:** On Windows, all child processes MUST be spawned with `creationflags=subprocess.CREATE_NO_WINDOW`.
- **Bounded Probing:** Discovery probing must complete within a strict timeout (max 5 seconds per probe) and never hang.
- **Fail-Safe Startup:** Inability to find Quarto never prevents Polpo application startup or non-export operations.

## Acceptance Criteria

- [ ] `QuartoRuntimeDiscovery` locates `quarto` on system `PATH` on Linux and Windows (`.cmd` / `.exe`).
- [ ] Explicit `configured_quarto_path` from `AppSettings` takes precedence over system `PATH`.
- [ ] If Quarto version is below `1.4.0`, reports `quarto_available=False` with minimum version warning in diagnostics.
- [ ] Correctly identifies XeLaTeX presence and detects whether TinyTeX is active.
- [ ] When Quarto is absent, returns `EngineReadinessDTO(quarto_available=False)` with copyable, platform-appropriate setup instructions.
- [ ] Probing does not spawn visible cmd.exe or terminal windows on Windows.
- [ ] All unit tests pass in `tests/unit/test_runtime_discovery.py` with mocked subprocess outputs.

## Verification / Tests Required

- `test_discovery_quarto_found_on_path()`: mocks successful Quarto 1.9.38 output.
- `test_discovery_user_configured_path_precedence()`: verifies user path overrides system PATH.
- `test_discovery_quarto_absent()`: verifies graceful DTO return with platform guidance.
- `test_discovery_quarto_version_too_low()`: rejects Quarto < 1.4.0 with diagnostic message.
- `test_discovery_windows_shim_resolution()`: mocks Windows `.cmd` execution and flags.
- `test_discovery_tinytex_vs_system_tex_detection()`: verifies distribution flag discrimination.

## Commit Boundary

Single commit: `feat(export): implement quarto and xelatex runtime discovery and readiness probing`

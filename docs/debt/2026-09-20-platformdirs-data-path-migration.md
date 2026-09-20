# Technical Debt: Desktop Data-Path Platform Migration (`platformdirs`)

**Date:** 2026-09-20
**Status:** Deferred / Architectural Debt
**Target File:** [`interfaces/desktop/composition.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/composition.py) (lines 66–75)
**Related Rules:** [`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rule 28 (Cross-Platform Desktop Requirements), Rule 29 (Local Application Data Boundaries)

---

## 1. Context & Current Behavior

The canonical desktop application composition root initializes local filesystem paths in [`DesktopAppContainer.__init__()`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/composition.py#L63-L84):

```python
# interfaces/desktop/composition.py:66-74
if db_path is None:
    data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    base_data = Path(data_home) / "polpot"
    base_data.mkdir(parents=True, exist_ok=True)
    db_path = base_data / "polpot.db"
    if artifacts_dir is None:
        artifacts_dir = base_data / "artifacts"
    if vault_path is None:
        vault_path = base_data / "credentials.enc"
```

### The Issue

This fallback implementation hardcodes POSIX / XDG data directory conventions (`~/.local/share/polpot`) across all operating systems. On non-Linux platforms:

1. **Windows:** Standard Windows desktop applications store local application data under `%LOCALAPPDATA%\PolpoT` (e.g., `C:\Users\<User>\AppData\Local\PolpoT`), not in a UNIX hidden directory (`~\.local\share\polpot`).
2. **macOS:** Standard macOS applications store application state under `~/Library/Application Support/PolpoT`, adhering to Apple File System hierarchy guidelines.

This current behavior violates [`AGENTS.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/AGENTS.md) Rule 29:
> *"Use platform-appropriate application-data directories. Do not hardcode a single Linux path for all platforms."*

---

## 2. Intended Migration

In a future hardening phase, migrate default desktop directory resolution to the standard, cross-platform `platformdirs` library:

```python
import platformdirs

if db_path is None:
    base_data = Path(platformdirs.user_data_dir("PolpoT", appauthor=False))
    base_data.mkdir(parents=True, exist_ok=True)
    db_path = base_data / "polpot.db"
    if artifacts_dir is None:
        artifacts_dir = base_data / "artifacts"
    if vault_path is None:
        vault_path = base_data / "credentials.enc"
```

### Expected Platform Resolutions

* **Windows:** `C:\Users\<User>\AppData\Local\PolpoT`
* **macOS:** `~/Library/Application Support/PolpoT`
* **Linux:** `~/.local/share/polpot` (honoring `$XDG_DATA_HOME`)

---

## 3. Scope of Migration

The future migration will require:

1. **Dependency Addition:** Add `platformdirs>=4.0.0` to `requirements.txt`.
2. **Composition Root Update:** Update default path calculation in [`interfaces/desktop/composition.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/composition.py).
3. **Data Migration / Backward Compatibility:** Check for the existence of legacy `~/.local/share/polpot` on Windows/macOS installations and offer seamless one-time migration of existing SQLite database files, artifacts, and encrypted vault files before binding new default locations.
4. **Integration Tests:** Verify default path resolution across Windows (`test-windows`), macOS (`test-macos`), and Linux (`test-linux`).

---

## 4. Rationale for Deferral

This task is intentionally deferred from **Phase 11A (CI Automation)** because:

1. **Phase 11A Invariant:** Phase 11A focuses strictly on CI matrix pipeline automation, secret scanning, and platform support documentation. Modifying production runtime data paths risks altering developer environments and existing test fixtures mid-phase.
2. **Deterministic Test Isolation:** All automated unit, characterization, and integration tests currently pass explicit, isolated `tmp_dir` parameters (`db_path`, `artifacts_dir`, `vault_path`) to `DesktopAppContainer` and `create_app()`, entirely bypassing default data-path resolution during CI and local testing.
3. **Zero Production Code Churn:** Ticket 06 mandates zero production Python code modifications.

---

## 5. Explicit Non-Goal for Ticket 06

**Phase 11A — Ticket 06 does NOT implement this migration.**

[`interfaces/desktop/composition.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/interfaces/desktop/composition.py) remains completely untouched, and `platformdirs` is not added to production dependencies during Ticket 06.

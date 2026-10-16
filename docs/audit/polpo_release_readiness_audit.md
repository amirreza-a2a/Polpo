# Polpo CI/CD and First Desktop Release Readiness Audit & Architecture Plan

**Mission Type:** Read-Only Audit and Release Architecture Planning (Zero Repository Mutations)
**Repository:** `amirreza-a2a/Polpo`
**Target Checkout:** `/home/amirreza-a2a/DevelopPOlpo/PolpoT` (Audited against repository snapshot & baseline metadata)
**Audit Date:** 2026-10-16

## 1. Executive Summary and Current Readiness Verdict

### Current Readiness Verdict: **NOT READY FOR PACKAGED END-USER RELEASE (READY FOR SOURCE-ONLY TAGGED PRE-RELEASE ONLY AFTER B-01..B-06 REMEDIATION)**

Polpo's core domain, application services, SQLite persistence, Pandoc AST pipeline, supervised MathJax daemon, and Quarto/XeLaTeX PDF export subsystem (`ADR-003`) are architecturally mature and well-tested at the source-checkout level. However, **Polpo currently has zero packaging build scripts, zero runtime binary bundling automation, and several critical runtime path/packaging blockers** that would cause a frozen/packaged binary distribution to fail on a clean end-user machine.

### Summary of Findings by Severity

| **Severity** | **Count** | **Summary of Findings** |
| **Blocker** | **6** | **B-01:** Zero packaging spec/config (PyInstaller/Briefcase) or build script exists (`pyproject.toml:1-12` only configures `pytest`).  **B-02:** No acquisition/bundling script exists to download, verify SHA-256 hashes, and stage the Pandoc `3.1.11` and Node.js `22.23.2` binaries declared in `resources/runtimes.json:1-66`; `resources/bin/` contains only `.gitkeep`.  **B-03:** Four runtime resource lookups bypass `get_runtime_resource_path()` (`infrastructure/paths.py:13`) and use raw `Path(__file__)` traversal (`interfaces/desktop/app.py:406`, `infrastructure/pdf/staging_workspace.py:248-250`, `infrastructure/persistence/sqlite/migration_runner.py:16`, `infrastructure/logging/setup.py:59`).  **B-04:** `interfaces/desktop/app.py:406-409` silently enters `app.exec()` even if `Main.qml` is missing or fails to load (`engine.rootObjects()` is empty), causing a headless zombie process instead of failing fast.  **B-05:** `interfaces/desktop/composition.py:78-86` hardcodes `~/.local/share/polpot` (`XDG_DATA_HOME`) across all operating systems—violating `AGENTS.md` Rule 29 and ignoring `platformdirs==4.3.6` (`requirements.txt:23`).  **B-06:** No single authoritative application version (`__version__` or `[project].version`) or `LICENSE` file exists anywhere in the repository. |
| **High** | **5** | **H-01:** `.github/workflows/periodic-validation.yml:43-115` triggers on `push: tags: ['v*']` but does **not** install Pandoc, Node.js `22.23.2`, or `resources/mathjax/node_modules`, causing live MathJax tests to silently `pytest.skip(...)` (`tests/unit/test_mathjax_client.py:101-109`) and `PandocParser` integration tests to rely on unpinned runner-image defaults.  **H-02:** `periodic-validation.yml` has no workflow dependency link to block any release workflow; two workflows triggered by `push: tags: ['v*']` run concurrently and independently.  **H-03:** `.github/workflows/ci.yml:109,159` uses mutable tag `actions/setup-node@v4` while all other actions are pinned to full commit SHAs, and `choco install pandoc` (`ci.yml:165`) & `apt-get install pandoc` (`ci.yml:116`) install unpinned OS-packaged Pandoc versions instead of the project-pinned `3.1.11` from `resources/runtimes.json`.  **H-04:** Clean installations start with an empty SQLite `prompts` table (`001_initial_schema.sql:13-22`); the 8 curated prompt templates in `prompts/templates/` and `prompts/pipeline2/` are never seeded into SQLite on first run.  **H-05:** Production `requirements.txt:1-14` bundles frozen legacy Telegram server dependencies (`python-telegram-bot==21.5`, `PyMySQL==1.1.1`) into the desktop environment. |
| **Medium** | **4** | **M-01:** `infrastructure/logging/setup.py:58-65` defaults `log_dir` to `project_root / "logs"` (read-only in packaged installs and violates `AGENTS.md` Rule 29) and is never invoked by `interfaces/desktop/app.py:403-410`.  **M-02:** `docs/packaging.md:64-83` references `resources/entitlements.plist`, which does not exist in the repository, and documents `resources/bin/linux-x64` whereas `resources/runtimes.json:14` uses `resources/bin/linux-x86_64`.  **M-03:** `.github/workflows/ci.yml` lacks a `concurrency:` group to cancel superseded PR runs and does not cache `resources/mathjax/node_modules`.  **M-04:** Product naming is split between `Polpo` (`README.md`, `ADR-003`) and `PolpoT` (`interfaces/desktop/app.py:229-230`, `~/.local/share/polpot`, `keyring_service_name="polpot_desktop"`). |
| **Low** | **2** | **L-01:** No `CHANGELOG.md` or structured release-notes template exists in the repository.  **L-02:** `scripts/run_quality_gates.py:25-30` runs 4 architecture test files in `DEFAULT_ARCHITECTURE_TESTS`, omitting `tests/unit/test_architecture_boundaries.py` and `tests/unit/test_architecture_security.py` during the `quality-gates` job (though they run later in `test-linux`/`test-windows`). |

## 2. Verified Repository Baseline

Per Section 2 of the mission instructions, the following facts are distinguished between **Verified from Repository Contents**, **User-Reported Local Checkout Metadata**, and **Unverified Live GitHub API State** (as this sandbox environment has no direct access to `/home/amirreza-a2a/DevelopPOlpo/PolpoT` or authenticated `gh` CLI access to private GitHub API endpoints):

| **Baseline Dimension** | **Verified Status / Value** | **Verification Source & Notes** |
| **Repository Identifier** | `amirreza-a2a/Polpo` | Verified in `.github/workflows/`, `README.md:3-4`, `docs/ci-repository-rules.md:3` |
| **Inspected Archive Snapshot** | `1ee57179b8bc7bf87ac69be50f5ff1bfbb56627f` + `README.md` update | Verified directly from uploaded repository archive `polpo` |
| **Reported Post-Merge `main` SHA** | `e4addf1ecb264e0fb00d1006e34b3121eab3d956` (`e4addf1`) | Reported in user branch audit after merging Persian RTL PDF fixes and branch cleanup |
| **Local Branches Retained** | `main`, `docs/phase-12-scratch-backfill` (`46e1f1a`), `chore/pandoc-mathjax-ticket-registration` (`2a16253`) | Reported in branch audit report; verify locally with `git branch -a` and `git status --short` |
| **`.github/workflows/` Inventory** | 1\. `.github/workflows/ci.yml` (184 lines)  2\. `.github/workflows/periodic-validation.yml` (115 lines) | Verified by exhaustive directory traversal of `.github/` — **no release workflow exists** |
| **Canonical Desktop Entry Point** | `interfaces/desktop/app.py:403` (`python -m interfaces.desktop.app` -> `main()`) | Verified in `interfaces/desktop/app.py:403-413` and `README.md:223` |
| **Legacy Frozen Entry Point** | `main.py:110` (`main()` — Telegram polling bot) | Verified in `main.py:1-327`; isolated per `AGENTS.md` Rule 4 |
| **Packaging / Build Manifests** | `pyproject.toml` (12 lines, pytest config only), `requirements.txt` (25 lines), `requirements-dev.txt` (4 lines), `resources/runtimes.json` (66 lines), `resources/mathjax/package.json` (13 lines), `resources/mathjax/package-lock.json` (93 lines) | Verified in repository root and `resources/`. **Zero** `.spec`, `setup.py`, `Briefcase`, or installer files exist |
| **License & Changelog Files** | **Absent** (`LICENSE` and `CHANGELOG.md` do not exist) | Verified in repository root (`README.md:382` references a license file that is not yet created) |
| **Git Tags & GitHub Releases** | **Unverified live / None in archive** | Run `git tag -l` and `gh release list` on the host checkout to confirm zero published GitHub Release objects exist |

### Commands to Re-Verify Host Checkout State Before Executing Tickets

```
git rev-parse --show-toplevel
git status --short --branch
git rev-parse HEAD origin/main
git branch -a
git tag -l
gh run list --branch main --limit 5
gh release list

```

## 3. Existing CI/CD Workflow Inventory and Responsibilities

### 3.1 `.github/workflows/ci.yml` (Tier-1 Blocking Continuous Integration)

* **Triggers (`ci.yml:11-16`):** `pull_request` targeting `[main]` and `push` to `[main]`.

* **Workflow-Level Permissions (`ci.yml:17-18`):** `contents: read` (least-privilege read-only default).

* **Job Dependency Graph:**

  * `secret-scan` (`ubuntu-24.04`) — runs independently in parallel with `quality-gates`.

  * `quality-gates` (`ubuntu-24.04`) — runs independently; gates `test-linux` and `test-windows`.

  * `test-linux` (`ubuntu-24.04`, `needs: quality-gates`) — runs full `pytest -q` suite on Linux.

  * `test-windows` (`windows-2025`, `needs: quality-gates`) — runs full `pytest -q` suite on Windows Server 2025.

* **What `ci.yml` Validates vs. What It Does Not:**

  * **Validates:** Secret hygiene via Gitleaks (`v3.0.0`), git diff whitespace hygiene (`git diff --check`), Python bytecode syntax compilation (`compileall`), AST architectural boundary tests (`scripts/run_quality_gates.py`), standalone MathJax worker protocol (`scripts/verify_mathjax_worker.py`), and source-tree unit/characterization/integration tests under `QT_QPA_PLATFORM=offscreen`.

  * **Does NOT Validate:** Packaged/frozen binary builds (`sys.frozen = True`), bundled Pandoc (`resources/bin/...`) or bundled Node.js (`resources/mathjax/node`) acquisition from `resources/runtimes.json`, real Quarto/XeLaTeX PDF compilation (`test_scenario_08_bilingual_persian_rtl_and_tikz_live_compilation` is skipped via `@pytest.mark.skipif(not is_quarto_installed())` at `tests/integration/test_quarto_pdf_export_e2e.py:431`), or clean-machine installation.

### 3.2 `.github/workflows/periodic-validation.yml` (Tier-2 Pre-Release & Weekly Validation)

* **Triggers (`periodic-validation.yml:15-38`):**

  1. `schedule`: Weekly on Mondays at 03:00 UTC (`cron: '0 3 * * 1'`).

  2. `workflow_dispatch`: Manual trigger with inputs `ref` (default `'main'`), `run_macos` (default `true`), `run_python_matrix` (default `true`).

  3. `push: tags: ['v*']`: Automatically triggers whenever any `v*` tag is pushed.

* **Ref Checked Out (`periodic-validation.yml:57,93`):** `ref: ${{ inputs.ref || github.ref }}`.

  * On `schedule`: `inputs.ref` is empty, so `github.ref` resolves to `refs/heads/main`.

  * On `push: tags: ['v*']`: `inputs.ref` is empty, so `github.ref` resolves to `refs/tags/v*` (the exact tagged commit).

  * On `workflow_dispatch`: Checks out the user-supplied `inputs.ref` (defaulting to `'main'`).

* **Critical Audit Findings on `periodic-validation.yml`:**

  1. **Missing Pandoc, Node.js, and MathJax Setup (`periodic-validation.yml:43-115`):** Unlike `ci.yml`, neither `test-macos` nor `test-python-matrix` installs Node.js `22.23.2`, runs `npm ci --prefix resources/mathjax`, or installs `pandoc`. Consequently, live MathJax worker tests silently `pytest.skip(...)` during pre-release tag runs!

  2. **Zero Release Blocking:** `periodic-validation.yml` is a standalone workflow. In GitHub Actions, two workflows triggered by `push: tags: ['v*']` execute in parallel with no dependency ordering. A failure in `periodic-validation.yml` **will not block** a separate tag-triggered release workflow unless they are unified into a single pipeline or linked via `workflow_call`.

## 4. Evidence-Based Findings (Blockers, High, Medium, Low)

### 4.1 Blocker Findings (Must Fix Before Any Packaged Release)

* **\[B-01\] Blocker — Zero Packaging Configuration or Build Script Exists (`pyproject.toml:1-12`)**

  * **Evidence:** `pyproject.toml` contains only 12 lines (`[tool.pytest.ini_options]`). No `[build-system]`, `[project]`, `[project.gui-scripts]`, PyInstaller `.spec` file, or Briefcase configuration exists anywhere in the repository.

  * **Impact:** There is currently no automated or reproducible way to build a standalone desktop executable, `.app`, `.dmg`, `.AppImage`, or Windows `.zip`/installer.

* **\[B-02\] Blocker — No Runtime Binary Acquisition Script for `resources/runtimes.json` (`resources/runtimes.json:1-66`, `resources/bin/.gitkeep`)**

  * **Evidence:** `resources/runtimes.json` defines pinned URLs, SHA-256 digests, and `target_path` locations for Pandoc `3.1.11` and Node.js `22.23.2` across `linux-x86_64`, `windows-x64`, `darwin-x86_64`, and `darwin-arm64`. However, `scripts/` contains only `configure_branch_protection.sh`, `run_quality_gates.py`, and `verify_mathjax_worker.py`. Nothing downloads, verifies SHA-256 checksums, extracts the archives, or runs `npm ci --omit=dev --prefix resources/mathjax` to populate `resources/bin/` and `resources/mathjax/node` prior to packaging.

  * **Impact:** Any packaged build produced without a staging script will lack bundled Pandoc and Node.js binaries, violating the hermetic offline Markdown/MathJax contract in `docs/packaging.md:11-27`.

* **\[B-03\] Blocker — Four Runtime Resource Paths Bypass `get_runtime_resource_path()` (`interfaces/desktop/app.py:406`, `infrastructure/pdf/staging_workspace.py:248-250`, `infrastructure/persistence/sqlite/migration_runner.py:16`, `infrastructure/logging/setup.py:59`)**

  * **Evidence:**

    1. `interfaces/desktop/app.py:406`: `qml_path = Path(__file__).parent / "qml" / "Main.qml"` instead of `get_runtime_resource_path("interfaces/desktop/qml/Main.qml")`.

    2. `infrastructure/pdf/staging_workspace.py:248-250`: `self.templates_dir = Path(__file__).resolve().parent.parent.parent / "resources" / "templates"` instead of `get_runtime_resource_path("resources/templates")`.

    3. `infrastructure/persistence/sqlite/migration_runner.py:16`: `MIGRATIONS_DIR_DEFAULT = Path(__file__).parent / "migrations"` instead of `get_runtime_resource_path("infrastructure/persistence/sqlite/migrations")`.

  * **Impact:** In frozen builds (`sys.frozen == True`), if Python modules are loaded from a compressed archive or `lib/` subdirectory while data files reside under `sys._MEIPASS` or the executable root, raw `Path(__file__)` parent-climbing resolves to the wrong directory, breaking QML startup, SQLite schema initialization, or PDF preamble staging.

* **\[B-04\] Blocker — Silent Headless Hang When `Main.qml` Is Missing or Fails QML Compilation (`interfaces/desktop/app.py:403-410`)**

  * **Evidence:**

    ```
    def main() -> int:
        app, engine, container = create_app()
        qml_path = Path(__file__).parent / "qml" / "Main.qml"
        if qml_path.exists():
            engine.load(str(qml_path))
        return app.exec()

    ```

    If `qml_path.exists()` is `False` (e.g., QML files omitted from package bundle) or if `engine.load(str(qml_path))` fails due to a missing QtQuick QML plugin (`not engine.rootObjects()`), `main()` still calls `app.exec()`, leaving an invisible background process running indefinitely with exit code `0` on quit instead of shutting down the container and returning a non-zero exit code.

  * **Impact:** Packaged application launch failures cannot be caught by automated smoke tests and leave zombie background threads/processes on user machines.

* **\[B-05\] Blocker — Hardcoded Linux Data Path Across All Platforms Violates `AGENTS.md` Rule 29 (`interfaces/desktop/composition.py:78-86`)**

  * **Evidence:**

    ```
    if db_path is None:
        data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        base_data = Path(data_home) / "polpot"

    ```

    Even though `platformdirs==4.3.6` is pinned in `requirements.txt:23`, `DesktopAppContainer.__init__` hardcodes `~/.local/share/polpot` on Windows and macOS instead of using `platformdirs.user_data_path("polpot", appauthor=False)` when `XDG_DATA_HOME` is unset.

  * **Impact:** Violates `AGENTS.md` Rule 29 (*"Use platform-appropriate application-data directories. Do not hardcode a single Linux path for all platforms"*) and writes user data to non-standard hidden dot-directories on Windows (`C:\Users\<user>\.local\share\polpot`) and macOS.

* **\[B-06\] Blocker — Missing Authoritative Version Source and Missing `LICENSE` File (`pyproject.toml`, `README.md:382`)**

  * **Evidence:** There is no `__version__` attribute in Python code, no `[project].version` in `pyproject.toml`, and no `LICENSE` file in the repository root despite `README.md:382` stating *"See the repository's license file for the applicable license terms."*

  * **Impact:** Releases cannot enforce tag-to-binary version parity (`v0.1.0` vs binary `--version`), and distributing binaries without a `LICENSE` file creates legal/licensing ambiguity.

### 4.2 High-Severity Findings

* **\[H-01\] High — `periodic-validation.yml` Omits Pandoc, Node.js `22.23.2`, and MathJax Worker Dependencies (`periodic-validation.yml:43-115`)**

  * **Evidence:** Neither `test-macos` (`lines 43-74`) nor `test-python-matrix` (`lines 75-115`) runs `actions/setup-node`, `npm ci --prefix resources/mathjax`, `python scripts/verify_mathjax_worker.py`, or installs `pandoc`.

  * **Impact:** During pre-release `v*` tag validation runs, all live MathJax tests (`tests/unit/test_mathjax_client.py:101-109`, `tests/unit/test_mathjax_worker_headless.py:86-112`) silently `pytest.skip(...)`, providing false confidence on macOS and Python 3.10/3.11.

* **\[H-02\] High — Tag-Triggered `periodic-validation.yml` Does Not Gate Release Publication (`periodic-validation.yml:35-37`)**

  * **Evidence:** `periodic-validation.yml` triggers on `push: tags: ['v*']` independently.

  * **Impact:** If a release workflow also triggers on `push: tags: ['v*']`, GitHub Actions starts both workflows concurrently; a failure in `periodic-validation.yml` will not prevent the release workflow from publishing broken artifacts.

* **\[H-03\] High — Unpinned `actions/setup-node@v4` and Unpinned OS-Package Pandoc in `ci.yml` (`ci.yml:109,116,159,165`)**

  * **Evidence:** `ci.yml` pins `actions/checkout` and `actions/setup-python` to full 40-character commit SHAs, but uses mutable tag `actions/setup-node@v4` (`lines 109, 159`). Furthermore, `ci.yml` installs Pandoc via `sudo apt-get install -y ... pandoc` (`line 116`, which installs Pandoc `3.1.3` on Ubuntu 24.04) and `choco install pandoc` (`line 165`, which installs the latest upstream Pandoc on Windows) rather than the project-pinned Pandoc `3.1.11` from `resources/runtimes.json`.

  * **Impact:** Supply-chain inconsistency (mutable action tag) and version drift between CI test runs and the pinned runtime manifest (`resources/runtimes.json`).

* **\[H-04\] High — Curated Prompt Templates Are Never Seeded into SQLite on Clean Installations (`001_initial_schema.sql:13-22`, `interfaces/desktop/composition.py:254-270`)**

  * **Evidence:** `001_initial_schema.sql` creates the `prompts` table with zero rows, and `DesktopAppContainer.initialize()` never seeds `prompts/templates/fa/*.txt`, `prompts/templates/en/*.txt`, or `prompts/pipeline2/unify_markdown.txt`.

  * **Impact:** A first-time desktop user launching a clean installation has zero prompts in the UI prompt selector, and jobs fall back to `"Convert page to markdown"` (`application/services/job_execution.py:205`).

* **\[H-05\] High — Production `requirements.txt` Mixes Legacy Telegram/MySQL Dependencies with Desktop Dependencies (`requirements.txt:1-14`)**

  * **Evidence:** `requirements.txt` includes `python-telegram-bot==21.5` (`line 2`) and `PyMySQL==1.1.1` (`line 13`).

  * **Impact:** Packaging the desktop app from `requirements.txt` bloats the frozen bundle with unused Telegram and MySQL libraries.

### 4.3 Medium & Low-Severity Findings

* **\[M-01\] Medium — `setup_logging()` Defaults to `project_root / "logs"` and Is Not Wired into Desktop Entrypoint (`infrastructure/logging/setup.py:58-65`, `interfaces/desktop/app.py:403-410`)**

  * **Evidence:** `setup_logging()` writes `polpot.log` under `Path(__file__).resolve().parent.parent.parent / "logs"` when `log_dir is None`, and `interfaces/desktop/app.py` never calls `setup_logging()`.

  * **Impact:** Calling `setup_logging()` in a read-only installation directory (`/opt`, `C:\Program Files`, `.app` bundle) raises `PermissionError`, and the desktop app currently lacks persistent rotating file logs with `SecretRedactingFilter` in user data storage.

* **\[M-02\] Medium — Path Mismatch Between `docs/packaging.md` and `resources/runtimes.json`, Plus Missing `resources/entitlements.plist` (`docs/packaging.md:18-20,64`)**

  * **Evidence:** `docs/packaging.md:18-20` documents `resources/bin/linux-x64/pandoc`, `resources/bin/win-x64/pandoc.exe`, and `resources/bin/macos-universal/pandoc`, whereas `resources/runtimes.json:14-60` targets `resources/bin/linux-x86_64/pandoc`, `resources/bin/windows-x64/pandoc.exe`, `resources/bin/darwin-x86_64/pandoc`, and `resources/bin/darwin-arm64/pandoc`. While `PandocBinaryResolver.get_bundled_path()` (`infrastructure/markdown/pandoc_binary.py:100-118`) checks both, `docs/packaging.md` should match `resources/runtimes.json`, and `resources/entitlements.plist` (`docs/packaging.md:64`) is missing from `resources/`.

* **\[M-03\] Medium — Missing Concurrency Cancellation and npm Cache in `ci.yml` (`.github/workflows/ci.yml:11-16,125,175`)**

  * **Evidence:** `ci.yml` has no `concurrency:` block to cancel superseded runs on rapid PR pushes, and `npm ci --prefix resources/mathjax` downloads packages uncached on every Linux and Windows run.

* **\[M-04\] Medium — Application Naming Split (`Polpo` vs `PolpoT`) (`interfaces/desktop/app.py:229-230`, `interfaces/desktop/composition.py:80,210`)**

  * **Evidence:** `app.setApplicationName("PolpoT")`, `keyring_service_name="polpot_desktop"`, and `~/.local/share/polpot` use `PolpoT`, while `README.md` and `ADR-003` use `Polpo`.

  * **Impact:** Must be consciously locked before the first release so user data directories and OS keyring service names remain stable across upgrades.

* **\[L-01\] Low — Missing `CHANGELOG.md` (`Repository Root`)**

* **\[L-02\] Low — `scripts/run_quality_gates.py:25-30` Omits `test_architecture_boundaries.py` and `test_architecture_security.py` from `DEFAULT_ARCHITECTURE_TESTS`**

## 5. Current Packaging and Runtime Dependency Map

| **Component / Asset** | **Source / Version** | **Runtime Resolution Path** | **Packaging Strategy for Release** | **Offline Startup Impact if Missing** |
| **Python Runtime & App Code** | Python `3.12` (`core/`, `application/`, `infrastructure/`, `interfaces/desktop/`) | Frozen executable (`sys.frozen`) | Bundled via **PyInstaller** (`--onedir` bundle) | Fatal (Application cannot start) |
| **PySide6 / Qt6 & QML Plugins** | `PySide6==6.8.0.2` (`QtQuick`, `QtQuick.Controls`, `QtQuick.Layouts`, `QtQuick.Window`, `QtSvg`) | Qt plugin & QML import paths inside frozen bundle | Bundled via PyInstaller PySide6 hooks + QML directory inclusion | Fatal (Application window cannot load) |
| **Desktop QML Views & Dialogs** | `interfaces/desktop/qml/**/*.qml` (30 `.qml` files) | `interfaces/desktop/app.py:406` (must migrate to `get_runtime_resource_path`) | Bundled as data files under `interfaces/desktop/qml/` | Fatal (Blank window / headless exit) |
| **SQLite Schema Migrations** | `infrastructure/persistence/sqlite/migrations/001..004_*.sql` (4 `.sql` files) | `migration_runner.py:16` (must migrate to `get_runtime_resource_path`) | Bundled as data files under `infrastructure/persistence/sqlite/migrations/` | Fatal (Tables missing on first startup) |
| **Default Prompt Templates** | `prompts/templates/{fa,en}/*.txt` & `prompts/pipeline2/unify_markdown.txt` | Must be seeded via `get_runtime_resource_path("prompts")` on first run | Bundled as data files under `prompts/` | Degraded (Empty prompt list on clean install) |
| **Pandoc Binary** | Pinned `3.1.11` (`resources/runtimes.json:6`) | `PandocBinaryResolver`: 1. `resources/bin/<platform>/pandoc[.exe]` -> 2. Settings -> 3. `PATH` | **Bundled inside package** via `scripts/fetch_runtimes.py` verifying SHA-256 | Markdown preview fails if neither bundled nor on `PATH` |
| **Node.js Runtime + MathJax Worker** | Node.js `22.23.2` LTS + `mathjax-full@3.2.2` (`resources/runtimes.json:39`, `resources/mathjax/`) | `resolve_node_executable()`: 1. `POLPO_NODE_PATH` -> 2. `resources/mathjax/node[.exe]` -> 3. `PATH` | **Bundled inside package** (`resources/mathjax/node[.exe]`, `mathjax_worker.js`, `node_modules/`) | Graceful fallback to `MathErrorCard` delegate in preview; app still starts |
| **LaTeX PDF Preamble Template** | `resources/templates/polpo_preamble.tex` | `staging_workspace.py:249` (must migrate to `get_runtime_resource_path`) | Bundled as data file under `resources/templates/polpo_preamble.tex` | PDF export fails with `FileNotFoundError` |
| **Quarto CLI (`>= 1.4.0`)** | External (`ADR-003 D01`) | `QuartoRuntimeDiscovery`: 1. Configured path -> 2. System `PATH` | **External Prerequisite** (Not bundled; guided by `PdfSetupAssistantDialog.qml`) | **Graceful degradation:** App starts and works 100%; only PDF export prompts setup |
| **XeLaTeX (`TinyTeX` / `TeX Live` / `MiKTeX`)** | External (`ADR-003 D01 & D04`) | `QuartoRuntimeDiscovery`: System `PATH` + standard user TinyTeX directories | **External Prerequisite** (Not bundled; consented `tlmgr` recovery for TinyTeX) | **Graceful degradation:** App starts and works 100%; only PDF export prompts setup |
| **User Data (`polpot.db`, `artifacts/`, `credentials.enc`, `logs/`)** | Created at runtime per user | `DesktopAppContainer` (`platformdirs` / `XDG_DATA_HOME`) | Stored in writable OS user-data directory (never inside app install dir) | Created automatically on first launch |

### Why **PyInstaller (`--onedir` mode)** Is the Recommended Packaging Tool

1. **Existing Codebase Support:** `infrastructure/paths.py:16-18` (`get_runtime_resource_path`) is already explicitly written and unit-tested (`tests/unit/test_runtime_paths.py`) around PyInstaller's `sys.frozen` and `sys._MEIPASS` contract!

2. **Startup Performance & Subprocess Execution (`--onedir` vs `--onefile`):** Polpo spawns persistent and frequent child subprocesses (`resources/bin/<platform>/pandoc` and `resources/mathjax/node resources/mathjax/mathjax_worker.js`). In PyInstaller `--onefile` mode, every launch unpacks \~150+ MB of Qt6, Node.js, `node_modules`, and Pandoc into a temporary directory (`_MEIxxxxxx`), adding 3–8 seconds to startup and risking antivirus false positives on Windows when executing unpacked binaries from `%TEMP%`. In `--onedir` mode (packaged as a `.tar.gz` / `.AppImage` on Linux and a `.zip` / Inno Setup installer on Windows), startup is instantaneous and child binaries execute directly from the application directory.

## 6. Release-Scope Decision Matrix & Recommended First-Release Scope

| **Criterion** | **Option A: Source-Only Tagged Release (v0.1.0)** | **Option B: Packaged Release for Tier-1 Platforms Only (Linux x86_64 + Windows x64) \[RECOMMENDED\]** | **Option C: Packaged Release Across Linux x86_64, Windows x64, and macOS ARM64/Universal** |
| **User Installation Experience** | **Poor for end-users:** Requires manual installation of Python 3.12, venv, pip compilers, Node.js 22.23.2, npm, and Pandoc 3.1.11. | **Turnkey on Linux & Windows:** Users download a self-contained archive (`Polpo-v0.1.0-linux-x86_64.tar.gz` or `Polpo-v0.1.0-windows-x64.zip`) with Python, PySide6, Pandoc 3.1.11, and Node.js 22.23.2 + MathJax pre-bundled. | **Turnkey across all 3 OSes**, *if and only if* Apple Developer ID signing and notarization are configured. |
| **Build & Maintenance Complexity** | Minimal (tag push only). | Moderate and deterministic: 1 `fetch_runtimes.py` script + 1 `polpo.spec` built on existing Tier-1 runners (`ubuntu-24.04` and `windows-2025`). | **High:** Requires paid Apple Developer ID certificate secrets, Keychain provisioning in CI, `codesign` inside-out signing (`docs/packaging.md:85-139`), and `xcrun notarytool` stapling. |
| **Qt/QML & Native Dependency Risks** | Low (developer builds locally). | Low–Medium: Controlled via PyInstaller `collect_qml_files` / PySide6 hooks and verified via `--self-test` launch smoke test. | High on macOS without notarization: Unsigned `.app` bundles containing `node` (V8 JIT requires `com.apple.security.cs.allow-jit`) are blocked by macOS Gatekeeper ("Polpo.app is damaged and can't be opened"). |
| **External Runtime Prerequisites** | Python, Node.js, npm, Pandoc, Quarto, XeLaTeX. | **None for core app, OCR, editor, or MathJax preview.** Only **Quarto CLI (>= 1.4.0) + XeLaTeX** remain external optional prerequisites for PDF export (`ADR-003 D01`). | Same as Option B. |
| **Required Automated Smoke Tests** | `pytest -q` on source checkout only. | Post-package `--self-test` smoke test executing the **frozen binary** outside the source tree on both `ubuntu-24.04` and `windows-2025` (verifying SQLite migration, QML engine load, bundled Pandoc probe, and bundled MathJax SVG render). | Same as Option B + macOS `.app` bundle `codesign --verify` and frozen binary `--self-test` on `macos-15`. |
| **Honest Platform Support Claims** | Development source snapshot only. | **Tier-1 Packaged Support:** Ubuntu 24.04 LTS x86_64 (and compatible modern glibc Linux) & Windows 10/11/Server x64 (validated on Windows Server 2025 CI; interactive desktop verified before stable tag). **Tier-2 Source Support:** macOS 15 ARM64. | Cannot honestly claim installable macOS release until Apple Developer ID signing & notarization secrets are provisioned. |

### Recommendation: **Option B (Packaged Release for Tier-1 Linux `x86_64` and Windows `x64`, with Quarto/XeLaTeX as External Optional Prerequisites per ADR-003)**

**Rationale:**

1. Option A fails to deliver a usable desktop product to non-developer users because Polpo's value proposition relies on a synchronized PySide6/QML GUI paired with Pandoc `3.1.11` and Node.js `22.23.2` (`mathjax-full`).

2. Option C is currently blocked on macOS by Apple Gatekeeper: because Node.js `22.23.2` requires V8 JIT entitlements (`com.apple.security.cs.allow-jit`), an unsigned/unnotarized macOS `.app` bundle downloaded from GitHub Releases will be quarantined by macOS Sequoia (`macos-15`). Keeping macOS as a **Tier-2 CI source-validated platform** for `v0.1.0` while shipping **hermetic packaged bundles for Linux `x86_64` and Windows `x64`** delivers immediate end-user value without dishonest platform claims or signing blockers.

3. Per `ADR-003 D01`, **Quarto CLI and XeLaTeX/TinyTeX must remain external prerequisites** guided by Polpo's built-in `PdfSetupAssistantDialog.qml`. Bundling Quarto (\~400 MB) and TeX Live/TinyTeX (\~300 MB–4 GB) would balloon the installer size by 5–10x and violate `ADR-003`.

## 7. Proposed CI/CD and Release Pipeline Architecture

### 7.1 Versioning & Tag Policy

* **Single Authoritative Version Source:** Create `core/version.py` containing `__version__ = "0.1.0"` (pure Python, zero dependencies, compliant with `core/` invariants) and reference it in `pyproject.toml` (`[project] version = "0.1.0"`) and `interfaces/desktop/app.py` (`app.setApplicationVersion(__version__)`, `--version` CLI flag).

* **Tag Convention:** Semantic Versioning prefixed with `v`:

  * **Pre-Release / Release Candidate:** `v0.1.0-rc.1`, `v0.1.0-beta.1` -> Automatically published as a **GitHub Pre-Release** (`prerelease: true`).

  * **Stable Release:** `v0.1.0` -> Published as a **GitHub Latest Release** (`prerelease: false`).

* **Tag-to-Source Verification Gate:** The release workflow must verify that the pushed Git tag (`${GITHUB_REF_NAME#v}`) exactly matches `core/version.py` (`__version__`) and that the tagged commit is an ancestor of `origin/main` (`git merge-base --is-ancestor HEAD origin/main`).

### 7.2 Workflow Trigger & Dependency Architecture Diagram

To eliminate the race condition where `periodic-validation.yml` and a release workflow run independently on `v*` tags (Finding **H-02**), remove `push: tags: ['v*']` from `periodic-validation.yml` (keeping `schedule` and `workflow_dispatch` there) and create a dedicated, atomic `.github/workflows/release.yml` triggered on `push: tags: ['v*']` where **every validation, build, and smoke-test job must succeed before the `publish-release` job can run**:

```
Trigger: git push origin v0.1.0-rc.1 (or v0.1.0)
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Job 1: validate-release-ref (ubuntu-24.04)              │
│  - permissions: contents: read                          │
│  - Verify tag matches core/version.py (__version__)     │
│  - Verify commit is ancestor of origin/main             │
│  - Run Gitleaks secret scan & run_quality_gates.py      │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴───────────────────────────────┐
        ▼                                            ▼
┌──────────────────────────────────────┐   ┌──────────────────────────────────────┐
│ Job 2: tier2-validation-matrix       │   │ Job 3: package-and-smoke-test        │
│ (needs: validate-release-ref)        │   │ (needs: validate-release-ref)        │
│  - macos-15 (Python 3.12 + Pandoc    │   │  Matrix:                             │
│    + Node 22.23.2 + MathJax)         │   │  1. ubuntu-24.04 (linux-x86_64)      │
│  - ubuntu-24.04 (Python 3.10 & 3.11  │   │  2. windows-2025 (windows-x64)       │
│    + Pandoc + Node 22.23.2 + MathJax)│   │  Steps per OS:                       │
│  - Runs verify_mathjax_worker.py     │   │  - Install desktop deps + PyInstaller│
│    and full pytest -q                │   │  - Run scripts/fetch_runtimes.py     │
└────────────────────┬─────────────────┘   │    (SHA-256 verified Pandoc + Node)  │
                     │                     │  - npm ci --omit=dev in mathjax/     │
                     │                     │  - Build PyInstaller bundle          │
                     │                     │  - Copy bundle to clean temp dir     │
                     │                     │    outside repo & run frozen binary: │
                     │                     │    Polpo --self-test                 │
                     │                     │  - Archive (.tar.gz / .zip) +        │
                     │                     │    generate .sha256 checksum         │
                     │                     │  - Upload build artifact             │
                     │                     └─────────────────┬────────────────────┘
                     └──────────────────┬────────────────────┘
                                        ▼
             ┌────────────────────────────────────────────────────┐
             │ Job 4: publish-release (ubuntu-24.04)              │
             │ (needs: [tier2-validation-matrix,                  │
             │          package-and-smoke-test])                  │
             │  - permissions: contents: write,                   │
             │                 id-token: write,                   │
             │                 attestations: write                │
             │  - Download Linux & Windows archives + .sha256     │
             │  - Combine SHA256SUMS.txt & verify checksums       │
             │  - Generate build provenance attestation           │
             │    (actions/attest-build-provenance)               │
             │  - Publish atomic GitHub Release (softprops/       │
             │    action-gh-release pinned by SHA, prerelease     │
             │    auto-detected if tag contains '-')              │
             └────────────────────────────────────────────────────┘

```

### 7.3 Frozen Binary `--self-test` Smoke Test Contract

Unit tests running against source files do not prove that a packaged PyInstaller binary works. Adding a deterministic `--self-test` CLI flag to `interfaces/desktop/app.py` allows CI to execute the compiled binary in a clean temporary directory (`QT_QPA_PLATFORM=offscreen`) with system `pandoc` and `node` removed from `PATH`, verifying:

1. `sys.frozen` is `True` and `get_runtime_resource_path()` resolves bundled assets inside the frozen distribution.

2. `DesktopAppContainer.initialize()` applies all 4 SQLite migrations (`001`..`004`) and seeds the default prompts into a temporary SQLite database.

3. `QQmlApplicationEngine` loads `Main.qml` without QML import/plugin errors and `len(engine.rootObjects()) == 1`.

4. `PandocBinaryResolver().resolve()` resolves the **bundled** Pandoc `3.1.11` binary inside the package and parses a sample Markdown + Persian + math string into AST with `data-pos`.

5. `MathJaxProcessSupervisor` spawns the **bundled** Node.js `22.23.2` binary and `mathjax_worker.js`, completes the `"ping" -> "pong"` handshake, and renders `$E=mc^2$` into a valid self-contained `<svg>`.

6. `StagingWorkspaceManager` stages `polpo_preamble.tex` without `FileNotFoundError`.

7. The container shuts down cleanly with exit code `0`.

## 8. Prioritized Implementation Roadmap & Ticket Breakdown

Below is the prioritized sequence of **7 small, independently reviewable tickets** required to reach First Desktop Release readiness.

| **Order** | **Ticket ID** | **Title** | **Severity Addressed** | **Depends On** |
| **1** | **TICK-REL-01** | Authoritative Version Single-Source (`core/version.py`), `pyproject.toml` Metadata, `LICENSE`, and `CHANGELOG.md` | **B-06, H-05, L-01** | None (Execute First) |
| **2** | **TICK-REL-02** | Frozen-Safe Resource Path Resolution, Cross-Platform User Data/Log Directories (`AGENTS.md` Rule 29), and Fail-Fast QML Startup + `--self-test` Mode | **B-03, B-04, B-05, M-01, M-04** | TICK-REL-01 |
| **3** | **TICK-REL-03** | Idempotent Default Prompt Seeding on First-Run SQLite Initialization | **H-04** | TICK-REL-02 |
| **4** | **TICK-REL-04** | Deterministic Runtime Binary Acquisition & Verification Script (`scripts/fetch_runtimes.py`) | **B-02, M-02** | TICK-REL-01 |
| **5** | **TICK-REL-05** | PyInstaller Desktop Packaging Spec (`packaging/polpo.spec`) & Reproducible Package Builder (`scripts/build_package.py`) | **B-01, H-05** | TICK-REL-02, TICK-REL-03, TICK-REL-04 |
| **6** | **TICK-REL-06** | Harden `.github/workflows/ci.yml` and `.github/workflows/periodic-validation.yml` (Action SHA Pinning, Pinned Pandoc/Node Setup, Quality Gates) | **H-01, H-02, H-03, M-03, L-02** | TICK-REL-04 |
| **7** | **TICK-REL-07** | End-to-End Release Pipeline (`.github/workflows/release.yml`) with Tag Verification, Smoke-Tested Builds, Checksums, and Atomic Publication | **All Release Architecture Requirements** | TICK-REL-05, TICK-REL-06 |

## 9. Explicit Acceptance Criteria and Test Requirements per Ticket

### TICK-REL-01: Authoritative Version Single-Source, `pyproject.toml`, `LICENSE`, and `CHANGELOG.md`

* **Scope:**

  1. Create `core/version.py` defining `__version__ = "0.1.0"` and `APP_NAME = "Polpo"`.

  2. Expand `pyproject.toml` with `[project]` metadata (`name = "polpo"`, `dynamic = ["version"]` or `version = "0.1.0"`, `requires-python = ">=3.10"`).

  3. Create `requirements-desktop.txt` containing only the desktop runtime dependencies (excluding `python-telegram-bot==21.5` and `PyMySQL==1.1.1`), while keeping `requirements.txt` intact (or having `requirements.txt` include `-r requirements-desktop.txt` plus legacy Telegram dependencies) so existing CI and legacy tests remain 100% compatible.

  4. Add `LICENSE` and `CHANGELOG.md` to the repository root.

* **Acceptance Criteria & Tests:**

  * New unit test `tests/unit/test_version_consistency.py` asserts `core.version.__version__` matches `pyproject.toml` and follows SemVer regex.

  * `core/version.py` has zero third-party or I/O imports (passes `scripts/run_quality_gates.py`).

### TICK-REL-02: Frozen-Safe Resource Paths, Cross-Platform User Directories, and `--self-test` / `--version` CLI Flags

* **Scope:**

  1. Update `infrastructure/pdf/staging_workspace.py:248-250` to default `self.templates_dir` to `get_runtime_resource_path("resources/templates")`.

  2. Update `infrastructure/persistence/sqlite/migration_runner.py:16` to resolve `MIGRATIONS_DIR_DEFAULT` via `get_runtime_resource_path("infrastructure/persistence/sqlite/migrations")`.

  3. Update `interfaces/desktop/app.py:403-413` to:

     * Support `--version` (prints `Polpo {__version__}` and exits `0`) and `--self-test` (runs full initialization, QML load, bundled Pandoc AST parse, MathJax SVG render, preamble staging check, and clean shutdown in an isolated temporary directory, exiting `0` on success or `1` on failure).

     * Resolve `Main.qml` via `get_runtime_resource_path("interfaces/desktop/qml/Main.qml")`.

     * Check `if not qml_path.is_file() or not engine.rootObjects():` after `engine.load(str(qml_path))`, shutting down the container and returning exit code `1` instead of hanging in `app.exec()`.

     * Initialize rotating file logging in the user data directory (`base_data / "logs"`).

  4. Update `interfaces/desktop/composition.py:78-86` to resolve `base_data` as:

     * `Path(os.environ["XDG_DATA_HOME"]) / "polpot"` when `XDG_DATA_HOME` is explicitly set (preserving Linux/test override behavior),

     * Otherwise `platformdirs.user_data_path("polpot", appauthor=False)` on Windows/macOS and `~/.local/share/polpot` on POSIX.

* **Acceptance Criteria & Tests:**

  * Unit tests in `tests/unit/test_runtime_paths.py` and `tests/unit/test_desktop_bootstrap_integration.py` verifying:

    * Simulated `sys.frozen = True` and `sys._MEIPASS` resolution for `StagingWorkspaceManager`, `SQLiteMigrationRunner`, and `Main.qml`.

    * `main(["--version"])` exits `0` and prints `__version__`.

    * `main(["--self-test"])` exits `0` under `QT_QPA_PLATFORM=offscreen`.

    * Missing `Main.qml` causes `main()` to return `1` after invoking `container.shutdown()` without calling `app.exec()`.

### TICK-REL-03: Idempotent Default Prompt Seeding on First-Run SQLite Initialization

* **Scope:**

  1. Resolve prompt template files via `get_runtime_resource_path("prompts/...")`.

  2. During `DesktopAppContainer.initialize()`, if `uow.prompts.list_all()` is empty (clean installation), seed the default Pipeline 1 (`prompts/templates/fa/stem_transcription.txt`, `prompts/templates/en/stem_handwritten_transcription.txt`, etc.) and Pipeline 2 (`prompts/pipeline2/unify_markdown.txt`) prompts into SQLite.

* **Acceptance Criteria & Tests:**

  * Unit test verifying that initializing `DesktopAppContainer` on an empty SQLite database populates default `pipeline1` and `pipeline2` prompts, and re-running `initialize()` does not duplicate prompts if the user has edited or deleted them.

### TICK-REL-04: Deterministic Runtime Binary Acquisition Script (`scripts/fetch_runtimes.py`)

* **Scope:**

  1. Create `scripts/fetch_runtimes.py` that reads `resources/runtimes.json`, downloads the platform-matched Pandoc `3.1.11` and Node.js `22.23.2` archives, strictly verifies their SHA-256 checksums against `resources/runtimes.json`, extracts the binaries to `target_path` (`resources/bin/<platform>/pandoc[.exe]` and `resources/mathjax/node[.exe]`), sets `+x` permissions on POSIX, and optionally runs `npm ci --omit=dev --prefix resources/mathjax`.

  2. Add `resources/entitlements.plist` (matching `docs/packaging.md:68-83`) and align directory names in `docs/packaging.md:18-20` with `resources/runtimes.json`.

* **Acceptance Criteria & Tests:**

  * Unit test `tests/unit/test_fetch_runtimes_script.py` using local synthetic `.tar.gz`/`.zip` archives to verify SHA-256 verification, mismatch rejection, path extraction, and `+x` permission setting without network access.

### TICK-REL-05: PyInstaller Desktop Packaging Spec (`packaging/polpo.spec`) & Build Runner (`scripts/build_package.py`)

* **Scope:**

  1. Create `packaging/polpo.spec` and `scripts/build_package.py` to build a self-contained `--onedir` desktop bundle from `interfaces/desktop/app.py` using `requirements-desktop.txt`.

  2. Explicitly bundle:

     * `interfaces/desktop/qml/` -> `interfaces/desktop/qml/`

     * `infrastructure/persistence/sqlite/migrations/*.sql` -> `infrastructure/persistence/sqlite/migrations/`

     * `resources/templates/polpo_preamble.tex` -> `resources/templates/`

     * `resources/runtimes.json` -> `resources/`

     * `resources/bin/<platform>/pandoc[.exe]` -> `resources/bin/<platform>/`

     * `resources/mathjax/node[.exe]`, `mathjax_worker.js`, `package.json`, `node_modules/` -> `resources/mathjax/`

     * `prompts/` -> `prompts/`

     * `LICENSE`, `README.md`, `CHANGELOG.md`

  3. Exclude `tests/`, `handlers/`, `interfaces/telegram/`, `database/`, `python-telegram-bot`, and `pymysql`.

  4. Produce deterministic archive names:

     * Linux: `Polpo-v{version}-linux-x86_64.tar.gz` + `.sha256`

     * Windows: `Polpo-v{version}-windows-x64.zip` + `.sha256`

* **Acceptance Criteria & Tests:**

  * Unit test verifying `scripts/build_package.py` preflight checks (fails fast if bundled `pandoc`, `node`, `node_modules/mathjax-full`, `Main.qml`, `polpo_preamble.tex`, or SQLite `.sql` migrations are missing before invoking PyInstaller).

### TICK-REL-06: Harden `.github/workflows/ci.yml` and `.github/workflows/periodic-validation.yml`

* **Scope:**

  1. In `.github/workflows/ci.yml`:

     * Pin `actions/setup-node` to its immutable commit SHA (`# v4.x.x`).

     * Add `concurrency: { group: ci-${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }} }`.

     * Use `scripts/fetch_runtimes.py` (with GitHub Actions cache keyed on `hashFiles('resources/runtimes.json', 'resources/mathjax/package-lock.json')`) so CI tests against the exact pinned Pandoc `3.1.11` and Node.js `22.23.2` binaries defined in `resources/runtimes.json`.

  2. In `.github/workflows/periodic-validation.yml`:

     * Add `scripts/fetch_runtimes.py` and `npm ci --prefix resources/mathjax` + `python scripts/verify_mathjax_worker.py` to both `test-macos` and `test-python-matrix` so live MathJax and Pandoc tests actually execute instead of skipping.

     * Remove `push: tags: ['v*']` from `periodic-validation.yml` (moving tag-triggered gating into `release.yml`).

  3. In `scripts/run_quality_gates.py`:

     * Add `tests/unit/test_architecture_boundaries.py` and `tests/unit/test_architecture_security.py` to `DEFAULT_ARCHITECTURE_TESTS`.

* **Acceptance Criteria & Tests:**

  * `test_quality_gates_script.py` passes; CI workflows pass on Linux, Windows, and macOS with zero skipped MathJax worker tests.

### TICK-REL-07: End-to-End Release Pipeline (`.github/workflows/release.yml`)

* **Scope:**

  1. Create `.github/workflows/release.yml` triggered on `push: tags: ['v*']` and `workflow_dispatch` (dry-run build mode without release publication).

  2. Implement the 4-job architecture from Section 7.2:

     * `validate-release-ref` (`contents: read`): Verifies tag matches `core/version.py`, commit is on `origin/main`, Gitleaks passes, and `run_quality_gates.py` passes.

     * `tier2-validation-matrix` (`contents: read`, `needs: validate-release-ref`): Runs `macos-15` (Python 3.12) and `ubuntu-24.04` (Python 3.10, 3.11) with full Pandoc `3.1.11` + Node `22.23.2` + MathJax verification.

     * `package-and-smoke-test` (`contents: read`, `needs: validate-release-ref`): Builds PyInstaller bundles on `ubuntu-24.04` and `windows-2025`, copies each built archive to an isolated temp directory outside the checkout, unpacks it, strips system `pandoc`/`node` from `PATH`, and runs `./Polpo --self-test` (`Polpo.exe --self-test` on Windows) under `QT_QPA_PLATFORM=offscreen`. Uploads archives and `.sha256` files on pass.

     * `publish-release` (`needs: [tier2-validation-matrix, package-and-smoke-test]`, `if: startsWith(github.ref, 'refs/tags/v')`): Grants `contents: write`, `id-token: write`, `attestations: write` **only** to this job, generates `SHA256SUMS.txt`, attests build provenance via `actions/attest-build-provenance`, and publishes the GitHub Release atomically (`prerelease: true` if tag contains `-`, else `false`).

## 10. Ticket Dependencies, Risks, Open Decisions, and Recommended Order

### Dependency Graph

```
TICK-REL-01 (Version, pyproject.toml, LICENSE, requirements-desktop.txt)
   ├──► TICK-REL-02 (Frozen-safe paths, platformdirs, fail-fast QML, --self-test)
   │       └──► TICK-REL-03 (Default prompt seeding on clean SQLite init)
   │               │
   └──► TICK-REL-04 (scripts/fetch_runtimes.py + SHA-256 verification)
           │       │
           ├───────┴──► TICK-REL-05 (packaging/polpo.spec + scripts/build_package.py)
           │                   │
           └──► TICK-REL-06 (Harden ci.yml & periodic-validation.yml)
                       │       │
                       └───────┴──► TICK-REL-07 (.github/workflows/release.yml)

```

### Release Readiness — Decisions Approved

The repository owner has formally reviewed and approved the open product decisions as follows:

1. **License Direction and Legal Obligations (`TICK-REL-01`):**
   - **Decision:** Use `AGPL-3.0-only` for Polpo.
   - **Legal Obligations & Context:** Polpo links with and bundles runtime components including `PyMuPDF==1.24.10`, which is licensed under the GNU Affero General Public License v3 (AGPLv3) by Artifex Software, Inc. In the absence of a commercial license from Artifex, distributing Polpo requires compliance with AGPLv3. Adopting `AGPL-3.0-only` for Polpo guarantees legal compatibility.
   - **Redistribution Obligations:** Complete corresponding source code must be made available under `AGPL-3.0-only` to all users receiving binary packages or interacting with the software remotely. All copyright notices, warranty disclaimers, and license notices must be preserved.
   - **Third-Party License Audit & Notice Requirement:** Prior to release publication, all runtime and bundled dependency licenses (including PySide6 under LGPLv3, Pandoc under GPLv2+, MathJax under Apache-2.0, Pillow under HPND, cryptography under Apache-2.0/BSD-3-Clause, keyring under MIT, etc.) must be audited, and required third-party license notices and attributions must be included in `LICENSE` or accompanying release documentation.
   - **Permissive/Proprietary License Constraint:** If the repository owner or project requirements ever demand a permissive (e.g. MIT/Apache-2.0) or commercial/proprietary license for Polpo, modifying the license file is strictly **blocked** until `PyMuPDF` is commercially licensed or replaced with a permissively licensed alternative (e.g. `pypdf`), and the full legal implications have been formally re-audited.

2. **Persistent Identity & Data Continuity (`TICK-REL-02`):**
   - **Decision:** Preserve the `polpot` data-directory slug and `polpot_desktop` keyring service slug for backward compatibility. Use `Polpo` for the executable and user-facing application identity.
   - **Migration & Safety:** Implement platform-aware directories using `platformdirs` while providing safe legacy-data discovery and migration mechanisms so that existing SQLite databases (`polpot.db`), artifacts, logs, and stored credentials (`polpot_desktop`) are neither stranded nor overwritten across upgrades.

3. **First Release Strategy & Quality Gates (`TICK-REL-07`):**
   - **Decision:** Target `v0.1.0-rc.1` as a GitHub Pre-Release before publishing stable `v0.1.0`.
   - **Release Gate Rules:** Do not tag or publish `v0.1.0-rc.1` or `v0.1.0` merely because version metadata has been added. Do not publish the RC until all release tickets (`TICK-REL-01` through `TICK-REL-07`) are implemented, CI is green, Linux and Windows packages pass artifact and runtime smoke checks, and off-repository smoke tests succeed.
   - **Interactive Acceptance:** Mandatory interactive acceptance checks on Ubuntu and Windows 10/11 desktop environments must succeed before promoting `v0.1.0-rc.1` to stable `v0.1.0`.

## 11. Precise List of Files Expected to Change per Phase

| **Ticket** | **Files to Create** | **Files to Modify** |
| **TICK-REL-01** | `core/version.py`  `requirements-desktop.txt`  `LICENSE`  `CHANGELOG.md`  `tests/unit/test_version_consistency.py` | `pyproject.toml`  `requirements.txt` |
| **TICK-REL-02** | `tests/unit/test_desktop_self_test.py` | `interfaces/desktop/app.py`  `interfaces/desktop/composition.py`  `infrastructure/pdf/staging_workspace.py`  `infrastructure/persistence/sqlite/migration_runner.py`  `infrastructure/logging/setup.py`  `tests/unit/test_runtime_paths.py` |
| **TICK-REL-03** | `application/services/prompt_seeding_service.py` (or seeding helper in `PromptService`)  `tests/unit/test_prompt_seeding.py` | `interfaces/desktop/composition.py` |
| **TICK-REL-04** | `scripts/fetch_runtimes.py`  `resources/entitlements.plist`  `tests/unit/test_fetch_runtimes_script.py` | `docs/packaging.md`  `.gitignore` |
| **TICK-REL-05** | `packaging/polpo.spec`  `scripts/build_package.py`  `tests/unit/test_build_package_preflight.py` | `.gitignore` |
| **TICK-REL-06** | *(None)* | `.github/workflows/ci.yml`  `.github/workflows/periodic-validation.yml`  `scripts/run_quality_gates.py`  `tests/unit/test_quality_gates_script.py` |
| **TICK-REL-07** | `.github/workflows/release.yml`  `tests/unit/test_release_workflow_contract.py` | `README.md`  `docs/packaging.md` |

## 12. Final Go/No-Go Checklist for Publishing the First Release

Before pushing `v0.1.0-rc.1` or `v0.1.0` to `origin`, every item below must be checked off:

* \[ \] **1. Single Version Authority:** `core/version.py` (`__version__`), `pyproject.toml`, `CHANGELOG.md`, and the target Git tag (`vX.Y.Z`) all match identically.

* \[ \] **2. License & Legal Clarity:** `LICENSE` is present in the repository root and bundled inside the release archive, compatible with `PySide6` (LGPLv3) and `PyMuPDF` licensing terms.

* \[ \] **3. Clean-Machine Path Safety:** `Main.qml`, `polpo_preamble.tex`, SQLite `.sql` migrations, and `prompts/` are all resolved via `get_runtime_resource_path()`, and user data/logs write exclusively to OS user-data directories (`platformdirs` / `XDG_DATA_HOME`).

* \[ \] **4. Default Prompts Seeded:** Launching the packaged binary against a brand-new SQLite database automatically seeds the curated Persian and English STEM transcription and Pipeline 2 prompts.

* \[ \] **5. Hermetic Bundled Runtimes:** `scripts/fetch_runtimes.py` has populated SHA-256-verified Pandoc `3.1.11` and Node.js `22.23.2` + `mathjax-full@3.2.2` inside the package bundle.

* \[ \] **6. Frozen Binary `--self-test` Gate Passed:** The compiled PyInstaller binary passes `./Polpo --self-test` (`Polpo.exe --self-test`) on both `ubuntu-24.04` and `windows-2025` in an isolated directory outside the repository with system `pandoc` and `node` stripped from `PATH`.

* \[ \] **7. Tier-1 & Tier-2 Test Matrices Green:** All 4 required `ci.yml` checks (`secret-scan`, `quality-gates`, `test-linux`, `test-windows`) and all `tier2-validation-matrix` checks (`macos-15`, Python `3.10`/`3.11`) pass on the exact tagged SHA with zero skipped MathJax worker tests.

* \[ \] **8. Interactive Desktop Verification (Pre-Stable Gate):** The `v0.1.0-rc.1` archives have been manually downloaded and launched on a real Ubuntu desktop and a real Windows 10/11 desktop to confirm window rendering, theme switching, math SVG display, and the Quarto/XeLaTeX PDF Setup Assistant dialog.
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-rc.2] - 2026-11-01

### Added
- **Pipeline 2 Desktop Integration & Unification:** Integrated two-stage multi-page document transcription and unification into the desktop runtime, enabling seamless transition from individual page processing to complete document assembly.
- **Pipeline 2 Artifact Lineage & OCC Publication:** Implemented SQLite migration `005` (`005_pipeline2_source_version.sql`) tracking `source_version` lineage on `pipeline2_jobs` (ADR-004); added optimistic concurrency control (OCC) to canonical document adoption in `DocumentPublicationService` and editor panes to prevent overwriting concurrent user edits.
- **Stabilization Roadmap & Test Baseline:** Established comprehensive reproducible testing baseline and persistent stabilization roadmap (`docs/quality/stabilization-roadmap.md`).

### Changed
- **Desktop Theme Tokens & Branding Reconciliation:** Reconciled application branding to `Polpo`, removed raw hex color hardcoding across QML in favor of centralized theme tokens, and enforced WCAG 2.1 AA contrast compliance across light and dark semantic themes.
- **Theme Lifecycle & Startup Palette Hardening:** Verified theme initialization and palette application at application startup, eliminated fallback theme branches, wired accessible focus rings across all interactive controls, and ensured theme switches propagate cleanly to MathJax rendering.
- **Developer Documentation & Comment Translation:** Translated developer-facing code comments and docstrings across the codebase to English per repository standing rules, reconciled public documentation with desktop-first reality, hardened documentation contract tests, and formalized agent skill routing guides.

### Fixed
- **Cross-Process Recovery Locking:** Hardened startup recovery with advisory cross-process database file locking and `BEGIN IMMEDIATE` transactions, preventing concurrent instances from racing during database recovery.
- **Pipeline 2 Persistence & Cooperative Cancellation:** Implemented atomic SQLite job claiming (`claim_next_pending`), worker concurrency safety, cooperative cancellation checkpoints, and graceful recovery for interrupted Pipeline 2 processing.
- **Markdown Editor OCC Synchronization:** Synchronized editor state on optimistic concurrency rejections and isolated refinement lifecycle state during parallel edits.
- **Cross-Platform Subprocess & Windows Flags:** Enforced `CREATE_NO_WINDOW` flags on Windows background subprocesses to prevent console window flashing, hardened headless `--self-test` coverage, and resolved test teardown concurrency races.
- **Data Migration Artifact Remapping:** Isolated artifact path remapper from process working directory and reliably remapped artifact references during legacy data migration.

### Security
- **CI Hardening & Node 24 Migration:** Migrated GitHub Actions workflows to Node 24 runners, pinned actions to immutable full-length commit hashes, eliminated unsafe workflow expression interpolations, and added automated workflow security contract scanners.
- **Secret Isolation & Allowlist Governance:** Audited codebase to guarantee zero plaintext secret leakage across entities, serialization, and logs; allowlisted synthetic test canaries in Gitleaks scanner configuration.

### Packaging
- **Universal Linux Installer & Updater:** Single-command user-space installer and update manager (`install.sh`, `polpo-update`, `polpo-uninstall`) with zero root/sudo requirement, advisory concurrency locking, sidecar SHA-256 verification, path-traversal prevention, atomic staging and directory switching, bounded version retention, and automatic rollback on failure.
- **Distro Dependency Helper:** Distro-aware system dependency diagnostic and resolution tool (`scripts/polpo_linux_deps.sh`, `polpo-deps`) supporting Debian/Ubuntu, Fedora, Arch Linux, openSUSE, and unknown glibc distros. Covers required Qt GUI libraries and optional PDF export toolchains (Quarto, XeLaTeX, poppler-utils, typst, weasyprint) with `--dry-run` and interactive consent prompts.
- **Release Packaging Enhancements:** Stable unversioned release asset aliases (`polpo-linux-x86_64.tar.gz`, `polpo-windows-x64.zip`) and sidecars generated via `scripts/build_package.py --create-alias`. Updated Linux packaging runner baseline to `ubuntu-22.04` to ensure `glibc >= 2.35` compatibility across Debian 12+, Ubuntu 22.04+, and modern distributions.

## [0.1.0-rc.1] - 2026-10-16

### Added
- **Desktop Application:** Modern desktop-first, local-first PySide6/QtQuick user interface with bilingual Persian RTL and English support.
- **Authoritative Version Metadata:** Single-source version definition in `core/version.py` (`0.1.0`), PEP 621 packaging metadata in `pyproject.toml`, and desktop-only dependencies in `requirements-desktop.txt` (`TICK-REL-01`).
- **Licensing & Legal Compliance:** Repository licensed under GNU Affero General Public License v3 (`AGPL-3.0-only`) with comprehensive third-party notices covering PyMuPDF (AGPLv3), PySide6 (LGPLv3), Pandoc (GPLv2+), and MathJax (Apache 2.0).
- **OCR & Document Transcription:** Two-stage processing pipeline supporting single-page transcription (Pipeline 1) and multi-page markdown unification (Pipeline 2) via Google Gemini and OpenAI vision models.
- **MathJax & Pandoc Subsystem:** Live supervised MathJax 3 daemon rendering mathematical expressions to high-fidelity SVGs, alongside Pandoc AST parsing with syntax highlighting and source position tracking.
- **PDF Export via Quarto & XeLaTeX:** Complete document export pipeline (`ADR-003`) supporting bilingual English and Persian typography, TikZ graphics rendering, and interactive setup assistant dialogs.
- **Local Persistence & Security:** Fully offline SQLite persistence with versioned migrations (`001` through `004`), OS Keyring integration with PBKDF2/Fernet encrypted passphrase fallback, and zero plaintext secret leakage across logs and serialization.
- **Cooperative Concurrency:** Transactional SQLite job claiming, non-blocking background workers on `QThreadPool`, and cooperative cancellation boundaries.
- **Packaging & Release Infrastructure:**
  - TICK-REL-02: Frozen-safe runtime resource resolution in `infrastructure/paths.py`, XDG cross-platform user directories via `platformdirs`, fail-fast QML startup, and `--self-test` headless sanity check mode (`interfaces/desktop/self_test.py`).
  - TICK-REL-03: Idempotent default prompt template seeding into clean SQLite databases on first run (`application/services/prompt_seeding_service.py`).
  - TICK-REL-04: Deterministic runtime binary acquisition script (`scripts/fetch_runtimes.py`) for pinned Pandoc and Node.js MathJax worker runtimes.
  - TICK-REL-05: Desktop PyInstaller standalone packaging specification (`packaging/polpo.spec`) and automated archive builder (`scripts/build_package.py`).
  - TICK-REL-06: Hardened continuous integration workflows, pre-release matrices, and quality gates runner (`scripts/run_quality_gates.py`).
  - TICK-REL-07: Cryptographically attested GitHub Actions release pipeline (`.github/workflows/release.yml`) for Linux and Windows desktop bundles.

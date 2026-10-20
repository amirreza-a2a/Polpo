# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Universal Linux Installer & Updater:** Single-command user-space installer and update manager (`install.sh`, `polpo-update`, `polpo-uninstall`) with zero root/sudo requirement, advisory concurrency locking, sidecar SHA-256 verification, path-traversal prevention, atomic staging and directory switching, bounded version retention, and automatic rollback on failure.
- **Distro Dependency Helper:** Distro-aware system dependency diagnostic and resolution tool (`scripts/polpo_linux_deps.sh`, `polpo-deps`) supporting Debian/Ubuntu, Fedora, Arch Linux, openSUSE, and unknown glibc distros. Covers required Qt GUI libraries and optional PDF export toolchains (Quarto, XeLaTeX, poppler-utils, typst, weasyprint) with `--dry-run` and interactive consent prompts.
- **Release Packaging Enhancements:** Stable unversioned release asset aliases (`polpo-linux-x86_64.tar.gz`, `polpo-windows-x64.zip`) and sidecars generated via `scripts/build_package.py --create-alias`. Updated Linux packaging runner baseline to `ubuntu-22.04` to ensure `glibc >= 2.35` compatibility across Debian 12+, Ubuntu 22.04+, and modern distributions.

### Planned
- TICK-REL-02: Frozen-safe resource paths, cross-platform user directories via `platformdirs`, fail-fast QML startup, and `--self-test` mode.
- TICK-REL-03: Idempotent default prompt seeding on first-run SQLite initialization.
- TICK-REL-04: Deterministic runtime binary acquisition script (`scripts/fetch_runtimes.py`).
- TICK-REL-05: PyInstaller desktop packaging spec (`packaging/polpo.spec`) and builder (`scripts/build_package.py`).
- TICK-REL-06: Hardened CI workflows and quality gates runner.
- TICK-REL-07: End-to-end GitHub Actions release workflow (`.github/workflows/release.yml`) targeting `v0.1.0-rc.1`.

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

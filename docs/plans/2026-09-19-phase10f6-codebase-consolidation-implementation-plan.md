# Phase 10F.6 Implementation Plan: Codebase Consolidation & Documentation Modernization

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a safe, dependency-aware, forensic consolidation of the PolpoT repository following Phase 10F.5 closure: remediate credential exposure and delete root junk, unify the bifurcated DTO packages into `application/dto/`, reorganize documentation into standard hierarchies, author the canonical `CONTEXT.md`, modernize `README.md` for the desktop product, normalize technical comments to English, and update production dependencies in `requirements.txt`.

**Architecture:** PolpoT is a desktop-first, local-first, serverless embedded application with PySide6/QML, SQLite WAL persistence, OS Keyring, and outbound BYOK AI calls. This plan preserves all Clean Architecture boundaries (`core/` pure Python, `application/` port-driven, presentation controllers isolated from storage/keyring), leaves the frozen Telegram transport intact, and touches zero product features.

**Tech Stack:** Python 3.10+, PySide6, SQLite3 WAL, OS Keyring / FernetPBKDF2, PyMuPDF, markdown-it-py 4.2.0, pytest.

**Spec:** Forensic Codebase Audit Report (Phase 10F.6), `AGENTS.md` Standing Rules.

## Global Constraints

- Desktop-first, local-first, serverless architecture: zero backend servers, FastAPI, or loopback HTTP listeners (AGENTS.md Rule 2 & 3).
- Clean Architecture inward dependency direction: `core/` has 0 imports of Qt, PySide6, SQLite, Keyring, or AI vendor SDKs; `application/` has 0 presentation or concrete infrastructure imports (AGENTS.md Rule 6).
- Presentation controllers must not import sqlite3, pymysql, keyring, or AI vendor SDKs directly (AGENTS.md Rule 6.3).
- SQLite WAL, Unit-of-Work transactions, and Optimistic Concurrency Control (OCC) invariants remain unchanged (AGENTS.md Rule 8 & 13).
- Markdown canonical version advancement remains exclusively through `MarkdownEditorService.commit_source_text()` (Phase 10F.1 invariant).
- Historical canonical Markdown artifacts (`output_{job_id}_v{N}.md`) remain strictly immutable.
- Frozen Telegram transport (`handlers/`, `main.py`, `interfaces/telegram/`, `infrastructure/composition.py`, MySQL persistence) remains untouched unless explicitly isolated (AGENTS.md Rule 4 & 30).
- All technical code documentation, comments, and docstrings must be in English (AGENTS.md Rule 20).
- No speculative abstractions or churn without forensic justification (AGENTS.md Rule 19 & 24).
- Verification before completion: all 822 automated tests must continue passing without regressions.

---

## Workstream Overview & Dependency Graph

```text
Workstream 1: Safety & Workspace Baseline (Tasks 1-2)
  ├── Task 1: Track Phase 10F.5 planning & design artifacts
  └── Task 2: Credential rotation & repository junk removal

Workstream 2: DTO & Codebase Consolidation (Tasks 3-4)
  ├── Task 3: Consolidate DTO package into canonical application/dto/ & remove application/dtos/
  └── Task 4: Add missing package __init__.py initializers & fix inverted infrastructure imports

Workstream 3: Documentation Reorganization & CONTEXT.md (Tasks 5-7)
  ├── Task 5: Reorganize docs/ directory hierarchy & archive historical root plans
  ├── Task 6: Author canonical CONTEXT.md domain glossary & seam map
  └── Task 7: Modernize README.md for desktop review workspace & diff3 three-way merge

Workstream 4: Code Quality, Language & Dependency Normalization (Tasks 8-9)
  ├── Task 8: Normalize technical comments and docstrings from Persian to English
  └── Task 9: Clean requirements.txt (add markdown-it-py, prune nest-asyncio) & final verification
```

---

## Detailed Task Breakdown

### Task 1: Track Phase 10F.5 Planning & Design Artifacts

**Files:**
- Untracked: `docs/plans/2026-09-18-phase10f5-codebase-design.md`
- Untracked: `docs/plans/2026-09-18-phase10f5-three-way-merge-implementation-plan.md`

**Interfaces:**
- Consumes: Existing untracked working tree files produced during Phase 10F.5 planning.
- Produces: Clean git status with Phase 10F.5 engineering records tracked in version control.

- [ ] **Step 1: Verify git status contains the two untracked planning files**

```bash
git status --porcelain
```
Expected:
`?? docs/plans/2026-09-18-phase10f5-codebase-design.md`
`?? docs/plans/2026-09-18-phase10f5-three-way-merge-implementation-plan.md`

- [ ] **Step 2: Stage and commit the two untracked Phase 10F.5 plans**

```bash
git add docs/plans/2026-09-18-phase10f5-codebase-design.md docs/plans/2026-09-18-phase10f5-three-way-merge-implementation-plan.md
git commit -m "docs(phase10f): track phase 10f.5 design and implementation plan"
```

- [ ] **Step 3: Verify git working tree is clean**

```bash
git status
```
Expected: `nothing to commit, working tree clean`

---

### Task 2: Credential Exposure Remediation & Repository Junk Removal

**Files to Delete:**
- Tracked scripts with leaked plain-text Gemini API key:
  - `testcrop.py`
  - `testcrop2.py`
- Tracked developer test artifacts & obsolete configs:
  - `image1.jpg`
  - `snapshot.py`
  - `passenger_wsgi.py`
  - `config.example.py`
- Tracked obsolete prompt text files in repo root and `prompts/`:
  - `prompt.txt`
  - `prompt2.txt`
  - `prompt2Di.txt`
  - `prompt2DiMadar.txt`
  - `prompt3.txt`
  - `promptElectro.txt`
  - `promptTikZ.txt`
  - `prompts/QuickConvertPrompt.txt`
  - `prompts/pipeline2/QuickConvertPrompt.txt`
  - `prompts/pipeline2/pipeline2_unify_prompt.txt`
- Untracked/gitignored junk files & vestigial directories:
  - `pdf_bot.zip`
  - `rate_limits.json`
  - `worker.log`
  - `stderr.log`
  - `tmp/restart.txt` (and `tmp/`)
  - `cropped_charts/`
  - `my_notes/`
  - `output_files/` (clear leftover test outputs, retain empty dir if gitignored)
  - `temp_files/` (clear leftover test PDFs, retain empty dir if gitignored)
  - `interfaces/api/` (empty directory skeleton)

**Interfaces:**
- Consumes: File system cleanup.
- Produces: Sanitized git tree with zero exposed credentials, zero dead hosting stubs, and zero loose binary archives.

- [ ] **Step 1: Security Alert & Rotation Notice**

> [!CAUTION]
> **Credential Revocation Required:** The key `GOOGLE_API_KEY_REMOVED` was exposed in plaintext in `testcrop.py` and `testcrop2.py`. The project maintainer must immediately revoke/rotate this key in the Google AI Studio / Cloud Console. Repository deletion alone does not invalidate a compromised credential.

- [ ] **Step 2: Remove tracked scripts, images, and loose prompts via git rm**

```bash
git rm testcrop.py testcrop2.py snapshot.py passenger_wsgi.py config.example.py image1.jpg
git rm prompt.txt prompt2.txt prompt2Di.txt prompt2DiMadar.txt prompt3.txt promptElectro.txt promptTikZ.txt
git rm prompts/QuickConvertPrompt.txt prompts/pipeline2/QuickConvertPrompt.txt prompts/pipeline2/pipeline2_unify_prompt.txt
```

- [ ] **Step 3: Remove untracked and gitignored junk files and vestigial directories**

```bash
rm -f pdf_bot.zip rate_limits.json worker.log stderr.log
rm -rf tmp cropped_charts my_notes interfaces/api
# Clear contents of local output/temp dirs while preserving folder if required by gitignore
rm -rf output_files/* temp_files/*
# Clean prompts directory if empty
rmdir prompts/pipeline2 prompts 2>/dev/null || true
```

- [ ] **Step 4: Verify test suite runs cleanly with zero regressions**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_architecture_boundaries.py tests/unit/test_architecture_security.py tests/unit/test_secret_non_persistence.py -q
```
Expected: All tests pass.

- [ ] **Step 5: Commit security remediation and repository junk cleanup**

```bash
git commit -m "chore(cleanup): purge exposed testcrop scripts, obsolete cpanel files, and loose prompts"
```

---

### Task 3: Consolidate DTO Package into Canonical `application/dto/`

**Files:**
- Create/Move: `application/dto/merge_dto.py` (canonical implementation)
- Delete: `application/dtos/merge_dto.py`
- Delete: `application/dtos/__init__.py`
- Delete: `application/dtos/` directory
- Modify imports:
  - `application/services/markdown_merge_service.py:10`
  - `interfaces/desktop/models/conflict_session.py:10`
  - `interfaces/desktop/controllers/markdown_editor_controller.py:10`
  - `tests/unit/test_markdown_merge_service.py:10`
  - `tests/unit/test_conflict_session.py:9`
  - `tests/unit/test_markdown_editor_conflict.py:11`
  - `tests/unit/test_review_workspace_conflict_qml.py:12`

**Interfaces:**
- Consumes: `ConflictHunkDTO`, `MergeAnalysisResultDTO`.
- Produces: Single unified `application/dto/` package containing all 9 DTO modules.

- [ ] **Step 1: Write canonical definitions to `application/dto/merge_dto.py`**

Replace the re-export shim in `application/dto/merge_dto.py` with the full canonical dataclass definitions:

```python
# ============================================================
#  application/dto/merge_dto.py
#  Transport-neutral DTOs for Three-Way Merge Analysis
# ============================================================

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ConflictHunkDTO:
    """Presentation DTO representing a single diff3 merge hunk or conflict hunk."""
    hunk_index: int
    hunk_type: str
    base_text: str
    local_text: str
    remote_text: str
    local_line_start: int
    local_line_end: int
    ast_label: str = ""


@dataclass(frozen=True)
class MergeAnalysisResultDTO:
    """
    Analysis result DTO produced by MarkdownMergeService.
    Encapsulates merge status, clean merged text (if clean), and AST-enriched hunks.
    """
    job_id: int
    merge_session_id: int
    base_version: int
    canonical_version: int
    has_conflicts: bool
    clean_text: Optional[str]
    hunks: Tuple[ConflictHunkDTO, ...]
    conflict_count: int
    auto_merged_count: int
    canonical_text: Optional[str] = None
```

- [ ] **Step 2: Update import statements across all 7 calling files**

Change:
`from application.dtos.merge_dto import ...`
to:
`from application.dto.merge_dto import ...`

In:
1. `application/services/markdown_merge_service.py`
2. `interfaces/desktop/models/conflict_session.py`
3. `interfaces/desktop/controllers/markdown_editor_controller.py`
4. `tests/unit/test_markdown_merge_service.py`
5. `tests/unit/test_conflict_session.py`
6. `tests/unit/test_markdown_editor_conflict.py`
7. `tests/unit/test_review_workspace_conflict_qml.py`

- [ ] **Step 3: Remove the redundant `application/dtos/` package**

```bash
git rm -r application/dtos
```

- [ ] **Step 4: Verify test suite passes for all merge and conflict tests**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_markdown_merge_service.py tests/unit/test_conflict_session.py tests/unit/test_markdown_editor_conflict.py tests/unit/test_review_workspace_conflict_qml.py -v
```
Expected: 43 passed, 0 failed.

- [ ] **Step 5: Verify no residual references to `application.dtos` exist in the repo**

```bash
git grep "application\.dtos"
```
Expected: 0 occurrences in python source files (excluding historical closure/diff docs).

- [ ] **Step 6: Commit DTO consolidation**

```bash
git add application/dto/merge_dto.py application/services/markdown_merge_service.py interfaces/desktop/models/conflict_session.py interfaces/desktop/controllers/markdown_editor_controller.py tests/unit/
git commit -m "refactor(dto): consolidate merge_dto into canonical application/dto package"
```

---

### Task 4: Add Missing Package Initializers & Fix Inverted Infrastructure Imports

**Files:**
- Create:
  - `application/dto/__init__.py`
  - `infrastructure/notifier/__init__.py`
  - `infrastructure/rate_limiting/__init__.py`
  - `infrastructure/storage/__init__.py`
- Modify:
  - `infrastructure/persistence/unit_of_work.py:6`
  - `infrastructure/persistence/credential_resolver.py:7`
  - `application/ports/__init__.py`

**Interfaces:**
- Consumes: Package structure hygiene.
- Produces: Proper Python package semantics and eliminates root-shim coupling (`database.connection`).

- [ ] **Step 1: Create missing `__init__.py` files**

Create `application/dto/__init__.py`:
```python
"""Application Data Transfer Objects (DTOs) and Command Objects."""
```

Create `infrastructure/notifier/__init__.py`:
```python
"""Infrastructure notification and event publication adapters."""
```

Create `infrastructure/rate_limiting/__init__.py`:
```python
"""Infrastructure rate limiting adapters."""
```

Create `infrastructure/storage/__init__.py`:
```python
"""Infrastructure artifact and file storage adapters."""
```

- [ ] **Step 2: Fix inverted imports in MySQL persistence modules**

In `infrastructure/persistence/unit_of_work.py:6`:
Replace:
`from database.connection import DatabaseManager`
With:
`from infrastructure.persistence.connection import DatabaseManager`

In `infrastructure/persistence/credential_resolver.py:7`:
Replace:
`from database.connection import DatabaseManager`
With:
`from infrastructure.persistence.connection import DatabaseManager`

- [ ] **Step 3: Update `application/ports/__init__.py` with modern desktop ports**

Export `ISettingsRepository`, `IVisualRegionRepository`, `IMarkdownParser`, `IAIExecutionService`, and `IApplicationEventPublisher`.

- [ ] **Step 4: Run unit tests to verify persistence and package imports**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_persistence_infrastructure.py tests/unit/test_architecture_boundaries.py -v
```
Expected: All tests pass.

- [ ] **Step 5: Commit package initializers and import cleanup**

```bash
git add application/dto/__init__.py infrastructure/notifier/__init__.py infrastructure/rate_limiting/__init__.py infrastructure/storage/__init__.py infrastructure/persistence/unit_of_work.py infrastructure/persistence/credential_resolver.py application/ports/__init__.py
git commit -m "refactor(infrastructure): add package initializers and fix persistence imports"
```

---

### Task 5: Reorganize Documentation Hierarchy & Archive Historical Root Plans

**Files:**
- Move:
  - `phase_8_desktop_architecture_plan.md` $\to$ `docs/plans/historical/phase_8_desktop_architecture_plan.md`
  - `phase_8b_sqlite_implementation_plan.md` $\to$ `docs/plans/historical/phase_8b_sqlite_implementation_plan.md`
  - `phase_8d_implementation_plan.md` $\to$ `docs/plans/historical/phase_8d_implementation_plan.md`
  - `docs/superpowers/plans/2026-09-17-phase10f1-native-markdown-editor.md` $\to$ `docs/plans/2026-09-17-phase10f1-native-markdown-editor.md`
  - `docs/plans/2026-09-18-phase10f4-closure-report.md` $\to$ `docs/closure/2026-09-18-phase10f4-closure-report.md`
  - `docs/phase10e_technical_debt.md` $\to$ `docs/debt/2026-09-17-phase10e-technical-debt.md`
- Delete directory: `docs/superpowers/`

**Interfaces:**
- Consumes: Documentation file system layout.
- Produces: Clean, standard `docs/` hierarchy (`adr/`, `plans/`, `plans/historical/`, `closure/`, `debt/`, `agents/`).

- [ ] **Step 1: Create target documentation directories**

```bash
mkdir -p docs/plans/historical docs/closure docs/debt
```

- [ ] **Step 2: Move historical Phase 8 plans from repository root**

```bash
git mv phase_8_desktop_architecture_plan.md docs/plans/historical/
git mv phase_8b_sqlite_implementation_plan.md docs/plans/historical/
git mv phase_8d_implementation_plan.md docs/plans/historical/
```

- [ ] **Step 3: Move misplaced plans, closure reports, and debt documents**

```bash
git mv docs/superpowers/plans/2026-09-17-phase10f1-native-markdown-editor.md docs/plans/
rm -rf docs/superpowers
git mv docs/plans/2026-09-18-phase10f4-closure-report.md docs/closure/
git mv docs/phase10e_technical_debt.md docs/debt/2026-09-17-phase10e-technical-debt.md
```

- [ ] **Step 4: Verify all links within moved docs**

Check and verify that relative links inside `docs/plans/historical/` and `docs/closure/` remain valid or are self-contained.

- [ ] **Step 5: Verify git status reflects pure file moves with zero content diff**

```bash
git status
```
Expected: `renamed: ...` entries with no untracked clutter.

- [ ] **Step 6: Commit documentation reorganization**

```bash
git commit -m "docs(structure): reorganize documentation hierarchy and archive historical plans"
```

---

### Task 6: Author Canonical `CONTEXT.md` Domain Glossary & Seam Map

**Files:**
- Create: `CONTEXT.md` in repository root

**Interfaces:**
- Consumes: Settled domain rules, ADR-001, Phase 10F.1–10F.5 architecture invariants, and Clean Architecture layer definitions.
- Produces: Authoritative repository context document defining core entities, review workspace concepts, and architectural seams for developers and AI agents.

- [ ] **Step 1: Draft canonical `CONTEXT.md`**

Include:
1. **Core Domain Glossary:**
   - `Job`: A unit of document processing work tracking lifecycle states (`PENDING`, `PROCESSING`, `DONE`, `FAILED`, `CANCELLED`), pipeline mode (Pipeline 1 standard OCR vs Pipeline 2 unified layout), and canonical artifact URI.
   - `VisualRegion`: An extracted visual crop entity with bounding box geometry `(x, y, width, height)` in normalized PDF points `(72 DPI)`, associated with page number, token label `crop_{id}`, and image artifact handle.
   - `ReviewWorkspace`: The desktop presentation environment integrating the dual-pane Document Viewer and Markdown Editor with continuous scroll synchronization.
   - `Canonical Markdown`: The authoritative, immutable document artifact (`output_{job_id}_v{version}.md`) stored on disk. Advances monotonically only through `commit_source_text()`.
   - `Draft Markdown`: The live, uncommitted in-memory editor buffer in `MarkdownEditorController`.
   - `OCC (Optimistic Concurrency Control)`: Watermark version checking (`base_version == active_version`) preventing concurrent save overwrites between human edits and background pipeline commits.
   - `ConflictSession`: The transient in-memory presentation model managing active three-way merge conflict hunks in the editor buffer.
   - `ConflictHunk`: A segmented diff hunk categorized as `CLEAN_LOCAL`, `CLEAN_REMOTE`, `CLEAN_SAME`, or `CONFLICT`.
   - `Preview Synchronization`: Normalized bidirectional scroll progress tracking with directional origin locks and preview pausing overlay during conflicts.
2. **Architectural Seams Map:**
   - Presentation $\to$ Application Services seam.
   - Application Services $\to$ Application Ports seam.
   - Ports $\to$ Infrastructure Adapters seam.
   - Raw Markdown Text vs Read-Only CommonMark AST seam (the AST is strictly queryable; never serialized back to Markdown text).
   - Secret Seam: Credential references (`CredentialRef`) in domain vs OS Keyring resolution in infrastructure.

- [ ] **Step 2: Verify `CONTEXT.md` matches `AGENTS.md` terminology**

Verify that all terms in `CONTEXT.md` correspond exactly to standing rules in `AGENTS.md`.

- [ ] **Step 3: Commit `CONTEXT.md`**

```bash
git add CONTEXT.md
git commit -m "docs(context): author canonical domain glossary and architectural seams map"
```

---

### Task 7: Modernize `README.md` for Desktop Review Workspace & Diff3

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Target Information Architecture from forensic audit report (Section H).
- Produces: Professional, accurate, English-only README reflecting the actual desktop product.

- [ ] **Step 1: Write modernized `README.md`**

Structure:
1. **Header & Badge Row:** Title, tagline, key technology badges (Python 3.10+, PySide6, SQLite WAL, OS Keyring, CommonMark). *Do NOT hardcode an ephemeral test count badge like "822 passed"*.
2. **Flagship Capabilities:**
   - Synchronized Dual-Pane Review Workspace (PDF Document Viewer + Segmented Markdown Preview).
   - Continuous Normalized Scroll Synchronization (16ms throttled, directional origin lock).
   - Interactive Visual Region Bounding Box Editor (selection, resizing, auto-recropping).
   - Native Markdown Source Editor (syntax highlighting, undo/redo, search & replace, OCC versioning).
   - Format-Preserving Three-Way Merge (`diff3`) & in-buffer conflict resolution toolbar.
3. **Architecture Principles:**
   - Desktop-First, Local-First, Serverless (zero loopback HTTP, zero FastAPI, zero external MySQL).
   - Direct Outbound AI Communications (BYOK for Gemini, OpenAI, custom endpoints).
   - Secure Keyring Storage (OS Keyring + Fernet PBKDF2 vault fallback; zero secrets in database).
   - Robust Concurrency (`QThreadPool`, cooperative pause/resume/cancel).
4. **Quickstart & Installation:**
   - Clean setup commands with venv.
   - Launching desktop client: `python -m interfaces.desktop.app`.
5. **Testing & Verification:**
   - Command: `pytest -q`.
   - Dedicated boundary and invariant test commands.
6. **Repository Structure Tree:**
   - Accurate directory tree including `core/markdown/`, `infrastructure/markdown/`, `interfaces/desktop/coordinators/`.
7. **Legacy Transport Note:**
   - Concise English note stating Telegram is a frozen legacy transport adapter. Mention that historical cPanel/MySQL instructions are preserved in `docs/plans/historical/`. *Remove lines 105–135 (the Persian cPanel guide)*.

- [ ] **Step 2: Check formatting and links**

Ensure all markdown headings, code blocks, and lists render cleanly.

- [ ] **Step 3: Commit modernized `README.md`**

```bash
git add README.md
git commit -m "docs(readme): modernize README for desktop review workspace and diff3 three-way merge"
```

---

### Task 8: Normalize Technical Comments and Docstrings to English

**Files:**
- Modify files with Persian technical comments identified in the forensic audit:
  - `core/entities/user.py`
  - `core/entities/prompt.py`
  - `core/entities/artifact.py`
  - `core/policies/fallback_policy.py`
  - `core/policies/retry_policy.py`
  - `core/exceptions/domain_exceptions.py`
  - `core/ai/exceptions.py`
  - `application/ports/document_processor.py`
  - `application/ports/storage.py`
  - `application/ports/provider_detector.py`
  - `application/ports/ai_provider.py`
  - `application/ports/ai_executor.py`
  - `application/services/user_service.py`
  - `application/services/prompt_service.py`
  - `application/services/api_service.py`
  - `infrastructure/logging/setup.py`
  - `infrastructure/storage/local_storage.py`
  - `infrastructure/notifier/event_notifier.py`
  - `infrastructure/rate_limiting/rate_limiter_adapter.py`
  - `infrastructure/ai/google_adapter.py`
  - `infrastructure/ai/openai_adapter.py`
  - `infrastructure/ai/provider_detector.py`
  - `infrastructure/ai/executor_service.py`
  - `infrastructure/composition.py`
  - `infrastructure/persistence/connection.py`
  - `infrastructure/persistence/migration_runner.py`
  - `infrastructure/persistence/unit_of_work.py`

*(Note: In `infrastructure/document/pymupdf_processor.py:116`, the regex pattern `r"^##\s*صفحه\s*\d+\s*$"`` is domain document matching logic for Persian text documents and must be preserved).*

**Interfaces:**
- Consumes: Non-compliant technical comments.
- Produces: 100% compliance with AGENTS.md Rule 20 across all active codebase layers.

- [ ] **Step 1: Translate Persian comments and docstrings in `core/` to English**

Update class docstrings, method docstrings, and inline notes in:
- `core/entities/user.py`
- `core/entities/prompt.py`
- `core/entities/artifact.py`
- `core/policies/fallback_policy.py`
- `core/policies/retry_policy.py`
- `core/exceptions/domain_exceptions.py`
- `core/ai/exceptions.py`

- [ ] **Step 2: Translate Persian comments and docstrings in `application/` to English**

Update port and service docstrings in:
- `application/ports/document_processor.py`
- `application/ports/storage.py`
- `application/ports/provider_detector.py`
- `application/ports/ai_provider.py`
- `application/ports/ai_executor.py`
- `application/services/user_service.py`
- `application/services/prompt_service.py`
- `application/services/api_service.py`

- [ ] **Step 3: Translate Persian comments and docstrings in `infrastructure/` to English**

Update adapter docstrings and comments in:
- `infrastructure/logging/setup.py`
- `infrastructure/storage/local_storage.py`
- `infrastructure/notifier/event_notifier.py`
- `infrastructure/rate_limiting/rate_limiter_adapter.py`
- `infrastructure/ai/google_adapter.py`
- `infrastructure/ai/openai_adapter.py`
- `infrastructure/ai/provider_detector.py`
- `infrastructure/ai/executor_service.py`
- `infrastructure/composition.py`
- `infrastructure/persistence/connection.py`
- `infrastructure/persistence/migration_runner.py`
- `infrastructure/persistence/unit_of_work.py`

- [ ] **Step 4: Verify zero syntax errors and run full test suite**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest -q
```
Expected: 822 passed.

- [ ] **Step 5: Verify Persian characters are eliminated from comments**

```bash
grep -rn "[آ-ی]" core/ application/ infrastructure/ --include="*.py" | grep -v "صفحه"
```
Expected: 0 matches.

- [ ] **Step 6: Commit technical comment normalization**

```bash
git add core/ application/ infrastructure/
git commit -m "style(docs): normalize technical comments and docstrings to English per AGENTS.md rule 20"
```

---

### Task 9: Clean `requirements.txt` & Perform Final Verification

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: Python dependency manifest.
- Produces: Accurate dependencies required to install, build, and run PolpoT desktop.

- [ ] **Step 1: Update `requirements.txt`**

Add `markdown-it-py>=3.0.0` (required by `infrastructure.markdown`).
Remove `nest-asyncio==1.6.0` (which was noted for deleted `backup.py` and is never imported).
Remove outdated Persian inline comments.

Updated `requirements.txt`:
```text
# ── Telegram Bot (Frozen Legacy Transport) ────────────────────────────
python-telegram-bot==21.5

# ── Document & PDF Processing ─────────────────────────────────────────
PyMuPDF==1.24.10
Pillow==10.4.0
markdown-it-py>=3.0.0

# ── AI APIs ────────────────────────────────────────────────────────────
google-genai==1.16.1
openai==1.82.0

# ── Database (Legacy Transport Only) ──────────────────────────────────
PyMySQL==1.1.1

# ── Configuration & Environment ────────────────────────────────────────
python-dotenv==1.0.1
pydantic==2.13.5

# ── Desktop Presentation & Security (Embedded Serverless Desktop) ──────
PySide6==6.8.0.2
keyring==24.3.1
cryptography==44.0.0
platformdirs==4.3.6
httpx==0.28.1
```

- [ ] **Step 2: Verify `git diff --check` is 100% clean**

```bash
git diff --check
```
Expected: 0 whitespace or formatting errors.

- [ ] **Step 3: Run full automated test suite (822 tests)**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest
```
Expected:
```text
============================= 822 passed in ~45s =============================
```

- [ ] **Step 4: Run dedicated Clean Architecture & security invariant tests**

```bash
/home/amirreza-a2a/madarsol/venv/bin/pytest tests/unit/test_architecture_boundaries.py tests/unit/test_architecture_events_and_serverless.py tests/unit/test_architecture_lifecycle.py tests/unit/test_architecture_security.py tests/unit/test_secret_non_persistence.py -v
```
Expected: 22 passed.

- [ ] **Step 5: Commit dependency cleanup**

```bash
git add requirements.txt
git commit -m "build(deps): update requirements.txt with markdown-it-py and prune obsolete dependencies"
```

---

## Verification Matrix

| Area | Check | Command | Success Criteria |
| :--- | :--- | :--- | :--- |
| **Git Baseline** | Working tree clean | `git status` | `nothing to commit, working tree clean` |
| **Security** | Zero leaked plaintext credentials | `git grep "AIzaSy"` | 0 matches |
| **DTO Package** | Unified `application/dto/` | `git grep "application\.dtos"` | 0 matches in Python code |
| **Imports** | Zero circular or broken imports | `python -m interfaces.desktop.app --help` | Exits cleanly without import errors |
| **Documentation** | Reorganized hierarchy | `ls -la docs/plans/historical/ docs/closure/` | Files present, links valid |
| **Language Rule** | English technical comments | `grep -rn "[آ-ی]" core/ application/ infrastructure/ --include="*.py" \| grep -v "صفحه"` | 0 matches |
| **Full Test Suite** | Zero regressions | `pytest -q` | **822 passed, 0 failed, 0 skipped** |
| **Architecture Boundaries** | Layer isolation preserved | `pytest tests/unit/test_architecture_boundaries.py` | 5 passed |
| **Security Invariants** | Secrets non-persistence verified | `pytest tests/unit/test_secret_non_persistence.py` | 4 passed |

---

## Regression Risks & Rollback Considerations

1. **DTO Package Unification:**
   - *Risk:* A third-party import site or test module could attempt `from application.dtos.merge_dto import ...` and fail with `ModuleNotFoundError`.
   - *Mitigation:* Comprehensive grep across all files (`application/`, `interfaces/`, `tests/`) confirmed only 7 files import this module. All 7 are updated atomically in Task 3.
   - *Rollback:* `git checkout HEAD~1` restores the previous commit if needed.
2. **Comment Translation:**
   - *Risk:* Accidental typo introducing a Python syntax error in docstrings or comments.
   - *Mitigation:* Running the full 822-test suite immediately following Task 8 guarantees every modified module is parsed and executed cleanly.
3. **Requirements.txt Changes:**
   - *Risk:* A dependency needed for testing or legacy runs is omitted.
   - *Mitigation:* PyMySQL, python-telegram-bot, and python-dotenv are intentionally preserved for legacy test execution; only `nest-asyncio` (confirmed unused) is pruned, and `markdown-it-py` (already installed in the venv and actively imported) is explicitly declared.

---

## Final Decision

# **READY FOR IMPLEMENTATION**

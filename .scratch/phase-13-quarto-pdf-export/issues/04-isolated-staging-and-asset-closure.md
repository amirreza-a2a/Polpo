# 04: Isolated Staging Workspace, Preamble Management & Visual Region Asset Closure

**What to build:** Implement `StagingWorkspaceManager` in `infrastructure/pdf/staging_workspace.py` and template assets in `resources/templates/`. Creates isolated ephemeral staging workspaces (`<artifacts_dir>/.pdf_staging/<job_id>_<token>/`), injects an application-owned `_quarto.yml` barrier (`project: {type: default}`) to prevent parent directory config inheritance, manages `polpo_preamble.tex` (Babel bidi, Persian/English font mappings, and TikZ LTR environment hooks), resolves local visual region crops (`crop_{region_id}.png`) with path containment, stages normalized `document.qmd`, and guarantees cleanup.

**Blocked by:** 01 (Metadata Normalizer, Allowlist Schema & Typesetting Header Compatibility).

**Status:** ready-for-agent

## Scope

- Implement `resources/templates/polpo_preamble.tex`:
  - Mathematical packages: `amsmath`, `amssymb`.
  - Diagram packages: `tikz`, `pgfplots` with `\pgfplotsset{compat=1.18}`.
  - Bidirectional engine: `babel` configured with `[bidi=default,provide=*]`.
  - Language configuration: `\babelprovide[main,import]{persian}`, `\babelprovide[import]{english}`.
  - Font mapping: `\babelfont{rm}{FreeSerif}`, `\babelfont{sf}{FreeSerif}`.
  - **ADR-003 D02 TikZ Isolation:** Injects `etoolbox` hook wrapping `tikzpicture` in `\begin{otherlanguage*}{english}` and `\end{otherlanguage*}` to prevent coordinate or label inversion by the ambient RTL engine.
- Implement `StagingWorkspaceManager` in `infrastructure/pdf/staging_workspace.py`:
  - **Workspace Allocation:** Creates unique directory `<job_artifacts_dir>/.pdf_staging/<job_id>_<hex_token>/`.
  - **Parent Configuration Barrier:** Writes `<staging>/_quarto.yml` containing:
    ```yaml
    project:
      type: default
    format:
      pdf:
        pdf-engine: xelatex
        pdf-engine-opts:
          - "-no-shell-escape"
          - "-interaction=nonstopmode"
          - "-halt-on-error"
    ```
    This stops Quarto from climbing ancestor directories to inherit ambient `_quarto.yml` settings.
  - **Preamble Placement:** Writes `polpo_preamble.tex` into the staging directory.
  - **Visual Region Asset Closure:** Reuses `DocumentAssetResolver` to locate local crop assets (`crop_{region_id}.png`). Strictly verifies each asset resides inside `job_artifacts_dir` (preventing directory traversal). Copies referenced crops into the staging workspace.
  - **F-02 Enforced (Staged Document Generation):** Calls `normalize_quarto_pdf_metadata()` (Ticket 01), formats staged YAML front matter, appends canonical Markdown body, and writes `<staging>/document.qmd`. Staged front matter sets `format.pdf.include-in-header: [{"file": "polpo_preamble.tex"}]`.
  - **Deterministic Cleanup:** Provides a context manager (`with staging.create_session(...) as session:`) ensuring the staging directory is completely unlinked in a `finally` block on success, failure, or cancellation.

## Non-Goals

- Executing Quarto rendering (handled by `QuartoPdfAdapter` in Ticket 05).
- Publishing output PDF files to user destination (handled by `AtomicPdfPublisher` in Ticket 06).
- Fetching remote images over HTTP(S) (prohibited by ADR-003).

## Files Likely Impacted

- Create: `resources/templates/polpo_preamble.tex`
- Create: `infrastructure/pdf/staging_workspace.py`
- Create: `tests/unit/test_staging_workspace.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class StagingSession:
    staging_dir: Path
    qmd_path: Path
    preamble_path: Path
    quarto_yml_path: Path
    staged_asset_count: int


class StagingWorkspaceManager:
    def __init__(self, templates_dir: Optional[Path] = None):
        ...

    @contextmanager
    def create_session(
        self,
        job_artifacts_dir: Path,
        job_id: int,
        raw_metadata: Dict[str, Any],
        canonical_markdown: str,
        asset_resolver: DocumentAssetResolver,
        is_tinytex: bool = False,
        default_title: str = "Document",
    ) -> Iterator[StagingSession]:
        """Allocates an isolated staging workspace and deterministically purges it on exit."""
        ...
```

## Architectural Invariants

- **Parent Project Isolation:** Every staging directory MUST contain its own `_quarto.yml` with `project.type: default`.
- **Path Containment:** Assets referenced outside `job_artifacts_dir` raise `AssetAccessError`. No relative path escaping `..` is permitted.
- **Source Preservation:** The canonical on-disk Markdown file is NEVER modified in place.
- **Zero Leakage:** Staging directories must not accumulate across crashes or errors; cleanup is guaranteed via `finally`.

## Acceptance Criteria

- [ ] Staging session creates an isolated folder under `<job_artifacts_dir>/.pdf_staging/`.
- [ ] Staging directory contains a barrier `_quarto.yml` with `project: {type: default}`.
- [ ] `polpo_preamble.tex` is copied into staging with Babel bidi settings, FreeSerif font mapping, and TikZ LTR hooks.
- [ ] Local image crops (`crop_{region_id}.png`) inside `job_artifacts_dir` are resolved and copied into staging.
- [ ] Asset references attempting directory traversal (`../../secret.png`) are rejected with `AssetAccessError`.
- [ ] Staged `document.qmd` contains normalized front matter with `include-in-header: [{"file": "polpo_preamble.tex"}]`.
- [ ] Staging directory and all contents are removed when the context manager exits, even if an exception is raised.
- [ ] All unit tests pass in `tests/unit/test_staging_workspace.py`.

## Verification / Tests Required

- `test_staging_creates_and_cleans_workspace()`: verifies directory allocation and deterministic cleanup on exit.
- `test_staging_writes_project_barrier()`: asserts `_quarto.yml` is present with `project: {type: default}`.
- `test_staging_copies_local_assets_with_path_containment()`: verifies valid crops are copied and directory traversal is rejected.
- `test_staging_cleans_up_on_unhandled_exception()`: simulates exception inside context block; asserts directory is purged.

## Commit Boundary

Single commit: `feat(export): implement isolated staging workspace and typesetting preamble management`

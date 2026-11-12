# TICK-ADE-14: Style Profile SQLite Persistence, Staging Metadata Injection & Font Resolution

- **Ticket ID:** `TICK-ADE-14`
- **Title:** Style Profile SQLite Persistence, Staging Metadata Injection & Font Resolution
- **Milestone:** Milestone E (Publication Style System)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Scope:** Implement `PublicationStyleService` in `application/services/publication_style_service.py` to persist user-customized publication styles in SQLite table `publication_style_profiles` (migration `006_agent_task_tables.sql`), load built-in profiles, resolve system font fallbacks, and inject validated profile settings into the Quarto ephemeral staging workspace.
- **Explicit Non-Goals:**
  - No editing of `resources/templates/polpo_preamble.tex` on disk.
  - No direct SQL execution from presentation controllers.
  - No injecting unallowlisted metadata keys or raw LaTeX fragments.
- **Prerequisites:** `TICK-ADE-03` and `TICK-ADE-13` completed; ADR-010 approved.
- **Dependencies (Blocked By):** `TICK-ADE-03`, `TICK-ADE-13`.
- **Unblocks:** `TICK-ADE-15`.

---

## 1. Existing Source-Code Integration Points

- `application/services/metadata_normalizer.py`: Validation against `ALLOWED_ROOT_KEYS_V1`.
- `infrastructure/pdf/staging_workspace.py`: Staging session creation and metadata injection hook.
- `infrastructure/persistence/sqlite/migrations/006_agent_task_tables.sql`: Persistence table definition.
- `resources/styles/*.json`: Built-in profile JSON sources.

---

## 2. Proposed Component Implementation

```python
# application/services/publication_style_service.py

class PublicationStyleService:
    def __init__(
        self,
        style_repo: IStyleProfileRepository,
        builtin_styles_dir: Path,
    ):
        ...

    def list_profiles(self) -> Sequence[PublicationStyleProfile]:
        ...

    def get_profile(self, profile_id: str) -> Optional[PublicationStyleProfile]:
        ...

    def save_custom_profile(self, profile: PublicationStyleProfile) -> None:
        ...

    def delete_custom_profile(self, profile_id: str) -> bool:
        ...

    def get_effective_profile(self, job_id: int, metadata: Dict[str, Any]) -> PublicationStyleProfile:
        ...

    def inject_profile_into_metadata(
        self,
        profile: PublicationStyleProfile,
        target_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        ...
```

---

## 3. Detailed Implementation Requirements

1. **SQLite Persistence (Finding I):**
   - Implements `IStyleProfileRepository` in `infrastructure/persistence/sqlite/style_profile_repository.py`.
   - Uses table `publication_style_profiles` defined in migration `006_agent_task_tables.sql`.
   - Built-in profile IDs are prefixed with `builtin:` and are read-only; attempts to delete them raise `ImmutableBuiltinProfileError`.
   - User profile IDs are prefixed with `user:` followed by a canonical UUID4 string (`user:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`).
2. **Metadata Injection & Allowlist Validation:**
   - Maps profile tokens into Quarto metadata: `documentclass`, `fontsize`, `linestretch`, `papersize`, `geometry`, `number-sections`, `number-depth`, `toc`, `toc-depth`, `colorlinks`, `linkcolor`, `urlcolor`, `citecolor`.
   - Translates `strikethrough_engine = "ulem"` into Quarto YAML / staging preamble configuration deterministically, adhering to maintainer proposal `D-PROP-08` (Option A vs Option B; awaiting maintainer approval).
   - Passes the resulting dictionary through `normalize_quarto_pdf_metadata()` which validates every key against `ALLOWED_ROOT_KEYS_V1`.
3. **Font Fallback Resolution:**
   - Verifies font availability using system font discovery. If a requested font is missing on the host machine, falls back gracefully to `FreeSerif` / `Vazirmatn` without failing compilation.

---

## 4. Acceptance Criteria

- [ ] `list_profiles()` returns all 4 built-in profiles plus saved custom profiles from SQLite.
- [ ] Custom profiles persist across SQLite connections and application restarts under migration 006.
- [ ] Attempting to delete or overwrite a built-in profile raises `ImmutableBuiltinProfileError`.
- [ ] Staging workspace injects profile geometry and typography cleanly into `_quarto.yml` and `document.qmd`.
- [ ] Missing fonts fall back gracefully to `FreeSerif` / `Vazirmatn`.
- [ ] Quarto PDF compilation with injected profile compiles cleanly under XeLaTeX.

---

## 5. Test Specifications

### 5.1 Integration Tests
- `tests/integration/test_publication_style_service.py`:
  - `test_custom_profile_crud_in_sqlite()`: Verifies persistence in migration 006 table (`publication_style_profiles`).
  - `test_builtin_profile_immutability()`: Confirms rejection of deletion with `ImmutableBuiltinProfileError`.
  - `test_delete_custom_profile_success_and_document_fallback()`: Verifies deleting a user profile succeeds and documents fall back to `builtin:academic`.
  - `test_metadata_injection_allowlist_compliance()`: Validates injected keys against `ALLOWED_ROOT_KEYS_V1`.
  - `test_font_fallback_resolution()`: Confirms fallback when font is absent.
  - `test_full_compilation_with_injected_academic_profile()`: Verifies real XeLaTeX build.

### 5.2 Negative & Security Tests
- `test_user_profile_id_canonical_uuid4_validation()`: Asserts non-UUID or non-canonical user profile IDs are rejected.
- `test_reject_unallowlisted_profile_keys()`: Asserts arbitrary keys injected into profile JSON are rejected.
- `test_reject_malicious_profile_json_injection()`: Asserts LaTeX macro commands, shell commands, or unallowlisted keys in `profile_json` are rejected before database commit.

### 5.3 Concurrency & Persistence Considerations
- Database mutations executed under `BEGIN IMMEDIATE` transactions.

---

## 6. Error and Recovery Behavior

- If a document references a deleted or unavailable profile, falls back to `builtin:academic` with a warning log.

---

## 7. Observability & Audit Requirements

- Logs profile resolution and fallback decisions.

---

## 8. Documentation Updates

- Document profile persistence and injection in ADR-010.

---

## 9. Required Project Skills

- `codebase-design`: Clean service contracts and repository boundaries.
- `tdd`: Integration testing of style profile persistence and injection.

---

## 10. Verification Commands

```bash
pytest tests/integration/test_publication_style_service.py -v
```

---

## 11. Definition of Done

- All integration tests pass with 100% coverage.
- Custom profile SQLite persistence and Quarto staging injection fully verified.

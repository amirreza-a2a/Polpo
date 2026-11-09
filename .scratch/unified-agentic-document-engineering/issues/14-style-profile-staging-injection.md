# TICK-ADE-14: Style Profile SQLite Persistence & Staging Metadata Barrier Injection

- **Ticket ID:** `TICK-ADE-14`
- **Milestone:** Milestone E (Publication Style System)
- **Priority:** P2
- **Status:** Proposed (Awaiting Maintainer Approval)
- **Blocked By:** `TICK-ADE-13`
- **Authoritative ADR:** ADR-010 (Publication Style Profile System & Rendering Governance)

---

## 1. Objective

Implement `PublicationStyleService` in `application/services/publication_style_service.py` to persist user-customized publication styles in SQLite, load built-in profiles, and inject validated profile settings into the Quarto ephemeral staging workspace.

---

## 2. User Value

Authors can save custom publication styles and apply them reliably across documents. Staging injection ensures that custom typography and geometry are compiled faithfully without breaking barrier isolation or existing preambles.

---

## 3. Scope

- Extend SQLite persistence in `infrastructure/persistence/sqlite/style_profile_repository.py`:
  - Table `publication_style_profiles`: `profile_id TEXT PRIMARY KEY`, `display_name TEXT`, `description TEXT`, `schema_version INTEGER`, `is_builtin INTEGER`, `profile_json TEXT`, `created_at REAL`, `updated_at REAL`.
- Implement `PublicationStyleService` in `application/services/publication_style_service.py`:
  - `list_profiles() -> Sequence[PublicationStyleProfile]`: Combines built-in profiles and custom SQLite profiles.
  - `get_profile(profile_id: str) -> Optional[PublicationStyleProfile]`
  - `save_custom_profile(profile: PublicationStyleProfile) -> None`: Validates schema and saves to SQLite.
  - `delete_custom_profile(profile_id: str) -> bool`: Deletes custom profile (built-in profiles cannot be deleted).
  - `inject_profile_into_metadata(profile: PublicationStyleProfile, target_metadata: Dict[str, Any]) -> Dict[str, Any]`: Maps profile tokens (`fontsize`, `geometry`, `linestretch`, `colorlinks`, etc.) into metadata allowlist keys.
- Update `QuartoPdfExportService` and `StagingWorkspaceManager` to consume the effective profile during staging creation.

---

## 4. Explicit Non-Goals

- No editing of `resources/templates/polpo_preamble.tex` on disk.
- No direct SQL execution from controllers.

---

## 5. Codebase References

- `application/services/metadata_normalizer.py` (`normalize_quarto_pdf_metadata`).
- `infrastructure/pdf/staging_workspace.py` (`create_session`).
- `resources/styles/*.json` (Built-in profile sources).

---

## 6. Proposed Files & Components

- Create: `infrastructure/persistence/sqlite/style_profile_repository.py`
- Create: `application/services/publication_style_service.py`
- Modify: `application/services/quarto_pdf_export_service.py`
- Create: `tests/integration/test_publication_style_service.py`

---

## 7. Domain & Architectural Constraints

- **Rule 8 & 9 Compliance:** Database mutations are transactional under `BEGIN IMMEDIATE`.
- **D-USER-03 Compliance:** User profile modifications remain local to SQLite and do not alter product files.

---

## 8. Detailed Implementation Requirements

1. When `inject_profile_into_metadata()` is invoked, the resulting dictionary must be validated against `ALLOWED_ROOT_KEYS_V1`.
2. Built-in profile IDs are prefixed with `builtin:` and are read-only. Calling `save_custom_profile()` or `delete_custom_profile()` on a `builtin:` ID raises `ImmutableBuiltinProfileError`.
3. Custom profile IDs are prefixed with `user:`.

---

## 9. Acceptance Criteria

- [ ] `list_profiles()` returns all 4 built-in profiles plus saved custom profiles.
- [ ] Custom profiles persist across SQLite connections and application restarts.
- [ ] Attempting to delete a built-in profile raises `ImmutableBuiltinProfileError`.
- [ ] Staging workspace injects profile geometry and typography cleanly into `_quarto.yml` and `document.qmd`.
- [ ] Quarto PDF compilation with injected profile compiles cleanly under XeLaTeX.

---

## 10. Required Tests

- Integration tests in `tests/integration/test_publication_style_service.py` verifying:
  - Custom profile CRUD in SQLite.
  - Immutability of built-in profiles.
  - Metadata injection and verification through `normalize_quarto_pdf_metadata()`.
  - Full candidate compilation under an injected profile.

---

## 11. Security Requirements

- Disallow profile JSON containing shell execution or macro redirectional parameters.

---

## 12. Completion Evidence & Follow-up

- Integration test execution evidence passing 100%.
- Completes Milestone E; unblocks Milestone F (`TICK-ADE-15`).

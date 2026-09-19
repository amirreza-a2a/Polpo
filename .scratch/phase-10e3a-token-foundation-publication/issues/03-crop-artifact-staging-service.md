# 03: Crop Artifact Staging Service (staging_id)

**What to build:** An application-level service that manages isolated filesystem staging directories for uncommitted visual crop files, keyed by an opaque `staging_id`. After this ticket, any future command or session can stage temporary crop images in an isolated directory, list them, verify their checksums, and discard them cleanly — without polluting the core artifact domain or permanent storage.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Application DTO `StagedCropHandle` — a lightweight handle for staged crop files (NOT in `core/entities/artifact.py`).
- Application service `CropArtifactStagingService` managing staging directories under `.staging/{staging_id}/`.
- SHA-256 checksum computation upon staging.
- `discard_staging(staging_id)` for clean directory removal.
- `list_staged(staging_id)` for enumerating staged crops.
- Path traversal protection and directory isolation.

## Non-Goals

- Modifying `core/entities/artifact.py` — staged state does not belong in the durable artifact domain.
- `ReviewWorkspaceSession` or `session_id` — staging uses an opaque `staging_id` that 10E.3b will map to session IDs.
- Publication or artifact promotion (ticket 06).
- Controllers, QML, or any presentation code.

## Files Likely Impacted

- Create: `application/dto/staged_crop.py`
- Create: `application/services/crop_artifact_staging_service.py`
- Create: `tests/unit/test_crop_artifact_staging_service.py`

## Public Interfaces / Contracts

```python
@dataclass(frozen=True)
class StagedCropHandle:
    staging_id: str
    region_id: str
    artifact_version: int
    staging_path: str       # Absolute path to the staged file
    dest_filename: str      # Target permanent filename
    sha256: str             # Hex digest of staged file content
    size_bytes: int

class CropArtifactStagingService:
    def stage_crop(self, job_id: int, staging_id: str, region_id: str,
                   version: int, image_bytes: bytes) -> StagedCropHandle: ...
    def list_staged(self, staging_id: str) -> List[StagedCropHandle]: ...
    def discard_staging(self, staging_id: str) -> None: ...
```

### Staging Identity Model

- `staging_id` is an opaque string (UUID4) owned by the caller.
- Storage path: `.staging/{staging_id}/crop_{region_id}_v{version}.jpg`.
- In Phase 10E.3b, `ReviewWorkspaceSession` will pass its session UUID as the `staging_id`.

## Architectural Invariants

- `StagedCropHandle` is an application DTO, NOT a core entity.
- `core/entities/artifact.py` is NOT modified — no `STAGED_CROP` artifact type.
- Staged files never appear in permanent artifact storage until explicitly promoted by the publication service.
- Path traversal and injection are prevented by path validation (basename sanitization, `is_relative_to` check).
- Thread-safe path isolation between different `staging_id` values.

## Acceptance Criteria

- [ ] `StagedCropHandle` is a frozen dataclass in `application/dto/`, not in `core/entities/`.
- [ ] Staging creates files exclusively under `.staging/{staging_id}/`.
- [ ] SHA-256 checksum and byte size are computed and stored in the handle upon staging.
- [ ] `discard_staging(staging_id)` completely removes the staging directory and all its contents.
- [ ] `list_staged(staging_id)` returns structured handles for all staged crops.
- [ ] Path traversal attempts (e.g., `../` in region_id) are rejected.
- [ ] Multiple concurrent staging_ids produce isolated directories with no cross-contamination.
- [ ] All new tests pass. No existing tests broken.

## Tests Required

- `test_staging_creates_file()`: verify file written to correct path.
- `test_staging_sha256_computed()`: verify hash matches content.
- `test_staging_directory_isolation()`: two staging_ids produce separate directories.
- `test_staging_discard_cleanup()`: verify directory and files fully removed.
- `test_staging_list_handles()`: verify structured handle enumeration.
- `test_staging_path_traversal_rejected()`: directory traversal in region_id rejected.
- `test_staging_idempotent_discard()`: discarding a non-existent staging_id does not raise.

## Commit Boundary

Single commit: `feat(staging): implement crop artifact staging service with opaque staging_id`

## Rollback

Reverting removes the staging service and DTO. No existing code depends on them yet.

## Out of Scope

- Artifact promotion to permanent storage (handled by publication service in ticket 06).
- Session management, ReviewWorkspaceSession (10E.3b).
- Publication intent manifests (ticket 06).

# ============================================================
#  tests/unit/test_crop_artifact_staging_service.py
#  Tests for Crop Artifact Staging Service with opaque staging_id
# ============================================================

import hashlib
from pathlib import Path
import pytest

from application.dto.staged_crop import StagedCropHandle
from application.services.crop_artifact_staging_service import CropArtifactStagingService


@pytest.fixture
def staging_service(tmp_path: Path) -> CropArtifactStagingService:
    return CropArtifactStagingService(base_dir=tmp_path)


def test_staging_creates_file(staging_service: CropArtifactStagingService):
    data = b"fake-jpeg-image-bytes-12345"
    handle = staging_service.stage_crop(
        job_id=42,
        staging_id="staging-session-abc",
        region_id="reg-001",
        version=2,
        image_bytes=data,
    )

    assert isinstance(handle, StagedCropHandle)
    assert handle.staging_id == "staging-session-abc"
    assert handle.region_id == "reg-001"
    assert handle.artifact_version == 2
    assert handle.dest_filename == "crop_reg-001_v2.jpg"
    assert handle.size_bytes == len(data)
    assert Path(handle.staging_path).exists()
    assert Path(handle.staging_path).read_bytes() == data


def test_staging_sha256_computed(staging_service: CropArtifactStagingService):
    data = b"image-content-for-hash-verification"
    expected_hash = hashlib.sha256(data).hexdigest()

    handle = staging_service.stage_crop(
        job_id=10,
        staging_id="session-hash-test",
        region_id="reg-hash",
        version=1,
        image_bytes=data,
    )

    assert handle.sha256 == expected_hash


def test_staging_directory_isolation(staging_service: CropArtifactStagingService):
    data1 = b"img-1"
    data2 = b"img-2"

    h1 = staging_service.stage_crop(1, "session-1", "reg-1", 1, data1)
    h2 = staging_service.stage_crop(1, "session-2", "reg-1", 1, data2)

    p1 = Path(h1.staging_path)
    p2 = Path(h2.staging_path)

    assert p1.parent != p2.parent
    assert p1.read_bytes() == data1
    assert p2.read_bytes() == data2


def test_staging_discard_cleanup(staging_service: CropArtifactStagingService):
    staging_id = "session-to-discard"
    handle = staging_service.stage_crop(1, staging_id, "r1", 1, b"discard-me")
    staging_file = Path(handle.staging_path)
    staging_dir = staging_file.parent

    assert staging_file.exists()
    assert staging_dir.exists()

    staging_service.discard_staging(staging_id)

    assert not staging_file.exists()
    assert not staging_dir.exists()


def test_staging_list_handles(staging_service: CropArtifactStagingService):
    s_id = "session-list"
    h1 = staging_service.stage_crop(1, s_id, "reg-A", 1, b"bytes-A")
    h2 = staging_service.stage_crop(1, s_id, "reg-B", 2, b"bytes-B")

    staged_items = staging_service.list_staged(s_id)
    assert len(staged_items) == 2
    items_by_region = {item.region_id: item for item in staged_items}

    assert "reg-A" in items_by_region
    assert items_by_region["reg-A"].artifact_version == 1
    assert items_by_region["reg-A"].dest_filename == "crop_reg-A_v1.jpg"

    assert "reg-B" in items_by_region
    assert items_by_region["reg-B"].artifact_version == 2
    assert items_by_region["reg-B"].dest_filename == "crop_reg-B_v2.jpg"


def test_staging_path_traversal_rejected(staging_service: CropArtifactStagingService):
    with pytest.raises(ValueError, match="Path traversal"):
        staging_service.stage_crop(1, "session-x", "../evil", 1, b"data")

    with pytest.raises(ValueError, match="Path traversal"):
        staging_service.stage_crop(1, "../../escape", "reg", 1, b"data")


def test_staging_dot_identifier_rejected(staging_service: CropArtifactStagingService):
    with pytest.raises(ValueError, match="Path traversal or dot-file"):
        staging_service.stage_crop(1, ".", "reg", 1, b"data")

    with pytest.raises(ValueError, match="Path traversal or dot-file"):
        staging_service.stage_crop(1, "valid-session", ".", 1, b"data")

    with pytest.raises(ValueError, match="Path traversal or dot-file"):
        staging_service.stage_crop(1, ".hidden-session", "reg", 1, b"data")


def test_discard_staging_never_deletes_base_dir(staging_service: CropArtifactStagingService):
    base = staging_service.base_dir
    assert base.exists()

    staging_service.discard_staging(".")
    assert base.exists()

    staging_service.discard_staging("..")
    assert base.exists()


def test_staging_invalid_windows_characters_rejected(staging_service: CropArtifactStagingService):
    invalid_chars = [":", "*", "?", '"', "<", ">", "|"]
    for c in invalid_chars:
        with pytest.raises(ValueError, match="invalid characters"):
            staging_service.stage_crop(1, f"session{c}bad", "reg", 1, b"data")
        with pytest.raises(ValueError, match="invalid characters"):
            staging_service.stage_crop(1, "valid-session", f"reg{c}bad", 1, b"data")


def test_staging_parameter_validations(staging_service: CropArtifactStagingService):
    # Job ID validation
    with pytest.raises(ValueError, match="job_id must be a positive integer"):
        staging_service.stage_crop(0, "session", "reg", 1, b"data")

    with pytest.raises(ValueError, match="job_id must be a positive integer"):
        staging_service.stage_crop(-5, "session", "reg", 1, b"data")

    # Version validation
    with pytest.raises(ValueError, match="version must be an integer >= 1"):
        staging_service.stage_crop(1, "session", "reg", 0, b"data")

    with pytest.raises(ValueError, match="version must be an integer >= 1"):
        staging_service.stage_crop(1, "session", "reg", -1, b"data")


def test_staging_idempotent_discard(staging_service: CropArtifactStagingService):
    staging_service.discard_staging("non-existent-session-id")

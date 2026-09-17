# ============================================================
#  tests/unit/test_canonical_markdown_version.py
#  Unit tests for Canonical Markdown version parsing and OCC snapshot
# ============================================================

import pytest
from core.entities.job import Job, JobStatus
from core.exceptions.domain_exceptions import DomainError, StaleDocumentVersionError
from core.markdown.version import (
    CanonicalMarkdownSnapshot,
    capture_canonical_markdown_snapshot,
    parse_canonical_markdown_version,
)


class TestCanonicalMarkdownVersionParsing:
    def test_versioned_canonical_output_uri(self):
        assert parse_canonical_markdown_version("output_1_v3.md") == 3
        assert parse_canonical_markdown_version("file:///var/data/polpot/output_42_v15.md") == 15
        assert parse_canonical_markdown_version("/home/user/artifacts/output_100_v1.md") == 1
        assert parse_canonical_markdown_version(r"C:\polpot\output_7_v4.md") == 4

    def test_unversioned_canonical_output_defaults_to_v1(self):
        assert parse_canonical_markdown_version("output_1.md") == 1
        assert parse_canonical_markdown_version("file:///var/data/polpot/output_42.md") == 1
        assert parse_canonical_markdown_version("/home/user/artifacts/output_999.md") == 1

    def test_arbitrary_and_page_files_not_classified_as_canonical(self):
        # Crucial invariant: page slices and crop files are NOT canonical document outputs
        assert parse_canonical_markdown_version("page_1.md") == 0
        assert parse_canonical_markdown_version("page_1_v2.md") == 0
        assert parse_canonical_markdown_version("crop_1_reg1_v3.jpg") == 0
        assert parse_canonical_markdown_version("source.pdf") == 0
        assert parse_canonical_markdown_version("artifact_123.bin") == 0
        assert parse_canonical_markdown_version("output_1_v2.txt") == 0

    def test_none_and_empty_output_path(self):
        assert parse_canonical_markdown_version(None) == 0
        assert parse_canonical_markdown_version("") == 0
        assert parse_canonical_markdown_version("   ") == 0


class TestCanonicalMarkdownSnapshot:
    def test_snapshot_capture_helper(self):
        snap = capture_canonical_markdown_snapshot("file:///path/output_1_v4.md")
        assert snap.output_path == "file:///path/output_1_v4.md"
        assert snap.active_version == 4

        snap_none = capture_canonical_markdown_snapshot(None)
        assert snap_none.output_path is None
        assert snap_none.active_version == 0

    def test_snapshot_is_immutable(self):
        snap = CanonicalMarkdownSnapshot(output_path="output_1_v2.md", active_version=2)
        with pytest.raises(AttributeError):
            snap.active_version = 3  # frozen dataclass


class TestJobEntityActiveMarkdownVersion:
    def test_job_active_markdown_version_property(self):
        job = Job(
            id=1,
            file_name="doc.pdf",
            file_path="file:///doc.pdf",
            output_path="file:///artifacts/output_1_v5.md",
        )
        assert job.active_markdown_version == 5

        job_unversioned = Job(
            id=2,
            file_name="doc.pdf",
            file_path="file:///doc.pdf",
            output_path="file:///artifacts/output_2.md",
        )
        assert job_unversioned.active_markdown_version == 1

        job_none = Job(
            id=3,
            file_name="doc.pdf",
            file_path="file:///doc.pdf",
            output_path=None,
        )
        assert job_none.active_markdown_version == 0


class TestStaleDocumentVersionError:
    def test_error_attributes_and_inheritance(self):
        err = StaleDocumentVersionError(job_id=42, base_version=3, current_version=4)
        assert isinstance(err, DomainError)
        assert err.job_id == 42
        assert err.base_version == 3
        assert err.current_version == 4
        assert "base version 3 is stale" in str(err)

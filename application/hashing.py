# ============================================================
#  application/hashing.py
#  Shared Application Hashing Utilities
# ============================================================

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_file_sha256(path: Path) -> str:
    """
    Computes the SHA-256 digest of a local file in 64 KiB chunks.
    A 0-byte file deterministically produces the standard empty string digest
    ('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855').
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

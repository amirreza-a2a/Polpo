#!/usr/bin/env python3
"""Deterministic Runtime Binary Acquisition and Staging Script.

Fetches, validates via SHA-256 digests, and stages the standalone third-party
runtime binaries (Pandoc 3.1.11 and Node.js 22.23.2 LTS) declared in
`resources/runtimes.json`.

Supports Linux x86_64, Windows x64, macOS x86_64, and macOS ARM64 targets.
Enforces archive path-traversal safety (anti-zip/tar-slip), atomic destination
publication, executable POSIX permissions, and optional MathJax npm dependency
installation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence
import urllib.error
import urllib.request
import zipfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_runtimes")


class ChecksumMismatchError(RuntimeError):
    """Raised when a downloaded archive's SHA-256 digest does not match the manifest."""
    pass


class UnsafeArchiveError(RuntimeError):
    """Raised when an archive member contains unsafe absolute or path-traversal paths."""
    pass


class BinaryNotFoundError(FileNotFoundError):
    """Raised when the expected binary subpath cannot be located in the extracted archive."""
    pass


class UnsupportedPlatformError(ValueError):
    """Raised when an unsupported platform identifier is requested."""
    pass


@dataclass
class RuntimeAcquisitionReport:
    """Diagnostic report for a runtime acquisition step."""
    runtime: str
    platform: str
    target_path: Path
    success: bool
    version: Optional[str] = None
    error: Optional[str] = None


def detect_current_platform() -> str:
    """Detects the current host platform matching runtimes.json platform keys."""
    os_name = sys.platform
    machine = platform.machine().lower()

    if os_name.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
    elif os_name in ("win32", "cygwin") or os.name == "nt":
        if machine in ("x86_64", "amd64", "x64"):
            return "windows-x64"
    elif os_name == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        elif machine in ("x86_64", "amd64"):
            return "darwin-x86_64"

    return f"unknown-{os_name}-{machine}"


def calculate_sha256(file_path: Path) -> str:
    """Calculates the lowercase hex SHA-256 digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def download_to_file(
    url: str,
    dest_path: Path,
    timeout: float = 60.0,
    max_retries: int = 3,
    chunk_size: int = 65536,
) -> None:
    """Downloads a file from a URL to dest_path with retry logic and timeout."""
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Downloading %s (attempt %d/%d)...", url, attempt, max_retries)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Polpo-Runtime-Acquisition/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                with dest_path.open("wb") as out_file:
                    while chunk := response.read(chunk_size):
                        out_file.write(chunk)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            logger.warning("Download failed on attempt %d: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"Failed to download {url} after {max_retries} attempts: {last_err}")


def _is_safe_tar_member(member: tarfile.TarInfo, target_dir: Path) -> bool:
    """Validates that a tar member does not traverse outside target_dir."""
    name = member.name
    if name.startswith("/") or name.startswith("\\"):
        return False
    if ":" in name:  # Windows drive letters or alternate data streams
        return False
    dest = (target_dir / name).resolve()
    try:
        dest.relative_to(target_dir.resolve())
    except ValueError:
        return False

    # Check symlinks and hardlinks for escape from target_dir
    if member.issym() or member.islnk():
        link_target = member.linkname
        if link_target.startswith("/") or link_target.startswith("\\") or ":" in link_target:
            return False
        member_dir = (target_dir / name).parent
        resolved_link = (member_dir / link_target).resolve()
        try:
            resolved_link.relative_to(target_dir.resolve())
        except ValueError:
            return False

    return True


def _is_safe_zip_member(member: zipfile.ZipInfo, target_dir: Path) -> bool:
    """Validates that a zip member does not traverse outside target_dir."""
    name = member.filename
    if name.startswith("/") or name.startswith("\\"):
        return False
    if ":" in name:  # Windows drive letters
        return False
    dest = (target_dir / name).resolve()
    try:
        dest.relative_to(target_dir.resolve())
        return True
    except ValueError:
        return False


def safe_extract_archive(archive_path: Path, archive_type: str, dest_dir: Path) -> None:
    """Extracts a tar.gz or zip archive into dest_dir safely, preventing directory traversal."""
    dest_dir_resolved = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    if archive_type == "tar.gz" or archive_path.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not _is_safe_tar_member(member, dest_dir_resolved):
                    raise UnsafeArchiveError(
                        f"Unsafe path traversal detected in tar archive member: {member.name}"
                    )
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=dest_dir, filter="data")
            else:
                tar.extractall(path=dest_dir)

    elif archive_type == "zip" or archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                if not _is_safe_zip_member(member, dest_dir_resolved):
                    raise UnsafeArchiveError(
                        f"Unsafe path traversal detected in zip archive member: {member.filename}"
                    )
            zf.extractall(path=dest_dir)
    else:
        raise ValueError(f"Unsupported archive type: {archive_type}")


class RuntimeAcquirer:
    """Manages acquisition and staging of runtimes defined in runtimes.json."""

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        manifest_data: Optional[Dict[str, Any]] = None,
        repo_root: Optional[Path] = None,
    ):
        if repo_root is None:
            self.repo_root = Path(__file__).resolve().parent.parent
        else:
            self.repo_root = Path(repo_root).resolve()

        if manifest_data is not None:
            self.manifest = manifest_data
        else:
            if manifest_path is None:
                manifest_path = self.repo_root / "resources" / "runtimes.json"
            if not manifest_path.is_file():
                raise FileNotFoundError(f"Runtimes manifest not found: {manifest_path}")
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def acquire_runtime(
        self,
        runtime_name: str,
        platform_name: str,
        force: bool = False,
        timeout: float = 60.0,
    ) -> RuntimeAcquisitionReport:
        """Acquires, validates, and stages a single runtime for a target platform."""
        runtimes_section = self.manifest.get("runtimes", {})
        if runtime_name not in runtimes_section:
            raise ValueError(f"Unknown runtime: '{runtime_name}'. Available: {list(runtimes_section.keys())}")

        runtime_entry = runtimes_section[runtime_name]
        version = runtime_entry.get("version", "unknown")
        platforms = runtime_entry.get("platforms", {})

        if platform_name not in platforms:
            raise UnsupportedPlatformError(
                f"Platform '{platform_name}' is not supported for {runtime_name}. Supported: {list(platforms.keys())}"
            )

        plat_spec = platforms[platform_name]
        url = plat_spec["url"]
        expected_sha = plat_spec["sha256"].lower()
        archive_type = plat_spec["archive_type"]
        binary_subpath = plat_spec["binary_subpath"]
        target_path = self.repo_root / plat_spec["target_path"]

        # Check if already present and valid
        if not force and target_path.is_file():
            logger.info(
                "Runtime '%s' (%s) already present at %s. Use --force to re-acquire.",
                runtime_name,
                platform_name,
                target_path,
            )
            return RuntimeAcquisitionReport(
                runtime=runtime_name,
                platform=platform_name,
                target_path=target_path,
                success=True,
                version=version,
            )

        # Download and stage in isolated temporary directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            archive_ext = ".tar.gz" if archive_type == "tar.gz" else ".zip"
            archive_dest = tmp_root / f"archive_{runtime_name}{archive_ext}"

            # 1. Download
            download_to_file(url, archive_dest, timeout=timeout)

            # 2. Strict SHA-256 verification before extraction
            actual_sha = calculate_sha256(archive_dest)
            if actual_sha != expected_sha:
                raise ChecksumMismatchError(
                    f"SHA-256 digest mismatch for {runtime_name} ({platform_name}): "
                    f"expected {expected_sha}, got {actual_sha}"
                )
            logger.info("SHA-256 verified for %s (%s): %s", runtime_name, platform_name, actual_sha)

            # 3. Safe extraction
            extract_dir = tmp_root / "extracted"
            safe_extract_archive(archive_dest, archive_type, extract_dir)

            # 4. Locate expected binary
            expected_binary = extract_dir / binary_subpath
            if not expected_binary.is_file():
                # Check if executable exists under extracted directory with matching basename
                candidate_name = Path(binary_subpath).name
                candidates = [p for p in extract_dir.rglob(candidate_name) if p.is_file()]
                if len(candidates) == 1:
                    expected_binary = candidates[0]
                elif len(candidates) > 1:
                    raise BinaryNotFoundError(
                        f"Ambiguous executable discovery for '{binary_subpath}' in {runtime_name} archive: "
                        f"found {len(candidates)} candidates: {[str(c.relative_to(extract_dir)) for c in candidates]}"
                    )
                else:
                    raise BinaryNotFoundError(
                        f"Expected executable '{binary_subpath}' not found in extracted archive for {runtime_name}."
                    )

            # 5. Atomic publication into target path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_target = target_path.with_name(f".tmp_{target_path.name}_{os.getpid()}")
            try:
                shutil.copy2(expected_binary, temp_target)

                # Set POSIX executable permissions (rwxr-xr-x)
                if os.name == "posix":
                    current_mode = temp_target.stat().st_mode
                    temp_target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                os.replace(temp_target, target_path)
            finally:
                if temp_target.exists():
                    temp_target.unlink()

        # 6. Verify resulting binary and report probed version where practical (host platform)
        probed_version = version
        if platform_name == detect_current_platform():
            try:
                proc = subprocess.run(
                    [str(target_path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    first_line = proc.stdout.strip().splitlines()[0] if proc.stdout else ""
                    logger.info("Verified %s executable on host: %s", runtime_name, first_line)
                    probed_version = first_line
                else:
                    logger.warning("Failed to execute %s (--version exited with %d): %s", runtime_name, proc.returncode, proc.stderr)
            except Exception as exc:
                logger.warning("Could not probe version for %s: %s", runtime_name, exc)
        else:
            logger.info("Skipping execution verification of %s for foreign platform '%s'", runtime_name, platform_name)

        logger.info("Successfully staged %s (%s) -> %s", runtime_name, platform_name, target_path)
        return RuntimeAcquisitionReport(
            runtime=runtime_name,
            platform=platform_name,
            target_path=target_path,
            success=True,
            version=probed_version,
        )

    def acquire_all(
        self,
        platform_name: str,
        force: bool = False,
        timeout: float = 60.0,
    ) -> List[RuntimeAcquisitionReport]:
        """Acquires all declared runtimes for the specified platform."""
        reports: List[RuntimeAcquisitionReport] = []
        for runtime_name in self.manifest.get("runtimes", {}):
            report = self.acquire_runtime(
                runtime_name=runtime_name,
                platform_name=platform_name,
                force=force,
                timeout=timeout,
            )
            reports.append(report)
        return reports


def install_mathjax_dependencies(repo_root: Optional[Path] = None, force: bool = False) -> None:
    """Installs pinned MathJax npm dependencies in resources/mathjax using lockfile."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    mathjax_dir = repo_root / "resources" / "mathjax"
    lockfile = mathjax_dir / "package-lock.json"
    if not lockfile.is_file():
        raise FileNotFoundError(f"MathJax package-lock.json not found: {lockfile}")

    mathjax_full = mathjax_dir / "node_modules" / "mathjax-full"
    if not force and mathjax_full.is_dir():
        logger.info("MathJax production dependencies already present in %s (skipping npm ci)", mathjax_full)
        return

    cmd = ["npm", "ci", "--omit=dev", "--prefix", str(mathjax_dir)]
    logger.info("Installing production MathJax npm dependencies: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"npm ci failed (exit code {result.returncode}):\n{result.stderr}")
    logger.info("MathJax npm dependencies successfully installed in %s", mathjax_dir / "node_modules")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for scripts/fetch_runtimes.py."""
    parser = argparse.ArgumentParser(
        description="Fetch, verify SHA-256, and stage standalone runtimes (Pandoc and Node.js) for Polpo."
    )
    parser.add_argument(
        "--platform",
        default=detect_current_platform(),
        help="Target platform identifier (linux-x86_64, windows-x64, darwin-x86_64, darwin-arm64, or 'all'). Default: host platform.",
    )
    parser.add_argument(
        "--runtime",
        choices=["pandoc", "node", "all"],
        default="all",
        help="Specific runtime to acquire (default: all).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to custom runtimes.json manifest.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if target binary is already present.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Network download timeout in seconds (default: 60.0).",
    )
    parser.add_argument(
        "--install-mathjax-deps",
        action="store_true",
        help="Opt-in: install MathJax production npm dependencies via 'npm ci --omit=dev'.",
    )
    parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="Explicitly skip npm dependencies installation.",
    )

    args = parser.parse_args(argv)

    try:
        acquirer = RuntimeAcquirer(manifest_path=args.manifest)

        platforms_to_fetch = []
        if args.platform == "all":
            platforms_to_fetch = ["linux-x86_64", "windows-x64", "darwin-x86_64", "darwin-arm64"]
        else:
            platforms_to_fetch = [args.platform]

        for plat in platforms_to_fetch:
            logger.info("Processing platform: %s", plat)
            if args.runtime == "all":
                acquirer.acquire_all(platform_name=plat, force=args.force, timeout=args.timeout)
            else:
                acquirer.acquire_runtime(
                    runtime_name=args.runtime,
                    platform_name=plat,
                    force=args.force,
                    timeout=args.timeout,
                )

        if args.install_mathjax_deps and not args.skip_npm:
            install_mathjax_dependencies(force=args.force)

        logger.info("Runtime acquisition completed successfully.")
        return 0
    except Exception as exc:
        logger.error("Runtime acquisition failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

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


class NpmNotFoundError(FileNotFoundError):
    """Raised when neither npm nor Node/npm-cli.js can be located on the system."""
    pass


class NpmExecutionError(RuntimeError):
    """Raised when npm execution returns a non-zero exit code."""
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


def _find_node_executable(repo_root: Path, is_windows: bool) -> Optional[Path]:
    """Finds a candidate Node.js executable."""
    ext = ".exe" if is_windows else ""

    # 1. Environment variable override
    env_node = os.environ.get("POLPO_NODE_PATH")
    if env_node:
        p = Path(env_node).expanduser().resolve()
        if p.is_file():
            if not is_windows or str(p).lower().endswith(".exe"):
                return p
        if p.is_dir() and (p / f"node{ext}").is_file():
            return (p / f"node{ext}").resolve()

    # 2. PATH lookup
    if is_windows:
        cands = ["node.exe"]
        if os.name == "nt":
            cands.append("node")
    else:
        cands = ["node"]

    for cand in cands:
        which_path = shutil.which(cand)
        if which_path:
            p = Path(which_path).resolve()
            if is_windows and not str(p).lower().endswith(".exe"):
                continue
            if p.is_file():
                return p

    # 3. Executable adjacent to npm/npm.cmd on PATH
    if is_windows:
        npm_cands = ["npm.cmd", "npm.bat", "npm.exe"]
        if os.name == "nt":
            npm_cands.append("npm")
        for npm_cand in npm_cands:
            npm_path = shutil.which(npm_cand)
            if npm_path:
                p = (Path(npm_path).parent / f"node{ext}").resolve()
                if p.is_file() and str(p).lower().endswith(".exe"):
                    return p

    # 4. Bundled or staged runtime in resources/mathjax/node[.exe]
    staged = repo_root / "resources" / "mathjax" / f"node{ext}"
    if staged.is_file():
        if not is_windows or str(staged).lower().endswith(".exe"):
            return staged.resolve()

    # 5. Standard Windows installation paths
    if is_windows:
        for env_var in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]:
            base = os.environ.get(env_var)
            if base:
                cand = (Path(base) / "nodejs" / f"node{ext}").resolve()
                if cand.is_file() and str(cand).lower().endswith(".exe"):
                    return cand

    return None


def _find_npm_cli_js(repo_root: Path, node_exe: Optional[Path], is_windows: bool) -> Optional[Path]:
    """Finds the npm-cli.js entrypoint script."""
    candidates: List[Path] = []

    # 0. Environment variable override
    env_npm = os.environ.get("POLPO_NPM_CLI_PATH") or os.environ.get("POLPO_NPM_PATH")
    if env_npm:
        p = Path(env_npm).expanduser().resolve()
        if p.is_file() and p.name == "npm-cli.js":
            return p
        if p.is_dir() and (p / "bin" / "npm-cli.js").is_file():
            return (p / "bin" / "npm-cli.js").resolve()
        if p.is_dir() and (p / "npm-cli.js").is_file():
            return (p / "npm-cli.js").resolve()

    # 1. Relative to discovered node executable
    if node_exe:
        node_dir = node_exe.parent
        if is_windows:
            # Windows layout (official zip, MSI, setup-node, Scoop): <node_dir>/node_modules/npm/bin/npm-cli.js
            candidates.append(node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js")
            candidates.append(node_dir / "node_modules" / "npm" / "index.js")
        else:
            # POSIX layout: <node_dir>/../lib/node_modules/npm/bin/npm-cli.js
            candidates.append(node_dir.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
            candidates.append(node_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
            candidates.append(node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js")

    # 2. Relative to npm / npm.cmd in PATH
    if is_windows:
        npm_names = ["npm.cmd", "npm.bat", "npm.exe"]
        if os.name == "nt":
            npm_names.append("npm")
    else:
        npm_names = ["npm"]

    for npm_name in npm_names:
        which_npm = shutil.which(npm_name)
        if which_npm:
            resolved_npm = Path(which_npm).resolve()
            if resolved_npm.is_file() and resolved_npm.name == "npm-cli.js":
                candidates.append(resolved_npm)

            npm_dir = Path(which_npm).parent
            if is_windows:
                candidates.append(npm_dir / "node_modules" / "npm" / "bin" / "npm-cli.js")
                candidates.append(npm_dir / "node_modules" / "npm" / "index.js")
            else:
                candidates.append(npm_dir.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
                candidates.append(npm_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
                candidates.append(npm_dir / "node_modules" / "npm" / "bin" / "npm-cli.js")

    # 3. Standard Windows system/user npm locations
    if is_windows:
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "node_modules" / "npm" / "bin" / "npm-cli.js")
        for env_var in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]:
            base = os.environ.get(env_var)
            if base:
                candidates.append(Path(base) / "nodejs" / "node_modules" / "npm" / "bin" / "npm-cli.js")

    # 4. Standard POSIX global paths
    if not is_windows:
        candidates.append(Path("/usr/local/lib/node_modules/npm/bin/npm-cli.js"))
        candidates.append(Path("/usr/lib/node_modules/npm/bin/npm-cli.js"))
        candidates.append(Path("/usr/share/nodejs/npm/bin/npm-cli.js"))

    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue

    return None


def resolve_npm_command(
    repo_root: Optional[Path] = None,
    sys_platform: Optional[str] = None,
) -> List[str]:
    """Resolves a cross-platform command prefix for invoking npm.

    On Windows:
      Directly invoking 'npm' via subprocess without shell=True fails with WinError 2
      because npm is distributed as a batch script ('npm.cmd'). Batch files also
      suffer from argument parsing vulnerabilities (CVE-2024-24576). The reliable,
      safe strategy on Windows is invoking Node with npm's JavaScript CLI entrypoint:
        [<node.exe>, <npm-cli.js>]
      If npm-cli.js is not found, a verified native npm.exe is accepted.
      Arbitrary execution of 'cmd.exe /c npm.cmd' is deliberately avoided to eliminate
      shell injection and batch argument parsing risks.

    On POSIX (Linux, macOS):
      'npm' is directly executable on PATH via shebang. If found, returns ['npm'].
      Falls back to [<node>, <npm-cli.js>] if npm is absent from PATH but Node is present.

    Environment Overrides:
      - POLPO_NODE_PATH: Explicit path to node / node.exe binary or directory.
      - POLPO_NPM_CLI_PATH: Explicit path to npm-cli.js or its containing directory.

    Raises:
      NpmNotFoundError: If neither npm nor Node/npm-cli.js can be located.
    """
    effective_platform = sys_platform if sys_platform is not None else sys.platform
    is_windows = effective_platform == "win32" or effective_platform.startswith("win")

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    if not is_windows:
        npm_path = shutil.which("npm")
        if npm_path:
            return ["npm"]

    # Search for Node executable and npm-cli.js (primary strategy on Windows; fallback on POSIX)
    node_exe = _find_node_executable(repo_root=repo_root, is_windows=is_windows)
    npm_cli_js = _find_npm_cli_js(repo_root=repo_root, node_exe=node_exe, is_windows=is_windows)

    if node_exe and npm_cli_js:
        logger.debug("Resolved npm invocation via Node CLI: %s %s", node_exe, npm_cli_js)
        return [str(node_exe), str(npm_cli_js)]

    if is_windows:
        # Check for native npm.exe (e.g. Volta, Scoop shim)
        cand_exes = ["npm.exe"]
        if os.name == "nt":
            cand_exes.append("npm")
        for cand in cand_exes:
            p = shutil.which(cand)
            if p and p.lower().endswith(".exe") and Path(p).is_file():
                return [p]

        raise NpmNotFoundError(
            "npm could not be located on Windows. "
            "Neither Node.js with 'npm-cli.js' nor native 'npm.exe' was found. "
            "Please ensure Node.js 22.x LTS is installed and present in PATH."
        )

    # POSIX fallback error when npm is not found
    raise NpmNotFoundError(
        "npm could not be located on PATH or via Node.js installation. "
        "Please ensure Node.js 22.x LTS and npm are installed."
    )


def install_mathjax_dependencies(
    repo_root: Optional[Path] = None,
    force: bool = False,
    timeout: Optional[float] = None,
    sys_platform: Optional[str] = None,
) -> None:
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

    npm_base_cmd = resolve_npm_command(repo_root=repo_root, sys_platform=sys_platform)
    cmd = npm_base_cmd + ["ci", "--omit=dev", "--prefix", str(mathjax_dir)]
    logger.info("Installing production MathJax npm dependencies: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_root),
        )
    except FileNotFoundError as exc:
        raise NpmNotFoundError(
            f"Failed to execute npm command '{cmd[0]}': {exc}. "
            "Please ensure Node.js and npm are installed and available in PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        stdout_str = exc.stdout or ""
        stderr_str = exc.stderr or ""
        details = []
        if stdout_str.strip():
            details.append(f"stdout:\n{stdout_str.strip()}")
        if stderr_str.strip():
            details.append(f"stderr:\n{stderr_str.strip()}")
        details_text = "\n".join(details)
        details_msg = f"\n{details_text}" if details_text else ""
        raise NpmExecutionError(f"npm ci timed out after {timeout} seconds:{details_msg}") from exc

    if result.returncode != 0:
        details = []
        if result.stdout and result.stdout.strip():
            details.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr and result.stderr.strip():
            details.append(f"stderr:\n{result.stderr.strip()}")
        details_text = "\n".join(details)
        details_msg = f"\n{details_text}" if details_text else ""
        raise NpmExecutionError(f"npm ci failed (exit code {result.returncode}):{details_msg}")

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
            install_mathjax_dependencies(repo_root=acquirer.repo_root, force=args.force)

        logger.info("Runtime acquisition completed successfully.")
        return 0
    except Exception as exc:
        logger.error("Runtime acquisition failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

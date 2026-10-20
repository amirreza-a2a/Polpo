#!/usr/bin/env python3
"""Reproducible Package Builder and Preflight Validation Runner for Polpo Desktop.

Executes deterministic packaging preflight checks, invokes PyInstaller with
`packaging/polpo.spec` to build a standalone `--onedir` bundle, produces
standard release archives (`Polpo-v{version}-{platform}.tar.gz` or `.zip`),
and generates SHA-256 checksum sidecars.

Normalizes archive timestamps and member ordering so repeated packaging of
identical staged bundle contents yields deterministic archive metadata and digests.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, List, Optional, Sequence, Tuple
import zipfile

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_package")


class PreflightCheckError(RuntimeError):
    """Raised when one or more mandatory preflight packaging prerequisites are unmet."""
    pass


@dataclass
class PreflightReport:
    """Diagnostic report detailing preflight validation outcomes."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def detect_host_platform() -> str:
    """Returns normalized host platform key matching runtimes.json."""
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


def get_archive_filename(version: str, target_platform: str) -> str:
    """Generates the authoritative release archive filename."""
    if target_platform == "windows-x64" or target_platform.startswith("windows"):
        return f"Polpo-v{version}-{target_platform}.zip"
    return f"Polpo-v{version}-{target_platform}.tar.gz"


def generate_checksum_sidecar(archive_path: Path) -> Path:
    """Calculates SHA-256 and writes standard GNU sha256sum sidecar file."""
    hasher = hashlib.sha256()
    with archive_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest().lower()

    sidecar_path = archive_path.with_name(f"{archive_path.name}.sha256")
    sidecar_content = f"{digest}  {archive_path.name}\n"
    sidecar_path.write_text(sidecar_content, encoding="utf-8")
    logger.info("Generated SHA-256 sidecar: %s -> %s", sidecar_path.name, digest)
    return sidecar_path


def run_preflight_checks(
    repo_root: Optional[Path] = None,
    target_platform: Optional[str] = None,
) -> PreflightReport:
    """Validates that all mandatory assets, migrations, runtimes, and licenses are staged."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    if target_platform is None:
        target_platform = detect_host_platform()

    errors: List[str] = []
    warnings: List[str] = []

    # 1. Spec file existence
    spec_path = repo_root / "packaging" / "polpo.spec"
    if not spec_path.is_file():
        errors.append(f"PyInstaller spec file missing: {spec_path}")

    # 2. Licensing and documentation
    for doc in ["LICENSE", "README.md", "CHANGELOG.md"]:
        doc_path = repo_root / doc
        if not doc_path.is_file():
            errors.append(f"Required release document missing: {doc_path}")

    # 3. QML entry interface
    qml_path = repo_root / "interfaces" / "desktop" / "qml" / "Main.qml"
    if not qml_path.is_file():
        errors.append(f"Desktop interface Main.qml missing: {qml_path}")

    # 4. SQLite schema migrations
    required_migrations = [
        "001_initial_schema.sql",
        "002_visual_regions.sql",
        "003_artifact_version_watermarks.sql",
        "004_publish_intents_and_document_versions.sql",
    ]
    migrations_dir = repo_root / "infrastructure" / "persistence" / "sqlite" / "migrations"
    if not migrations_dir.is_dir():
        errors.append(f"SQLite migrations directory missing: {migrations_dir}")
    else:
        for m in required_migrations:
            if not (migrations_dir / m).is_file():
                errors.append(f"Required SQLite migration missing: {migrations_dir / m}")

    # 5. Templates and runtime acquisition manifest
    preamble_path = repo_root / "resources" / "templates" / "polpo_preamble.tex"
    if not preamble_path.is_file():
        errors.append(f"LaTeX typesetting preamble missing: {preamble_path}")

    manifest_path = repo_root / "resources" / "runtimes.json"
    if not manifest_path.is_file():
        errors.append(f"Runtimes acquisition manifest missing: {manifest_path}")

    # 6. Curated prompt templates (9 files)
    prompts_dir = repo_root / "prompts"
    required_prompts = [
        "templates/fa/stem_transcription.txt",
        "templates/fa/stem_transcription_with_figure_description.txt",
        "templates/fa/stem_transcription_with_figure_description_and_netlist.txt",
        "templates/fa/educational_stem_transcription_griffiths.txt",
        "templates/fa/electromagnetics_griffiths_editor.txt",
        "templates/fa/stem_transcription_tikz.txt",
        "templates/fa/quick_convert.txt",
        "templates/en/stem_handwritten_transcription.txt",
        "pipeline2/unify_markdown.txt",
    ]
    for p in required_prompts:
        prompt_file = prompts_dir / p
        if not prompt_file.is_file():
            errors.append(f"Required curated prompt template missing: {prompt_file}")

    # 7. Staged Pandoc binary for target platform
    if target_platform == "windows-x64":
        pandoc_bin = repo_root / "resources" / "bin" / "windows-x64" / "pandoc.exe"
        node_bin = repo_root / "resources" / "mathjax" / "node.exe"
    elif target_platform == "linux-x86_64":
        pandoc_bin = repo_root / "resources" / "bin" / "linux-x86_64" / "pandoc"
        node_bin = repo_root / "resources" / "mathjax" / "node"
    elif target_platform in ("darwin-x86_64", "darwin-arm64"):
        pandoc_bin = repo_root / "resources" / "bin" / target_platform / "pandoc"
        node_bin = repo_root / "resources" / "mathjax" / "node"
    else:
        pandoc_bin = repo_root / "resources" / "bin" / target_platform / "pandoc"
        node_bin = repo_root / "resources" / "mathjax" / "node"

    if not pandoc_bin.is_file():
        errors.append(
            f"Staged Pandoc binary missing for {target_platform}: {pandoc_bin}. "
            f"Run 'python scripts/fetch_runtimes.py --platform {target_platform}' to stage."
        )

    # 8. Staged Node.js binary for target platform
    if not node_bin.is_file():
        errors.append(
            f"Staged Node.js binary missing for {target_platform}: {node_bin}. "
            f"Run 'python scripts/fetch_runtimes.py --platform {target_platform}' to stage."
        )

    # 9. MathJax worker and production node_modules
    mathjax_dir = repo_root / "resources" / "mathjax"
    if not (mathjax_dir / "mathjax_worker.js").is_file():
        errors.append(f"MathJax worker script missing: {mathjax_dir / 'mathjax_worker.js'}")
    if not (mathjax_dir / "package.json").is_file():
        errors.append(f"MathJax package.json missing: {mathjax_dir / 'package.json'}")
    if not (mathjax_dir / "package-lock.json").is_file():
        errors.append(f"MathJax package-lock.json missing: {mathjax_dir / 'package-lock.json'}")

    mathjax_full = mathjax_dir / "node_modules" / "mathjax-full"
    if not mathjax_full.is_dir():
        errors.append(
            f"MathJax production dependency 'mathjax-full' missing: {mathjax_full}. "
            f"Run 'python scripts/fetch_runtimes.py --install-mathjax-deps' to stage."
        )

    # 10. Linux packaging and installer scripts
    if target_platform == "linux-x86_64":
        deps_script = repo_root / "scripts" / "polpo_linux_deps.sh"
        if not deps_script.is_file():
            errors.append(f"Linux dependency resolver script missing: {deps_script}")
        installer_script = repo_root / "install.sh"
        if not installer_script.is_file():
            errors.append(f"Linux installer script missing: {installer_script}")

    is_valid = len(errors) == 0
    return PreflightReport(is_valid=is_valid, errors=errors, warnings=warnings)


import gzip

def create_deterministic_tar_gz(
    source_dir: Path,
    out_path: Path,
    root_arcname: str = "Polpo",
    fixed_mtime: int = 1700000000,
) -> None:
    """Creates a deterministic tar.gz archive with normalized gzip header, mtime, ownership, and permissions."""
    source_dir = source_dir.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_entries = sorted(source_dir.rglob("*"), key=lambda p: str(p.relative_to(source_dir)))

    with out_path.open("wb") as raw_f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=fixed_mtime) as gz_f:
            with tarfile.open(mode="w:", fileobj=gz_f) as tar:
                # Add root container directory
                root_ti = tarfile.TarInfo(name=root_arcname)
                root_ti.type = tarfile.DIRTYPE
                root_ti.mode = 0o755
                root_ti.mtime = fixed_mtime
                root_ti.uid = 0
                root_ti.gid = 0
                root_ti.uname = ""
                root_ti.gname = ""
                tar.addfile(root_ti)

                for path in all_entries:
                    rel = path.relative_to(source_dir)
                    arcname = f"{root_arcname}/{rel.as_posix()}"

                    if path.is_symlink():
                        ti = tarfile.TarInfo(name=arcname)
                        ti.type = tarfile.SYMTYPE
                        ti.linkname = os.readlink(path)
                        ti.mtime = fixed_mtime
                        ti.uid = 0
                        ti.gid = 0
                        ti.uname = ""
                        ti.gname = ""
                        tar.addfile(ti)
                    elif path.is_dir():
                        ti = tarfile.TarInfo(name=arcname)
                        ti.type = tarfile.DIRTYPE
                        ti.mode = 0o755
                        ti.mtime = fixed_mtime
                        ti.uid = 0
                        ti.gid = 0
                        ti.uname = ""
                        ti.gname = ""
                        tar.addfile(ti)
                    elif path.is_file():
                        st = path.stat()
                        is_executable = bool(st.st_mode & 0o111)
                        ti = tarfile.TarInfo(name=arcname)
                        ti.type = tarfile.REGTYPE
                        ti.size = st.st_size
                        ti.mode = 0o755 if is_executable else 0o644
                        ti.mtime = fixed_mtime
                        ti.uid = 0
                        ti.gid = 0
                        ti.uname = ""
                        ti.gname = ""
                        with path.open("rb") as f:
                            tar.addfile(ti, f)


def create_deterministic_zip(
    source_dir: Path,
    out_path: Path,
    root_arcname: str = "Polpo",
    fixed_date_time: Tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0),
) -> None:
    """Creates a deterministic zip archive with normalized timestamp, file ordering, and permissions."""
    source_dir = source_dir.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_entries = sorted(source_dir.rglob("*"), key=lambda p: str(p.relative_to(source_dir)))

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in all_entries:
            rel = path.relative_to(source_dir)
            arcname = f"{root_arcname}/{rel.as_posix()}"

            zi = zipfile.ZipInfo(filename=arcname, date_time=fixed_date_time)
            if path.is_dir():
                zi.filename += "/"
                zi.external_attr = 0o755 << 16 | 0x10  # directory attribute
                zf.writestr(zi, b"")
            elif path.is_file():
                st = path.stat()
                is_executable = bool(st.st_mode & 0o111) or path.name.lower().endswith((".exe", ".dll"))
                permissions = 0o755 if is_executable else 0o644
                zi.external_attr = permissions << 16
                with path.open("rb") as f:
                    zf.writestr(zi, f.read())


def create_stable_alias(archive_path: Path, target_platform: str) -> Optional[Tuple[Path, Path]]:
    """Creates a stable download alias (e.g. polpo-linux-x86_64.tar.gz) and its SHA-256 sidecar."""
    if target_platform == "linux-x86_64":
        alias_name = "polpo-linux-x86_64.tar.gz"
    elif target_platform == "windows-x64":
        alias_name = "polpo-windows-x64.zip"
    else:
        return None

    alias_path = archive_path.with_name(alias_name)
    shutil.copy2(archive_path, alias_path)
    sidecar_path = generate_checksum_sidecar(alias_path)
    logger.info("Generated stable alias: %s and checksum: %s", alias_path.name, sidecar_path.name)
    return alias_path, sidecar_path


def build_package(
    repo_root: Optional[Path] = None,
    target_platform: Optional[str] = None,
    dist_dir: Optional[Path] = None,
    pyinstaller_bin: Optional[str] = None,
    create_alias: bool = False,
) -> Tuple[Path, Path]:
    """Runs preflight checks, executes PyInstaller, and produces a normalized archive + SHA-256 sidecar."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    if target_platform is None:
        target_platform = detect_host_platform()

    if dist_dir is None:
        dist_dir = repo_root / "dist"
    else:
        dist_dir = Path(dist_dir).resolve()

    host_platform = detect_host_platform()
    if target_platform != host_platform:
        raise RuntimeError(
            f"Cannot build package for target platform '{target_platform}' on host '{host_platform}'. "
            f"PyInstaller does not support cross-compilation."
        )

    # 1. Run Preflight Validation
    report = run_preflight_checks(repo_root=repo_root, target_platform=target_platform)
    if not report.is_valid:
        error_msg = "\n  - ".join(["Packaging preflight checks failed:"] + report.errors)
        logger.error(error_msg)
        raise PreflightCheckError(error_msg)

    # 2. Check PyInstaller availability
    if pyinstaller_bin is None:
        pyinstaller_bin = shutil.which("pyinstaller")
        if not pyinstaller_bin:
            venv_pyinstaller = repo_root / ".venv" / ("Scripts/pyinstaller.exe" if os.name == "nt" else "bin/pyinstaller")
            if venv_pyinstaller.is_file():
                pyinstaller_bin = str(venv_pyinstaller)

    if not pyinstaller_bin:
        raise RuntimeError("PyInstaller executable not found in PATH or .venv.")

    # 3. Read authoritative version
    from core.version import __version__ as app_version

    spec_path = repo_root / "packaging" / "polpo.spec"
    build_staging_dir = dist_dir / "build_temp"
    output_bundle_dir = dist_dir / "Polpo"

    cmd = [
        pyinstaller_bin,
        "--noconfirm",
        "--clean",
        "--distpath", str(dist_dir),
        "--workpath", str(build_staging_dir),
        str(spec_path),
    ]

    logger.info("Executing PyInstaller command: %s", " ".join(cmd))
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("PyInstaller build failed (exit code %d):\nSTDOUT:\n%s\nSTDERR:\n%s", res.returncode, res.stdout, res.stderr)
        raise RuntimeError(f"PyInstaller build failed: {res.stderr}")

    if not output_bundle_dir.is_dir():
        raise RuntimeError(f"Expected PyInstaller bundle directory not found at {output_bundle_dir}")

    # 4. Create release archive
    archive_name = get_archive_filename(app_version, target_platform)
    archive_out_path = dist_dir / archive_name

    logger.info("Creating deterministic distribution archive: %s", archive_out_path)
    if archive_name.endswith(".zip"):
        create_deterministic_zip(output_bundle_dir, archive_out_path, root_arcname="Polpo")
    else:
        create_deterministic_tar_gz(output_bundle_dir, archive_out_path, root_arcname="Polpo")

    # 5. Generate checksum sidecar
    sidecar_path = generate_checksum_sidecar(archive_out_path)

    # 6. Generate stable download alias if requested
    if create_alias:
        create_stable_alias(archive_out_path, target_platform)

    # 7. Clean up temporary workpath
    if build_staging_dir.exists():
        shutil.rmtree(build_staging_dir, ignore_errors=True)

    logger.info("Package build completed successfully:")
    logger.info("  Archive:  %s", archive_out_path)
    logger.info("  Checksum: %s", sidecar_path)

    return archive_out_path, sidecar_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for scripts/build_package.py."""
    parser = argparse.ArgumentParser(
        description="Run packaging preflight checks and build reproducible Polpo desktop distributions."
    )
    parser.add_argument(
        "--platform",
        default=detect_host_platform(),
        help="Target platform identifier (default: host platform).",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run preflight validation checks without building package.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=None,
        help="Custom destination directory for distribution archives (default: dist/).",
    )
    parser.add_argument(
        "--pyinstaller-bin",
        default=None,
        help="Explicit path to pyinstaller executable.",
    )
    parser.add_argument(
        "--create-alias",
        action="store_true",
        help="Create stable unversioned release asset alias (e.g. polpo-linux-x86_64.tar.gz).",
    )

    args = parser.parse_args(argv)

    try:
        report = run_preflight_checks(target_platform=args.platform)
        if not report.is_valid:
            print("Preflight validation FAILED:")
            for err in report.errors:
                print(f"  - [FAIL] {err}")
            return 1
        else:
            print(f"Preflight validation PASSED for platform '{args.platform}'.")
            if args.preflight_only:
                return 0

        archive_path, sidecar_path = build_package(
            target_platform=args.platform,
            dist_dir=args.dist_dir,
            pyinstaller_bin=args.pyinstaller_bin,
            create_alias=args.create_alias,
        )
        print(f"Build succeeded: {archive_path.name}")
        return 0
    except Exception as exc:
        logger.error("Build failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

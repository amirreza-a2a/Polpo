"""Unit tests for Polpo Linux installer (install.sh).

Tests all lifecycle phases (first install, upgrade, rollback, uninstall,
concurrency, unsafe archive rejection, checksum verification, and user data preservation)
using synthetic archives and isolated temporary directories without downloading real releases.
"""

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent.parent / "install.sh"
DEPS_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "polpo_linux_deps.sh"


def make_tar_entry(name: str, content: bytes, is_dir: bool = False, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    ti = tarfile.TarInfo(name=name)
    ti.size = len(content) if not is_dir else 0
    ti.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    ti.mode = mode
    ti.mtime = 1700000000
    return ti, content


def create_synthetic_archive(
    dest_dir: Path,
    version: str = "0.1.0",
    corrupt_tar: bool = False,
    missing_executable: bool = False,
    unsafe_entry: str | None = None,
) -> tuple[Path, Path]:
    """Generates a synthetic Polpo-v{version}-linux-x86_64.tar.gz archive and sidecar."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / f"Polpo-v{version}-linux-x86_64.tar.gz"

    if corrupt_tar:
        archive_path.write_bytes(b"not a gzip or tar file at all")
    else:
        with tarfile.open(archive_path, "w:gz") as tar:
            # Root directory
            ti, _ = make_tar_entry("Polpo", b"", is_dir=True, mode=0o755)
            tar.addfile(ti)

            # Application executable
            if not missing_executable:
                exe_content = f"#!/bin/sh\necho 'Polpo {version}'\n".encode("utf-8")
                ti, _ = make_tar_entry("Polpo/Polpo", exe_content, mode=0o755)
                import io
                tar.addfile(ti, io.BytesIO(exe_content))

            # Resources and deps script
            res_content = b'{"version": 1}\n'
            ti, _ = make_tar_entry("Polpo/resources/runtimes.json", res_content, mode=0o644)
            import io
            tar.addfile(ti, io.BytesIO(res_content))

            deps_content = DEPS_SCRIPT.read_bytes()
            ti, _ = make_tar_entry("Polpo/scripts/polpo_linux_deps.sh", deps_content, mode=0o755)
            tar.addfile(ti, io.BytesIO(deps_content))

            # Unsafe paths for security tests
            if unsafe_entry == "absolute":
                ti, _ = make_tar_entry("/etc/passwd", b"root:x:0:0:::", mode=0o644)
                tar.addfile(ti, io.BytesIO(b"root:x:0:0:::"))
            elif unsafe_entry == "traversal":
                ti, _ = make_tar_entry("Polpo/../../escape.txt", b"escaped", mode=0o644)
                tar.addfile(ti, io.BytesIO(b"escaped"))
            elif unsafe_entry == "bad_root":
                ti, _ = make_tar_entry("MaliciousRoot/evil.sh", b"evil", mode=0o755)
                tar.addfile(ti, io.BytesIO(b"evil"))

    # Generate sha256 sidecar
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    sidecar_path = archive_path.with_name(f"{archive_path.name}.sha256")
    sidecar_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")

    return archive_path, sidecar_path


def run_installer(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [str(INSTALL_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=run_env,
    )


def test_first_install(tmp_path: Path):
    """First install sets up ~/.local/opt/polpo, symlinks, and polpo executable."""
    install_dir = tmp_path / "opt_polpo"
    bin_dir = tmp_path / "bin"
    archive, sidecar = create_synthetic_archive(tmp_path / "dist", version="0.1.0")

    res = run_installer([
        "--archive", str(archive),
        "--dir", str(install_dir),
        "--bin-dir", str(bin_dir),
    ])

    assert res.returncode == 0
    assert "Polpo 0.1.0 successfully installed!" in res.stdout

    # Verify directory layout
    assert (install_dir / "versions" / "0.1.0").is_dir()
    assert (install_dir / "current").is_symlink()
    assert os.readlink(install_dir / "current") == "versions/0.1.0"
    assert (bin_dir / "polpo").is_symlink()
    assert (bin_dir / "polpo-update").is_symlink()
    assert (bin_dir / "polpo-deps").is_symlink()
    assert (bin_dir / "polpo-uninstall").is_symlink()

    # Verify installed polpo executable runs
    proc = subprocess.run([str(bin_dir / "polpo")], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "Polpo 0.1.0" in proc.stdout


def test_upgrade_preserves_rollback_previous(tmp_path: Path):
    """Upgrading to 0.2.0 updates current symlink and sets previous symlink to 0.1.0."""
    install_dir = tmp_path / "opt_polpo"
    bin_dir = tmp_path / "bin"

    # 1. Install 0.1.0
    v1_archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0")
    run_installer(["--archive", str(v1_archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir)])

    # 2. Upgrade to 0.2.0
    v2_archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.2.0")
    res = run_installer(["--archive", str(v2_archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir), "--update"])

    assert res.returncode == 0
    assert (install_dir / "current").is_symlink()
    assert os.readlink(install_dir / "current") == "versions/0.2.0"
    assert (install_dir / "previous").is_symlink()
    assert os.readlink(install_dir / "previous") == "versions/0.1.0"

    # Execution now runs 0.2.0
    proc = subprocess.run([str(bin_dir / "polpo")], capture_output=True, text=True)
    assert "Polpo 0.2.0" in proc.stdout


def test_rollback_to_previous_version(tmp_path: Path):
    """--rollback switches the current symlink back to the previous version."""
    install_dir = tmp_path / "opt_polpo"
    bin_dir = tmp_path / "bin"

    # 1. Install 0.1.0 then 0.2.0
    v1_archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0")
    run_installer(["--archive", str(v1_archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir)])
    v2_archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.2.0")
    run_installer(["--archive", str(v2_archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir)])

    # 2. Execute rollback
    res = run_installer(["--rollback", "--dir", str(install_dir), "--bin-dir", str(bin_dir)])
    assert res.returncode == 0
    assert "Rollback complete" in res.stdout
    assert os.readlink(install_dir / "current") == "versions/0.1.0"

    # Execution is now rolled back to 0.1.0
    proc = subprocess.run([str(bin_dir / "polpo")], capture_output=True, text=True)
    assert "Polpo 0.1.0" in proc.stdout


def test_uninstall_preserves_user_data(tmp_path: Path):
    """Uninstall removes install_dir and bin symlinks, but strictly preserves ~/.local/share/polpot."""
    install_dir = tmp_path / "opt_polpo"
    bin_dir = tmp_path / "bin"
    fake_user_data = tmp_path / ".local" / "share" / "polpot"
    fake_user_data.mkdir(parents=True)
    (fake_user_data / "polpot.db").write_text("sqlite database content")
    (fake_user_data / "credentials.enc").write_text("vault content")

    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0")
    run_installer(["--archive", str(archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir)])

    assert install_dir.is_dir()
    assert (bin_dir / "polpo").is_symlink()

    # Run uninstall
    res = run_installer(["--uninstall", "--dir", str(install_dir), "--bin-dir", str(bin_dir)])
    assert res.returncode == 0
    assert not install_dir.exists()
    assert not (bin_dir / "polpo").exists()

    # User data MUST remain intact
    assert fake_user_data.is_dir()
    assert (fake_user_data / "polpot.db").read_text() == "sqlite database content"
    assert (fake_user_data / "credentials.enc").read_text() == "vault content"


def test_checksum_mismatch_rejected(tmp_path: Path):
    """Installer must fail and abort when sha256 checksum does not match archive."""
    install_dir = tmp_path / "opt_polpo"
    archive, sidecar = create_synthetic_archive(tmp_path / "dist", version="0.1.0")

    # Tamper with the sidecar hash
    sidecar.write_text("0" * 64 + f"  {archive.name}\n")

    res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
    assert res.returncode == 1
    assert "SHA-256 checksum mismatch" in res.stderr
    assert not install_dir.exists()


def test_unsafe_archive_absolute_path_rejected(tmp_path: Path):
    """Installer must reject archive containing absolute file paths."""
    install_dir = tmp_path / "opt_polpo"
    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0", unsafe_entry="absolute")

    res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
    assert res.returncode == 1
    assert "contains absolute path" in res.stderr
    assert not install_dir.exists()


def test_unsafe_archive_traversal_rejected(tmp_path: Path):
    """Installer must reject archive containing path traversal (..)."""
    install_dir = tmp_path / "opt_polpo"
    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0", unsafe_entry="traversal")

    res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
    assert res.returncode == 1
    assert "directory traversal" in res.stderr
    assert not install_dir.exists()


def test_unsafe_archive_bad_root_rejected(tmp_path: Path):
    """Installer must reject archive that does not start with Polpo/ container."""
    install_dir = tmp_path / "opt_polpo"
    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0", unsafe_entry="bad_root")

    res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
    assert res.returncode == 1
    assert "unexpected top-level entry" in res.stderr
    assert not install_dir.exists()


def test_missing_binary_in_archive_rejected(tmp_path: Path):
    """Installer must verify extracted Polpo executable exists before switching current link."""
    install_dir = tmp_path / "opt_polpo"
    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0", missing_executable=True)

    res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
    assert res.returncode == 1
    assert "Application binary missing at expected path" in res.stderr
    assert not install_dir.exists()


def test_stable_channel_absence_reports_actionable_error():
    """Default stable channel reports actionable guidance if no stable release exists."""
    # Use non-existent repo or mock endpoint
    res = run_installer(["--channel", "stable", "--repo", "amirreza-a2a/Polpo-nonexistent-9999"])
    assert res.returncode == 1
    assert "No published stable release was found" in res.stderr
    assert "--channel preview" in res.stdout or "--channel preview" in res.stderr


def test_same_version_reinstall_behavior(tmp_path: Path):
    """Same-version reinstallation in update mode reports up to date unless --force is given."""
    install_dir = tmp_path / "opt_polpo"
    bin_dir = tmp_path / "bin"
    archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0")

    run_installer(["--archive", str(archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir)])

    # Reinstall same version in update mode without --force
    res = run_installer(["--archive", str(archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir), "--update"])
    assert res.returncode == 0
    assert "Polpo is already up to date" in res.stdout

    # Reinstall same version with --force
    res_force = run_installer(["--archive", str(archive), "--dir", str(install_dir), "--bin-dir", str(bin_dir), "--force"])
    assert res_force.returncode == 0
    assert "Polpo 0.1.0 successfully installed!" in res_force.stdout


def test_unsupported_architecture_rejected(tmp_path: Path):
    """Installer must detect and reject non-x86_64 architectures with exit code 2."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    uname_mock = mock_bin / "uname"
    uname_mock.write_text("#!/bin/sh\nif [ \"$1\" = '-m' ]; then echo 'aarch64'; else echo 'Linux'; fi\n")
    uname_mock.chmod(0o755)

    fake_env = {"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"}
    res = run_installer(["--dir", str(tmp_path / "opt")], env=fake_env)
    assert res.returncode == 2
    assert "only provides official standalone binaries for x86_64" in res.stderr
    assert "aarch64" in res.stderr


def test_alpine_musl_rejected(tmp_path: Path):
    """Installer must detect and reject musl/Alpine environments with exit code 2."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    ldd_mock = mock_bin / "ldd"
    ldd_mock.write_text("#!/bin/sh\necho 'musl libc (x86_64)'\n")
    ldd_mock.chmod(0o755)

    fake_env = {"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"}
    res = run_installer(["--dir", str(tmp_path / "opt")], env=fake_env)
    assert res.returncode == 2
    assert "requires glibc and does not support musl" in res.stderr


def test_missing_required_tools_rejected(tmp_path: Path):
    """Installer must fail cleanly if tar or sha256sum is missing."""
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    for tool in ["bash", "sh", "uname", "grep", "cut", "head", "cat", "echo", "id", "mktemp", "mkdir", "rm"]:
        src = Path(f"/bin/{tool}")
        if not src.exists():
            src = Path(f"/usr/bin/{tool}")
        if src.exists():
            (mock_bin / tool).symlink_to(src)

    fake_env = {"PATH": str(mock_bin)}
    res = run_installer(["--dir", str(tmp_path / "opt")], env=fake_env)
    assert res.returncode == 1
    assert "Missing required system utilities" in res.stderr


def test_concurrent_install_lock_contention(tmp_path: Path):
    """A concurrent installation attempt while lock is held must abort safely."""
    install_dir = tmp_path / "opt_polpo"
    lock_hash = hashlib.sha256(str(install_dir).encode("utf-8")).hexdigest()[:16]
    lock_dir = Path(f"/tmp/polpo-install-{os.getuid()}-{lock_hash}.lock.d")
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "pid").write_text(str(os.getpid()))

    try:
        archive, _ = create_synthetic_archive(tmp_path / "dist", version="0.1.0")
        res = run_installer(["--archive", str(archive), "--dir", str(install_dir)])
        assert res.returncode == 1
        assert "Another Polpo installation or update process is currently running" in res.stderr
    finally:
        import shutil
        shutil.rmtree(lock_dir, ignore_errors=True)

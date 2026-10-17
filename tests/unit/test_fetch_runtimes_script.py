# ============================================================
#  tests/unit/test_fetch_runtimes_script.py
# ============================================================

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

import scripts.fetch_runtimes as fetch_runtimes


class TestFetchRuntimesScript(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name)
        self.resources_path = self.root_path / "resources"
        self.resources_path.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_synthetic_tar_gz(self, members: dict[str, bytes]) -> tuple[bytes, str]:
        """Creates an in-memory tar.gz archive and returns (bytes, sha256_hex)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, content in members.items():
                ti = tarfile.TarInfo(name=name)
                ti.size = len(content)
                ti.mode = 0o755
                tar.addfile(ti, io.BytesIO(content))
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        return data, sha

    def _create_synthetic_zip(self, members: dict[str, bytes]) -> tuple[bytes, str]:
        """Creates an in-memory zip archive and returns (bytes, sha256_hex)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        return data, sha

    def test_correct_sha256_validation_and_extraction_tar_gz(self):
        """Valid archive matching expected SHA-256 is verified and extracted to target path."""
        binary_subpath = "pandoc-3.1.11/bin/pandoc"
        target_subpath = "resources/bin/linux-x86_64/pandoc"
        dummy_content = b"#!/bin/sh\necho 'pandoc 3.1.11'\n"

        archive_bytes, archive_sha = self._create_synthetic_tar_gz({
            binary_subpath: dummy_content,
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/pandoc.tar.gz",
                            "sha256": archive_sha,
                            "binary_subpath": binary_subpath,
                            "target_path": target_subpath,
                        }
                    }
                }
            }
        }

        # Mock download to return synthetic archive bytes
        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            def dl_side_effect(url, dest, **kwargs):
                dest.write_bytes(archive_bytes)
            mock_dl.side_effect = dl_side_effect

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            report = acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")

            self.assertTrue(report.success)
            target_file = self.root_path / target_subpath
            self.assertTrue(target_file.is_file())
            self.assertEqual(target_file.read_bytes(), dummy_content)
            if os.name == "posix":
                self.assertTrue(bool(target_file.stat().st_mode & stat.S_IXUSR))

    def test_correct_sha256_validation_and_extraction_zip(self):
        """Valid zip archive matching expected SHA-256 is verified and extracted to target path."""
        binary_subpath = "pandoc-3.1.11/pandoc.exe"
        target_subpath = "resources/bin/windows-x64/pandoc.exe"
        dummy_content = b"MZ\x90\x00_DUMMY_WINDOWS_EXE_"

        archive_bytes, archive_sha = self._create_synthetic_zip({
            binary_subpath: dummy_content,
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "windows-x64": {
                            "archive_type": "zip",
                            "url": "https://example.com/pandoc.zip",
                            "sha256": archive_sha,
                            "binary_subpath": binary_subpath,
                            "target_path": target_subpath,
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            def dl_side_effect(url, dest, **kwargs):
                dest.write_bytes(archive_bytes)
            mock_dl.side_effect = dl_side_effect

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            report = acquirer.acquire_runtime(runtime_name="pandoc", platform_name="windows-x64")

            self.assertTrue(report.success)
            target_file = self.root_path / target_subpath
            self.assertTrue(target_file.is_file())
            self.assertEqual(target_file.read_bytes(), dummy_content)

    def test_sha256_mismatch_rejection_leaves_target_clean(self):
        """If downloaded archive digest does not match manifest, acquisition fails and target is not created."""
        target_subpath = "resources/bin/linux-x86_64/pandoc"
        archive_bytes, _ = self._create_synthetic_tar_gz({
            "pandoc": b"dummy",
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/pandoc.tar.gz",
                            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                            "binary_subpath": "pandoc",
                            "target_path": target_subpath,
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            def dl_side_effect(url, dest, **kwargs):
                dest.write_bytes(archive_bytes)
            mock_dl.side_effect = dl_side_effect

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.ChecksumMismatchError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")

            target_file = self.root_path / target_subpath
            self.assertFalse(target_file.exists())

    def test_failed_extraction_preserves_existing_valid_runtime(self):
        """If an existing binary is present and an acquisition fails, the existing binary is not deleted or corrupted."""
        target_subpath = "resources/bin/linux-x86_64/pandoc"
        target_file = self.root_path / target_subpath
        target_file.parent.mkdir(parents=True, exist_ok=True)
        valid_preexisting_content = b"ORIGINAL_PANDOC_BINARY"
        target_file.write_bytes(valid_preexisting_content)

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/pandoc.tar.gz",
                            "sha256": "badhash",
                            "binary_subpath": "pandoc",
                            "target_path": target_subpath,
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = RuntimeError("Simulated network timeout")

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(RuntimeError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64", force=True)

            self.assertTrue(target_file.is_file())
            self.assertEqual(target_file.read_bytes(), valid_preexisting_content)

    def test_unsafe_tar_member_path_rejected(self):
        """Tar archive containing path traversal (e.g. ../../escape) is rejected."""
        traversal_bytes, traversal_sha = self._create_synthetic_tar_gz({
            "../../escape.sh": b"malicious",
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/escape.tar.gz",
                            "sha256": traversal_sha,
                            "binary_subpath": "../../escape.sh",
                            "target_path": "resources/bin/linux-x86_64/pandoc",
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = lambda u, dest, **kw: dest.write_bytes(traversal_bytes)

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.UnsafeArchiveError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")

    def test_unsafe_zip_member_path_rejected(self):
        """Zip archive containing path traversal (e.g. ../../evil.exe) is rejected."""
        traversal_bytes, traversal_sha = self._create_synthetic_zip({
            "../../evil.exe": b"malicious",
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "windows-x64": {
                            "archive_type": "zip",
                            "url": "https://example.com/escape.zip",
                            "sha256": traversal_sha,
                            "binary_subpath": "../../evil.exe",
                            "target_path": "resources/bin/windows-x64/pandoc.exe",
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = lambda u, dest, **kw: dest.write_bytes(traversal_bytes)

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.UnsafeArchiveError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="windows-x64")

    def test_missing_binary_in_archive_fails_explicitly(self):
        """If expected binary_subpath is absent from archive, raises BinaryNotFoundError."""
        archive_bytes, archive_sha = self._create_synthetic_tar_gz({
            "wrong_path/binary": b"content",
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/pandoc.tar.gz",
                            "sha256": archive_sha,
                            "binary_subpath": "pandoc-3.1.11/bin/pandoc",
                            "target_path": "resources/bin/linux-x86_64/pandoc",
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = lambda u, dest, **kw: dest.write_bytes(archive_bytes)

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.BinaryNotFoundError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")

    def test_unsupported_platform_handling(self):
        """Requesting an unsupported platform name raises UnsupportedPlatformError."""
        acquirer = fetch_runtimes.RuntimeAcquirer(
            manifest_data={"runtimes": {"pandoc": {"platforms": {"linux-x86_64": {}}}}},
            repo_root=self.root_path,
        )
        with self.assertRaises(fetch_runtimes.UnsupportedPlatformError):
            acquirer.acquire_runtime(runtime_name="pandoc", platform_name="solaris-sparc")

    def test_optional_mathjax_npm_install_invocation(self):
        """Passing --install-mathjax-deps executes npm ci with lockfile and production flags."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path)

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            cmd = args[0]
            self.assertEqual(cmd[:3], ["npm", "ci", "--omit=dev"])
            self.assertEqual(cmd[3], "--prefix")
            self.assertEqual(Path(cmd[4]).resolve(), mathjax_dir.resolve())

    def test_ambiguous_binary_in_archive_fails_explicitly(self):
        """If multiple executable candidates match candidate_name in archive, raises BinaryNotFoundError."""
        archive_bytes, archive_sha = self._create_synthetic_tar_gz({
            "dir_a/pandoc": b"candidate_a",
            "dir_b/pandoc": b"candidate_b",
        })

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/pandoc.tar.gz",
                            "sha256": archive_sha,
                            "binary_subpath": "wrong_dir/pandoc",
                            "target_path": "resources/bin/linux-x86_64/pandoc",
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = lambda u, dest, **kw: dest.write_bytes(archive_bytes)

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.BinaryNotFoundError) as ctx:
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")
            self.assertIn("Ambiguous executable discovery", str(ctx.exception))

    def test_unsafe_tar_symlink_or_hardlink_traversal_rejected(self):
        """Tar archive containing symlink with escaping target is rejected."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            ti = tarfile.TarInfo(name="escape_link")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "../../etc/shadow"
            tar.addfile(ti)
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()

        manifest = {
            "runtimes": {
                "pandoc": {
                    "version": "3.1.11",
                    "platforms": {
                        "linux-x86_64": {
                            "archive_type": "tar.gz",
                            "url": "https://example.com/escape.tar.gz",
                            "sha256": sha,
                            "binary_subpath": "escape_link",
                            "target_path": "resources/bin/linux-x86_64/pandoc",
                        }
                    }
                }
            }
        }

        with patch.object(fetch_runtimes, "download_to_file") as mock_dl:
            mock_dl.side_effect = lambda u, dest, **kw: dest.write_bytes(data)

            acquirer = fetch_runtimes.RuntimeAcquirer(
                manifest_data=manifest,
                repo_root=self.root_path,
            )
            with self.assertRaises(fetch_runtimes.UnsafeArchiveError):
                acquirer.acquire_runtime(runtime_name="pandoc", platform_name="linux-x86_64")

    def test_download_to_file_retry_and_exhaustion(self):
        """download_to_file retries on transient errors and raises RuntimeError on exhaustion."""
        import urllib.error
        dest = self.root_path / "test_download.tmp"

        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            with self.assertRaises(RuntimeError) as ctx:
                fetch_runtimes.download_to_file(
                    url="https://example.com/file.tar.gz",
                    dest_path=dest,
                    max_retries=3,
                )
            self.assertIn("Failed to download", str(ctx.exception))
            self.assertEqual(mock_urlopen.call_count, 3)

    def test_mathjax_npm_install_skips_when_already_cached(self):
        """When node_modules/mathjax-full is already present, npm ci is skipped unless force=True."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")
        mj_full = mathjax_dir / "node_modules" / "mathjax-full"
        mj_full.mkdir(parents=True, exist_ok=True)

        with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, force=False)
            mock_run.assert_not_called()

            fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, force=True)
            mock_run.assert_called_once()

    def test_cli_parsing_and_main_dispatch(self):
        """CLI main() parses arguments correctly and dispatches acquisition."""
        with patch.object(fetch_runtimes.RuntimeAcquirer, "acquire_all") as mock_acquire_all:
            mock_acquire_all.return_value = []
            code = fetch_runtimes.main(["--platform", "linux-x86_64", "--skip-npm"])
            self.assertEqual(code, 0)
            mock_acquire_all.assert_called_once()

    def test_windows_npm_cli_resolution_from_node_dir(self):
        """On Windows, resolve_npm_command discovers npm-cli.js adjacent to node.exe."""
        fake_node_dir = self.root_path / "tools" / "node"
        fake_cli_dir = fake_node_dir / "node_modules" / "npm" / "bin"
        fake_cli_dir.mkdir(parents=True, exist_ok=True)
        fake_node = fake_node_dir / "node.exe"
        fake_node.write_text("", encoding="utf-8")
        fake_cli = fake_cli_dir / "npm-cli.js"
        fake_cli.write_text("", encoding="utf-8")

        def fake_which(cmd):
            if cmd in ("node.exe", "node"):
                return str(fake_node)
            return None

        with patch("shutil.which", side_effect=fake_which), patch.dict(os.environ, {}, clear=True):
            cmd = fetch_runtimes.resolve_npm_command(
                repo_root=self.root_path,
                sys_platform="win32",
            )
            self.assertEqual(len(cmd), 2)
            self.assertEqual(Path(cmd[0]).resolve(), fake_node.resolve())
            self.assertEqual(Path(cmd[1]).resolve(), fake_cli.resolve())

    def test_windows_npm_cli_resolution_from_npm_cmd_dir(self):
        """On Windows, resolve_npm_command finds node and npm-cli.js via npm.cmd directory when node not in PATH."""
        fake_npm_dir = self.root_path / "tools" / "npm_dir"
        fake_cli_dir = fake_npm_dir / "node_modules" / "npm" / "bin"
        fake_cli_dir.mkdir(parents=True, exist_ok=True)
        fake_node = fake_npm_dir / "node.exe"
        fake_node.write_text("", encoding="utf-8")
        fake_cmd = fake_npm_dir / "npm.cmd"
        fake_cmd.write_text("", encoding="utf-8")
        fake_cli = fake_cli_dir / "npm-cli.js"
        fake_cli.write_text("", encoding="utf-8")

        def fake_which(cmd):
            if cmd in ("npm.cmd", "npm"):
                return str(fake_cmd)
            return None

        with patch("shutil.which", side_effect=fake_which), patch.dict(os.environ, {}, clear=True):
            cmd = fetch_runtimes.resolve_npm_command(
                repo_root=self.root_path,
                sys_platform="win32",
            )
            self.assertEqual(len(cmd), 2)
            self.assertEqual(Path(cmd[0]).resolve(), fake_node.resolve())
            self.assertEqual(Path(cmd[1]).resolve(), fake_cli.resolve())

    def test_windows_npm_resolution_native_exe_fallback(self):
        """On Windows, if a native npm.exe is found and npm-cli.js is absent, returns [npm.exe]."""
        fake_bin_dir = self.root_path / "tools" / "bin"
        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        fake_npm_exe = fake_bin_dir / "npm.exe"
        fake_npm_exe.write_text("", encoding="utf-8")

        def fake_which(cmd):
            if cmd in ("npm.exe", "npm"):
                return str(fake_npm_exe)
            return None

        with patch("shutil.which", side_effect=fake_which), patch.dict(os.environ, {}, clear=True):
            cmd = fetch_runtimes.resolve_npm_command(
                repo_root=self.root_path,
                sys_platform="win32",
            )
            self.assertEqual(len(cmd), 1)
            self.assertEqual(Path(cmd[0]).resolve(), fake_npm_exe.resolve())

    def test_windows_npm_cmd_does_not_invoke_cmd_exe_fallback(self):
        """On Windows, if npm-cli.js is absent and only npm.cmd exists, cmd.exe is NOT invoked; NpmNotFoundError is raised."""
        fake_bin_dir = self.root_path / "tools" / "bin"
        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        fake_npm_cmd = fake_bin_dir / "npm.cmd"
        fake_npm_cmd.write_text("", encoding="utf-8")

        def fake_which(cmd):
            if cmd in ("npm.cmd", "npm"):
                return str(fake_npm_cmd)
            return None

        with patch("shutil.which", side_effect=fake_which), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(fetch_runtimes.NpmNotFoundError) as ctx:
                fetch_runtimes.resolve_npm_command(
                    repo_root=self.root_path,
                    sys_platform="win32",
                )
            self.assertIn("Neither Node.js with 'npm-cli.js' nor native 'npm.exe' was found", str(ctx.exception))

    def test_subprocess_never_requests_shell_execution(self):
        """install_mathjax_dependencies never invokes subprocess.run with shell=True."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        for platform_id in ["linux", "win32", "darwin"]:
            with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]), \
                 patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, sys_platform=platform_id)
                mock_run.assert_called_once()
                kwargs = mock_run.call_args[1]
                self.assertFalse(kwargs.get("shell", False), f"shell=True detected for platform {platform_id}")

    def test_missing_npm_or_node_diagnostics_windows(self):
        """On Windows, if neither node nor npm can be located, raises NpmNotFoundError with clear diagnostics."""
        with patch("shutil.which", return_value=None), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(fetch_runtimes.NpmNotFoundError) as ctx:
                fetch_runtimes.resolve_npm_command(
                    repo_root=self.root_path,
                    sys_platform="win32",
                )
            self.assertIn("npm could not be located on Windows", str(ctx.exception))
            self.assertIn("Node.js 22.x LTS", str(ctx.exception))

    def test_missing_npm_diagnostics_posix(self):
        """On POSIX, if neither npm nor node can be located, raises NpmNotFoundError with clear diagnostics."""
        with patch("shutil.which", return_value=None), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(fetch_runtimes.NpmNotFoundError) as ctx:
                fetch_runtimes.resolve_npm_command(
                    repo_root=self.root_path,
                    sys_platform="linux",
                )
            self.assertIn("npm could not be located on PATH", str(ctx.exception))

    def test_correct_subprocess_args_for_windows_and_posix(self):
        """install_mathjax_dependencies invokes subprocess with proper arguments on Windows and POSIX."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        # POSIX verification
        with patch("shutil.which", return_value="/usr/bin/npm"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, sys_platform="linux")

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd[:4], ["npm", "ci", "--omit=dev", "--prefix"])
            self.assertEqual(Path(cmd[4]).resolve(), mathjax_dir.resolve())

        # Windows verification
        fake_node = self.root_path / "node.exe"
        fake_cli = self.root_path / "npm-cli.js"
        with patch.object(fetch_runtimes, "resolve_npm_command", return_value=[str(fake_node), str(fake_cli)]):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, sys_platform="win32")

                mock_run.assert_called_once()
                cmd = mock_run.call_args[0][0]
                self.assertEqual(Path(cmd[0]).resolve(), fake_node.resolve())
                self.assertEqual(Path(cmd[1]).resolve(), fake_cli.resolve())
                self.assertEqual(cmd[2:5], ["ci", "--omit=dev", "--prefix"])
                self.assertEqual(Path(cmd[5]).resolve(), mathjax_dir.resolve())

    def test_subprocess_filenotfound_raises_npm_not_found_error(self):
        """When subprocess.run raises FileNotFoundError, install_mathjax_dependencies raises NpmNotFoundError."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        with patch("subprocess.run", side_effect=FileNotFoundError("Executable not found")):
            with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]):
                with self.assertRaises(fetch_runtimes.NpmNotFoundError) as ctx:
                    fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path)
                self.assertIn("Failed to execute npm command 'npm'", str(ctx.exception))

    def test_npm_non_zero_exit_propagates_status_and_output(self):
        """When npm ci fails with non-zero exit status, stdout and stderr are included in NpmExecutionError."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="npm notice run in progress",
                stderr="npm ERR! code ENOENT\nnpm ERR! package.json missing",
            )
            with self.assertRaises(fetch_runtimes.NpmExecutionError) as ctx:
                fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path)

            err_msg = str(ctx.exception)
            self.assertIn("exit code 1", err_msg)
            self.assertIn("npm notice run in progress", err_msg)
            self.assertIn("npm ERR! code ENOENT", err_msg)
            self.assertIsInstance(ctx.exception, RuntimeError)

    def test_npm_timeout_expired_propagates_details(self):
        """When npm ci exceeds timeout, NpmExecutionError is raised with diagnostic details."""
        mathjax_dir = self.root_path / "resources" / "mathjax"
        mathjax_dir.mkdir(parents=True, exist_ok=True)
        (mathjax_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        with patch.object(fetch_runtimes, "resolve_npm_command", return_value=["npm"]):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["npm"], timeout=10.0, output="partial stdout", stderr="partial stderr"),
            ):
                with self.assertRaises(fetch_runtimes.NpmExecutionError) as ctx:
                    fetch_runtimes.install_mathjax_dependencies(repo_root=self.root_path, timeout=10.0)

                err_msg = str(ctx.exception)
                self.assertIn("timed out after 10.0 seconds", err_msg)
                self.assertIn("partial stdout", err_msg)
                self.assertIn("partial stderr", err_msg)

    def test_environment_variable_overrides_for_node_and_npm_cli(self):
        """POLPO_NODE_PATH and POLPO_NPM_CLI_PATH take precedence when configured."""
        fake_node = self.root_path / "custom" / "bin" / "node.exe"
        fake_node.parent.mkdir(parents=True, exist_ok=True)
        fake_node.write_text("", encoding="utf-8")

        fake_cli = self.root_path / "custom" / "npm-cli.js"
        fake_cli.write_text("", encoding="utf-8")

        with patch.dict(os.environ, {"POLPO_NODE_PATH": str(fake_node), "POLPO_NPM_CLI_PATH": str(fake_cli)}, clear=True):
            cmd = fetch_runtimes.resolve_npm_command(
                repo_root=self.root_path,
                sys_platform="win32",
            )
            self.assertEqual(len(cmd), 2)
            self.assertEqual(Path(cmd[0]).resolve(), fake_node.resolve())
            self.assertEqual(Path(cmd[1]).resolve(), fake_cli.resolve())

    def test_posix_npm_cli_resolution_when_npm_binary_missing(self):
        """On POSIX, if 'npm' binary is not on PATH, falls back to [<node>, <npm-cli.js>]."""
        fake_bin_dir = self.root_path / "custom" / "bin"
        fake_lib_dir = self.root_path / "custom" / "lib" / "node_modules" / "npm" / "bin"
        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        fake_lib_dir.mkdir(parents=True, exist_ok=True)

        fake_node = fake_bin_dir / "node"
        fake_node.write_text("", encoding="utf-8")
        fake_cli = fake_lib_dir / "npm-cli.js"
        fake_cli.write_text("", encoding="utf-8")

        def fake_which(cmd):
            if cmd == "node":
                return str(fake_node)
            return None

        with patch("shutil.which", side_effect=fake_which), patch.dict(os.environ, {}, clear=True):
            cmd = fetch_runtimes.resolve_npm_command(
                repo_root=self.root_path,
                sys_platform="linux",
            )
            self.assertEqual(len(cmd), 2)
            self.assertEqual(Path(cmd[0]).resolve(), fake_node.resolve())
            self.assertEqual(Path(cmd[1]).resolve(), fake_cli.resolve())


if __name__ == "__main__":
    unittest.main()

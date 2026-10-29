# ============================================================
#  tests/unit/test_release_workflow_contract.py
# ============================================================

from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests.unit.workflow_expression_scanner import find_unsafe_run_interpolations


class TestReleaseWorkflowContract(unittest.TestCase):
    """Rigorous contract and security assertions for .github/workflows/release.yml."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.workflow_path = cls.repo_root / ".github" / "workflows" / "release.yml"
        if not cls.workflow_path.is_file():
            raise FileNotFoundError(f"Workflow file missing: {cls.workflow_path}")
        cls.content = cls.workflow_path.read_text(encoding="utf-8")

    def test_workflow_file_exists(self):
        """Verifies release.yml exists at the canonical repository path."""
        self.assertTrue(self.workflow_path.is_file())
        self.assertGreater(len(self.content), 1000)

    def test_actionlint_validation(self):
        """Verifies release.yml passes actionlint with zero errors if installed."""
        actionlint_bin = shutil.which("actionlint")
        if not actionlint_bin:
            self.skipTest("actionlint executable not found in PATH")

        result = subprocess.run(
            [actionlint_bin, str(self.workflow_path)],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        )
        self.assertEqual(
            result.returncode,
            0,
            f"actionlint failed on release.yml:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )

    def test_all_actions_pinned_by_40_char_sha(self):
        """Verifies every GitHub action invocation is pinned to an immutable 40-char SHA."""
        # Find all uses: <action>@<ref> lines
        uses_matches = re.findall(r"uses:\s+([^\s]+)", self.content)
        self.assertGreaterEqual(len(uses_matches), 10, "Expected at least 10 action invocations")

        sha40_pattern = re.compile(r"^[a-zA-Z0-9_\-\.\/]+@[a-f0-9]{40}$")
        for action_ref in uses_matches:
            self.assertTrue(
                sha40_pattern.match(action_ref),
                f"Action '{action_ref}' is not pinned by a 40-character commit SHA",
            )

    def test_workflow_triggers_contract(self):
        """Verifies the workflow triggers on v* tags, release-validation branches, and manual workflow_dispatch without dry_run."""
        self.assertIn("tags:\n      - 'v*'", self.content)
        self.assertIn("branches:\n      - 'release-validation/**'", self.content)
        self.assertIn("workflow_dispatch:", self.content)
        self.assertNotIn("dry_run:", self.content)

    def test_no_unsafe_context_interpolation_in_run_blocks(self):
        """Verifies that no GitHub Actions expressions (${{ ... }}) are interpolated directly into run: script bodies.

        All dynamically evaluated context values (e.g. github.ref, inputs, runner.temp) must be passed
        via step-level 'env:' variables and referenced via quoted shell variables to eliminate script-injection risks.
        """
        violations = find_unsafe_run_interpolations(self.content)
        self.assertEqual(
            violations,
            [],
            f"Found unsafe ${{ ... }} context expressions inside run: blocks in release.yml:\n"
            + "\n".join(violations),
        )

    def test_pyinstaller_installation_is_pinned_and_quoted(self):
        """Verifies PyInstaller is installed via python -m pip with an exact, quoted version pin."""
        self.assertIn('python -m pip install "pyinstaller==6.22.3"', self.content)
        self.assertNotIn("pip install pyinstaller>=", self.content)

    def test_smoke_test_isolation_and_cleanup_contract(self):
        """Verifies smoke tests use unique directories under RUNNER_TEMP, trap/finally cleanup, and isolated profiles."""
        # Linux isolation & cleanup
        self.assertIn('mktemp -d "${BASE_TEMP:-/tmp}/polpo_smoke_XXXXXX"', self.content)
        self.assertIn('trap \'rm -rf "${SMOKE_DIR}"\' EXIT', self.content)
        self.assertIn('export XDG_DATA_HOME="${SMOKE_DIR}/user_data"', self.content)

        # Windows isolation & cleanup
        self.assertIn('Join-Path $baseTemp "polpo_smoke_$smokeId"', self.content)
        self.assertIn('Start-Process -FilePath $exePath -ArgumentList "--self-test" -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog', self.content)
        self.assertIn('Get-Content $stdoutLog', self.content)
        self.assertIn('$proc.ExitCode', self.content)
        self.assertIn('$env:LOCALAPPDATA = Join-Path $smokeDir "AppData\\Local"', self.content)
        self.assertIn('Remove-Item -Recurse -Force $smokeDir', self.content)

    def test_least_privilege_permissions(self):
        """Verifies top-level and per-job permissions follow the least-privilege model."""
        # Top-level permissions must be read-only
        top_perm_match = re.search(r"^permissions:\s*\n\s+contents:\s*read", self.content, re.MULTILINE)
        self.assertIsNotNone(top_perm_match, "Top-level workflow must enforce permissions: contents: read")

        # Jobs 1, 2, 3 must have contents: read
        self.assertIn("validate-release-ref:\n    name: validate-release-ref\n    runs-on: ubuntu-24.04\n    permissions:\n      contents: read", self.content)

        # Job 4 (publish-release) must be the ONLY job requesting contents: write, id-token: write, attestations: write
        job_blocks = self.content.split("\n  publish-release:")
        self.assertEqual(len(job_blocks), 2, "Expected exactly one publish-release job definition")
        publish_block = job_blocks[1]

        self.assertIn("contents: write", publish_block)
        self.assertIn("id-token: write", publish_block)
        self.assertIn("attestations: write", publish_block)

        # Before publish-release, there must NOT be any contents: write or id-token: write
        pre_publish_content = job_blocks[0]
        self.assertNotIn("contents: write", pre_publish_content)
        self.assertNotIn("id-token: write", pre_publish_content)
        self.assertNotIn("attestations: write", pre_publish_content)

    def test_job_dag_and_dependency_architecture(self):
        """Verifies the 4-job dependency DAG from audit Section 7.2."""
        # Job 1: validate-release-ref (root job)
        self.assertIn("validate-release-ref:", self.content)

        # Job 2: tier2-validation-matrix depends on validate-release-ref
        self.assertIn("tier2-validation-matrix:", self.content)
        self.assertRegex(
            self.content,
            r"(?s)tier2-validation-matrix:.*?needs:\s*validate-release-ref",
        )

        # Job 3: package-and-smoke-test depends on validate-release-ref
        self.assertIn("package-and-smoke-test:", self.content)
        self.assertRegex(
            self.content,
            r"(?s)package-and-smoke-test:.*?needs:\s*validate-release-ref",
        )

        # Job 4: publish-release depends on BOTH tier2 and package jobs
        self.assertRegex(
            self.content,
            r"(?s)publish-release:.*?needs:\s*\n\s+-\s*tier2-validation-matrix\s*\n\s+-\s*package-and-smoke-test",
        )

    def test_validate_release_ref_gating_logic(self):
        """Verifies validate-release-ref verifies tag vs core/version.py and main ancestry."""
        self.assertIn("core.version.__version__", self.content)
        self.assertIn("git merge-base --is-ancestor HEAD origin/main", self.content)
        self.assertIn("gitleaks/gitleaks-action", self.content)
        self.assertIn("libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3", self.content)
        self.assertIn("scripts/run_quality_gates.py --mode=working-tree", self.content)

    def test_tier2_validation_matrix_platforms(self):
        """Verifies tier-2 validation covers macOS 15 and Linux Python 3.10 and 3.11."""
        self.assertIn("macos-15", self.content)
        self.assertIn("python-version: '3.10'", self.content)
        self.assertIn("python-version: '3.11'", self.content)
        self.assertIn("python scripts/fetch_runtimes.py --install-mathjax-deps", self.content)
        self.assertIn("python scripts/verify_mathjax_worker.py", self.content)
        self.assertIn("pytest -q", self.content)

    def test_package_and_smoke_test_contract(self):
        """Verifies package job builds via build_package.py and smoke tests frozen binary outside repo."""
        self.assertIn("platform_id: linux-x86_64", self.content)
        self.assertIn("platform_id: windows-x64", self.content)
        self.assertIn("python scripts/build_package.py", self.content)

        # Linux smoke test outside repository with constrained PATH
        self.assertIn("Smoke test packaged Linux binary outside repository", self.content)
        self.assertIn("tar -xzf", self.content)
        self.assertIn('PATH="/usr/bin:/bin"', self.content)
        self.assertIn("--self-test", self.content)

        # Windows smoke test outside repository with constrained PATH
        self.assertIn("Smoke test packaged Windows binary outside repository", self.content)
        self.assertIn("Expand-Archive", self.content)
        self.assertIn('C:\\Windows\\System32;C:\\Windows', self.content)
        self.assertIn("Polpo.exe", self.content)

        # Packaging baseline and alias generation contract
        self.assertIn("os: ubuntu-22.04", self.content)
        self.assertIn("python scripts/build_package.py --create-alias", self.content)
        self.assertIn("dist/polpo-*.tar.gz", self.content)
        self.assertIn("dist/polpo-*.tar.gz.sha256", self.content)

        # Upload artifacts
        self.assertIn("actions/upload-artifact", self.content)
        self.assertIn("if-no-files-found: error", self.content)

    def test_publish_release_provenance_and_publishing(self):
        """Verifies publish-release verifies checksums, generates provenance, and publishes."""
        self.assertIn("actions/download-artifact", self.content)
        self.assertIn("merge-multiple: true", self.content)
        self.assertIn("sha256sum -c", self.content)
        self.assertIn("SHA256SUMS.txt", self.content)
        self.assertIn("actions/attest-build-provenance", self.content)
        self.assertIn("softprops/action-gh-release", self.content)
        self.assertIn("contains(github.ref_name, '-')", self.content)
        self.assertIn("fail_on_unmatched_files: true", self.content)

    def test_manual_dispatch_never_publishes_and_only_push_tag_can_publish(self):
        """Rigorous assertion that publish-release is reachable ONLY on git tag push events.

        Manual workflow_dispatch and release-validation branch pushes must NEVER be capable
        of publishing a release. Upstream failures in validation or packaging must block publication.
        """
        # Extract publish-release job definition
        job_blocks = self.content.split("\n  publish-release:")
        self.assertEqual(len(job_blocks), 2, "Expected exactly one publish-release job definition")
        publish_block = job_blocks[1]

        # 1. Condition must require push event
        self.assertIn("github.event_name == 'push'", publish_block)

        # 2. Condition must require tag ref
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", publish_block)

        # 3. workflow_dispatch or manual inputs must NOT be able to trigger publish-release
        self.assertNotIn("inputs.dry_run", publish_block)
        self.assertNotIn("workflow_dispatch", publish_block)

        # 4. Strict dependency on BOTH validation matrix and smoke test
        self.assertIn("tier2-validation-matrix", publish_block)
        self.assertIn("package-and-smoke-test", publish_block)


if __name__ == "__main__":
    unittest.main()

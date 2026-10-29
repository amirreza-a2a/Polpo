# ============================================================
#  tests/unit/test_ci_workflow_contract.py
#  CI-01 — Continuous Integration and Quality-Gate Contract Tests
# ============================================================

from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests.unit.workflow_expression_scanner import find_unsafe_run_interpolations


class TestCIWorkflowContract(unittest.TestCase):
    """Rigorous contract and security assertions for CI workflows and quality gates."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.ci_path = cls.repo_root / ".github" / "workflows" / "ci.yml"
        cls.periodic_path = cls.repo_root / ".github" / "workflows" / "periodic-validation.yml"
        cls.release_path = cls.repo_root / ".github" / "workflows" / "release.yml"

        for p in (cls.ci_path, cls.periodic_path, cls.release_path):
            if not p.is_file():
                raise FileNotFoundError(f"Workflow file missing: {p}")

        cls.ci_content = cls.ci_path.read_text(encoding="utf-8")
        cls.periodic_content = cls.periodic_path.read_text(encoding="utf-8")
        cls.release_content = cls.release_path.read_text(encoding="utf-8")
        cls.all_workflows = {
            "ci.yml": cls.ci_content,
            "periodic-validation.yml": cls.periodic_content,
            "release.yml": cls.release_content,
        }

    def test_workflow_files_exist_and_non_empty(self):
        """All three workflow files must exist and contain substantial configuration."""
        for name, content in self.all_workflows.items():
            with self.subTest(workflow=name):
                self.assertGreater(len(content), 500, f"{name} is too short")

    def test_actionlint_validation(self):
        """Verifies all workflow files pass actionlint with zero errors if installed."""
        actionlint_bin = shutil.which("actionlint")
        if not actionlint_bin:
            self.skipTest("actionlint executable not found in PATH")

        for name, content in self.all_workflows.items():
            with self.subTest(workflow=name):
                wf_path = self.repo_root / ".github" / "workflows" / name
                result = subprocess.run(
                    [actionlint_bin, str(wf_path)],
                    capture_output=True,
                    text=True,
                    cwd=str(self.repo_root),
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"actionlint failed on {name}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
                )

    def test_all_actions_pinned_by_40_char_sha(self):
        """Every action invocation across all workflows must be pinned to an immutable 40-character SHA."""
        sha40_pattern = re.compile(r"^[a-zA-Z0-9_\-\.\/]+@[a-f0-9]{40}$")
        for name, content in self.all_workflows.items():
            uses_matches = re.findall(r"uses:\s+([^\s]+)", content)
            self.assertGreater(len(uses_matches), 0, f"No actions found in {name}")
            for action_ref in uses_matches:
                with self.subTest(workflow=name, action=action_ref):
                    self.assertTrue(
                        sha40_pattern.match(action_ref),
                        f"Action '{action_ref}' in {name} is not pinned by a 40-character commit SHA",
                    )

    def test_all_actions_have_version_comments(self):
        """Every action invocation line must contain a clarifying '# v...' or '# <name>@...' version comment."""
        for name, content in self.all_workflows.items():
            for line_no, line in enumerate(content.splitlines(), 1):
                if re.search(r"^\s*-\s+.*uses:\s+([^\s]+)", line) or re.search(r"^\s*uses:\s+([^\s]+)", line):
                    with self.subTest(workflow=name, line=line_no):
                        self.assertIn(
                            "#",
                            line,
                            f"Action line in {name}:{line_no} lacks an explanatory version comment: {line}",
                        )

    def test_no_legacy_node20_pinned_shas(self):
        """Asserts retired Node.js 20 action SHAs are eliminated across all workflows."""
        legacy_node20_shas = {
            "1d0ff469b7ec7b3cb9d8673fde0c81c44821de2a": "actions/setup-node@v4.2.0 (Node 20)",
            "d4323d4df104b026a6aa633fdb11d772146be0bf": "actions/cache@v4.2.2 (Node 20)",
            "65c4c4a1ddee5b72f698fdd19549f0f0fb45cf08": "actions/upload-artifact@v4.6.0 (Node 20)",
            "fa0a91b85d4f404e444e00e005971372dc801d16": "actions/download-artifact@v4.1.8 (Node 20)",
            "c074443f1aee8d4aeeae555aebba3282517141b2": "actions/attest-build-provenance@v2.2.3 (Node 20 wrapper)",
            "c95fe1489396fe8a9eb87c0abf8aa5b2ef267fda": "softprops/action-gh-release@v2.2.1 (Node 20)",
        }
        for name, content in self.all_workflows.items():
            for sha, desc in legacy_node20_shas.items():
                with self.subTest(workflow=name, sha=sha):
                    self.assertNotIn(
                        sha,
                        content,
                        f"Found retired Node 20 action SHA {sha} ({desc}) in {name}",
                    )

    def test_node24_action_pins_verified(self):
        """Verifies exact immutable commit SHAs for Node 24 migrated actions."""
        expected_pins = {
            "actions/setup-node": "949feb2413d6458794dcd2491c4babbbce0c15c1",  # v7.1.0
            "actions/cache": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",       # v6.1.0
            "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",  # v7.0.2
            "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",  # v8.0.2
            "actions/attest-build-provenance": "4d101475d8b20a2381f78447822ac1eab6504dd8",  # v4.2.2
            "softprops/action-gh-release": "efb35369e0ad2afab669f228072c1b0d510eae64",      # v3.0.3
            "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",                 # v7.0.1
            "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",             # v7.0.0
            "gitleaks/gitleaks-action": "e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e",         # v3.0.0
        }
        for name, content in self.all_workflows.items():
            for action_prefix, expected_sha in expected_pins.items():
                if f"uses: {action_prefix}@" in content:
                    pattern = rf"uses:\s+{re.escape(action_prefix)}@([a-f0-9]{{40}})"
                    matches = re.findall(pattern, content)
                    for match_sha in matches:
                        with self.subTest(workflow=name, action=action_prefix):
                            self.assertEqual(
                                match_sha,
                                expected_sha,
                                f"In {name}, {action_prefix} is pinned to {match_sha}, expected {expected_sha}",
                            )

    def test_ci_workflow_required_jobs_and_permissions(self):
        """Verifies CI workflow jobs match branch protection contract with least privilege."""
        # Top-level permissions must be read-only
        top_perm = re.search(r"^permissions:\s*\n\s+contents:\s*read", self.ci_content, re.MULTILINE)
        self.assertIsNotNone(top_perm, "ci.yml top-level permissions must be contents: read")

        # Required contexts: quality-gates, test-linux, test-windows, secret-scan
        required_contexts = {"quality-gates", "test-linux", "test-windows", "secret-scan"}
        jobs_section = self.ci_content.split("jobs:\n", 1)[1]
        workflow_jobs = set(re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", jobs_section, re.MULTILINE))
        self.assertTrue(
            required_contexts.issubset(workflow_jobs),
            f"ci.yml jobs {workflow_jobs} missing required contexts {required_contexts}",
        )

        # test-linux and test-windows must depend on quality-gates
        self.assertRegex(
            self.ci_content,
            r"(?s)test-linux:.*?needs:\s*quality-gates",
            "test-linux must have needs: quality-gates",
        )
        self.assertRegex(
            self.ci_content,
            r"(?s)test-windows:.*?needs:\s*quality-gates",
            "test-windows must have needs: quality-gates",
        )

        # No continue-on-error permitted
        self.assertNotIn("continue-on-error: true", self.ci_content)

    def test_ci_workflow_quality_gates_execution(self):
        """Verifies quality gates step invokes scripts/run_quality_gates.py in both PR and push events."""
        self.assertIn("python scripts/run_quality_gates.py", self.ci_content)
        self.assertIn("--mode=commit-range", self.ci_content)
        self.assertIn("github.event_name == 'pull_request'", self.ci_content)
        self.assertIn("github.event_name == 'push'", self.ci_content)

    def test_runtime_cache_key_and_paths_contract(self):
        """Verifies runtime cache key incorporates os, arch, manifest hash, and package-lock hash without restore-keys."""
        expected_key_pattern = r"runtimes-\$\{\{\s*runner\.os\s*\}\}-\$\{\{\s*runner\.arch\s*\}\}-\$\{\{\s*hashFiles\('resources/runtimes\.json',\s*'resources/mathjax/package-lock\.json'\)\s*\}\}"
        for name, content in self.all_workflows.items():
            if "actions/cache@" in content:
                with self.subTest(workflow=name):
                    self.assertRegex(
                        content,
                        expected_key_pattern,
                        f"{name} cache key does not match canonical runtimes key format",
                    )
                    self.assertIn("resources/bin", content)
                    self.assertIn("resources/mathjax/node", content)
                    self.assertIn("resources/mathjax/node.exe", content)
                    self.assertIn("resources/mathjax/node_modules", content)
                    # Runtime cache must not use loose restore-keys to prevent corrupt/partial cache reuse
                    self.assertNotIn("restore-keys:", content)
                    # Standalone verification must be present
                    self.assertIn("scripts/verify_mathjax_worker.py", content)

    def test_no_unsafe_context_interpolation_in_run_blocks(self):
        """Verifies that no GitHub Actions expressions (${{ ... }}) are interpolated directly into run: script bodies across all workflows.

        All dynamically evaluated context values (e.g. github.ref, inputs, commit SHAs) must be passed
        via step-level 'env:' variables and referenced via quoted shell variables to eliminate script-injection risks.
        """
        for name, content in self.all_workflows.items():
            violations = find_unsafe_run_interpolations(content)
            with self.subTest(workflow=name):
                self.assertEqual(
                    violations,
                    [],
                    f"Found unsafe ${{ ... }} context expressions inside run: blocks in {name}:\n"
                    + "\n".join(violations),
                )

    def test_release_restricted_actions_isolation(self):
        """Verifies publishing actions are strictly isolated to the publish-release job in release.yml."""
        restricted_actions = [
            "softprops/action-gh-release",
            "actions/attest-build-provenance",
        ]
        # In ci.yml and periodic-validation.yml, restricted actions must never appear
        for name in ("ci.yml", "periodic-validation.yml"):
            for action in restricted_actions:
                with self.subTest(workflow=name, action=action):
                    self.assertNotIn(action, self.all_workflows[name])

        # In release.yml, restricted actions must appear ONLY in publish-release
        job_blocks = self.release_content.split("\n  publish-release:")
        pre_publish = job_blocks[0]
        for action in restricted_actions:
            with self.subTest(workflow="release.yml", action=action, section="pre-publish"):
                self.assertNotIn(action, pre_publish)

    def test_ci_and_release_matrix_consistency(self):
        """Verifies CI/release matrices validate the supported Python versions and platform families."""
        # pyproject.toml requires Python >= 3.10 and declares 3.10, 3.11, 3.12
        for py_ver in ("3.10", "3.11", "3.12"):
            found = False
            for content in self.all_workflows.values():
                if f"'{py_ver}'" in content or f'"{py_ver}"' in content or f" {py_ver}" in content:
                    found = True
                    break
            self.assertTrue(found, f"Python version {py_ver} declared in pyproject.toml is not tested in any workflow")

        # OS runners: Linux (ubuntu-24.04 and 22.04), Windows (windows-2025), macOS (macos-15)
        for os_runner in ("ubuntu-24.04", "windows-2025", "macos-15", "ubuntu-22.04"):
            found = any(os_runner in content for content in self.all_workflows.values())
            self.assertTrue(found, f"Platform runner {os_runner} is not present in workflow matrices")

    def test_release_manual_dispatch_safety_invariant(self):
        """Verifies release workflow publish job is restricted to tag push events."""
        job_blocks = self.release_content.split("\n  publish-release:")
        self.assertEqual(len(job_blocks), 2, "Expected exactly one publish-release job definition")
        publish_block = job_blocks[1]

        self.assertIn("github.event_name == 'push'", publish_block)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", publish_block)
        self.assertNotIn("workflow_dispatch", publish_block)
        self.assertNotIn("inputs.dry_run", publish_block)

        # Write permissions strictly isolated to publish-release
        self.assertIn("contents: write", publish_block)
        self.assertIn("id-token: write", publish_block)
        self.assertIn("attestations: write", publish_block)

        pre_publish = job_blocks[0]
        self.assertNotIn("contents: write", pre_publish)
        self.assertNotIn("id-token: write", pre_publish)
        self.assertNotIn("attestations: write", pre_publish)



class TestWorkflowExpressionScanner(unittest.TestCase):
    """Regression and contract tests for find_unsafe_run_interpolations helper."""

    def test_detects_unsafe_inline_run_scalars(self):
        """Unsafe ${{ ... }} expressions must be detected on inline run: scalar commands."""
        test_cases = [
            ("double_quoted", '      - run: echo "${{ github.event.issue.title }}"'),
            ("single_quoted", "      - run: echo '${{ github.actor }}'"),
            ("unquoted_bare", "      - run: echo ${{ github.sha }}"),
            ("expression_only", "      - run: ${{ github.action }}"),
            ("python_inline", '      - run: python -c "print(\'${{ github.ref }}\')"'),
            ("without_leading_dash", '        run: echo "${{ github.sha }}"'),
            ("with_trailing_comment", '      - run: echo "hello" # ${{ github.sha }}'),
        ]

        for name, line in test_cases:
            content = f"jobs:\n  test:\n    steps:\n{line}\n"
            violations = find_unsafe_run_interpolations(content)
            with self.subTest(case=name):
                self.assertEqual(len(violations), 1, f"Expected 1 violation for {name}, got {violations}")
                self.assertIn("${{", violations[0])

    def test_detects_unsafe_multiline_script_expressions(self):
        """Unsafe ${{ ... }} expressions must be detected across all supported YAML block scalar styles."""
        test_cases = [
            ("literal_pipe", "        run: |\n          echo \"${{ github.sha }}\""),
            ("literal_strip", "        run: |-\n          echo \"${{ github.sha }}\""),
            ("literal_keep", "        run: |+\n          echo \"${{ github.sha }}\""),
            ("folded", "        run: >\n          echo \"${{ github.sha }}\""),
            ("folded_strip", "        run: >-\n          echo \"${{ github.sha }}\""),
            ("folded_keep", "        run: >+\n          echo \"${{ github.sha }}\""),
            ("explicit_indent", "        run: |2\n          echo \"${{ github.sha }}\""),
            ("bare_multiline", "        run:\n          echo \"${{ github.sha }}\""),
            ("with_header_comment", "        run: | # execute test\n          echo \"${{ github.sha }}\""),
            ("hyphen_literal_pipe", "      - run: |\n          echo \"${{ github.sha }}\""),
        ]

        for name, snippet in test_cases:
            content = f"jobs:\n  test:\n    steps:\n{snippet}\n"
            violations = find_unsafe_run_interpolations(content)
            with self.subTest(case=name):
                self.assertEqual(len(violations), 1, f"Expected 1 violation for {name}, got {violations}")
                self.assertIn("${{", violations[0])

    def test_allows_safe_step_level_env_variables(self):
        """Expressions placed in step-level env: variables must be permitted without false positives."""
        safe_workflow = """
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Safe multiline step
        env:
          TAG: ${{ github.ref_name }}
          COMMIT_SHA: ${{ github.sha }}
        run: |
          echo "Building for tag: $TAG"
          echo "Commit: $COMMIT_SHA"
      - name: Safe inline step
        env:
          ACTOR: ${{ github.actor }}
        run: echo "Triggered by $ACTOR"
"""
        violations = find_unsafe_run_interpolations(safe_workflow)
        self.assertEqual(violations, [], f"Expected zero violations, got: {violations}")

    def test_allows_unrelated_expressions_in_non_script_fields(self):
        """Unrelated expressions in if:, with:, name:, runs-on:, and matrix fields must not be flagged."""
        safe_workflow = """
name: CI-${{ github.workflow }}
on:
  push:
    branches:
      - ${{ github.event.repository.default_branch }}
jobs:
  matrix-job:
    if: ${{ github.actor != 'dependabot[bot]' }}
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest]
    steps:
      - name: Checkout step ${{ matrix.os }}
        id: checkout
        if: ${{ success() }}
        uses: actions/checkout@v4
        with:
          ref: ${{ github.sha }}
          token: ${{ secrets.GITHUB_TOKEN }}
      - name: Run safe script
        run: echo "Static script"
"""
        violations = find_unsafe_run_interpolations(safe_workflow)
        self.assertEqual(violations, [], f"Expected zero violations, got: {violations}")

    def test_multiline_script_preserves_blank_lines_and_detects_violations(self):
        """Blank lines inside multiline script bodies must not terminate scanning prematurely."""
        workflow_with_blank_lines = """
jobs:
  test:
    steps:
      - name: Step with blank lines and comments
        run: |
          echo "Start"

          # Middle comment
          echo "${{ github.event.issue.title }}"

          echo "End"
        env:
          KEY: ${{ github.ref }}
"""
        violations = find_unsafe_run_interpolations(workflow_with_blank_lines)
        self.assertEqual(len(violations), 1, f"Expected exactly 1 violation, got: {violations}")
        self.assertIn("github.event.issue.title", violations[0])
        # Ensure the sibling env: variable was not treated as a violation
        for v in violations:
            self.assertNotIn("KEY:", v)

    def test_consecutive_run_steps_and_indentation_transitions(self):
        """Transitions between multiline and inline run steps must be tracked cleanly."""
        workflow = """
jobs:
  test:
    steps:
      - name: First step safe
        run: |
          echo "safe multiline"
      - name: Second step unsafe inline
        run: echo "${{ github.sha }}"
      - name: Third step safe
        run: echo "all good"
      - name: Fourth step unsafe multiline
        run: |
          echo "${{ github.ref }}"
"""
        violations = find_unsafe_run_interpolations(workflow)
        self.assertEqual(len(violations), 2, f"Expected 2 violations, got: {violations}")
        self.assertIn("github.sha", violations[0])
        self.assertIn("github.ref", violations[1])


if __name__ == "__main__":
    unittest.main()

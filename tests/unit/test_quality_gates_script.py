# ============================================================
#  tests/unit/test_quality_gates_script.py
#  Verification suite for Ticket 11A-02: Reusable Quality Gates Runner
# ============================================================

from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, call, patch
import pytest

from scripts.run_quality_gates import (
    DEFAULT_ARCHITECTURE_TESTS,
    DEFAULT_COMPILE_DIRS,
    is_zero_sha,
    parse_args,
    resolve_commit_range,
    run_architecture_invariants,
    run_bytecode_compilation,
    run_diff_check,
    run_quality_gates,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestCliArgumentParsing:
    """CLI parsing validation for working-tree and commit-range modes."""

    def test_default_mode_is_working_tree(self):
        args = parse_args([])
        assert args.mode == "working-tree"
        assert args.base is None
        assert args.head is None

    def test_explicit_working_tree_mode(self):
        args = parse_args(["--mode=working-tree"])
        assert args.mode == "working-tree"
        assert args.base is None
        assert args.head is None

    def test_commit_range_mode_with_base_and_head(self):
        args = parse_args(["--mode=commit-range", "--base", "abc1234", "--head", "def5678"])
        assert args.mode == "commit-range"
        assert args.base == "abc1234"
        assert args.head == "def5678"

    def test_commit_range_missing_base_raises_system_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--mode=commit-range", "--head", "def5678"])
        assert exc_info.value.code != 0

    def test_commit_range_missing_head_raises_system_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--mode=commit-range", "--base", "abc1234"])
        assert exc_info.value.code != 0

    def test_invalid_mode_raises_system_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--mode=unsupported-mode"])
        assert exc_info.value.code != 0


class TestZeroShaDetection:
    """Zero-SHA detection for push events on newly created branches."""

    def test_40_zeroes_is_zero_sha(self):
        assert is_zero_sha("0" * 40) is True

    def test_64_zeroes_is_zero_sha(self):
        assert is_zero_sha("0" * 64) is True

    def test_zero_sha_with_whitespace_is_zero_sha(self):
        assert is_zero_sha("  " + "0" * 40 + " \n") is True

    def test_standard_commit_sha_is_not_zero_sha(self):
        assert is_zero_sha("11718c251e8b3efd1dfbffb4473db8e564908b25") is False
        assert is_zero_sha("11718c2") is False

    def test_empty_string_and_none_are_not_zero_sha(self):
        assert is_zero_sha("") is False
        assert is_zero_sha(None) is False

    def test_string_with_mixed_zeros_is_not_zero_sha(self):
        assert is_zero_sha("000000000000000000000000000000000000000a") is False


class TestCommitRangeResolution:
    """Commit-range resolution including zero-SHA boundary edge cases."""

    def test_standard_commit_range_preserved(self):
        base, head = resolve_commit_range("sha_base_123", "sha_head_456")
        assert base == "sha_base_123"
        assert head == "sha_head_456"

    @patch("subprocess.run")
    def test_zero_sha_resolves_to_parent_when_head_has_parent(self, mock_run):
        # Mock git rev-parse HEAD~1 returning success
        mock_run.return_value = MagicMock(returncode=0, stdout="parent_sha_789\n")

        base, head = resolve_commit_range("0" * 40, "head_sha_999")

        assert base == "head_sha_999~1"
        assert head == "head_sha_999"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "git" in cmd
        assert "rev-parse" in cmd

    @patch("subprocess.run")
    def test_zero_sha_falls_back_to_empty_tree_when_head_is_root(self, mock_run):
        # rev-parse HEAD~1 fails (root commit, no parent)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal: bad revision")

        base, head = resolve_commit_range("0" * 40, "root_sha_001")

        assert base == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        assert head == "root_sha_001"



class TestDiffHygieneGate:
    """Validation of git diff --check across working tree and commit ranges."""

    @patch("subprocess.run")
    def test_working_tree_mode_checks_unstaged_and_cached_diffs(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        passed, details = run_diff_check(mode="working-tree", repo_root=REPO_ROOT)

        assert passed is True
        assert mock_run.call_count == 2
        # Check both unstaged and cached diff checks were run
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["git", "diff", "--check"] in calls
        assert ["git", "diff", "--cached", "--check"] in calls

    @patch("subprocess.run")
    def test_working_tree_mode_fails_if_unstaged_diff_fails(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=2, stdout="file.py:10: trailing whitespace.\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        passed, details = run_diff_check(mode="working-tree", repo_root=REPO_ROOT)

        assert passed is False
        assert "trailing whitespace" in details

    @patch("subprocess.run")
    def test_working_tree_mode_fails_if_cached_diff_fails(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=2, stdout="file.py:20: trailing whitespace.\n", stderr=""),
        ]

        passed, details = run_diff_check(mode="working-tree", repo_root=REPO_ROOT)

        assert passed is False
        assert "trailing whitespace" in details

    @patch("subprocess.run")
    def test_commit_range_mode_runs_diff_check_range(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        passed, details = run_diff_check(
            mode="commit-range",
            base="commit_aaa",
            head="commit_bbb",
            repo_root=REPO_ROOT,
        )

        assert passed is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "diff", "--check", "commit_aaa..commit_bbb"]

    @patch("subprocess.run")
    def test_commit_range_mode_fails_on_whitespace_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout="src.py:5: trailing whitespace.\n",
            stderr="",
        )

        passed, details = run_diff_check(
            mode="commit-range",
            base="commit_aaa",
            head="commit_bbb",
            repo_root=REPO_ROOT,
        )

        assert passed is False
        assert "trailing whitespace" in details

    @patch("subprocess.run")
    def test_commit_range_mode_handles_zero_sha_base(self, mock_run):
        # 1st call: rev-parse HEAD~1 succeeds
        # 2nd call: git diff --check head~1..head succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="head~1_sha\n"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        passed, details = run_diff_check(
            mode="commit-range",
            base="0" * 40,
            head="head_sha_xxx",
            repo_root=REPO_ROOT,
        )

        assert passed is True
        assert mock_run.call_count == 2
        diff_cmd = mock_run.call_args_list[1][0][0]
        assert diff_cmd == ["git", "diff", "--check", "head_sha_xxx~1..head_sha_xxx"]


class TestBytecodeCompilationGate:
    """Validation of compileall.compile_dir execution over packages."""

    @patch("compileall.compile_dir")
    def test_compileall_invoked_for_all_default_directories(self, mock_compile):
        mock_compile.return_value = True

        passed, details = run_bytecode_compilation(repo_root=REPO_ROOT)

        assert passed is True
        # Verify compile_dir was invoked for all target directories
        assert len(mock_compile.call_args_list) == len(DEFAULT_COMPILE_DIRS)
        called_dirs = [Path(c[0][0]).name for c in mock_compile.call_args_list]
        for expected_dir in DEFAULT_COMPILE_DIRS:
            assert expected_dir in called_dirs


        # Ensure every compile_dir invocation used force=False and quiet=1
        for call_item in mock_compile.call_args_list:
            kwargs = call_item[1]
            assert kwargs.get("force") is False, f"force must be False in {call_item}"
            assert kwargs.get("quiet") == 1, f"quiet must be 1 in {call_item}"


    @patch("compileall.compile_dir")
    def test_compileall_fails_immediately_on_syntax_error(self, mock_compile):
        # Fail on first directory
        mock_compile.return_value = False

        passed, details = run_bytecode_compilation(repo_root=REPO_ROOT)

        assert passed is False
        assert mock_compile.call_count == 1
        assert "Compilation failed" in details


class TestArchitectureInvariantsGate:
    """Validation of architecture AST invariant test suite invocation."""

    @patch("subprocess.run")
    def test_architecture_invariants_invokes_targeted_suites(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="30 passed in 1.5s\n", stderr="")

        passed, details = run_architecture_invariants(repo_root=REPO_ROOT)

        assert passed is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ["-m", "pytest", "-q"]
        for suite in DEFAULT_ARCHITECTURE_TESTS:
            assert suite in cmd

    @patch("subprocess.run")
    def test_architecture_invariants_fails_when_tests_fail(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED invariant test\n", stderr="")

        passed, details = run_architecture_invariants(repo_root=REPO_ROOT)

        assert passed is False
        assert "FAILED invariant test" in details


class TestRunQualityGatesOrchestration:
    """End-to-end orchestration and exit code propagation."""

    @patch("scripts.run_quality_gates.run_diff_check")
    @patch("scripts.run_quality_gates.run_bytecode_compilation")
    @patch("scripts.run_quality_gates.run_architecture_invariants")
    def test_all_gates_pass_returns_zero(self, mock_arch, mock_comp, mock_diff):
        mock_diff.return_value = (True, "Diff clean")
        mock_comp.return_value = (True, "Compilation clean")
        mock_arch.return_value = (True, "Architecture clean")

        exit_code = run_quality_gates(["--mode=working-tree"], repo_root=REPO_ROOT)

        assert exit_code == 0
        mock_diff.assert_called_once()
        mock_comp.assert_called_once()
        mock_arch.assert_called_once()

    @patch("scripts.run_quality_gates.run_diff_check")
    @patch("scripts.run_quality_gates.run_bytecode_compilation")
    @patch("scripts.run_quality_gates.run_architecture_invariants")
    def test_diff_failure_returns_non_zero(self, mock_arch, mock_comp, mock_diff):
        mock_diff.return_value = (False, "Whitespace error detected")
        mock_comp.return_value = (True, "Compilation clean")
        mock_arch.return_value = (True, "Architecture clean")

        exit_code = run_quality_gates(["--mode=working-tree"], repo_root=REPO_ROOT)

        assert exit_code != 0

    @patch("scripts.run_quality_gates.run_diff_check")
    @patch("scripts.run_quality_gates.run_bytecode_compilation")
    @patch("scripts.run_quality_gates.run_architecture_invariants")
    def test_compilation_failure_returns_non_zero(self, mock_arch, mock_comp, mock_diff):
        mock_diff.return_value = (True, "Diff clean")
        mock_comp.return_value = (False, "Syntax error in core")
        mock_arch.return_value = (True, "Architecture clean")

        exit_code = run_quality_gates(["--mode=working-tree"], repo_root=REPO_ROOT)

        assert exit_code != 0

    @patch("scripts.run_quality_gates.run_diff_check")
    @patch("scripts.run_quality_gates.run_bytecode_compilation")
    @patch("scripts.run_quality_gates.run_architecture_invariants")
    def test_architecture_failure_returns_non_zero(self, mock_arch, mock_comp, mock_diff):
        mock_diff.return_value = (True, "Diff clean")
        mock_comp.return_value = (True, "Compilation clean")
        mock_arch.return_value = (False, "Invariant test failed")

        exit_code = run_quality_gates(["--mode=working-tree"], repo_root=REPO_ROOT)

        assert exit_code != 0

    @patch("scripts.run_quality_gates.run_diff_check")
    @patch("scripts.run_quality_gates.run_bytecode_compilation")
    @patch("scripts.run_quality_gates.run_architecture_invariants")
    def test_commit_range_arguments_forwarded(self, mock_arch, mock_comp, mock_diff):
        mock_diff.return_value = (True, "Diff clean")
        mock_comp.return_value = (True, "Compilation clean")
        mock_arch.return_value = (True, "Architecture clean")

        exit_code = run_quality_gates(
            ["--mode=commit-range", "--base", "sha_111", "--head", "sha_222"],
            repo_root=REPO_ROOT,
        )

        assert exit_code == 0
        mock_diff.assert_called_once_with(
            mode="commit-range",
            base="sha_111",
            head="sha_222",
            repo_root=REPO_ROOT,
        )


class TestEdgeCasesAndSubprocess:
    """Additional edge case handling and direct script subprocess verification."""

    def test_get_empty_tree_sha_returns_canonical_constant(self):
        from scripts.run_quality_gates import EMPTY_TREE_SHA, get_empty_tree_sha

        assert get_empty_tree_sha() == EMPTY_TREE_SHA
        assert get_empty_tree_sha(REPO_ROOT) == EMPTY_TREE_SHA


    def test_run_diff_check_unsupported_mode(self):
        passed, details = run_diff_check(mode="invalid-mode", repo_root=REPO_ROOT)
        assert passed is False
        assert "Unsupported mode" in details

    def test_run_diff_check_commit_range_missing_args(self):
        passed, details = run_diff_check(mode="commit-range", base=None, head="abc", repo_root=REPO_ROOT)
        assert passed is False
        assert "requires both --base and --head" in details

    def test_run_bytecode_compilation_skips_nonexistent_dir(self, tmp_path):
        # A directory list containing non-existent directory should skip it cleanly
        passed, details = run_bytecode_compilation(repo_root=tmp_path, directories=["does_not_exist"])
        assert passed is True
        assert "compiled cleanly" in details

    def test_script_cli_help_flag(self):
        script_path = REPO_ROOT / "scripts" / "run_quality_gates.py"
        res = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "PolpoT Reusable Quality Gates" in res.stdout

    def test_script_cli_invalid_mode_exit_code(self):
        script_path = REPO_ROOT / "scripts" / "run_quality_gates.py"
        res = subprocess.run(
            [sys.executable, str(script_path), "--mode=bogus"],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0

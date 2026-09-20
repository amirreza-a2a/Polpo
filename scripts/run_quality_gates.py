#!/usr/bin/env python3
# ============================================================
#  scripts/run_quality_gates.py
#  Reusable Quality Gates & Hygiene Validation Runner
#  Phase 11A — Ticket 11A-02
# ============================================================

import argparse
import compileall
from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys

# Default directories checked for Python bytecode compilation
DEFAULT_COMPILE_DIRS = [
    "core",
    "application",
    "infrastructure",
    "interfaces",
    "tests",
]

# Targeted AST architecture invariant test suites
DEFAULT_ARCHITECTURE_TESTS = [
    "tests/unit/test_phase10e3a_architecture_invariants.py",
    "tests/unit/test_phase4_architecture.py",
    "tests/unit/test_desktop_presentation_invariants.py",
]

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def is_zero_sha(sha: str | None) -> bool:
    """
    Determine if a SHA string represents an all-zero hash (e.g. GitHub before-SHA
    when pushing a newly created branch).
    """
    if not sha:
        return False
    s = sha.strip()
    return len(s) >= 7 and set(s) == {"0"}


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate command line arguments for quality gates runner."""
    parser = argparse.ArgumentParser(
        description="PolpoT Reusable Quality Gates & Hygiene Validation Runner",
    )
    parser.add_argument(
        "--mode",
        choices=["working-tree", "commit-range"],
        default="working-tree",
        help="Validation mode: 'working-tree' for local changes or 'commit-range' for CI.",
    )
    parser.add_argument(
        "--base",
        type=str,
        default=None,
        help="Base commit SHA (required for commit-range mode).",
    )
    parser.add_argument(
        "--head",
        type=str,
        default=None,
        help="Head commit SHA (required for commit-range mode).",
    )

    parsed = parser.parse_args(args)

    if parsed.mode == "commit-range":
        if not parsed.base or not parsed.head:
            parser.error("--mode=commit-range requires both --base and --head")

    return parsed


def get_empty_tree_sha(repo_root: Path | None = None) -> str:
    """Return the canonical Git empty-tree SHA for root commit comparisons."""
    return EMPTY_TREE_SHA



def resolve_commit_range(
    base: str,
    head: str,
    repo_root: Path | None = None,
) -> tuple[str, str]:
    """
    Resolve base and head for commit range validation.
    Handles zero-SHA boundary edge cases (e.g. GitHub event 'before' on new branches)
    by falling back to the parent commit of head or root commit diff.
    """
    head_clean = head.strip()
    base_clean = base.strip()

    if not is_zero_sha(base_clean):
        return base_clean, head_clean

    cwd = repo_root or Path.cwd()
    # Check if head has a parent commit
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{head_clean}~1^{{commit}}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        return f"{head_clean}~1", head_clean

    # Head has no parent (initial/root commit in repo)
    empty_tree = get_empty_tree_sha(cwd)
    return empty_tree, head_clean


def run_diff_check(
    mode: str,
    base: str | None = None,
    head: str | None = None,
    repo_root: Path | None = None,
) -> tuple[bool, str]:
    """
    Validate whitespace and formatting hygiene using native git diff mechanisms.
    In working-tree mode: checks unstaged and cached changes.
    In commit-range mode: checks all commits between base and head.
    """
    cwd = repo_root or Path.cwd()

    if mode == "working-tree":
        # Check unstaged working tree changes
        res_unstaged = subprocess.run(
            ["git", "diff", "--check"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if res_unstaged.returncode != 0:
            details = (res_unstaged.stdout + "\n" + res_unstaged.stderr).strip()
            return False, f"Unstaged whitespace error(s) detected:\n{details}"

        # Check staged index changes
        res_cached = subprocess.run(
            ["git", "diff", "--cached", "--check"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if res_cached.returncode != 0:
            details = (res_cached.stdout + "\n" + res_cached.stderr).strip()
            return False, f"Staged (cached) whitespace error(s) detected:\n{details}"

        return True, "Working tree and staged index are whitespace-clean."

    if mode == "commit-range":
        if not base or not head:
            return False, "commit-range mode requires both --base and --head"

        resolved_base, resolved_head = resolve_commit_range(base, head, repo_root=cwd)
        diff_spec = f"{resolved_base}..{resolved_head}"

        res = subprocess.run(
            ["git", "diff", "--check", diff_spec],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            details = (res.stdout + "\n" + res.stderr).strip()
            return False, f"Whitespace error(s) in commit range {diff_spec}:\n{details}"

        return True, f"Commit range {diff_spec} is whitespace-clean."

    return False, f"Unsupported mode: {mode}"


def run_bytecode_compilation(
    repo_root: Path | None = None,
    directories: Sequence[str] | None = None,
) -> tuple[bool, str]:
    """
    Verify Python bytecode compilation across project packages.
    Fails immediately on any syntax error.
    """
    cwd = repo_root or Path.cwd()
    dirs = directories or DEFAULT_COMPILE_DIRS

    for d in dirs:
        target = cwd / d
        if not target.exists():
            continue

        success = compileall.compile_dir(str(target), force=False, quiet=1)
        if not success:
            return False, f"Compilation failed with syntax error in directory: {d}"

    return True, f"Python bytecode compiled cleanly across: {', '.join(dirs)}"


def run_architecture_invariants(
    repo_root: Path | None = None,
    test_files: Sequence[str] | None = None,
) -> tuple[bool, str]:
    """
    Execute targeted AST architecture invariant suites using pytest.
    Fails if any invariant test assertion fails.
    """
    cwd = repo_root or Path.cwd()
    tests = list(test_files or DEFAULT_ARCHITECTURE_TESTS)

    cmd = [sys.executable, "-m", "pytest", "-q", *tests]
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    if res.returncode != 0:
        details = (res.stdout + "\n" + res.stderr).strip()
        return False, f"Architecture invariant tests failed (exit code {res.returncode}):\n{details}"

    summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "passed"
    return True, f"Architecture invariants verified ({summary})"


def run_quality_gates(
    argv: Sequence[str] | None = None,
    repo_root: Path | None = None,
) -> int:
    """
    Main orchestration entry point for quality gates.
    Executes whitespace hygiene, bytecode compilation, and architecture invariants.
    Returns 0 on success, non-zero on any failure.
    """
    args = parse_args(argv)
    root = repo_root or Path(__file__).resolve().parent.parent

    print("=" * 65)
    print(f"PolpoT Quality Gates Runner [mode: {args.mode}]")
    print("=" * 65)

    failures = 0

    # 1. Diff & whitespace hygiene
    print("[1/3] Validating diff and whitespace hygiene...")
    passed, details = run_diff_check(
        mode=args.mode,
        base=args.base,
        head=args.head,
        repo_root=root,
    )
    if passed:
        print(f"  [PASS] {details}")
    else:
        print(f"  [FAIL] {details}")
        failures += 1

    # 2. Bytecode compilation
    print("[2/3] Validating Python bytecode compilation...")
    passed, details = run_bytecode_compilation(repo_root=root)
    if passed:
        print(f"  [PASS] {details}")
    else:
        print(f"  [FAIL] {details}")
        failures += 1

    # 3. Architecture AST invariants
    print("[3/3] Validating architecture AST invariants...")
    passed, details = run_architecture_invariants(repo_root=root)
    if passed:
        print(f"  [PASS] {details}")
    else:
        print(f"  [FAIL] {details}")
        failures += 1

    print("-" * 65)
    if failures == 0:
        print("All quality gates passed successfully.")
        return 0

    print(f"Quality gate validation failed ({failures} failure(s) detected).")
    return 1


if __name__ == "__main__":
    sys.exit(run_quality_gates(sys.argv[1:]))

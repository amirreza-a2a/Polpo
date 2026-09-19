# 02: Reusable Quality Gates & Hygiene Validation Runner

**What to build:** Create a standardized, reusable quality gate runner script (`scripts/run_quality_gates.py`) capable of running both locally and in CI. The script orchestrates whitespace and line-ending hygiene checks, Python bytecode compilation across all packages, and static AST architecture invariant checks. The script accepts explicit commit-range arguments (`--base <sha>` and `--head <sha>`) to accurately validate multi-commit push events (`before..after`) and pull request ranges (`base.sha..head.sha`) in CI, while providing a dedicated `--mode=working-tree` for local developer use.

**Blocked by:** 01 (Standardized Project & Test Configuration)

**Status:** ready-for-agent

## Scope

- Create `scripts/run_quality_gates.py` supporting explicit execution modes:
  - `--mode=working-tree` (default for local development):
    - Validates uncommitted working-tree and staged changes via `git diff --check` and `git diff --cached --check`.
  - `--mode=commit-range --base <sha> --head <sha>` (for CI push and PR events):
    - Validates the complete range of commits introduced: `git diff --check <base>..<head>`.
    - Correctly handles multi-commit pushes using `github.event.before` as `--base` and `github.event.after` (or `github.sha`) as `--head`.
    - Handles initial commit or zero-SHA boundary edge cases (when `before` is `0000000000000000000000000000000000000000`, e.g., on a newly pushed branch) by falling back to the parent commit of the earliest pushed commit or single-commit diff.
    - For pull request events, validates `github.event.pull_request.base.sha` as `--base` and `github.event.pull_request.head.sha` as `--head`.
- Syntax & Bytecode Compilation:
  - Executes `compileall.compile_dir` across `core/`, `application/`, `infrastructure/`, `interfaces/`, and `tests/` with `force=False` and `quiet=1`, failing immediately on syntax errors.
- Architecture Invariant Checks:
  - Executes the targeted AST architecture test suites:
    - `tests/unit/test_phase10e3a_architecture_invariants.py`
    - `tests/unit/test_phase4_architecture.py`
    - `tests/unit/test_desktop_presentation_invariants.py`
- Emits structured, legible console output and terminates with exit code `0` on success and non-zero on any failure.

## Non-Goals

- Replacing full pytest suite execution (this runner is designed as a fast fail-first gate executing in < 15 seconds).
- Implementing bespoke regex-based secret scanning (handled by `gitleaks` in Ticket 06).
- Adding unneeded third-party linter dependencies.

## Files Likely Impacted

- Create: `scripts/run_quality_gates.py`
- Create: `tests/unit/test_quality_gates_script.py`

## Acceptance Criteria

- [ ] `scripts/run_quality_gates.py` exists, is written in standard Python 3.10+, and requires no third-party libraries outside `pytest`.
- [ ] Running `python scripts/run_quality_gates.py --mode=working-tree` checks working tree and index cleanly, failing if uncommitted files contain whitespace errors.
- [ ] Running `python scripts/run_quality_gates.py --mode=commit-range --base <sha1> --head <sha2>` validates the complete commit range between `sha1` and `sha2`.
- [ ] Multi-commit pushes in CI validate every commit in the push range, not just `HEAD~1..HEAD`.
- [ ] Zero-SHA edge cases (new branch creation in push event) are gracefully detected and handled without crashing.
- [ ] Pull request invocations properly receive and validate the PR base and head SHAs.
- [ ] Bytecode compilation tests all project packages and immediately fails on syntax errors.
- [ ] Architecture invariant suites are invoked and any invariant violation produces a non-zero exit code.
- [ ] Unit tests in `test_quality_gates_script.py` thoroughly test CLI argument parsing, commit-range resolution, zero-SHA fallback, and failure exit codes.

## Verification / Tests Required

- Run `python scripts/run_quality_gates.py --mode=working-tree` locally; verify all checks pass in < 15s.
- Test commit-range validation against known commit pairs (e.g. `python scripts/run_quality_gates.py --mode=commit-range --base HEAD~2 --head HEAD`).
- Run `pytest tests/unit/test_quality_gates_script.py`.

## Cross-Platform Implications

- Implemented in pure Python using `subprocess.run(["git", ...])`, avoiding bash-specific pipeline constructs or GNU-specific flags, ensuring flawless execution on Linux, Windows, and macOS.

## Failure & Rollback Considerations

- Purely additive script under `scripts/`. In the event of an issue, developers can fall back to direct `git diff --check` and `pytest tests/unit/test_phase10e3a_architecture_invariants.py`.

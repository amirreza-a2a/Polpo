# ============================================================
#  tests/unit/workflow_expression_scanner.py
#  Reusable GitHub Actions Workflow Expression Scanner
# ============================================================

from collections.abc import Sequence
import re

# Recognizes YAML block scalar headers (literal '|' or folded '>') with optional
# chomping (+/-), indentation indicators, and trailing comments.
BLOCK_SCALAR_HEADER_PATTERN = re.compile(
    r"^[|>](?:[\-+]?\d*|\d*[\-+]?)(?:\s*#.*)?$"
)

# Matches 'run:' step declarations, with or without leading list marker ('- ').
RUN_KEY_PATTERN = re.compile(
    r"^(\s*(?:-\s*)?)run:\s*(.*)$"
)


def find_unsafe_run_interpolations(content: str) -> list[str]:
    """Scan a GitHub Actions YAML workflow string for unsafe ${{ ... }} expression interpolation in run blocks.

    Checks both:
    1. Inline scalar commands on the same line as the 'run:' key.
    2. Multiline script bodies under block scalar forms ('|', '>', '|-' etc.) or bare 'run:'.

    Correctly terminates scanning at the boundary of the script body so that step-level
    'env:', 'if:', 'with:', or subsequent sibling keys are not mistaken for shell script commands.

    Returns a list of violation strings formatted as: 'Line <number>: <line content>'.
    """
    lines = content.splitlines()
    in_multiline = False
    run_indent = 0
    violations: list[str] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # If currently scanning a multiline script body
        if in_multiline:
            if stripped:
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= run_indent:
                    in_multiline = False
                    # Script body terminated; fall through to evaluate whether this line
                    # introduces a new run: block or sibling key.
                elif "${{" in line:
                    violations.append(f"Line {i}: {stripped}")
                    continue
                else:
                    continue
            else:
                # Blank lines inside a multiline script body do not terminate the block.
                continue

        # Check whether this line defines a 'run:' key
        run_match = RUN_KEY_PATTERN.match(line)
        if run_match:
            run_indent = len(run_match.group(1))
            after_run = run_match.group(2).strip()

            if not after_run or after_run.startswith("#") or BLOCK_SCALAR_HEADER_PATTERN.match(after_run):
                in_multiline = True
            else:
                in_multiline = False
                if "${{" in line:
                    violations.append(f"Line {i}: {stripped}")

    return violations

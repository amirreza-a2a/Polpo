# ============================================================
#  core/markdown/diagram_extractor.py
#  Deterministic LaTeX Diagram Environment Extractor & Validator
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional, Sequence, Tuple

SUPPORTED_DIAGRAM_ENVIRONMENTS: Tuple[str, ...] = (
    "tikzpicture",
    "circuitikz",
    "tikzcd",
)

# Regex matching LaTeX environment delimiters: \begin{name} or \end{name}
_ENV_DELIMITER_PATTERN = re.compile(
    r"\\(begin|end)\s*\{([a-zA-Z0-9*_-]+)\}",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ExtractedDiagram:
    """Represents an extracted LaTeX diagram environment.

    Attributes:
        environment: The top-level environment name (e.g., 'tikzpicture', 'circuitikz').
        source: The exact verbatim LaTeX code substring including enclosing begin/end.
        is_complete: True if the environment and all nested environments are properly closed.
        start_index: Start character index within the source text.
        end_index: End character index within the source text.
        error: Descriptive validation or syntax error if incomplete.
    """

    environment: str
    source: str
    is_complete: bool
    start_index: int = 0
    end_index: int = 0
    error: Optional[str] = None


def _is_escaped(text: str, idx: int) -> bool:
    """Returns True if the character at idx in text is preceded by an odd number of backslashes."""
    count = 0
    i = idx - 1
    while i >= 0 and text[i] == "\\":
        count += 1
        i -= 1
    return (count % 2) == 1


def _is_in_comment(text: str, idx: int) -> bool:
    """Checks whether the character at idx is inside a LaTeX comment line (% ...)."""
    line_start = text.rfind("\n", 0, idx)
    line_start = 0 if line_start == -1 else line_start + 1
    comment_pos = text.find("%", line_start, idx)
    while comment_pos != -1:
        if not _is_escaped(text, comment_pos):
            return True
        comment_pos = text.find("%", comment_pos + 1, idx)
    return False


def extract_diagrams(
    raw_latex: str,
    supported_environments: Sequence[str] = SUPPORTED_DIAGRAM_ENVIRONMENTS,
) -> List[ExtractedDiagram]:
    """Deterministically extracts and validates all diagram environments from LaTeX text.

    Args:
        raw_latex: Raw LaTeX string (e.g. from a raw fenced block or document snippet).
        supported_environments: Environment names to recognize as diagram boundaries.

    Returns:
        List of ExtractedDiagram instances in order of occurrence.
    """
    if not raw_latex or not raw_latex.strip():
        return []

    supported_set = set(supported_environments)
    diagrams: List[ExtractedDiagram] = []
    cursor = 0
    length = len(raw_latex)

    while cursor < length:
        match = _ENV_DELIMITER_PATTERN.search(raw_latex, cursor)
        if not match:
            break

        cmd, env_name = match.group(1), match.group(2)
        match_start, match_end = match.span()

        if _is_in_comment(raw_latex, match_start):
            cursor = match_end
            continue

        if cmd != "begin" or env_name not in supported_set:
            cursor = match_end
            continue

        # Found a supported top-level diagram environment begin
        diag_start = match_start
        diag_env = env_name
        env_stack: List[str] = [diag_env]
        scan_cursor = match_end
        is_complete = False
        error_msg: Optional[str] = None
        diag_end = length

        while scan_cursor < length:
            inner_match = _ENV_DELIMITER_PATTERN.search(raw_latex, scan_cursor)
            if not inner_match:
                # Reached end of text without closing outer environment
                error_msg = f"Unclosed environment '{diag_env}'"
                diag_end = length
                break

            i_cmd, i_env = inner_match.group(1), inner_match.group(2)
            i_start, i_end = inner_match.span()

            if _is_in_comment(raw_latex, i_start):
                scan_cursor = i_end
                continue

            if i_cmd == "begin":
                env_stack.append(i_env)
                scan_cursor = i_end
            elif i_cmd == "end":
                if not env_stack:
                    error_msg = f"Unexpected \\end{{{i_env}}} without matching \\begin"
                    diag_end = i_end
                    break

                current_top = env_stack[-1]
                if current_top == i_env:
                    env_stack.pop()
                    if not env_stack:
                        # Top-level diagram environment successfully closed
                        is_complete = True
                        diag_end = i_end
                        break
                    scan_cursor = i_end
                else:
                    # Mismatch: encountered \end{foo} when stack top is bar
                    if i_env == diag_env:
                        # Outer environment closed before inner environments were closed
                        error_msg = f"Unclosed nested environment '{current_top}' before \\end{{{diag_env}}}"
                        diag_end = i_end
                        break
                    else:
                        # Attempt to close intermediate environment
                        if i_env in env_stack:
                            while env_stack and env_stack[-1] != i_env:
                                env_stack.pop()
                            if env_stack:
                                env_stack.pop()
                        scan_cursor = i_end

        if not is_complete and error_msg is None:
            if env_stack:
                error_msg = f"Unclosed environment '{env_stack[-1]}'"

        source_slice = raw_latex[diag_start:diag_end]
        diagrams.append(
            ExtractedDiagram(
                environment=diag_env,
                source=source_slice,
                is_complete=is_complete,
                start_index=diag_start,
                end_index=diag_end,
                error=error_msg,
            )
        )
        cursor = diag_end

    return diagrams


def contains_diagram(
    raw_latex: str,
    supported_environments: Sequence[str] = SUPPORTED_DIAGRAM_ENVIRONMENTS,
) -> bool:
    """Quickly tests whether the raw LaTeX text contains any supported diagram environment."""
    if not raw_latex or not raw_latex.strip():
        return False
    # Fast regex pre-filter
    for env in supported_environments:
        if f"\\begin{{{env}}}" in raw_latex or f"\\begin {{{env}}}" in raw_latex:
            return True
    return bool(extract_diagrams(raw_latex, supported_environments))

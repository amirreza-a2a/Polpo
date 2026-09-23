"""Synchronous subprocess adapter for Pandoc AST execution with data-pos extraction.

Executes `pandoc -f commonmark_x+sourcepos -t json` synchronously with strict timeout,
encoding, and capability validation, and parses source position attribute tuples.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

from infrastructure.markdown.exceptions import (
    MarkdownParserError,
    MarkdownParserTimeoutError,
)
from infrastructure.markdown.pandoc_binary import (
    MINIMUM_PANDOC_API_VERSION,
    PandocBinaryResolver,
)

_DATA_POS_PATTERN = re.compile(r"^(\d+):(\d+)-(\d+):(\d+)$")


def extract_raw_sourcepos(attr: Any) -> Optional[Tuple[int, int, int, int]]:
    """Extract raw `(start_line, start_col, end_line, end_col)` from AST attributes or node.

    Accepts:
      - 3-element Pandoc attribute list: `[id: str, classes: list, key_values: list]`
      - Key-value pair list: `[["data-pos", "1:1-2:1"], ...]`
      - AST node dictionary: e.g. `{"t": "Header", "c": [...]}` or `{"t": "Div", "c": [...]}`

    Returns:
      4-tuple of integer 1-indexed coordinates `(start_line, start_col, end_line, end_col)`
      or None if `data-pos` is missing or malformed.
    """
    if attr is None:
        return None

    # Handle dictionary AST node wrappers
    if isinstance(attr, dict):
        node_c = attr.get("c")
        node_t = attr.get("t")
        if isinstance(node_c, list) and node_c:
            if node_t == "Header" and len(node_c) >= 2:
                return extract_raw_sourcepos(node_c[1])
            elif node_t in ("Div", "Span", "CodeBlock", "Table"):
                return extract_raw_sourcepos(node_c[0])
            else:
                for item in node_c:
                    found = extract_raw_sourcepos(item)
                    if found is not None:
                        return found
        return None

    if not isinstance(attr, list):
        return None

    pos_str: Optional[str] = None

    # Case 1: Standard 3-element attribute list [identifier, classes, key_value_pairs]
    if len(attr) >= 3 and isinstance(attr[2], list):
        for kv in attr[2]:
            if isinstance(kv, (list, tuple)) and len(kv) >= 2 and kv[0] == "data-pos":
                if isinstance(kv[1], str):
                    pos_str = kv[1]
                break

    # Case 2: Direct list of key-value pairs [[k, v], ...]
    if pos_str is None:
        for item in attr:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == "data-pos":
                if isinstance(item[1], str):
                    pos_str = item[1]
                break

    if pos_str is None:
        return None

    # Handle single range or multiple semicolon-delimited ranges (e.g. Pandoc tables: "3:1-4:1;2:1-6:1")
    ranges = [r.strip() for r in pos_str.split(";") if r.strip()]
    parsed_ranges: list[Tuple[int, int, int, int]] = []
    for r in ranges:
        match = _DATA_POS_PATTERN.match(r)
        if match:
            parsed_ranges.append(tuple(map(int, match.groups())))  # type: ignore[arg-type]

    if not parsed_ranges:
        return None

    # Overall bounding box: start at min (start_line, start_col), end at max (end_line, end_col)
    start_line, start_col = min((sl, sc) for sl, sc, el, ec in parsed_ranges)
    end_line, end_col = max((el, ec) for sl, sc, el, ec in parsed_ranges)
    return (start_line, start_col, end_line, end_col)


class PandocRunner:
    """Synchronous runner for Pandoc JSON AST transformation with sourcepos."""

    def __init__(
        self,
        binary_resolver: Optional[PandocBinaryResolver] = None,
        binary_path: Optional[Path | str] = None,
    ) -> None:
        if binary_path is not None:
            self.binary_path = Path(binary_path).expanduser().resolve()
        else:
            resolver = binary_resolver or PandocBinaryResolver()
            self.binary_path = resolver.resolve()

    def run(self, text: str, timeout_seconds: float = 5.0) -> dict[str, Any]:
        """Execute pandoc -f commonmark_x+sourcepos -t json synchronously.

        Args:
            text: Markdown source text to transform.
            timeout_seconds: Subprocess timeout limit in seconds.

        Returns:
            Parsed Pandoc JSON AST dictionary.

        Raises:
            MarkdownParserTimeoutError: If Pandoc execution exceeds the timeout limit.
            MarkdownParserError: If execution exits non-zero, returns invalid JSON, or
                                 emits an incompatible API version.
        """
        cmd = [str(self.binary_path), "-f", "commonmark_x+sourcepos", "-t", "json"]

        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # Suppress console window flash when spawned from desktop GUI on Windows
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.run(
                cmd,
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                **kwargs,
            )
        except subprocess.TimeoutExpired as err:
            err_stderr = (
                err.stderr.decode("utf-8", errors="replace")
                if isinstance(err.stderr, bytes)
                else (err.stderr or "")
            )
            raise MarkdownParserTimeoutError(
                f"Pandoc process timed out after {timeout_seconds}s.",
                timeout_seconds=timeout_seconds,
                stderr=err_stderr,
            ) from err
        except OSError as err:
            raise MarkdownParserError(
                f"Failed to execute Pandoc process: {err}",
                returncode=-1,
                stderr=str(err),
            ) from err

        if proc.returncode != 0:
            raise MarkdownParserError(
                f"Pandoc execution failed with exit code {proc.returncode}: {proc.stderr.strip()}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as err:
            raise MarkdownParserError(
                f"Pandoc emitted invalid JSON: {err}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            ) from err

        if not isinstance(data, dict):
            raise MarkdownParserError(
                f"Pandoc JSON AST is not a dictionary: {type(data)}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            )

        api_version = data.get("pandoc-api-version")
        if not isinstance(api_version, list) or not all(isinstance(x, int) for x in api_version):
            raise MarkdownParserError(
                f"Pandoc output missing valid 'pandoc-api-version' integer array: {api_version}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            )

        padded_api_version = tuple(api_version) + (0,) * max(0, 3 - len(api_version))
        if padded_api_version < MINIMUM_PANDOC_API_VERSION:
            version_str = ".".join(map(str, api_version))
            required_str = ".".join(map(str, MINIMUM_PANDOC_API_VERSION))
            raise MarkdownParserError(
                f"Pandoc API version '{version_str}' is older than required {required_str}.",
                returncode=proc.returncode,
                stderr=proc.stderr,
            )

        return data

"""Tests for synchronous Pandoc runner and data-pos extraction (TICK-004)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.markdown.exceptions import (
    MarkdownParserError,
    MarkdownParserTimeoutError,
    PandocError,
)
from infrastructure.markdown.pandoc_runner import (
    PandocRunner,
    extract_raw_sourcepos,
)

SAMPLE_VALID_AST = {
    "pandoc-api-version": [1, 23, 1],
    "meta": {},
    "blocks": [
        {
            "t": "Header",
            "c": [
                1,
                ["sample-header", [], [["data-pos", "1:1-1:16"]]],
                [{"t": "Str", "c": "Sample"}, {"t": "Space"}, {"t": "Str", "c": "Header"}],
            ],
        },
        {
            "t": "Div",
            "c": [
                ["wrapper-div", ["note"], [["data-pos", "3:1-5:4"]]],
                [
                    {
                        "t": "Para",
                        "c": [
                            {
                                "t": "Span",
                                "c": [
                                    ["", [], [["data-pos", "4:1-4:12"]]],
                                    [{"t": "Str", "c": "Inside"}, {"t": "Space"}, {"t": "Str", "c": "note"}],
                                ],
                            }
                        ],
                    }
                ],
            ],
        },
    ],
}


def test_exception_inheritance():
    """Verify MarkdownParserError and MarkdownParserTimeoutError hierarchy."""
    assert issubclass(MarkdownParserError, PandocError)
    assert issubclass(MarkdownParserTimeoutError, MarkdownParserError)

    err = MarkdownParserError("Parse failed", returncode=1, stderr="Syntax error")
    assert "Parse failed" in str(err)
    assert err.returncode == 1
    assert err.stderr == "Syntax error"

    timeout_err = MarkdownParserTimeoutError("Process timed out", timeout_seconds=2.5)
    assert "Process timed out" in str(timeout_err)
    assert timeout_err.timeout_seconds == 2.5


def test_pandoc_runner_successful_execution():
    """Verify PandocRunner executes pandoc and decodes valid JSON AST."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    mock_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_VALID_AST),
        stderr="",
    )

    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = runner.run("# Sample Header\n", timeout_seconds=4.0)

        assert result == SAMPLE_VALID_AST
        mock_run.assert_called_once_with(
            [str(mock_binary), "-f", "commonmark_x+sourcepos", "-t", "json"],
            input="# Sample Header\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
            check=False,
        )


def test_pandoc_runner_utf8_stdin_preservation():
    """Verify PandocRunner passes Unicode/Persian text correctly to stdin."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    persian_text = "سلام دنیا 🎉\n"
    mock_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(SAMPLE_VALID_AST),
        stderr="",
    )

    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        runner.run(persian_text)
        assert mock_run.call_args.kwargs["input"] == persian_text


def test_pandoc_runner_timeout_raises_structured_error():
    """Verify timeout terminates and raises MarkdownParserTimeoutError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["pandoc"], timeout=1.5)):
        with pytest.raises(MarkdownParserTimeoutError) as exc_info:
            runner.run("Long text", timeout_seconds=1.5)

        assert exc_info.value.timeout_seconds == 1.5
        assert "timed out after 1.5s" in str(exc_info.value)


def test_pandoc_runner_non_zero_exit_raises_structured_error():
    """Verify non-zero return code raises MarkdownParserError with actionable stderr."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    mock_proc = MagicMock(
        returncode=2,
        stdout="",
        stderr="pandoc: Unknown option --bad-flag",
    )

    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(MarkdownParserError) as exc_info:
            runner.run("Some text")

        assert exc_info.value.returncode == 2
        assert "Unknown option --bad-flag" in exc_info.value.stderr
        assert "failed with exit code 2" in str(exc_info.value)


def test_pandoc_runner_invalid_json_raises_error():
    """Verify invalid JSON stdout raises MarkdownParserError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    mock_proc = MagicMock(
        returncode=0,
        stdout="<not-json-content>",
        stderr="",
    )

    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(MarkdownParserError, match="emitted invalid JSON"):
            runner.run("Some text")


def test_pandoc_runner_outdated_api_version_raises_error():
    """Verify API version older than [1, 23, 0] raises MarkdownParserError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    old_ast = {
        "pandoc-api-version": [1, 22, 2],
        "meta": {},
        "blocks": [],
    }

    mock_proc = MagicMock(
        returncode=0,
        stdout=json.dumps(old_ast),
        stderr="",
    )

    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(MarkdownParserError, match="older than required 1.23.0"):
            runner.run("Some text")


def test_pandoc_runner_valid_api_version_accepted():
    """Verify API versions >= 1.23.0 pass validation."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    valid_versions = [[1, 23, 0], [1, 23, 1], [1, 24, 0], [2, 0, 0]]
    for ver in valid_versions:
        ast = {"pandoc-api-version": ver, "meta": {}, "blocks": []}
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(ast), stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            res = runner.run("Text")
            assert res["pandoc-api-version"] == ver


def test_pandoc_runner_auto_resolves_binary_path():
    """Verify PandocRunner discovers binary via PandocBinaryResolver if not explicitly passed."""
    mock_resolved = Path("/auto/discovered/pandoc")
    with patch("infrastructure.markdown.pandoc_runner.PandocBinaryResolver") as mock_resolver_cls:
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = mock_resolved
        mock_resolver_cls.return_value = mock_resolver

        runner = PandocRunner()
        assert runner.binary_path == mock_resolved


# Tests for extract_raw_sourcepos


def test_extract_raw_sourcepos_from_standard_block_attr():
    """Verify sourcepos extraction from standard 3-tuple [id, classes, kvs]."""
    attr = ["header-id", ["class1"], [["data-pos", "10:1-12:35"]]]
    pos = extract_raw_sourcepos(attr)
    assert pos == (10, 1, 12, 35)


def test_extract_raw_sourcepos_from_div_and_span_attr():
    """Verify sourcepos extraction from Div and Span attribute structures."""
    div_attr = ["", ["warning"], [["data-pos", "1:1-4:1"]]]
    assert extract_raw_sourcepos(div_attr) == (1, 1, 4, 1)

    span_attr = ["my-span", [], [["custom", "val"], ["data-pos", "5:2-5:18"]]]
    assert extract_raw_sourcepos(span_attr) == (5, 2, 5, 18)


def test_extract_raw_sourcepos_from_kvs_list_directly():
    """Verify sourcepos extraction when passed key-value pairs directly."""
    kvs = [["unrelated", "x"], ["data-pos", "3:1-3:15"]]
    assert extract_raw_sourcepos(kvs) == (3, 1, 3, 15)


def test_extract_raw_sourcepos_from_ast_node_dict():
    """Verify sourcepos extraction when passed an AST block/inline dictionary."""
    header_node = {
        "t": "Header",
        "c": [1, ["hdr", [], [["data-pos", "2:1-2:10"]]], [{"t": "Str", "c": "H"}]],
    }
    assert extract_raw_sourcepos(header_node) == (2, 1, 2, 10)

    div_node = {
        "t": "Div",
        "c": [["", [], [["data-pos", "4:1-8:1"]]], []],
    }
    assert extract_raw_sourcepos(div_node) == (4, 1, 8, 1)

    span_node = {
        "t": "Span",
        "c": [["", [], [["data-pos", "1:3-1:8"]]], []],
    }
    assert extract_raw_sourcepos(span_node) == (1, 3, 1, 8)

    table_node = {
        "t": "Table",
        "c": [["table-id", [], [["data-pos", "3:1-4:1;2:1-6:1"]]], []],
    }
    assert extract_raw_sourcepos(table_node) == (2, 1, 6, 1)


def test_extract_raw_sourcepos_semicolon_delimited_ranges():
    """Verify extract_raw_sourcepos computes outer bounding box for composite ranges."""
    attr = ["", [], [["data-pos", "3:1-4:15;2:5-6:20"]]]
    pos = extract_raw_sourcepos(attr)
    assert pos == (2, 5, 6, 20)


def test_pandoc_runner_timeout_preserves_stderr():
    """Verify MarkdownParserTimeoutError captures partial stderr emitted before timeout."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    timeout_exc = subprocess.TimeoutExpired(cmd=["pandoc"], timeout=1.0, stderr=b"partial stderr message")
    with patch("subprocess.run", side_effect=timeout_exc):
        with pytest.raises(MarkdownParserTimeoutError) as exc_info:
            runner.run("text")
        assert exc_info.value.stderr == "partial stderr message"


def test_pandoc_runner_non_integer_api_version_raises_error():
    """Verify non-integer components in pandoc-api-version raise MarkdownParserError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    bad_ast = {"pandoc-api-version": [1, "23", 0], "meta": {}, "blocks": []}
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(bad_ast), stderr="")
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(MarkdownParserError, match="missing valid 'pandoc-api-version' integer array"):
            runner.run("Text")


def test_extract_raw_sourcepos_missing_returns_none():
    """Verify extract_raw_sourcepos returns None when data-pos attribute is missing."""
    assert extract_raw_sourcepos(["id", [], [["other", "val"]]]) is None
    assert extract_raw_sourcepos([]) is None
    assert extract_raw_sourcepos(None) is None
    assert extract_raw_sourcepos({"t": "Space"}) is None


def test_extract_raw_sourcepos_malformed_returns_none():
    """Verify extract_raw_sourcepos returns None for malformed coordinate strings."""
    assert extract_raw_sourcepos(["id", [], [["data-pos", "invalid-coord"]]]) is None
    assert extract_raw_sourcepos(["id", [], [["data-pos", "1:2"]]]) is None
    assert extract_raw_sourcepos(["id", [], [["data-pos", "1:2-3"]]]) is None
    assert extract_raw_sourcepos(["id", [], [["data-pos", 123]]]) is None


def test_pandoc_runner_os_error_raises_error():
    """Verify OSError on spawn raises MarkdownParserError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    with patch("subprocess.run", side_effect=OSError("Exec format error")):
        with pytest.raises(MarkdownParserError, match="Failed to execute Pandoc process"):
            runner.run("Some text")


def test_pandoc_runner_json_not_dict_raises_error():
    """Verify JSON output that is a list raises MarkdownParserError."""
    mock_binary = Path("/mock/bin/pandoc")
    runner = PandocRunner(binary_path=mock_binary)

    mock_proc = MagicMock(returncode=0, stdout="[1, 2, 3]", stderr="")
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(MarkdownParserError, match="JSON AST is not a dictionary"):
            runner.run("Some text")


def test_extract_raw_sourcepos_non_list_non_dict():
    """Verify non-collection types return None."""
    assert extract_raw_sourcepos("not-a-list") is None
    assert extract_raw_sourcepos(42) is None
    assert extract_raw_sourcepos(3.14) is None


def test_real_system_pandoc_runner_integration():
    """If real Pandoc is present on the testing host, verify runner executes real input."""
    import shutil
    if not shutil.which("pandoc"):
        pytest.skip("Pandoc not installed on test host")

    runner = PandocRunner()
    result = runner.run("# Integration Test\n\nParagraph text.\n")
    assert "pandoc-api-version" in result
    assert "blocks" in result

    # Check extract_raw_sourcepos works on real parsed blocks
    header_block = result["blocks"][0]
    pos = extract_raw_sourcepos(header_block)
    assert pos is not None
    assert pos[0] == 1  # starts at line 1

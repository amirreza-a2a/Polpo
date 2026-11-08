# ============================================================
#  tests/unit/test_latex_repair_security.py
#  Unit Tests for LaTeX Repair Security and Input Validation
# ============================================================

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from application.services.latex_repair_service import LaTeXRepairService, validate_latex_safety


def test_validate_latex_safety_rejects_input_variants():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    input_variants = [
        r"\input{secret.tex}",
        r"\input secret.tex",
        r"\input {secret.tex}",
        r"\input  /etc/passwd",
        r"\include{subfile}",
        r"\include {subfile}",
    ]
    for variant in input_variants:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": variant,
            "change_summary": "Malicious input attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for: {variant}"
        assert err is not None
        assert "forbidden" in err.lower() or "input" in err.lower() or "include" in err.lower()


def test_validate_latex_safety_rejects_filesystem_primitives():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    primitives = [
        r"\openin\file=canary.txt",
        r"\openin 1=canary.txt",
        r"\read\file to \data",
        r"\read1 to \data",
        r"\openout\outfile=canary.txt",
        r"\openout 1=canary.txt",
        r"\catcode`\@=11",
        r"\special{ps: ...}",
    ]
    for primitive in primitives:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": primitive,
            "change_summary": "Malicious primitive attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for: {primitive}"
        assert err is not None
        assert "forbidden" in err.lower()


def test_validate_latex_safety_rejects_shell_escape_and_write():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    shell_attacks = [
        r"\write18{id}",
        r"\write 18 {id}",
        r"\write 18{whoami}",
        r"\immediate\write18{cat /etc/passwd}",
        r"\immediate \write 18 {rm -rf /}",
        r"\write\stream{hello}",
    ]
    for attack in shell_attacks:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": attack,
            "change_summary": "Shell escape attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for: {attack}"
        assert err is not None
        assert "forbidden" in err.lower()


def test_validate_latex_safety_rejects_path_traversal():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    traversals = [
        r"\includegraphics{../canary.png}",
        r"\pgfplotstableread{../../data.csv}\table",
        r"..\something",
    ]
    for t in traversals:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": t,
            "change_summary": "Traversal attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for: {t}"
        assert err is not None
        assert "forbidden" in err.lower() or "traversal" in err.lower()


def test_validate_latex_safety_rejects_macro_and_primitive_tricks():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    tricks = [
        r"\csname input\endcsname{canary.txt}",
        r"\csname @@input\endcsname{canary.txt}",
        r"\csname write18\endcsname{id}",
        r"\@@input canary.txt",
        r"\@input canary.txt",
        r"\immediate\write18{id}",
        r"\scantokens{\input{secret}}",
        r"\directlua{os.execute('id')}",
        r"\luaexec{os.execute('id')}",
        r"\newread\myread",
        r"\newwrite\mywrite",
        r"\closein\myread",
        r"\closeout\mywrite",
    ]
    for trick in tricks:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": trick,
            "change_summary": "Macro or primitive execution trick",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for macro trick: {trick}"
        assert err is not None
        assert "forbidden" in err.lower()


def test_validate_latex_safety_rejects_filecontents_environment():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    filecontents_attacks = [
        r"""\begin{filecontents}{evil.tex}
MALICIOUS PAYLOAD
\end{filecontents}
\draw (0,0);""",
        r"""\begin{filecontents*}{evil.txt}
MALICIOUS PAYLOAD
\end{filecontents*}
\draw (0,0);""",
    ]
    for attack in filecontents_attacks:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": attack,
            "change_summary": "Filecontents creation attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for filecontents: {attack}"
        assert err is not None
        assert "forbidden" in err.lower() or "filecontents" in err.lower()


def test_validate_latex_safety_rejects_absolute_paths():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    absolute_path_attacks = [
        r"\includegraphics{/etc/passwd}",
        r"\includegraphics{/tmp/canary.png}",
        r"\includegraphics{/var/log/syslog}",
        r"\includegraphics{/home/user/secret.png}",
        r"\includegraphics{/custom/img.png}",
        r"\includegraphics{/secret.png}",
        r"\includegraphics{\\server\share\canary.png}",
        r"\includegraphics{//server/share/canary.png}",
        r"\includegraphics{C:/Windows/win.ini}",
        r"\includegraphics{C:\Windows\System32\cmd.exe}",
        r"\pgfplotstableread{/tmp/data.csv}\table",
        r"\pgfplotstableread{/data/canary.csv}\table",
        r"\openin\file=/custom_canary.txt",
    ]
    for attack in absolute_path_attacks:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": attack,
            "change_summary": "Absolute path traversal attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for absolute path: {attack}"
        assert err is not None
        assert "forbidden" in err.lower() or "absolute" in err.lower()


def test_validate_latex_safety_rejects_package_file_inclusion_commands():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    package_inclusion_attacks = [
        r"\lstinputlisting{canary.txt}",
        r"\verbatiminput{canary.txt}",
        r"\tcbinputlisting{canary.txt}",
        r"\import{subdir/}{file.tex}",
        r"\subimport{subdir/}{file.tex}",
        r"\inputfrom{subdir/}{file.tex}",
        r"\subinputfrom{subdir/}{file.tex}",
        r"\catchfile{\data}{canary.txt}{}",
        r"\catchfiledef{\data}{canary.txt}{}",
    ]
    for attack in package_inclusion_attacks:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": attack,
            "change_summary": "Package file inclusion attempt",
            "preserved_invariants": [],
        })
        data, err = service.parse_and_validate_ai_response(raw, r"\draw (0,0);")
        assert data is None, f"Expected rejection for package command: {attack}"
        assert err is not None
        assert "forbidden" in err.lower()


def test_validate_latex_safety_preserves_legitimate_comments_and_names():
    service = LaTeXRepairService(MagicMock(), MagicMock())

    legitimate_samples = [
        (
            r"""\begin{tikzpicture}
    % Note: do not use \input here
    \draw (0,0) -- (2,2);
\end{tikzpicture}""",
            r"\begin{tikzpicture}\draw (0,0) -- (2,2);\end{tikzpicture}"
        ),
        (
            r"""\begin{tikzpicture}
    \node {input signal};
    \node {100\% complete};
\end{tikzpicture}""",
            r"\begin{tikzpicture}\node {signal};\end{tikzpicture}"
        ),
        (
            r"""\begin{matrix}
    a & b \\
    c & d
\end{matrix}""",
            r"\begin{matrix} a \end{matrix}"
        ),
        (
            r"""\draw (0,0) \\input text line""",
            r"\draw (0,0);"
        ),
        (
            r"""\node at (1/2, 3/4) {Value: $x / y$};""",
            r"\node {0};"
        ),
        (
            r"""\draw (0,0) -- (1/2, 1);""",
            r"\draw (0,0) -- (1, 1);"
        ),
    ]

    for candidate, original in legitimate_samples:
        raw = json.dumps({
            "schema_version": 1,
            "repaired_source": candidate,
            "change_summary": "Safe repair with benign keywords",
            "preserved_invariants": ["syntax_valid"],
        })
        data, err = service.parse_and_validate_ai_response(raw, original)
        assert err is None, f"Falsely rejected safe candidate: {candidate}, error: {err}"
        assert data is not None
        assert data["repaired_source"] == candidate

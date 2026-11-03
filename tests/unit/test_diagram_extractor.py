# ============================================================
#  tests/unit/test_diagram_extractor.py
# ============================================================

import pytest

from core.markdown.diagram_extractor import (
    ExtractedDiagram,
    contains_diagram,
    extract_diagrams,
)


def test_extract_simple_tikzpicture():
    raw_tex = r"""
\begin{tikzpicture}
    \draw[thick] (0,0) -- (2,1);
\end{tikzpicture}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "tikzpicture"
    assert d.is_complete is True
    assert d.error is None
    assert r"\draw[thick] (0,0) -- (2,1);" in d.source
    assert contains_diagram(raw_tex) is True


def test_extract_tikzpicture_with_options_and_pgfplots_axis():
    raw_tex = r"""
\begin{tikzpicture}[scale=1.2]
    \begin{axis}[grid=major, xlabel={$x$}, ylabel={$y$}]
        \addplot[blue, domain=-2:2] {x^2};
    \end{axis}
\end{tikzpicture}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "tikzpicture"
    assert d.is_complete is True
    assert "scale=1.2" in d.source
    assert r"\addplot" in d.source


def test_extract_circuitikz():
    raw_tex = r"""
\begin{circuitikz}[american]
    \draw (0,0) to[R=10<\ohm>] (2,0) to[C=1<\micro\farad>] (2,2) -- (0,2) to[battery1] (0,0);
\end{circuitikz}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "circuitikz"
    assert d.is_complete is True


def test_extract_multiple_diagrams():
    raw_tex = r"""
\begin{tikzpicture}
    \node {A};
\end{tikzpicture}

Some intermediate text or spacing.

\begin{circuitikz}
    \draw (0,0) to[R] (1,0);
\end{circuitikz}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 2
    assert diagrams[0].environment == "tikzpicture"
    assert diagrams[0].is_complete is True
    assert diagrams[1].environment == "circuitikz"
    assert diagrams[1].is_complete is True


def test_extract_incomplete_top_level_environment():
    raw_tex = r"""
\begin{tikzpicture}
    \draw (0,0) -- (1,1);
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "tikzpicture"
    assert d.is_complete is False
    assert "Unclosed environment" in (d.error or "")


def test_extract_incomplete_nested_environment():
    raw_tex = r"""
\begin{tikzpicture}
    \begin{axis}
        \addplot {x};
\end{tikzpicture}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "tikzpicture"
    assert d.is_complete is False
    assert "Unclosed nested environment 'axis'" in (d.error or "")


def test_non_diagram_latex_returns_empty():
    raw_tex = r"""
\newpage
\vspace{1cm}
\begin{table}[h]
    \centering
    \begin{tabular}{|c|c|}
        a & b \\
    \end{tabular}
\end{table}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 0
    assert contains_diagram(raw_tex) is False


def test_extract_diagram_preserves_persian_and_math():
    raw_tex = r"""
\begin{tikzpicture}
    \node at (0,0) {ورودی $x(t)$};
    \node at (2,0) {خروجی $y(t)$};
    \draw[->] (0,0) -- (2,0);
\end{tikzpicture}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    assert "ورودی" in diagrams[0].source
    assert "خروجی" in diagrams[0].source


def test_extract_tikzcd():
    raw_tex = r"""
\begin{tikzcd}[row sep=large]
    A \arrow[r, "f"] & B \arrow[d, "g"] \\
    C \arrow[u, "h"] & D
\end{tikzcd}
"""
    diagrams = extract_diagrams(raw_tex)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.environment == "tikzcd"
    assert d.is_complete is True
    assert d.error is None
    assert "row sep=large" in d.source
    assert contains_diagram(raw_tex) is True

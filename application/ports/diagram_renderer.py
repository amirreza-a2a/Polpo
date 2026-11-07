# ============================================================
#  application/ports/diagram_renderer.py
#  Application Port & DTOs for Technical Diagram Rendering
# ============================================================

"""
Application port and domain value objects for technical diagram rendering.

Defines the technology-agnostic interface for transforming raw LaTeX diagram
code (TikZ, PGFPlots, CircuitTikZ) into standalone vector SVG graphics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
from typing import Callable, Mapping, Optional, Sequence, Union


@dataclass(frozen=True)
class DiagramRenderRequest:
    """Request DTO specifying a LaTeX diagram to render into vector SVG.

    Attributes:
        source: Verbatim LaTeX diagram code (e.g. \\begin{tikzpicture}...\\end{tikzpicture}).
        profile: Diagram rendering profile (default: "default").
        preamble: Optional additional LaTeX packages or macros needed for this diagram.
    """

    source: str
    profile: str = "default"
    preamble: str = ""

    def compute_hash(self) -> str:
        """Derives a deterministic SHA-256 cache key including renderer contract version."""
        raw_key = f"1.0|{self.profile}|{self.preamble.strip()}|{self.source.strip()}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DiagramRenderResult:
    """Successful rendering result containing generated vector SVG XML and layout metrics.

    Attributes:
        hash: Deterministic SHA-256 identifier matching the request.
        svg_xml: Complete standalone vector SVG XML text.
        width: Rendered diagram width in points.
        height: Rendered diagram height in points.
        status: Rendering status token (default: 'rendered').
    """

    hash: str
    svg_xml: str
    width: float
    height: float
    status: str = "rendered"


class DiagramRenderError(Exception):
    """Exception and error DTO emitted when rendering a diagram fails.

    Attributes:
        code: Error classification token (e.g., 'syntax', 'timeout', 'unavailable').
        message: Descriptive error message explaining the failure.
        returncode: Optional exit code returned by the compiler process.
        tex_line: Optional line number reported in the compiled .tex document.
        source_line: Optional fragment-relative line number in the source diagram.
        log_excerpt: Bounded and sanitized compiler log excerpt.
        source_sha256: SHA-256 hash of the failing diagram source fragment.
        error_category: Categorized error classifier.
    """

    def __init__(
        self,
        code: str,
        message: str,
        returncode: Optional[int] = None,
        tex_line: Optional[int] = None,
        source_line: Optional[int] = None,
        log_excerpt: str = "",
        source_sha256: str = "",
        error_category: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.returncode = returncode
        self.tex_line = tex_line
        self.source_line = source_line
        self.log_excerpt = log_excerpt
        self.source_sha256 = source_sha256
        self.error_category = error_category or code
        super().__init__(message)

    def __str__(self) -> str:
        loc = f" at line {self.source_line}" if self.source_line else ""
        return f"{self.__class__.__name__}({self.code}{loc}): {self.message}"


class DiagramSyntaxError(DiagramRenderError):
    """Raised when the diagram source contains invalid LaTeX or package syntax."""


class DiagramTimeoutError(DiagramRenderError):
    """Raised when compilation exceeds the configured execution deadline."""


class DiagramRendererUnavailableError(DiagramRenderError):
    """Raised when required host compiler tools (e.g. XeLaTeX/pdfLaTeX) are absent."""


class DiagramCancelledError(DiagramRenderError):
    """Raised when rendering was cancelled cooperatively before or during execution."""


class DiagramOutputLimitError(DiagramRenderError):
    """Raised when the generated SVG or output dimensions exceed configured safety limits."""


class IDiagramRenderer(ABC):
    """Application port interface for asynchronous or synchronous diagram rendering."""

    @abstractmethod
    def render(
        self,
        request: DiagramRenderRequest,
        cancel_fn: Optional[Callable[[], bool]] = None,
    ) -> DiagramRenderResult:
        """Renders a single diagram request into vector SVG.

        Args:
            request: Typed diagram render request.
            cancel_fn: Optional callable returning True if cancelled.

        Returns:
            DiagramRenderResult with generated SVG and metrics.

        Raises:
            DiagramRenderError or its subclasses on compilation failure.
        """

    @abstractmethod
    def render_batch(
        self,
        requests: Sequence[DiagramRenderRequest],
        cancel_fn: Optional[Callable[[], bool]] = None,
    ) -> Mapping[str, Union[DiagramRenderResult, DiagramRenderError]]:
        """Renders multiple diagram requests in isolated batches.

        Returns:
            Mapping from request hash to either DiagramRenderResult or DiagramRenderError.
        """

    @abstractmethod
    def probe_readiness(self) -> bool:
        """Returns True if the underlying compiler runtime is available and operational."""

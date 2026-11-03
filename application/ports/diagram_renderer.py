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
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.code}): {self.message}"


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

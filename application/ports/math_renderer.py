"""Application port and domain value objects for mathematical equation rendering.

Defines the technology-agnostic interface for transforming raw LaTeX/TeX
formulas into native SVG graphic representations.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Mapping, Sequence, Union


@dataclass(frozen=True)
class MathRenderRequest:
    """Request DTO specifying a TeX mathematical formula to render.

    Attributes:
        tex: Raw LaTeX/TeX formula string.
        display: True for standalone display equation ($$...$$), False for inline ($...$).
        em: Font em size in pixels (default 16).
        ex: Font ex size in pixels (default 8).
    """

    tex: str
    display: bool
    em: int = 16
    ex: int = 8

    def compute_hash(self) -> str:
        """Derives a deterministic SHA-256 cache key including renderer version and parameters."""
        raw_key = f"3.2.2|{self.display}|{self.em}|{self.ex}|{self.tex}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MathRenderResult:
    """Successful rendering result containing generated SVG XML and layout metrics.

    Attributes:
        hash: Deterministic SHA-256 identifier matching the request.
        svg_xml: Complete standalone SVG XML text.
        width: Rendered width string (e.g., '2.5ex').
        height: Rendered height string (e.g., '1.2ex').
        vertical_align: Vertical alignment offset string (e.g., '-0.33ex').
    """

    hash: str
    svg_xml: str
    width: str
    height: str
    vertical_align: str


@dataclass(frozen=True)
class MathRenderError(Exception):
    """Exception and error DTO emitted when rendering a formula fails.

    Attributes:
        code: Error code (e.g., JSON-RPC or parser error code).
        message: Descriptive error message explaining the failure.
    """

    code: int
    message: str

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.code}): {self.message}"


@dataclass(frozen=True)
class MathWorkerStartupError(MathRenderError):
    """Raised when the worker subprocess cannot be located, spawned, or initialized."""


@dataclass(frozen=True)
class MathRenderTimeoutError(MathRenderError):
    """Raised when a rendering request exceeds the configured deadline."""


@dataclass(frozen=True)
class MathWorkerCrashedError(MathRenderError):
    """Raised when the worker process exits unexpectedly or terminates during execution."""


@dataclass(frozen=True)
class MathCircuitBreakerOpenError(MathRenderError):
    """Raised when requests are rejected because the supervisor circuit breaker is OPEN."""


@dataclass(frozen=True)
class MathSupervisorShutdownError(MathRenderError):
    """Raised when requests are submitted to a supervisor that has been shut down."""


@dataclass(frozen=True)
class MathBufferLimitExceededError(MathRenderError):
    """Raised when input TeX or rendered output SVG exceeds maximum allowed buffer size."""


@dataclass(frozen=True)
class MathSyntaxError(MathRenderError):
    """Raised when TeX formula syntax is invalid and cannot be parsed or rendered."""


class IMathRenderer(ABC):
    """Port interface for mathematical equation rendering services."""

    @abstractmethod
    def render(self, request: MathRenderRequest) -> MathRenderResult:
        """Render a single TeX math expression to SVG.

        Args:
            request: Formula rendering specifications.

        Returns:
            Rendered SVG and layout metrics.

        Raises:
            MathRenderError: If formula syntax is invalid or worker execution fails.
        """
        raise NotImplementedError

    @abstractmethod
    def render_batch(self, requests: List[MathRenderRequest]) -> List[MathRenderResult]:
        """Render multiple TeX math expressions in a batch.

        Args:
            requests: Sequence of formula rendering specifications.

        Returns:
            List of rendered SVG results corresponding to inputs.

        Raises:
            MathRenderError: If batch processing encounters a fatal error.
        """
        raise NotImplementedError

    @abstractmethod
    def render_batch_isolated(
        self, requests: Sequence[MathRenderRequest]
    ) -> Mapping[str, Union[MathRenderResult, MathRenderError]]:
        """Render multiple TeX math expressions with per-formula failure isolation.

        Unlike render_batch(), an individual formula syntax error, timeout, or crash
        does not abort processing for subsequent requests in the batch.

        Args:
            requests: Sequence of formula rendering specifications.

        Returns:
            Mapping of formula SHA-256 hash to either MathRenderResult (on success)
            or MathRenderError (on failure).

        Raises:
            MathSupervisorShutdownError: If the supervisor is shut down during processing.
        """
        raise NotImplementedError


__all__ = [
    "IMathRenderer",
    "MathBufferLimitExceededError",
    "MathCircuitBreakerOpenError",
    "MathRenderError",
    "MathRenderRequest",
    "MathRenderResult",
    "MathRenderTimeoutError",
    "MathSupervisorShutdownError",
    "MathSyntaxError",
    "MathWorkerCrashedError",
    "MathWorkerStartupError",
]

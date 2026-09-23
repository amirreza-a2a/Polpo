"""Presentation image provider serving rendered MathJax SVGs to Qt Quick (TICK-010).

Intercepts 'image://math/{hash}', looks up the pre-rendered SVG in MathSvgCache,
and rasterizes it using QSvgRenderer into a sharp QImage scaled by devicePixelRatio.
Never invokes MathJax synchronously or blocks the GUI thread.
"""

from __future__ import annotations

import logging
from typing import Optional

from infrastructure.math.lru_cache import MathSvgCache
from interfaces.desktop.qt_compat import (
    QByteArray,
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QQuickImageProvider,
    QSize,
    QSvgRenderer,
)

logger = logging.getLogger(__name__)


class MathImageProvider(QQuickImageProvider):
    """Read-only, cache-backed QQuickImageProvider for MathJax formulas."""

    def __init__(
        self,
        cache: MathSvgCache,
        device_pixel_ratio: Optional[float] = None,
    ) -> None:
        """Initialize the image provider.

        Args:
            cache: Thread-safe in-memory cache holding rendered MathJax SVGs.
            device_pixel_ratio: Optional explicit DPI scaling factor. If None, queries primary screen.
        """
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = cache
        self._device_pixel_ratio = device_pixel_ratio

    @property
    def cache(self) -> MathSvgCache:
        """Return the underlying MathSvgCache instance."""
        return self._cache

    def get_device_pixel_ratio(self) -> float:
        """Return the explicit devicePixelRatio or query active screen."""
        if self._device_pixel_ratio is not None and self._device_pixel_ratio > 0:
            return self._device_pixel_ratio
        app = QGuiApplication.instance()
        if app is not None:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                dpr = screen.devicePixelRatio()
                if dpr > 0:
                    return dpr
        return 1.0

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        """Handle QML image requests for 'image://math/{id}'.

        Args:
            id: Deterministic formula hash string.
            size: Output size parameter populated by provider.
            requestedSize: Requested dimensions from QML (or <= 0 if unspecified).

        Returns:
            Rendered QImage scaled by devicePixelRatio, or 1x1 transparent image on cache miss.
        """
        cached = self._cache.get(id)
        if cached is None:
            logger.debug(f"MathSvgCache miss for formula hash: {id}")
            transparent = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            transparent.fill(QColor(0, 0, 0, 0))
            if size is not None:
                size.setWidth(1)
                size.setHeight(1)
            return transparent

        renderer = QSvgRenderer(QByteArray(cached.svg_xml.encode("utf-8")))
        if not renderer.isValid():
            logger.warning(f"Invalid SVG in MathSvgCache for formula hash: {id}")
            transparent = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            transparent.fill(QColor(0, 0, 0, 0))
            if size is not None:
                size.setWidth(1)
                size.setHeight(1)
            return transparent

        dpr = self.get_device_pixel_ratio()

        # Parse layout metrics (ex to pixel conversion at base scale 8px/ex)
        w_px = self._parse_dimension(cached.width, fallback=30.0, base_unit=8.0)
        h_px = self._parse_dimension(cached.height, fallback=15.0, base_unit=8.0)

        if requestedSize.width() > 0 and requestedSize.height() > 0:
            target_w = requestedSize.width()
            target_h = requestedSize.height()
        elif requestedSize.width() > 0:
            target_w = requestedSize.width()
            target_h = max(1, round(target_w * (h_px / max(w_px, 1.0))))
        elif requestedSize.height() > 0:
            target_h = requestedSize.height()
            target_w = max(1, round(target_h * (w_px / max(h_px, 1.0))))
        else:
            target_w = max(1, round(w_px * dpr))
            target_h = max(1, round(h_px * dpr))

        image = QImage(target_w, target_h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))

        painter = QPainter(image)
        renderer.render(painter)
        painter.end()

        image.setDevicePixelRatio(dpr)

        if size is not None:
            size.setWidth(target_w)
            size.setHeight(target_h)

        return image

    @staticmethod
    def _parse_dimension(dim_str: str, fallback: float, base_unit: float) -> float:
        """Parse dimension strings (e.g. '8.699ex', '20px') into logical pixel numbers."""
        if not dim_str:
            return fallback
        s = dim_str.strip().lower()
        if s.endswith("ex"):
            try:
                return float(s[:-2]) * base_unit
            except ValueError:
                return fallback
        if s.endswith("em"):
            try:
                return float(s[:-2]) * (base_unit * 2.0)
            except ValueError:
                return fallback
        if s.endswith("px"):
            try:
                return float(s[:-2])
            except ValueError:
                return fallback
        try:
            return float(s)
        except ValueError:
            return fallback

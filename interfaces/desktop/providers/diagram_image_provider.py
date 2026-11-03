# ============================================================
#  interfaces/desktop/providers/diagram_image_provider.py
#  QQuickImageProvider for Rendered Vector LaTeX Diagrams
# ============================================================

from __future__ import annotations

import logging
from typing import Optional

from infrastructure.diagram.cache import DiagramSvgCache
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


def parse_diagram_image_url(url_id: str) -> tuple[str, str]:
    """Parses 'image://diagram/{id}' parameter into (theme_name, diagram_hash).

    Supports:
      - 2-component URL: '{theme}/{hash}' -> (theme, hash)
      - 1-component URL: '{hash}' -> ('dark', hash)
    """
    if not url_id:
        return "dark", ""
    parts = [p.strip() for p in url_id.strip("/").split("/") if p.strip()]
    if len(parts) >= 2:
        return parts[0].lower(), parts[-1]
    if len(parts) == 1:
        return "dark", parts[0]
    return "dark", ""


class DiagramImageProvider(QQuickImageProvider):
    """Read-only, cache-backed QQuickImageProvider serving rendered LaTeX diagram SVGs.

    Intercepts 'image://diagram/{theme}/{hash}' or 'image://diagram/{hash}',
    retrieves the pre-rendered vector SVG from DiagramSvgCache, and rasterizes
    it via QSvgRenderer into a crisp QImage scaled by devicePixelRatio.
    Guarantees non-blocking GUI execution by operating strictly on cached assets.
    """

    def __init__(
        self,
        cache: DiagramSvgCache,
        device_pixel_ratio: Optional[float] = None,
    ) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = cache
        self._device_pixel_ratio = device_pixel_ratio

    @property
    def cache(self) -> DiagramSvgCache:
        """Returns the underlying DiagramSvgCache instance."""
        return self._cache

    def get_device_pixel_ratio(self) -> float:
        """Returns the configured devicePixelRatio or queries the primary screen."""
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
        """Handles QML image requests for 'image://diagram/{id}'.

        Args:
            id: Deterministic URL parameter ('{theme}/{hash}' or '{hash}').
            size: Output size populated with actual image dimensions.
            requestedSize: Requested dimensions from QML (or <= 0 if unspecified).

        Returns:
            Rendered QImage scaled by devicePixelRatio, or 1x1 transparent image on miss.
        """
        _, diagram_hash = parse_diagram_image_url(id)
        if not diagram_hash:
            return self._make_transparent_image(size)

        cached = self._cache.get(diagram_hash)
        if cached is None:
            logger.debug("DiagramSvgCache miss for hash: %s (id: %s)", diagram_hash, id)
            return self._make_transparent_image(size)

        try:
            renderer = QSvgRenderer(QByteArray(cached.svg_xml.encode("utf-8")))
            if not renderer.isValid():
                logger.warning("Invalid SVG XML in cache for diagram hash: %s", diagram_hash)
                return self._make_transparent_image(size)

            dpr = self.get_device_pixel_ratio()
            orig_size = renderer.defaultSize()

            base_w = orig_size.width() if orig_size.isValid() and orig_size.width() > 0 else cached.width
            base_h = orig_size.height() if orig_size.isValid() and orig_size.height() > 0 else cached.height

            if requestedSize is not None and requestedSize.width() > 0:
                target_w = requestedSize.width()
            else:
                target_w = max(1, int(round(base_w * dpr)))

            if requestedSize is not None and requestedSize.height() > 0:
                target_h = requestedSize.height()
            else:
                target_h = max(1, int(round(base_h * dpr)))

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
        except Exception as e:
            logger.error("Failed to render diagram SVG for hash %s: %s", diagram_hash, e)
            return self._make_transparent_image(size)

    def _make_transparent_image(self, size: Optional[QSize]) -> QImage:
        transparent = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        transparent.fill(QColor(0, 0, 0, 0))
        if size is not None:
            size.setWidth(1)
            size.setHeight(1)
        return transparent

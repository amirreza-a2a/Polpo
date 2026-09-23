"""Unit tests for presentation MathImageProvider (TICK-010).

Verifies QQuickImageProvider behavior for image://math/{hash}:
- Cache miss returns 1x1 transparent image gracefully without error.
- Cache hit rasterizes SVG into valid QImage via QSvgRenderer.
- Device pixel ratio scaling produces sharp high-DPI output.
- Custom requested dimensions are respected.
- Invalid SVG XML produces safe fallback without crashing.
"""

from __future__ import annotations

import os

# Ensure Qt runs in offscreen headless mode during tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage

from application.ports.math_renderer import MathRenderResult
from infrastructure.math.lru_cache import MathSvgCache
from interfaces.desktop.providers.math_image_provider import MathImageProvider

_VALID_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10ex" height="4ex" viewBox="0 0 100 40">'
    '<rect width="100" height="40" fill="blue"/>'
    "</svg>"
)


@pytest.fixture(scope="module")
def qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture
def cache() -> MathSvgCache:
    return MathSvgCache(capacity=20)


@pytest.fixture
def provider(cache: MathSvgCache) -> MathImageProvider:
    return MathImageProvider(cache=cache, device_pixel_ratio=1.0)


def test_cache_miss_returns_1x1_transparent_image(qapp: QGuiApplication, provider: MathImageProvider):
    """When formula hash is not in cache, provider returns 1x1 transparent QImage."""
    out_size = QSize()
    image = provider.requestImage("non_existent_hash", out_size, QSize(-1, -1))

    assert isinstance(image, QImage)
    assert not image.isNull()
    assert image.width() == 1
    assert image.height() == 1

    # Check alpha channel is transparent (0)
    pixel_color = QColor.fromRgba(image.pixel(0, 0))
    assert pixel_color.alpha() == 0


def test_cache_hit_returns_rendered_qimage(qapp: QGuiApplication, provider: MathImageProvider, cache: MathSvgCache):
    """When formula hash is present, provider renders SVG into a valid QImage."""
    formula_hash = "abc123hash"
    cache.put(
        formula_hash,
        MathRenderResult(
            hash=formula_hash,
            svg_xml=_VALID_SVG,
            width="10ex",
            height="4ex",
            vertical_align="0ex",
        ),
    )

    out_size = QSize()
    image = provider.requestImage(formula_hash, out_size, QSize(-1, -1))

    assert isinstance(image, QImage)
    assert not image.isNull()
    # At 8px/ex base unit and DPR 1.0: 10ex = 80px, 4ex = 32px
    assert image.width() == 80
    assert image.height() == 32
    assert out_size.width() == 80
    assert out_size.height() == 32


def test_device_pixel_ratio_scaling(qapp: QGuiApplication, cache: MathSvgCache):
    """Higher devicePixelRatio scales the pixel dimensions and sets image devicePixelRatio."""
    provider = MathImageProvider(cache=cache, device_pixel_ratio=2.0)
    formula_hash = "dpr_test_hash"
    cache.put(
        formula_hash,
        MathRenderResult(
            hash=formula_hash,
            svg_xml=_VALID_SVG,
            width="5ex",
            height="2ex",
            vertical_align="0ex",
        ),
    )

    out_size = QSize()
    image = provider.requestImage(formula_hash, out_size, QSize(-1, -1))

    # At 8px/ex: 5ex = 40px base. At DPR 2.0: 80px pixel width
    assert image.width() == 80
    assert image.height() == 32
    assert image.devicePixelRatio() == 2.0


def test_explicit_requested_size_respected(qapp: QGuiApplication, provider: MathImageProvider, cache: MathSvgCache):
    """When QML provides explicit requested dimensions, provider scales to that size."""
    formula_hash = "req_size_hash"
    cache.put(
        formula_hash,
        MathRenderResult(
            hash=formula_hash,
            svg_xml=_VALID_SVG,
            width="10ex",
            height="4ex",
            vertical_align="0ex",
        ),
    )

    out_size = QSize()
    image = provider.requestImage(formula_hash, out_size, QSize(200, 100))

    assert image.width() == 200
    assert image.height() == 100
    assert out_size.width() == 200
    assert out_size.height() == 100


def test_invalid_svg_returns_1x1_transparent_image(qapp: QGuiApplication, provider: MathImageProvider, cache: MathSvgCache):
    """Corrupted SVG XML in cache returns transparent image instead of crashing."""
    corrupted_hash = "bad_svg_hash"
    cache.put(
        corrupted_hash,
        MathRenderResult(
            hash=corrupted_hash,
            svg_xml="<not-a-valid-svg>>>",
            width="10ex",
            height="4ex",
            vertical_align="0ex",
        ),
    )

    out_size = QSize()
    image = provider.requestImage(corrupted_hash, out_size, QSize(-1, -1))

    assert isinstance(image, QImage)
    assert image.width() == 1
    assert image.height() == 1
    pixel_color = QColor.fromRgba(image.pixel(0, 0))
    assert pixel_color.alpha() == 0

# ============================================================
#  tests/unit/test_math_image_provider_theming.py
#  Unit tests for theme-aware math rendering and URL parsing
# ============================================================

import sys
import pytest

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap

from application.ports.math_renderer import MathRenderResult
from infrastructure.math.lru_cache import MathSvgCache
from interfaces.desktop.providers.math_image_provider import (
    MathImageProvider,
    parse_math_image_url,
    inject_svg_color,
    MATH_THEME_COLORS,
    DEFAULT_MATH_THEME,
)

_SAMPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10ex" height="4ex" viewBox="0 0 100 40">'
    '<circle cx="50" cy="20" r="15" fill="currentColor"/>'
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
    return MathSvgCache(capacity=10)


@pytest.fixture
def provider(cache: MathSvgCache) -> MathImageProvider:
    return MathImageProvider(cache=cache, device_pixel_ratio=1.0)


class TestMathImageProviderTheming:
    def test_parse_math_image_url_3_component(self):
        theme, formula_hash = parse_math_image_url("dark/abc123hash")
        assert theme == "dark"
        assert formula_hash == "abc123hash"

        theme, formula_hash = parse_math_image_url("light/def456hash")
        assert theme == "light"
        assert formula_hash == "def456hash"

        # Case insensitivity for theme
        theme, formula_hash = parse_math_image_url("LIGHT/def456hash")
        assert theme == "light"
        assert formula_hash == "def456hash"

    def test_parse_math_image_url_legacy_2_component_fallback_to_dark(self):
        theme, formula_hash = parse_math_image_url("legacy_hash_only")
        assert theme == "dark"
        assert formula_hash == "legacy_hash_only"

    def test_parse_math_image_url_invalid_or_empty(self):
        theme, formula_hash = parse_math_image_url("")
        assert theme == "dark"
        assert formula_hash == ""

        # Unknown theme defaults to dark
        theme, formula_hash = parse_math_image_url("unknown_theme/hash123")
        assert theme == "dark"
        assert formula_hash == "hash123"

    def test_inject_svg_color_preserves_original_string(self):
        original = _SAMPLE_SVG
        injected = inject_svg_color(original, "#1f2328")
        assert 'color="#1f2328"' in injected
        assert original != injected
        assert 'color=' not in original

    def test_inject_svg_color_replaces_existing_color_without_duplicates(self):
        svg_with_color = (
            '<svg xmlns="http://www.w3.org/2000/svg" color="#000000" width="10ex" height="4ex">'
            '<circle cx="50" cy="20" r="15" fill="currentColor"/>'
            '</svg>'
        )
        injected = inject_svg_color(svg_with_color, "#e6edf3")
        assert 'color="#e6edf3"' in injected
        assert 'color="#000000"' not in injected
        # Must have exactly one color attribute
        assert injected.count('color=') == 1

        # Must produce valid SVG renderer instance
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtCore import QByteArray
        renderer = QSvgRenderer(QByteArray(injected.encode("utf-8")))
        assert renderer.isValid()

    def test_dark_and_light_render_distinct_glyph_colors(self, qapp, provider, cache):
        formula_hash = "color_test_hash"
        cache.put(
            formula_hash,
            MathRenderResult(
                hash=formula_hash,
                svg_xml=_SAMPLE_SVG,
                width="10ex",
                height="4ex",
                vertical_align="0ex",
            ),
        )

        out_size = QSize()
        dark_image = provider.requestImage(f"dark/{formula_hash}", out_size, QSize(-1, -1))
        light_image = provider.requestImage(f"light/{formula_hash}", out_size, QSize(-1, -1))

        assert isinstance(dark_image, QImage)
        assert isinstance(light_image, QImage)

        # Sample pixel near the center (glyph stroke)
        center_x = round(dark_image.width() / 2)
        center_y = round(dark_image.height() / 2)

        dark_color = dark_image.pixelColor(center_x, center_y)
        light_color = light_image.pixelColor(center_x, center_y)

        # Both images must be valid and non-transparent at glyph center
        assert dark_color.alpha() > 200
        assert light_color.alpha() > 200

        # Dark theme renders bright glyph (#e6edf3 -> near white); light theme renders dark glyph (#1f2328 -> near black)
        assert dark_color.name().lower() != light_color.name().lower()
        assert dark_color.lightness() > light_color.lightness()

        # Cache remains untouched and holds original SVG
        cached_entry = cache.get(formula_hash)
        assert cached_entry is not None
        assert cached_entry.svg_xml == _SAMPLE_SVG

    def test_request_pixmap_returns_valid_pixmap(self, qapp, provider, cache):
        formula_hash = "pixmap_hash"
        cache.put(
            formula_hash,
            MathRenderResult(
                hash=formula_hash,
                svg_xml=_SAMPLE_SVG,
                width="10ex",
                height="4ex",
                vertical_align="0ex",
            ),
        )

        out_size = QSize()
        pixmap = provider.requestPixmap(f"light/{formula_hash}", out_size, QSize(-1, -1))
        assert isinstance(pixmap, QPixmap)
        assert not pixmap.isNull()
        assert pixmap.width() > 0

    def test_cache_miss_returns_1x1_transparent_for_both_themes(self, qapp, provider):
        for path in ("dark/missing", "light/missing", "missing_legacy"):
            out_size = QSize()
            img = provider.requestImage(path, out_size, QSize(-1, -1))
            assert img.width() == 1
            assert img.height() == 1
            assert img.pixelColor(0, 0).alpha() == 0

    def test_zero_gui_or_controller_imports_in_provider_module(self):
        import interfaces.desktop.providers.math_image_provider as mod
        # Must not import controllers or GUI models
        assert not hasattr(mod, "ThemeController")
        assert not hasattr(mod, "SettingsController")
        assert not hasattr(mod, "DesktopAppContainer")

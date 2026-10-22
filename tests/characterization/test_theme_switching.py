# ============================================================
#  tests/characterization/test_theme_switching.py
#  Characterization tests for Desktop Theme Lifecycle & Switching
# ============================================================

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from infrastructure.math.lru_cache import MathSvgCache
from application.ports.math_renderer import MathRenderResult
from interfaces.desktop.controllers.theme_controller import ThemeController
from interfaces.desktop.controllers.markdown_editor_controller import MarkdownEditorController
from interfaces.desktop.providers.math_image_provider import MathImageProvider
from interfaces.desktop.qt_compat import (
    QCoreApplication,
    QGuiApplication,
    QSize,
    QTextDocument,
    Qt,
)
from interfaces.desktop.theme import DARK_PALETTE, LIGHT_PALETTE


@pytest.fixture(scope="session")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


class TestThemeSwitchingCharacterization:
    """
    Characterization tests verifying theme lifecycle, platform scheme handling,
    MathJax SVG cache neutrality, and presentation state preservation across theme transitions.
    """

    def test_headless_initialization_defaults_to_dark_under_unknown_scheme(self, qapp):
        """
        On headless Linux, minimal WMs, or when the XDG Desktop Portal is absent,
        Qt.ColorScheme.Unknown must deterministically resolve to 'dark'.
        """
        tc = ThemeController("system")
        # Explicitly simulate Unknown scheme from desktop portal
        tc._on_color_scheme_changed(Qt.ColorScheme.Unknown)
        assert tc.resolvedTheme == "dark"
        assert tc.current_palette is DARK_PALETTE
        assert tc.background == DARK_PALETTE.background
        assert tc.textPrimary == DARK_PALETTE.textPrimary

    def test_live_os_color_scheme_change_propagates_on_system_preference(self, qapp):
        """
        When preference is 'system', genuine OS scheme changes must propagate to
        all presentation properties and emit appropriate notification signals.
        """
        tc = ThemeController("system")
        resolved_signals = []
        theme_signals = []

        tc.resolvedThemeChanged.connect(resolved_signals.append)
        tc.themeChanged.connect(lambda: theme_signals.append(True))

        # 1. OS changes to Light
        tc._on_color_scheme_changed(Qt.ColorScheme.Light)
        assert tc.resolvedTheme == "light"
        assert tc.background == LIGHT_PALETTE.background
        assert tc.textPrimary == LIGHT_PALETTE.textPrimary
        assert tc.mathForeground == LIGHT_PALETTE.mathForeground
        assert resolved_signals == ["light"]
        assert len(theme_signals) == 1

        # 2. OS changes to Dark
        tc._on_color_scheme_changed(Qt.ColorScheme.Dark)
        assert tc.resolvedTheme == "dark"
        assert tc.background == DARK_PALETTE.background
        assert tc.textPrimary == DARK_PALETTE.textPrimary
        assert tc.mathForeground == DARK_PALETTE.mathForeground
        assert resolved_signals == ["light", "dark"]
        assert len(theme_signals) == 2

    def test_explicit_user_preference_immune_to_os_appearance_events(self, qapp):
        """
        When the user explicitly chooses 'dark' or 'light', external OS scheme
        changes must NOT override the user's active theme.
        """
        # Explicit dark preference
        tc_dark = ThemeController("dark")
        signals_dark = []
        tc_dark.resolvedThemeChanged.connect(signals_dark.append)

        tc_dark._on_color_scheme_changed(Qt.ColorScheme.Light)
        assert tc_dark.resolvedTheme == "dark"
        assert len(signals_dark) == 0

        # Explicit light preference
        tc_light = ThemeController("light")
        signals_light = []
        tc_light.resolvedThemeChanged.connect(signals_light.append)

        tc_light._on_color_scheme_changed(Qt.ColorScheme.Dark)
        assert tc_light.resolvedTheme == "light"
        assert len(signals_light) == 0

    def test_math_image_provider_url_isolation_and_cache_neutrality(self, qapp):
        """
        Verifies:
        1. MathJax formula hash and cached raw SVG stay strictly stable during theme switches.
        2. Presentation URLs 'image://math/{theme}/{hash}' isolate Qt Quick's image cache.
        3. Provider renders glyphs in the active theme's foreground color.
        4. Switching dark -> light -> dark returns to the original foreground deterministically.
        """
        cache = MathSvgCache(capacity=100)
        formula_hash = "characterization_test_formula_hash_01"
        raw_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="10ex" height="4ex" viewBox="0 0 100 40">'
            '<circle cx="50" cy="20" r="15" fill="currentColor"/>'
            '</svg>'
        )
        cache.put(
            formula_hash,
            MathRenderResult(
                hash=formula_hash,
                svg_xml=raw_svg,
                width="10ex",
                height="4ex",
                vertical_align="0ex",
            ),
        )

        provider = MathImageProvider(cache=cache, device_pixel_ratio=1.0)
        out_size = QSize()

        # Step 1: Render dark
        img_dark = provider.requestImage(f"dark/{formula_hash}", out_size, QSize(-1, -1))
        assert not img_dark.isNull()
        assert img_dark.width() > 1
        assert img_dark.height() > 1

        # Verify underlying cache content has NOT changed
        cached_entry = cache.get(formula_hash)
        assert cached_entry is not None
        assert cached_entry.svg_xml == raw_svg

        # Step 2: Render light
        img_light = provider.requestImage(f"light/{formula_hash}", out_size, QSize(-1, -1))
        assert not img_light.isNull()

        # Cache content must still remain identical (cache neutrality)
        assert cache.get(formula_hash).svg_xml == raw_svg

        # Step 3: Switch back to dark
        img_dark_again = provider.requestImage(f"dark/{formula_hash}", out_size, QSize(-1, -1))
        assert not img_dark_again.isNull()

        # Verify dark and light produced distinct renderings at glyph center
        cx = round(img_dark.width() / 2)
        cy = round(img_dark.height() / 2)
        color_dark = img_dark.pixelColor(cx, cy)
        color_light = img_light.pixelColor(cx, cy)
        color_dark_again = img_dark_again.pixelColor(cx, cy)

        assert color_dark.alpha() > 200
        assert color_light.alpha() > 200
        assert color_dark.lightness() > color_light.lightness()
        assert color_dark.name().lower() != color_light.name().lower()
        # Repeated dark rendering is deterministic
        assert color_dark.name().lower() == color_dark_again.name().lower()

    def test_editor_state_preserved_across_theme_switches(self, qapp):
        """
        Verifies that theme switching reconfigures syntax highlighting formats
        without altering the Markdown text buffer, cursor position, or dirty state.
        """
        mock_editor_service = MagicMock()
        mock_merge_service = MagicMock()
        editor_ctrl = MarkdownEditorController(
            editor_service=mock_editor_service,
            merge_service=mock_merge_service,
        )

        doc = QTextDocument()
        initial_text = "# Heading 1\n\nSome paragraph text.\n\n`inline code`\n"
        doc.setPlainText(initial_text)

        mock_quick_doc = MagicMock()
        mock_quick_doc.textDocument.return_value = doc
        editor_ctrl.attachTextDocument(mock_quick_doc)

        editor_ctrl.updateCursorPosition(14)
        assert editor_ctrl.cursorPosition == 14

        # Initial theme is dark
        assert editor_ctrl._syntax_theme == "dark"
        assert editor_ctrl._highlighter.current_theme == "dark"

        # Switch to light theme
        editor_ctrl.set_syntax_theme("light")
        assert editor_ctrl._syntax_theme == "light"
        assert editor_ctrl._highlighter.current_theme == "light"

        # Verify buffer content and cursor position are strictly preserved
        assert doc.toPlainText() == initial_text
        assert editor_ctrl.cursorPosition == 14

        # Switch back to dark theme
        editor_ctrl.set_syntax_theme("dark")
        assert editor_ctrl._syntax_theme == "dark"
        assert editor_ctrl._highlighter.current_theme == "dark"
        assert doc.toPlainText() == initial_text
        assert editor_ctrl.cursorPosition == 14

    def test_qml_view_live_theme_bindings_update_without_restart(self, qapp):
        """
        Verifies that QML bindings to ThemeController properties update live
        when themePreference changes from dark to light to dark.
        """
        from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

        tc = ThemeController("dark")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("theme", tc)

        qml_src = b"""
        import QtQuick 2.15
        Item {
            id: root
            property color currentBg: theme.background
            property color currentText: theme.textPrimary
            property color currentMath: theme.mathForeground
        }
        """
        component = QQmlComponent(engine)
        component.setData(qml_src, "")
        assert not component.isError(), f"QML error: {component.errors()}"

        item = component.create()
        assert item is not None

        # Verify initial dark properties
        assert item.property("currentBg").name().lower() == DARK_PALETTE.background.lower()
        assert item.property("currentText").name().lower() == DARK_PALETTE.textPrimary.lower()
        assert item.property("currentMath").name().lower() == DARK_PALETTE.mathForeground.lower()

        # Switch to light
        tc.set_theme_preference("light")
        QCoreApplication.processEvents()

        assert item.property("currentBg").name().lower() == LIGHT_PALETTE.background.lower()
        assert item.property("currentText").name().lower() == LIGHT_PALETTE.textPrimary.lower()
        assert item.property("currentMath").name().lower() == LIGHT_PALETTE.mathForeground.lower()

        # Switch back to dark
        tc.set_theme_preference("dark")
        QCoreApplication.processEvents()

        assert item.property("currentBg").name().lower() == DARK_PALETTE.background.lower()
        assert item.property("currentText").name().lower() == DARK_PALETTE.textPrimary.lower()
        assert item.property("currentMath").name().lower() == DARK_PALETTE.mathForeground.lower()

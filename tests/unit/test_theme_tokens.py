# ============================================================
#  tests/unit/test_theme_tokens.py
#  Unit tests for desktop semantic design tokens and Qt palette
# ============================================================

import re
from dataclasses import fields, FrozenInstanceError
import pytest

from interfaces.desktop.qt_compat import QGuiApplication, QPalette, QColor
from interfaces.desktop.theme import (
    ThemePalette,
    DARK_PALETTE,
    LIGHT_PALETTE,
    THEME_PALETTES,
    create_qt_palette,
)


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


class TestThemeTokens:
    def test_palette_has_exactly_41_tokens(self):
        palette_fields = fields(ThemePalette)
        assert len(palette_fields) == 41, f"Expected exactly 41 tokens, got {len(palette_fields)}"

    def test_palette_is_immutable(self):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            DARK_PALETTE.background = "#ff0000"  # type: ignore

    def test_dark_palette_tokens_are_valid_hex(self):
        hex_pattern = re.compile(r"^#[0-9a-fA-F]{6}$")
        for field in fields(ThemePalette):
            val = getattr(DARK_PALETTE, field.name)
            assert isinstance(val, str), f"Token {field.name} must be str"
            assert hex_pattern.match(val), f"Token {field.name}='{val}' is not a valid 6-char hex color"

    def test_light_palette_tokens_are_valid_hex(self):
        hex_pattern = re.compile(r"^#[0-9a-fA-F]{6}$")
        for field in fields(ThemePalette):
            val = getattr(LIGHT_PALETTE, field.name)
            assert isinstance(val, str), f"Token {field.name} must be str"
            assert hex_pattern.match(val), f"Token {field.name}='{val}' is not a valid 6-char hex color"

    def test_palettes_differ_meaningfully(self):
        assert DARK_PALETTE.background != LIGHT_PALETTE.background
        assert DARK_PALETTE.surface != LIGHT_PALETTE.surface
        assert DARK_PALETTE.textPrimary != LIGHT_PALETTE.textPrimary
        assert DARK_PALETTE.mathForeground != LIGHT_PALETTE.mathForeground

    def test_theme_palettes_mapping(self):
        assert set(THEME_PALETTES.keys()) == {"dark", "light"}
        assert THEME_PALETTES["dark"] is DARK_PALETTE
        assert THEME_PALETTES["light"] is LIGHT_PALETTE

    def test_create_qt_palette_roles(self, qapp):
        dark_qp = create_qt_palette(DARK_PALETTE)
        assert isinstance(dark_qp, QPalette)

        # Window & text
        assert dark_qp.color(QPalette.ColorRole.Window).name().lower() == DARK_PALETTE.background.lower()
        assert dark_qp.color(QPalette.ColorRole.WindowText).name().lower() == DARK_PALETTE.textPrimary.lower()

        # Base & alternate base
        assert dark_qp.color(QPalette.ColorRole.Base).name().lower() == DARK_PALETTE.surfaceSunken.lower()
        assert dark_qp.color(QPalette.ColorRole.AlternateBase).name().lower() == DARK_PALETTE.surface.lower()

        # Highlight & text
        assert dark_qp.color(QPalette.ColorRole.Highlight).name().lower() == DARK_PALETTE.selectionBackground.lower()
        assert dark_qp.color(QPalette.ColorRole.HighlightedText).name().lower() == DARK_PALETTE.selectionText.lower()

        # Buttons
        assert dark_qp.color(QPalette.ColorRole.Button).name().lower() == DARK_PALETTE.surface.lower()
        assert dark_qp.color(QPalette.ColorRole.ButtonText).name().lower() == DARK_PALETTE.textPrimary.lower()

        # Disabled roles
        assert dark_qp.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name().lower() == DARK_PALETTE.textMuted.lower()
        assert dark_qp.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name().lower() == DARK_PALETTE.textMuted.lower()

        # Light palette mapping
        light_qp = create_qt_palette(LIGHT_PALETTE)
        assert light_qp.color(QPalette.ColorRole.Window).name().lower() == LIGHT_PALETTE.background.lower()
        assert light_qp.color(QPalette.ColorRole.WindowText).name().lower() == LIGHT_PALETTE.textPrimary.lower()
        assert light_qp.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name().lower() == LIGHT_PALETTE.textMuted.lower()

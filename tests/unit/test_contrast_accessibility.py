# ============================================================
#  tests/unit/test_contrast_accessibility.py
#  WCAG 2.2 Accessibility and Contrast Verification for Polpo Theming
# ============================================================

import pytest

from interfaces.desktop.syntax.markdown_syntax_highlighter import (
    DARK_SYNTAX_PALETTE,
    LIGHT_SYNTAX_PALETTE,
)
from interfaces.desktop.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    contrast_ratio,
    relative_luminance,
)


class TestContrastHelper:
    """Verifies the mathematical correctness of WCAG 2.2 relative luminance and contrast calculations."""

    def test_pure_white_and_pure_black_luminance(self):
        assert relative_luminance("#ffffff") == pytest.approx(1.0, abs=1e-5)
        assert relative_luminance("#fff") == pytest.approx(1.0, abs=1e-5)
        assert relative_luminance("#000000") == pytest.approx(0.0, abs=1e-5)
        assert relative_luminance("#000") == pytest.approx(0.0, abs=1e-5)

    def test_maximum_and_minimum_contrast_ratios(self):
        # Pure black against pure white gives the theoretical maximum: 21:1
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
        assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
        # Identical colors give the theoretical minimum: 1:1
        assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
        assert contrast_ratio("#123456", "#123456") == pytest.approx(1.0, abs=0.01)

    def test_wcag_standard_benchmark_ratios(self):
        # #767676 on #ffffff is standard WCAG AA 4.54:1 benchmark
        cr_76 = contrast_ratio("#767676", "#ffffff")
        assert cr_76 >= 4.5
        assert cr_76 == pytest.approx(4.54, abs=0.05)

        # #595959 on #ffffff is standard WCAG AAA 7.0:1 benchmark
        cr_59 = contrast_ratio("#595959", "#ffffff")
        assert cr_59 >= 7.0
        assert cr_59 == pytest.approx(7.00, abs=0.05)

    def test_invalid_hex_handling(self):
        with pytest.raises(TypeError):
            relative_luminance(None)  # type: ignore

        for bad in ("", "#12", "#1234", "#1234567", "invalid", "#gggggg"):
            with pytest.raises(ValueError):
                relative_luminance(bad)


class TestThemePaletteContrast:
    """
    Audits actual rendered foreground/background token pairs from DARK_PALETTE and LIGHT_PALETTE
    against normative WCAG 2.2 criteria.
    """

    def test_dark_palette_primary_text_meets_wcag_aa(self):
        # Normal text requires >= 4.5:1 under WCAG 2.2 SC 1.4.3
        for surf_name in ("background", "surface", "surfaceElevated"):
            surf_hex = getattr(DARK_PALETTE, surf_name)
            cr = contrast_ratio(DARK_PALETTE.textPrimary, surf_hex)
            assert cr >= 4.5, f"textPrimary on {surf_name} failed WCAG AA ({cr:.2f}:1 < 4.5:1)"

    def test_light_palette_primary_text_meets_wcag_aa(self):
        for surf_name in ("background", "surface", "surfaceElevated"):
            surf_hex = getattr(LIGHT_PALETTE, surf_name)
            cr = contrast_ratio(LIGHT_PALETTE.textPrimary, surf_hex)
            assert cr >= 4.5, f"textPrimary on {surf_name} failed WCAG AA ({cr:.2f}:1 < 4.5:1)"

    def test_dark_palette_secondary_text_meets_wcag_aa(self):
        cr_bg = contrast_ratio(DARK_PALETTE.textSecondary, DARK_PALETTE.background)
        cr_surf = contrast_ratio(DARK_PALETTE.textSecondary, DARK_PALETTE.surface)
        assert cr_bg >= 4.5, f"Dark textSecondary on background: {cr_bg:.2f}:1 < 4.5:1"
        assert cr_surf >= 4.5, f"Dark textSecondary on surface: {cr_surf:.2f}:1 < 4.5:1"

    def test_light_palette_secondary_text_meets_wcag_aa(self):
        cr_bg = contrast_ratio(LIGHT_PALETTE.textSecondary, LIGHT_PALETTE.background)
        cr_surf = contrast_ratio(LIGHT_PALETTE.textSecondary, LIGHT_PALETTE.surface)
        assert cr_bg >= 4.5, f"Light textSecondary on background: {cr_bg:.2f}:1 < 4.5:1"
        assert cr_surf >= 4.5, f"Light textSecondary on surface: {cr_surf:.2f}:1 < 4.5:1"

    def test_code_and_math_foreground_contrast(self):
        # Code text in both themes
        assert contrast_ratio(DARK_PALETTE.codeText, DARK_PALETTE.codeBackground) >= 4.5
        assert contrast_ratio(LIGHT_PALETTE.codeText, LIGHT_PALETTE.codeBackground) >= 4.5

        # Math formula foreground in both themes
        assert contrast_ratio(DARK_PALETTE.mathForeground, DARK_PALETTE.background) >= 4.5
        assert contrast_ratio(DARK_PALETTE.mathForeground, DARK_PALETTE.surface) >= 4.5
        assert contrast_ratio(LIGHT_PALETTE.mathForeground, LIGHT_PALETTE.background) >= 4.5
        assert contrast_ratio(LIGHT_PALETTE.mathForeground, LIGHT_PALETTE.surface) >= 4.5

    def test_selection_text_contrast(self):
        assert contrast_ratio(DARK_PALETTE.selectionText, DARK_PALETTE.selectionBackground) >= 4.5
        assert contrast_ratio(LIGHT_PALETTE.selectionText, LIGHT_PALETTE.selectionBackground) >= 4.5

    def test_feedback_banner_text_contrast(self):
        """Notification and alert banner text must achieve >= 4.5:1 against banner backgrounds."""
        for name, p in (("Dark", DARK_PALETTE), ("Light", LIGHT_PALETTE)):
            cr_err = contrast_ratio(p.errorText, p.errorBackground)
            assert cr_err >= 4.5, f"{name} error banner contrast: {cr_err:.2f}:1 < 4.5:1"

            cr_warn = contrast_ratio(p.warningText, p.warningBackground)
            assert cr_warn >= 4.5, f"{name} warning banner contrast: {cr_warn:.2f}:1 < 4.5:1"

            cr_succ = contrast_ratio(p.successText, p.successBackground)
            assert cr_succ >= 4.5, f"{name} success banner contrast: {cr_succ:.2f}:1 < 4.5:1"

            cr_info = contrast_ratio(p.infoText, p.infoBackground)
            assert cr_info >= 4.5, f"{name} info banner contrast: {cr_info:.2f}:1 < 4.5:1"

    def test_focus_ring_indicator_meets_sc_1_4_11(self):
        """
        WCAG 2.2 SC 1.4.11 (Non-text Contrast) requires >= 3:1 against adjacent colors
        for visual focus indicators and component boundaries.
        """
        # Dark focus ring on background and surface
        cr_dark_bg = contrast_ratio(DARK_PALETTE.focusRing, DARK_PALETTE.background)
        cr_dark_surf = contrast_ratio(DARK_PALETTE.focusRing, DARK_PALETTE.surface)
        assert cr_dark_bg >= 3.0, f"Dark focus ring on background {cr_dark_bg:.2f}:1 < 3.0:1"
        assert cr_dark_surf >= 3.0, f"Dark focus ring on surface {cr_dark_surf:.2f}:1 < 3.0:1"

        # Light focus ring on background and surface
        cr_light_bg = contrast_ratio(LIGHT_PALETTE.focusRing, LIGHT_PALETTE.background)
        cr_light_surf = contrast_ratio(LIGHT_PALETTE.focusRing, LIGHT_PALETTE.surface)
        assert cr_light_bg >= 3.0, f"Light focus ring on background {cr_light_bg:.2f}:1 < 3.0:1"
        assert cr_light_surf >= 3.0, f"Light focus ring on surface {cr_light_surf:.2f}:1 < 3.0:1"

    def test_disabled_controls_normative_exemption_and_distinguishability(self):
        """
        WCAG 2.2 SC 1.4.3 explicitly exempts inactive (disabled) user interface components:
        'Text or images of text that are part of an inactive user interface component... have no contrast requirement.'
        This test documents this formal exemption and proves that disabled text is visually
        differentiated from active text on the button surface.
        """
        for name, p in (("Dark", DARK_PALETTE), ("Light", LIGHT_PALETTE)):
            active_cr = contrast_ratio(p.textPrimary, p.surface)
            disabled_cr = contrast_ratio(p.textMuted, p.surface)
            # Active text must pass AA (>= 4.5:1)
            assert active_cr >= 4.5
            # Disabled text must be meaningfully distinct from active text (different luminance/contrast)
            assert active_cr - disabled_cr >= 8.0, (
                f"{name} disabled text differentiation insufficient: active={active_cr:.2f}, disabled={disabled_cr:.2f}"
            )


class TestMarkdownSyntaxHighlightingAccessibility:
    """Verifies that syntax categories in MarkdownSyntaxHighlighter remain legible against editor backgrounds."""

    def test_dark_syntax_palette_readability(self):
        editor_bg = DARK_PALETTE.background  # #0d1117
        palette = DARK_SYNTAX_PALETTE

        # Headings H1 through H6
        for level, h_color in enumerate(palette["headings"], start=1):
            cr = contrast_ratio(h_color, editor_bg)
            assert cr >= 4.5, f"Dark H{level} ({h_color}) on editor bg: {cr:.2f}:1 < 4.5:1"

        # Inline elements
        assert contrast_ratio(palette["code_block_fg"], palette["code_block_bg"]) >= 4.5
        assert contrast_ratio(palette["code_span_fg"], palette["code_span_bg"]) >= 4.5
        assert contrast_ratio(palette["region_fg"], palette["region_bg"]) >= 4.5
        assert contrast_ratio(palette["link_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["bold_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["italic_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["blockquote_fg"], editor_bg) >= 4.5

    def test_light_syntax_palette_readability(self):
        editor_bg = LIGHT_PALETTE.background  # #ffffff
        palette = LIGHT_SYNTAX_PALETTE

        # Headings H1 through H6
        for level, h_color in enumerate(palette["headings"], start=1):
            cr = contrast_ratio(h_color, editor_bg)
            assert cr >= 4.5, f"Light H{level} ({h_color}) on editor bg: {cr:.2f}:1 < 4.5:1"

        # Inline elements
        assert contrast_ratio(palette["code_block_fg"], palette["code_block_bg"]) >= 4.5
        assert contrast_ratio(palette["code_span_fg"], palette["code_span_bg"]) >= 4.5
        assert contrast_ratio(palette["region_fg"], palette["region_bg"]) >= 4.5
        assert contrast_ratio(palette["link_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["bold_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["italic_fg"], editor_bg) >= 4.5
        assert contrast_ratio(palette["blockquote_fg"], editor_bg) >= 4.5

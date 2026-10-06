# ============================================================
#  tests/unit/test_theme_controller.py
#  Unit tests for desktop ThemeController presentation state
# ============================================================

from dataclasses import fields
from unittest.mock import MagicMock
import pytest

from interfaces.desktop.qt_compat import QGuiApplication, Qt
from interfaces.desktop.theme import (
    ThemePalette,
    DARK_PALETTE,
    LIGHT_PALETTE,
)
from interfaces.desktop.controllers.theme_controller import ThemeController


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


class TestThemeController:
    def test_initial_preference_explicit_dark(self, qapp):
        tc = ThemeController("dark")
        assert tc.themePreference == "dark"
        assert tc.resolvedTheme == "dark"
        assert tc.current_palette is DARK_PALETTE

    def test_initial_preference_explicit_light(self, qapp):
        tc = ThemeController("light")
        assert tc.themePreference == "light"
        assert tc.resolvedTheme == "light"
        assert tc.current_palette is LIGHT_PALETTE

    def test_initial_preference_invalid_defaults_to_system(self, qapp):
        tc = ThemeController("invalid_theme_value")
        assert tc.themePreference == "system"
        assert tc.resolvedTheme in ("dark", "light")

    def test_all_41_semantic_tokens_accessible_as_qproperties(self, qapp):
        tc = ThemeController("dark")
        palette_fields = fields(ThemePalette)
        assert len(palette_fields) == 41

        for field in palette_fields:
            val = tc.property(field.name)
            assert val is not None, f"Property '{field.name}' not accessible via Qt property system"
            expected = getattr(DARK_PALETTE, field.name)
            assert val == expected, f"Property '{field.name}' has unexpected value '{val}', expected '{expected}'"

    def test_switching_theme_preference_emits_signals_and_updates_properties(self, qapp):
        tc = ThemeController("dark")
        pref_signals = []
        resolved_signals = []
        theme_signals = []

        tc.themePreferenceChanged.connect(pref_signals.append)
        tc.resolvedThemeChanged.connect(resolved_signals.append)
        tc.themeChanged.connect(lambda: theme_signals.append(True))

        # Switch dark -> light
        tc.set_theme_preference("light")

        assert tc.themePreference == "light"
        assert tc.resolvedTheme == "light"
        assert tc.current_palette is LIGHT_PALETTE
        assert tc.property("background") == LIGHT_PALETTE.background
        assert tc.property("mathForeground") == LIGHT_PALETTE.mathForeground

        assert pref_signals == ["light"]
        assert resolved_signals == ["light"]
        assert len(theme_signals) == 1

        # Switch light -> dark
        tc.set_theme_preference("dark")
        assert tc.themePreference == "dark"
        assert tc.resolvedTheme == "dark"
        assert tc.current_palette is DARK_PALETTE
        assert tc.property("background") == DARK_PALETTE.background
        assert tc.property("mathForeground") == DARK_PALETTE.mathForeground

        assert pref_signals == ["light", "dark"]
        assert resolved_signals == ["light", "dark"]
        assert len(theme_signals) == 2

    def test_identical_preference_is_noop(self, qapp):
        tc = ThemeController("dark")
        pref_signals = []
        resolved_signals = []
        theme_signals = []

        tc.themePreferenceChanged.connect(pref_signals.append)
        tc.resolvedThemeChanged.connect(resolved_signals.append)
        tc.themeChanged.connect(lambda: theme_signals.append(True))

        tc.set_theme_preference("dark")

        assert len(pref_signals) == 0
        assert len(resolved_signals) == 0
        assert len(theme_signals) == 0

    def test_invalid_preference_is_ignored(self, qapp):
        tc = ThemeController("dark")
        tc.set_theme_preference("bogus")
        assert tc.themePreference == "dark"
        assert tc.resolvedTheme == "dark"

    def test_system_theme_dynamic_os_appearance_change(self, qapp):
        tc = ThemeController("system")
        resolved_signals = []
        theme_signals = []

        tc.resolvedThemeChanged.connect(resolved_signals.append)
        tc.themeChanged.connect(lambda: theme_signals.append(True))

        # Simulate OS switching to Light
        tc._on_color_scheme_changed(Qt.ColorScheme.Light)
        assert tc.resolvedTheme == "light"
        assert tc.property("background") == LIGHT_PALETTE.background

        # Simulate OS switching to Dark
        tc._on_color_scheme_changed(Qt.ColorScheme.Dark)
        assert tc.resolvedTheme == "dark"
        assert tc.property("background") == DARK_PALETTE.background

        # Simulate OS reporting Unknown (fallback to dark)
        tc._on_color_scheme_changed(Qt.ColorScheme.Unknown)
        assert tc.resolvedTheme == "dark"

    def test_fixed_preference_ignores_os_appearance_change(self, qapp):
        tc = ThemeController("dark")
        resolved_signals = []
        tc.resolvedThemeChanged.connect(resolved_signals.append)

        # OS reports Light, but user preference is fixed dark
        tc._on_color_scheme_changed(Qt.ColorScheme.Light)
        assert tc.resolvedTheme == "dark"
        assert len(resolved_signals) == 0

    def test_qguiapplication_palette_updated_on_change(self, qapp):
        tc = ThemeController("dark")
        tc.set_theme_preference("light")
        app_palette = qapp.palette()
        # Window color in app_palette should reflect light palette background
        assert app_palette.color(qapp.palette().ColorRole.Window).name().lower() == LIGHT_PALETTE.background.lower()

        tc.set_theme_preference("dark")
        app_palette = qapp.palette()
        assert app_palette.color(qapp.palette().ColorRole.Window).name().lower() == DARK_PALETTE.background.lower()

    def test_settings_controller_triggers_theme_update(self, qapp):
        from unittest.mock import MagicMock
        from core.entities.settings import AppSettings
        from interfaces.desktop.controllers.settings_controller import SettingsController

        mock_settings_service = MagicMock()
        mock_settings_service.get_settings.return_value = AppSettings(theme="dark")
        mock_settings_service.update_settings.side_effect = lambda s: s
        mock_artifact_service = MagicMock()

        sc = SettingsController(mock_settings_service, mock_artifact_service)
        tc = ThemeController("dark")

        # Wire identical to app.py
        sc.settings_changed.connect(lambda: tc.set_theme_preference(sc.theme))

        # Save settings with new theme 'light'
        success = sc.save_settings(
            theme="light",
            max_concurrent_jobs=2,
            missed_schedule_policy="prompt",
            artifact_retention_days=30,
            auto_retry=True,
            auto_pipeline2=False,
        )
        assert success is True
        assert sc.theme == "light"
        assert tc.themePreference == "light"
        assert tc.resolvedTheme == "light"
        assert tc.property("background") == LIGHT_PALETTE.background

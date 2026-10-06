# ============================================================
#  interfaces/desktop/controllers/theme_controller.py
#  Desktop Presentation Controller for Application Theme
# ============================================================

from typing import Optional

from interfaces.desktop.qt_compat import (
    QObject,
    Signal,
    Slot,
    Property,
    QGuiApplication,
    Qt,
)
from interfaces.desktop.theme import (
    ThemePalette,
    DARK_PALETTE,
    THEME_PALETTES,
    create_qt_palette,
)


class ThemeController(QObject):
    """
    Presentation controller managing desktop theme state, OS color-scheme detection,
    and exposure of semantic design tokens to QML on the Qt GUI thread.
    """

    themePreferenceChanged = Signal(str)
    resolvedThemeChanged = Signal(str)
    themeChanged = Signal()

    def __init__(
        self,
        initial_preference: str = "system",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        pref = initial_preference.strip().lower() if isinstance(initial_preference, str) else "system"
        if pref not in ("system", "dark", "light"):
            pref = "system"
        self._theme_preference: str = pref
        self._resolved_theme: str = self._resolve_theme(self._theme_preference)
        self._current_palette: ThemePalette = THEME_PALETTES.get(self._resolved_theme, DARK_PALETTE)

        self._connect_os_hints()

    def _connect_os_hints(self) -> None:
        app = QGuiApplication.instance()
        if app is not None:
            hints = app.styleHints()
            if hints is not None:
                try:
                    hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
                except (AttributeError, RuntimeError):
                    pass

    def _resolve_theme(self, preference: str) -> str:
        if preference == "light":
            return "light"
        if preference == "dark":
            return "dark"

        # System preference: query QStyleHints with deterministic Unknown -> dark fallback
        app = QGuiApplication.instance()
        if app is not None:
            hints = app.styleHints()
            if hints is not None:
                try:
                    scheme = hints.colorScheme()
                    if scheme == Qt.ColorScheme.Light:
                        return "light"
                    # Qt.ColorScheme.Dark or Qt.ColorScheme.Unknown
                    return "dark"
                except (AttributeError, RuntimeError):
                    return "dark"
        return "dark"

    def _on_color_scheme_changed(self, scheme=None) -> None:
        if self._theme_preference != "system":
            return
        if scheme == Qt.ColorScheme.Light:
            new_resolved = "light"
        else:
            new_resolved = "dark"
        self._apply_resolved_theme(new_resolved)

    def _apply_resolved_theme(self, new_resolved: str) -> None:
        if new_resolved != self._resolved_theme:
            self._resolved_theme = new_resolved
            self._current_palette = THEME_PALETTES.get(self._resolved_theme, DARK_PALETTE)
            app = QGuiApplication.instance()
            if app is not None:
                try:
                    app.setPalette(create_qt_palette(self._current_palette))
                except (AttributeError, RuntimeError):
                    pass
            self.resolvedThemeChanged.emit(self._resolved_theme)
            self.themeChanged.emit()

    @property
    def current_palette(self) -> ThemePalette:
        """Returns the active ThemePalette instance."""
        return self._current_palette

    # --- Q_PROPERTY Bindings for Theme State ---

    def _get_theme_preference(self) -> str:
        return self._theme_preference

    def _get_resolved_theme(self) -> str:
        return self._resolved_theme

    themePreference = Property(str, _get_theme_preference, notify=themePreferenceChanged)
    resolvedTheme = Property(str, _get_resolved_theme, notify=resolvedThemeChanged)

    # --- Q_PROPERTY Bindings for Semantic Design Tokens (41 tokens) ---

    # Surfaces (6)
    background = Property(str, lambda self: self._current_palette.background, notify=themeChanged)
    surface = Property(str, lambda self: self._current_palette.surface, notify=themeChanged)
    surfaceSunken = Property(str, lambda self: self._current_palette.surfaceSunken, notify=themeChanged)
    surfaceElevated = Property(str, lambda self: self._current_palette.surfaceElevated, notify=themeChanged)
    surfaceHover = Property(str, lambda self: self._current_palette.surfaceHover, notify=themeChanged)
    surfaceActive = Property(str, lambda self: self._current_palette.surfaceActive, notify=themeChanged)

    # Typography (5)
    textPrimary = Property(str, lambda self: self._current_palette.textPrimary, notify=themeChanged)
    textSecondary = Property(str, lambda self: self._current_palette.textSecondary, notify=themeChanged)
    textMuted = Property(str, lambda self: self._current_palette.textMuted, notify=themeChanged)
    textSubtle = Property(str, lambda self: self._current_palette.textSubtle, notify=themeChanged)
    textInverse = Property(str, lambda self: self._current_palette.textInverse, notify=themeChanged)

    # Borders (3)
    border = Property(str, lambda self: self._current_palette.border, notify=themeChanged)
    borderSubtle = Property(str, lambda self: self._current_palette.borderSubtle, notify=themeChanged)
    borderStrong = Property(str, lambda self: self._current_palette.borderStrong, notify=themeChanged)

    # Interactive (5)
    accent = Property(str, lambda self: self._current_palette.accent, notify=themeChanged)
    accentHover = Property(str, lambda self: self._current_palette.accentHover, notify=themeChanged)
    accentActive = Property(str, lambda self: self._current_palette.accentActive, notify=themeChanged)
    accentText = Property(str, lambda self: self._current_palette.accentText, notify=themeChanged)
    focusRing = Property(str, lambda self: self._current_palette.focusRing, notify=themeChanged)

    # Selection (2)
    selectionBackground = Property(str, lambda self: self._current_palette.selectionBackground, notify=themeChanged)
    selectionText = Property(str, lambda self: self._current_palette.selectionText, notify=themeChanged)

    # Error (4)
    error = Property(str, lambda self: self._current_palette.error, notify=themeChanged)
    errorBackground = Property(str, lambda self: self._current_palette.errorBackground, notify=themeChanged)
    errorBorder = Property(str, lambda self: self._current_palette.errorBorder, notify=themeChanged)
    errorText = Property(str, lambda self: self._current_palette.errorText, notify=themeChanged)

    # Warning (4)
    warning = Property(str, lambda self: self._current_palette.warning, notify=themeChanged)
    warningBackground = Property(str, lambda self: self._current_palette.warningBackground, notify=themeChanged)
    warningBorder = Property(str, lambda self: self._current_palette.warningBorder, notify=themeChanged)
    warningText = Property(str, lambda self: self._current_palette.warningText, notify=themeChanged)

    # Success (4)
    success = Property(str, lambda self: self._current_palette.success, notify=themeChanged)
    successBackground = Property(str, lambda self: self._current_palette.successBackground, notify=themeChanged)
    successBorder = Property(str, lambda self: self._current_palette.successBorder, notify=themeChanged)
    successText = Property(str, lambda self: self._current_palette.successText, notify=themeChanged)

    # Info (4)
    info = Property(str, lambda self: self._current_palette.info, notify=themeChanged)
    infoBackground = Property(str, lambda self: self._current_palette.infoBackground, notify=themeChanged)
    infoBorder = Property(str, lambda self: self._current_palette.infoBorder, notify=themeChanged)
    infoText = Property(str, lambda self: self._current_palette.infoText, notify=themeChanged)

    # Code & Math (4)
    codeBackground = Property(str, lambda self: self._current_palette.codeBackground, notify=themeChanged)
    codeBorder = Property(str, lambda self: self._current_palette.codeBorder, notify=themeChanged)
    codeText = Property(str, lambda self: self._current_palette.codeText, notify=themeChanged)
    mathForeground = Property(str, lambda self: self._current_palette.mathForeground, notify=themeChanged)

    # --- Presentation Slots ---

    @Slot(str)
    def set_theme_preference(self, pref: str) -> None:
        """
        Updates the active theme preference ('system', 'dark', 'light'), resolves
        the runtime theme, updates the Qt application palette, and emits signals.
        """
        if not isinstance(pref, str):
            return
        cleaned_pref = pref.strip().lower()
        if cleaned_pref not in ("system", "dark", "light"):
            return

        pref_changed = (cleaned_pref != self._theme_preference)
        self._theme_preference = cleaned_pref

        new_resolved = self._resolve_theme(self._theme_preference)
        theme_changed = (new_resolved != self._resolved_theme)

        if theme_changed:
            self._resolved_theme = new_resolved
            self._current_palette = THEME_PALETTES.get(self._resolved_theme, DARK_PALETTE)
            app = QGuiApplication.instance()
            if app is not None:
                try:
                    app.setPalette(create_qt_palette(self._current_palette))
                except (AttributeError, RuntimeError):
                    pass

        if pref_changed:
            self.themePreferenceChanged.emit(self._theme_preference)
        if theme_changed:
            self.resolvedThemeChanged.emit(self._resolved_theme)
            self.themeChanged.emit()

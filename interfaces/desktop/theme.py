# ============================================================
#  interfaces/desktop/theme.py
#  Semantic Design Tokens and Qt Palette Adapter for PolpoT
# ============================================================

from dataclasses import dataclass
from typing import Mapping

from interfaces.desktop.qt_compat import QPalette, QColor


@dataclass(frozen=True)
class ThemePalette:
    """
    Immutable semantic palette contract for PolpoT desktop theming.
    Contains exactly 41 design tokens across surfaces, typography,
    borders, interactive elements, feedback states, and code/math surfaces.
    """
    # Surfaces (6)
    background: str
    surface: str
    surfaceSunken: str
    surfaceElevated: str
    surfaceHover: str
    surfaceActive: str

    # Typography (5)
    textPrimary: str
    textSecondary: str
    textMuted: str
    textSubtle: str
    textInverse: str

    # Borders (3)
    border: str
    borderSubtle: str
    borderStrong: str

    # Interactive (5)
    accent: str
    accentHover: str
    accentActive: str
    accentText: str
    focusRing: str

    # Selection (2)
    selectionBackground: str
    selectionText: str

    # Error (4)
    error: str
    errorBackground: str
    errorBorder: str
    errorText: str

    # Warning (4)
    warning: str
    warningBackground: str
    warningBorder: str
    warningText: str

    # Success (4)
    success: str
    successBackground: str
    successBorder: str
    successText: str

    # Info (4)
    info: str
    infoBackground: str
    infoBorder: str
    infoText: str

    # Code & Math (4)
    codeBackground: str
    codeBorder: str
    codeText: str
    mathForeground: str


DARK_PALETTE = ThemePalette(
    # Surfaces
    background="#0d1117",
    surface="#161b22",
    surfaceSunken="#010409",
    surfaceElevated="#21262d",
    surfaceHover="#30363d",
    surfaceActive="#282e33",
    # Typography
    textPrimary="#f0f6fc",
    textSecondary="#8b949e",
    textMuted="#6e7681",
    textSubtle="#484f58",
    textInverse="#0d1117",
    # Borders
    border="#30363d",
    borderSubtle="#21262d",
    borderStrong="#6e7681",
    # Interactive
    accent="#58a6ff",
    accentHover="#79b8ff",
    accentActive="#388bfd",
    accentText="#ffffff",
    focusRing="#1f6feb",
    # Selection
    selectionBackground="#1f6feb",
    selectionText="#ffffff",
    # Error
    error="#f85149",
    errorBackground="#2d1b1f",
    errorBorder="#7f1d1d",
    errorText="#ff7b72",
    # Warning
    warning="#d29922",
    warningBackground="#261c0e",
    warningBorder="#9e6a03",
    warningText="#e3b341",
    # Success
    success="#3fb950",
    successBackground="#0f2417",
    successBorder="#238636",
    successText="#56d364",
    # Info
    info="#58a6ff",
    infoBackground="#0d2136",
    infoBorder="#1f6feb",
    infoText="#79b8ff",
    # Code & Math
    codeBackground="#161b22",
    codeBorder="#30363d",
    codeText="#e6edf3",
    mathForeground="#e6edf3",
)

LIGHT_PALETTE = ThemePalette(
    # Surfaces
    background="#ffffff",
    surface="#f6f8fa",
    surfaceSunken="#eaeef2",
    surfaceElevated="#ffffff",
    surfaceHover="#f3f4f6",
    surfaceActive="#ebecf0",
    # Typography
    textPrimary="#1f2328",
    textSecondary="#656d76",
    textMuted="#8c959f",
    textSubtle="#afb8c1",
    textInverse="#ffffff",
    # Borders
    border="#d0d7de",
    borderSubtle="#e1e4e8",
    borderStrong="#8c959f",
    # Interactive
    accent="#0969da",
    accentHover="#085fc7",
    accentActive="#054da5",
    accentText="#ffffff",
    focusRing="#0969da",
    # Selection
    selectionBackground="#0969da",
    selectionText="#ffffff",
    # Error
    error="#cf222e",
    errorBackground="#ffebe9",
    errorBorder="#ff8182",
    errorText="#a40e26",
    # Warning
    warning="#9a6700",
    warningBackground="#fff8c5",
    warningBorder="#d4a72c",
    warningText="#7d4e00",
    # Success
    success="#1a7f37",
    successBackground="#dafbe1",
    successBorder="#4ac26b",
    successText="#116329",
    # Info
    info="#0969da",
    infoBackground="#ddf4ff",
    infoBorder="#54aeff",
    infoText="#0550ae",
    # Code & Math
    codeBackground="#f6f8fa",
    codeBorder="#d0d7de",
    codeText="#1f2328",
    mathForeground="#1f2328",
)

THEME_PALETTES: Mapping[str, ThemePalette] = {
    "dark": DARK_PALETTE,
    "light": LIGHT_PALETTE,
}


def create_qt_palette(palette: ThemePalette) -> QPalette:
    """
    Constructs an auxiliary standard QPalette mapped from PolpoT semantic tokens.
    Keeps standard Qt widgets, menus, and file dialogs visually aligned with the active theme.
    """
    qt_palette = QPalette()

    # Window & text
    qt_palette.setColor(QPalette.ColorRole.Window, QColor(palette.background))
    qt_palette.setColor(QPalette.ColorRole.WindowText, QColor(palette.textPrimary))

    # Base & controls
    qt_palette.setColor(QPalette.ColorRole.Base, QColor(palette.surfaceSunken))
    qt_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.surface))

    # Text
    qt_palette.setColor(QPalette.ColorRole.Text, QColor(palette.textPrimary))
    qt_palette.setColor(QPalette.ColorRole.BrightText, QColor(palette.textInverse))
    qt_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(palette.textMuted))

    # Buttons
    qt_palette.setColor(QPalette.ColorRole.Button, QColor(palette.surface))
    qt_palette.setColor(QPalette.ColorRole.ButtonText, QColor(palette.textPrimary))

    # Tooltips
    qt_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.surfaceElevated))
    qt_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(palette.textPrimary))

    # Selection & Links
    qt_palette.setColor(QPalette.ColorRole.Highlight, QColor(palette.selectionBackground))
    qt_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(palette.selectionText))
    qt_palette.setColor(QPalette.ColorRole.Link, QColor(palette.accent))
    qt_palette.setColor(QPalette.ColorRole.LinkVisited, QColor(palette.accentHover))

    # Mid / Shadow / Borders
    qt_palette.setColor(QPalette.ColorRole.Mid, QColor(palette.border))
    qt_palette.setColor(QPalette.ColorRole.Dark, QColor(palette.borderStrong))
    qt_palette.setColor(QPalette.ColorRole.Light, QColor(palette.surfaceElevated))

    # Disabled state for standard Qt dialogs and controls
    qt_palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(palette.textMuted))
    qt_palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(palette.textMuted))
    qt_palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(palette.textMuted))

    return qt_palette

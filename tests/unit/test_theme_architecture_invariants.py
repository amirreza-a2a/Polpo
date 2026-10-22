# ============================================================
#  tests/unit/test_theme_architecture_invariants.py
#  Architectural Invariant Enforcement for Desktop Theme System
# ============================================================

import re
import unittest
from pathlib import Path
from typing import List, Tuple

from interfaces.desktop.theme import DARK_PALETTE, LIGHT_PALETTE, ThemePalette


HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}")
RGBA_COLOR_PATTERN = re.compile(r"\b(?:rgb|rgba|Qt\.rgba|Qt\.color)\s*\([^)]*\)")
DIRECT_NAMED_COLOR_PATTERN = re.compile(r"\b(?:color|border\.color)\s*:\s*[\"']([a-zA-Z]+)[\"']")

# Whitelisted files with domain classification constants:
# 1. BoundingBoxOverlay.qml for explicit CV / canvas annotation colors
CV_CANVAS_WHITELIST = frozenset({
    "#00f0ff",  # Active selection cyan
    "#2a9d8f",  # Accepted region green
    "#f77f00",  # Modified region amber
    "#9d4edd",  # User manual / rubber-band purple
    "#00b4d8",  # AI detected default cyan
    "#0077b6",  # Resize handle border dark cyan
    "#ffffff",  # Resize handle fill & badge text white
    "#000000",  # Selected badge text black
})

# Whitelisted RGBA expressions strictly inside BoundingBoxOverlay.qml for CV canvas rendering:
# - Dynamic alpha shading of the region box: Qt.rgba(boxColor.r, boxColor.g, boxColor.b, isSelected ? 0.25 : 0.15)
# - Semi-transparent rubber-band creation box (#9d4edd with 0.2 alpha): Qt.rgba(0.62, 0.31, 0.87, 0.2)
CV_CANVAS_RGBA_WHITELIST_PREFIXES = (
    "Qt.rgba(boxColor.r, boxColor.g, boxColor.b",
    "Qt.rgba(0.62, 0.31, 0.87, 0.2)",
)

# 2. MathErrorCard.qml for explicit P08 math failure category badge colors
MATH_ERROR_BADGE_WHITELIST = frozenset({
    "#9a3412",  # Syntax error amber
    "#92400e",  # Timeout / degraded amber-brown
    "#991b1b",  # Worker crash / buffer limit / circuit breaker dark red
    "#7f1d1d",  # Generic / unknown error deep red
})

PERMITTED_DIRECT_NAMED_COLORS = frozenset({"transparent"})

CSS_NAMED_COLOR_LITERALS = frozenset({
    "black", "white", "gray", "grey", "silver", "maroon", "red", "purple", "fuchsia",
    "green", "lime", "olive", "yellow", "navy", "blue", "teal", "aqua", "orange",
    "goldenrod", "darkgoldenrod", "darkred", "darkgreen", "darkblue", "lightblue",
    "lightgreen", "darkgray", "lightgray", "brown",
})
NAMED_COLOR_LITERAL_PATTERN = re.compile(r"[\"']([a-zA-Z]+)[\"']")

BBOX_OVERLAY_RELATIVE_PATH = "interfaces/desktop/qml/components/BoundingBoxOverlay.qml"
MATH_ERROR_CARD_RELATIVE_PATH = "interfaces/desktop/qml/components/MathErrorCard.qml"


def scan_qml_file_for_hex_colors(file_path: Path) -> List[Tuple[int, str, str]]:
    """
    Scans a QML file line-by-line and returns a list of (line_number, match, line_content).
    Strips single-line comments before matching.
    """
    matches = []
    content = file_path.read_text(encoding="utf-8")
    for line_idx, line in enumerate(content.splitlines(), start=1):
        clean_line = re.sub(r"//.*$", "", line).strip()
        found = HEX_COLOR_PATTERN.findall(clean_line)
        for hex_val in found:
            matches.append((line_idx, hex_val, clean_line))
    return matches


def scan_qml_file_for_rgba_colors(file_path: Path) -> List[Tuple[int, str, str]]:
    """
    Scans a QML file line-by-line for rgb/rgba/Qt.rgba/Qt.color expressions.
    Strips single-line comments before matching.
    """
    matches = []
    content = file_path.read_text(encoding="utf-8")
    for line_idx, line in enumerate(content.splitlines(), start=1):
        clean_line = re.sub(r"//.*$", "", line).strip()
        found = RGBA_COLOR_PATTERN.findall(clean_line)
        for val in found:
            matches.append((line_idx, val, clean_line))
    return matches


def scan_qml_content_for_direct_named_colors(content: str) -> List[Tuple[int, str, str]]:
    """
    Scans QML text content for direct and conditional named color assignments
    (e.g. color: 'white' or ? theme.textPrimary : 'goldenrod') that bypass semantic tokens.
    """
    matches = []
    for line_idx, line in enumerate(content.splitlines(), start=1):
        clean_line = re.sub(r"//.*$", "", line).strip()
        # Direct assignments: color: "..." or border.color: "..."
        for named in DIRECT_NAMED_COLOR_PATTERN.findall(clean_line):
            if named.lower() not in PERMITTED_DIRECT_NAMED_COLORS:
                matches.append((line_idx, named, clean_line))
        # Quoted CSS color names in conditional expressions or returns
        for quoted in NAMED_COLOR_LITERAL_PATTERN.findall(clean_line):
            if quoted.lower() in CSS_NAMED_COLOR_LITERALS and quoted.lower() not in PERMITTED_DIRECT_NAMED_COLORS:
                matches.append((line_idx, quoted, clean_line))
    # Deduplicate matches per line and token
    seen = set()
    deduped = []
    for m in matches:
        key = (m[0], m[1].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    return deduped


def scan_qml_file_for_direct_named_colors(file_path: Path) -> List[Tuple[int, str, str]]:
    """
    Scans a QML file for direct or conditional named color assignments
    that bypass semantic theme tokens.
    """
    return scan_qml_content_for_direct_named_colors(file_path.read_text(encoding="utf-8"))


class TestThemeArchitectureInvariants(unittest.TestCase):
    """
    Enforces architectural invariants for the Polpo desktop theme system:
    1. Zero unauthorized hex literals in QML presentation files.
    2. Zero unauthorized rgb/rgba expressions in QML presentation files.
    3. Zero unauthorized direct named color assignments in QML presentation files.
    4. Explicit, strictly bounded whitelist for domain classification constants (CV overlay & math error badges).
    5. 100% theme token definition coverage across light and dark palettes (exactly 41 tokens).
    6. Consistent Polpo product branding in scoped presentation components.
    7. Elimination of arbitrary fallback literals in Main.qml and Sidebar.qml.
    """

    @property
    def qml_root(self) -> Path:
        repo_root = Path(__file__).resolve().parent.parent.parent
        return repo_root / "interfaces" / "desktop" / "qml"

    def test_qml_discovery_sanity(self):
        """Ensures the scanner discovers all active QML files."""
        self.assertTrue(self.qml_root.is_dir(), f"QML root does not exist: {self.qml_root}")
        qml_files = list(self.qml_root.rglob("*.qml"))
        self.assertGreaterEqual(
            len(qml_files),
            30,
            f"Expected at least 30 QML files to be scanned, found {len(qml_files)}",
        )

    def test_zero_unauthorized_hex_colors_in_qml(self):
        """
        Scans every QML file in the desktop UI tree for hex color literals.
        Outside of strictly bounded domain classification constants, zero hex colors are permitted.
        """
        violations = []
        qml_files = sorted(self.qml_root.rglob("*.qml"))

        for qml_file in qml_files:
            rel_path = qml_file.relative_to(self.qml_root.parent.parent.parent).as_posix()
            matches = scan_qml_file_for_hex_colors(qml_file)

            if rel_path == BBOX_OVERLAY_RELATIVE_PATH:
                for line_no, hex_val, line_text in matches:
                    if hex_val.lower() not in CV_CANVAS_WHITELIST:
                        violations.append(
                            f"{rel_path}:{line_no} unauthorized hex literal '{hex_val}' "
                            f"(not in CV whitelist): {line_text}"
                        )
            elif rel_path == MATH_ERROR_CARD_RELATIVE_PATH:
                for line_no, hex_val, line_text in matches:
                    if hex_val.lower() not in MATH_ERROR_BADGE_WHITELIST:
                        violations.append(
                            f"{rel_path}:{line_no} unauthorized hex literal '{hex_val}' "
                            f"(not in Math Error Badge whitelist): {line_text}"
                        )
            else:
                for line_no, hex_val, line_text in matches:
                    violations.append(
                        f"{rel_path}:{line_no} unauthorized hex literal '{hex_val}': {line_text}"
                    )

        if violations:
            violation_report = "\n".join(violations)
            self.fail(
                f"Detected {len(violations)} architectural hex color violation(s) in QML:\n"
                f"{violation_report}\n\n"
                "All UI colors must reference semantic theme tokens (e.g. theme.surface, theme.accent, theme.textPrimary)."
            )

    def test_zero_unauthorized_rgba_colors_in_qml(self):
        """
        Scans every QML file in the desktop UI tree for rgb/rgba/Qt.rgba expressions.
        Outside of strictly bounded CV canvas overlays in BoundingBoxOverlay.qml, zero are permitted.
        """
        violations = []
        qml_files = sorted(self.qml_root.rglob("*.qml"))

        for qml_file in qml_files:
            rel_path = qml_file.relative_to(self.qml_root.parent.parent.parent).as_posix()
            matches = scan_qml_file_for_rgba_colors(qml_file)

            if rel_path == BBOX_OVERLAY_RELATIVE_PATH:
                for line_no, expr, line_text in matches:
                    is_whitelisted = any(
                        expr.startswith(prefix) for prefix in CV_CANVAS_RGBA_WHITELIST_PREFIXES
                    )
                    if not is_whitelisted:
                        violations.append(
                            f"{rel_path}:{line_no} unauthorized RGBA expression '{expr}' "
                            f"(not in CV RGBA whitelist): {line_text}"
                        )
            else:
                for line_no, expr, line_text in matches:
                    violations.append(
                        f"{rel_path}:{line_no} unauthorized RGBA expression '{expr}': {line_text}"
                    )

        if violations:
            violation_report = "\n".join(violations)
            self.fail(
                f"Detected {len(violations)} architectural RGBA color violation(s) in QML:\n"
                f"{violation_report}\n\n"
                "All application overlays and colors must reference semantic theme tokens or component opacity."
            )

    def test_zero_unauthorized_direct_named_colors_in_qml(self):
        """
        Scans every QML file in the desktop UI tree for direct named color assignments.
        Direct assignments like color: "white" bypass semantic theming and are prohibited.
        """
        violations = []
        qml_files = sorted(self.qml_root.rglob("*.qml"))

        for qml_file in qml_files:
            rel_path = qml_file.relative_to(self.qml_root.parent.parent.parent).as_posix()
            matches = scan_qml_file_for_direct_named_colors(qml_file)
            for line_no, named_val, line_text in matches:
                violations.append(
                    f"{rel_path}:{line_no} unauthorized direct named color '{named_val}': {line_text}"
                )

        if violations:
            violation_report = "\n".join(violations)
            self.fail(
                f"Detected {len(violations)} unauthorized direct named color assignment(s) in QML:\n"
                f"{violation_report}\n\n"
                "UI colors must reference semantic theme tokens (e.g. theme.accentText, theme.textPrimary)."
            )

    def test_bounding_box_overlay_whitelist_strictness(self):
        """
        Verifies that BoundingBoxOverlay.qml is NOT globally exempt:
        any hex color present must strictly belong to CV_CANVAS_WHITELIST.
        """
        bbox_path = self.qml_root / "components" / "BoundingBoxOverlay.qml"
        self.assertTrue(bbox_path.is_file(), f"BoundingBoxOverlay.qml missing at {bbox_path}")

        matches = scan_qml_file_for_hex_colors(bbox_path)
        found_hex_set = {hex_val.lower() for _, hex_val, _ in matches}

        unauthorized = found_hex_set - CV_CANVAS_WHITELIST
        self.assertEqual(
            unauthorized,
            set(),
            f"BoundingBoxOverlay contains unauthorized hex colors outside CV canvas whitelist: {unauthorized}",
        )

        rgba_matches = scan_qml_file_for_rgba_colors(bbox_path)
        for _, expr, _ in rgba_matches:
            self.assertTrue(
                any(expr.startswith(prefix) for prefix in CV_CANVAS_RGBA_WHITELIST_PREFIXES),
                f"BoundingBoxOverlay contains non-whitelisted RGBA expression: {expr}",
            )

    def test_math_error_card_whitelist_strictness(self):
        """
        Verifies that MathErrorCard.qml is NOT globally exempt:
        any hex color present must strictly belong to MATH_ERROR_BADGE_WHITELIST.
        """
        card_path = self.qml_root / "components" / "MathErrorCard.qml"
        self.assertTrue(card_path.is_file(), f"MathErrorCard.qml missing at {card_path}")

        matches = scan_qml_file_for_hex_colors(card_path)
        found_hex_set = {hex_val.lower() for _, hex_val, _ in matches}

        unauthorized = found_hex_set - MATH_ERROR_BADGE_WHITELIST
        self.assertEqual(
            unauthorized,
            set(),
            f"MathErrorCard contains unauthorized hex colors outside Math Error Badge whitelist: {unauthorized}",
        )

    def test_invariant_scanner_positive_detection(self):
        """Verifies that the scanner correctly identifies unauthorized color literals in synthetic text."""
        # 1. Hex detection
        test_hex = "Rectangle { color: '#ff00aa'; border.color: '#123' }"
        hex_matches = HEX_COLOR_PATTERN.findall(test_hex)
        self.assertEqual(hex_matches, ["#ff00aa", "#123"])

        # 2. RGBA detection
        test_rgba = "Rectangle { color: Qt.rgba(0.5, 0.2, 0.1, 0.8); border.color: rgba(10, 20, 30, 0.5) }"
        rgba_matches = RGBA_COLOR_PATTERN.findall(test_rgba)
        self.assertEqual(rgba_matches, ["Qt.rgba(0.5, 0.2, 0.1, 0.8)", "rgba(10, 20, 30, 0.5)"])

        # 3. Direct named color detection
        test_named = 'Text { color: "white"; border.color: "red" }'
        named_matches = [
            n for _, n, _ in scan_qml_content_for_direct_named_colors(test_named)
        ]
        self.assertEqual(named_matches, ["white", "red"])

        # 4. Conditional fallback named color detection
        test_conditional = 'Text { color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "goldenrod" }'
        cond_matches = [
            n for _, n, _ in scan_qml_content_for_direct_named_colors(test_conditional)
        ]
        self.assertEqual(cond_matches, ["goldenrod"])

    def test_synthetic_unauthorized_color_in_whitelisted_component(self):
        """
        Verifies that introducing an unauthorized color into BoundingBoxOverlay or MathErrorCard
        is caught deterministically even though the files contain whitelisted constants.
        """
        # Unauthorized hex in CV overlay
        mock_bbox_with_unauthorized_hex = (
            "Item {\n"
            "    property color cvColor: '#2a9d8f'\n"
            "    property color badUiColor: '#ff00aa'\n"
            "}\n"
        )
        lines = mock_bbox_with_unauthorized_hex.splitlines()
        found_bad = []
        for idx, line in enumerate(lines, start=1):
            for h in HEX_COLOR_PATTERN.findall(line):
                if h.lower() not in CV_CANVAS_WHITELIST:
                    found_bad.append((idx, h))
        self.assertEqual(found_bad, [(3, "#ff00aa")])

        # Unauthorized RGBA in MathErrorCard
        mock_math_with_bad_rgba = (
            "Rectangle {\n"
            "    color: Qt.rgba(1.0, 0.0, 0.0, 0.5)\n"
            "}\n"
        )
        rgba_matches = RGBA_COLOR_PATTERN.findall(mock_math_with_bad_rgba)
        self.assertEqual(rgba_matches, ["Qt.rgba(1.0, 0.0, 0.0, 0.5)"])

    def test_product_name_branding_in_scoped_qml(self):
        """Verifies consistent Polpo branding in Main.qml and Sidebar.qml without stale PolpoT."""
        main_qml = (self.qml_root / "Main.qml").read_text(encoding="utf-8")
        sidebar_qml = (self.qml_root / "components" / "Sidebar.qml").read_text(encoding="utf-8")

        # Main window title
        self.assertIn('title: "Polpo — Desktop Document Intelligence"', main_qml)
        self.assertNotIn("PolpoT", main_qml)

        # Sidebar branding
        self.assertIn('text: "Polpo Desktop"', sidebar_qml)
        self.assertNotIn("PolpoT", sidebar_qml)

    def test_main_and_sidebar_theme_fallbacks_eliminated(self):
        """
        Verifies that Main.qml and Sidebar.qml consume semantic theme tokens directly
        without arbitrary hardcoded color fallbacks ('black', 'white', 'gray', 'blue').
        """
        main_qml = (self.qml_root / "Main.qml").read_text(encoding="utf-8")
        sidebar_qml = (self.qml_root / "components" / "Sidebar.qml").read_text(encoding="utf-8")

        # Ensure no fallback literals in Main.qml
        for fallback in ('"black"', '"white"', '"gray"'):
            self.assertNotIn(
                f": {fallback}",
                main_qml,
                f"Main.qml should not contain fallback literal {fallback}",
            )

        # Ensure no fallback literals in Sidebar.qml
        for fallback in ('"white"', '"gray"', '"blue"'):
            self.assertNotIn(
                f": {fallback}",
                sidebar_qml,
                f"Sidebar.qml should not contain fallback literal {fallback}",
            )

    def test_theme_fallbacks_eliminated_across_qml(self):
        """
        Verifies that all QML presentation components consume semantic theme tokens
        without arbitrary hardcoded color fallbacks ('white', 'black', 'gray', 'blue', 'goldenrod', etc.).
        """
        violations = []
        for qml_file in sorted(self.qml_root.rglob("*.qml")):
            rel_path = qml_file.relative_to(self.qml_root.parent.parent.parent).as_posix()
            if rel_path == BBOX_OVERLAY_RELATIVE_PATH:
                continue
            matches = scan_qml_file_for_direct_named_colors(qml_file)
            for line_no, named_val, line_text in matches:
                violations.append(
                    f"{rel_path}:{line_no} unauthorized named color literal '{named_val}': {line_text}"
                )
        self.assertEqual(
            violations,
            [],
            f"Detected {len(violations)} unauthorized named color literal(s) across QML files:\n"
            + "\n".join(violations),
        )

    def test_theme_palette_completeness(self):
        """Verifies that DARK_PALETTE and LIGHT_PALETTE have all 41 semantic tokens defined."""
        expected_token_count = 41
        fields = ThemePalette.__dataclass_fields__
        self.assertEqual(
            len(fields),
            expected_token_count,
            f"Expected {expected_token_count} theme tokens, got {len(fields)}",
        )

        hex_pattern = re.compile(r"^#[0-9a-fA-F]{6}$")
        for token_name in fields:
            dark_val = getattr(DARK_PALETTE, token_name)
            light_val = getattr(LIGHT_PALETTE, token_name)

            self.assertIsInstance(dark_val, str, f"DARK_PALETTE.{token_name} is not a string")
            self.assertIsInstance(light_val, str, f"LIGHT_PALETTE.{token_name} is not a string")
            self.assertTrue(
                bool(hex_pattern.match(dark_val)),
                f"DARK_PALETTE.{token_name} is not a valid 6-char hex: {dark_val}",
            )
            self.assertTrue(
                bool(hex_pattern.match(light_val)),
                f"LIGHT_PALETTE.{token_name} is not a valid 6-char hex: {light_val}",
            )

    def test_cv_canvas_annotation_palette_preservation(self):
        """Verifies that the CV canvas annotation palette values in BoundingBoxOverlay are preserved."""
        expected_cv_colors = {
            "#00f0ff",
            "#2a9d8f",
            "#f77f00",
            "#9d4edd",
            "#00b4d8",
            "#0077b6",
            "#ffffff",
            "#000000",
        }
        self.assertEqual(CV_CANVAS_WHITELIST, expected_cv_colors)


if __name__ == "__main__":
    unittest.main()

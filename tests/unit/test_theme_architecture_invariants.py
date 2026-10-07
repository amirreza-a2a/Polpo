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

# The only whitelisted files are domain classification constants:
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

# 2. MathErrorCard.qml for explicit P08 math failure category badge colors
MATH_ERROR_BADGE_WHITELIST = frozenset({
    "#9a3412",  # Syntax error amber
    "#92400e",  # Timeout / degraded amber-brown
    "#991b1b",  # Worker crash / buffer limit / circuit breaker dark red
    "#7f1d1d",  # Generic / unknown error deep red
})

BBOX_OVERLAY_RELATIVE_PATH = "interfaces/desktop/qml/components/BoundingBoxOverlay.qml"
MATH_ERROR_CARD_RELATIVE_PATH = "interfaces/desktop/qml/components/MathErrorCard.qml"


def scan_qml_file_for_hex_colors(file_path: Path) -> List[Tuple[int, str, str]]:
    """
    Scans a QML file line-by-line and returns a list of (line_number, match, line_content).
    """
    matches = []
    content = file_path.read_text(encoding="utf-8")
    for line_idx, line in enumerate(content.splitlines(), start=1):
        found = HEX_COLOR_PATTERN.findall(line)
        for hex_val in found:
            matches.append((line_idx, hex_val, line.strip()))
    return matches


class TestThemeArchitectureInvariants(unittest.TestCase):
    """
    Enforces architectural invariants for the PolpoT desktop theme system:
    1. Zero hardcoded hex literals in QML presentation files.
    2. Explicit, strictly bounded whitelist for domain classification constants (CV overlay & math error badges).
    3. 100% theme token definition coverage across light and dark palettes.
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
            20,
            f"Expected at least 20 QML files to be scanned, found {len(qml_files)}",
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

    def test_bounding_box_overlay_whitelist_strictness(self):
        """
        Verifies that BoundingBoxOverlay.qml is NOT globally exempt:
        any hex color present must strictly belong to CV_CANVAS_WHITELIST.
        """
        bbox_path = self.qml_root / "components" / "BoundingBoxOverlay.qml"
        self.assertTrue(bbox_path.is_file(), f"BoundingBoxOverlay.qml missing at {bbox_path}")

        matches = scan_qml_file_for_hex_colors(bbox_path)
        found_hex_set = {hex_val.lower() for _, hex_val, _ in matches}

        # Verify no unauthorized hex is present
        unauthorized = found_hex_set - CV_CANVAS_WHITELIST
        self.assertEqual(
            unauthorized,
            set(),
            f"BoundingBoxOverlay contains unauthorized hex colors outside CV canvas whitelist: {unauthorized}",
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
        """Verifies that the scanner correctly identifies unauthorized hex literals in mock text."""
        test_content = "Rectangle { color: '#ff00aa'; border.color: '#123' }"
        matches = HEX_COLOR_PATTERN.findall(test_content)
        self.assertEqual(matches, ["#ff00aa", "#123"])

    def test_theme_palette_completeness(self):
        """Verifies that DARK_PALETTE and LIGHT_PALETTE have all 41 semantic tokens defined."""
        expected_token_count = 41
        fields = ThemePalette.__dataclass_fields__
        self.assertEqual(
            len(fields),
            expected_token_count,
            f"Expected {expected_token_count} theme tokens, got {len(fields)}",
        )

        for token_name in fields:
            dark_val = getattr(DARK_PALETTE, token_name)
            light_val = getattr(LIGHT_PALETTE, token_name)

            self.assertIsInstance(dark_val, str, f"DARK_PALETTE.{token_name} is not a string")
            self.assertIsInstance(light_val, str, f"LIGHT_PALETTE.{token_name} is not a string")
            self.assertTrue(
                dark_val.startswith("#"),
                f"DARK_PALETTE.{token_name} does not start with '#': {dark_val}",
            )
            self.assertTrue(
                light_val.startswith("#"),
                f"LIGHT_PALETTE.{token_name} does not start with '#': {light_val}",
            )


if __name__ == "__main__":
    unittest.main()

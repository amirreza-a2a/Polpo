# Investigation P01 — Desktop Theme System: Evidence Validation & Architectural Audit

**Document:** `docs/problems/investigations/P01-desktop-theme-system-validation.md`
**Problem Register Reference:** `P01`
**Target Platform:** PySide6 6.8.0.2 / Qt 6.8 on Linux, Windows 10/11, macOS
**Execution Boundary:** Research and Evidence Validation only. No production code modified.

---

## 1. Executive Summary

This research document validates and falsifies the architectural propositions from the initial P01 investigation against primary sources: official Qt 6.8 documentation, PySide6 runtime behavior, and the live PolpoT codebase.

### Primary Confirmations
1. **Zero-Overhead MathJax Vector Theming:** Confirmed that `mathjax-full@3.2.2` outputs vector glyphs referencing `currentColor`. `QSvgRenderer` renders these glyphs with full anti-aliasing in any specified color when injected via `<svg color="{themeColor}" ...>`. MathJax Node.js formulas never need re-rendering on theme switches; `MathSvgCache` remains 100% theme-neutral and untouched.
2. **Qt Quick URL-Based Pixmap Invalidation:** Confirmed that Qt Quick internally caches `image://` pixmaps by URL string. Passing `image://math/{theme}/{hash}` is the only guaranteed mechanism in Qt Quick to invalidate and reload math images across theme transitions without cache stalling.
3. **Semantic Token Requirement:** Confirmed that standard `QPalette` is structurally incapable of serving as the primary theme source of truth for PolpoT due to domain-specific requirements (code blocks, math error cards, region overlays, multi-level semantic badges). A Polpo-owned semantic token layer is strictly necessary.

### Critical Falsifications & Necessary Changes
1. **Falsification of Dual QML Exposure:** The proposition to expose `ThemeController` both as a context property (`theme`) and as a registered singleton (`Theme`) is **rejected**. Dual exposure creates conflicting ownership semantics, requires unnecessary `import PolpoT.Theme 1.0` boilerplate across all 26 QML files, and risks null-reference errors during teardown. **Recommendation:** Expose solely as `theme` context property, consistent with all 14 other controllers in the repository.
2. **Falsification of Live Settings Preview (Option A):** The proposal for a "live preview with discard/revert" in `SettingsView.qml` conflicts with Polpo's existing settings architecture. Polpo has no draft state, no discard hooks, no navigation interception, and no transactional settings model. Implementing live preview with silent reversion on tab-switching would produce disorienting UI flicker. **Recommendation:** Adopt **Option B (Apply on Save)**, which preserves the batch persistence contract of `savePrefsBtn` with the smallest coherent design.
3. **Narrowing of the Zero-Hex Invariant:** The proposition of "zero raw hex colors anywhere in QML" is **falsified**. `BoundingBoxOverlay.qml` contains 26 hex literals that represent computer vision region annotation classifications and canvas resize handles rendered over user PDF documents. These must remain component-local constants. The architectural invariant is refined to: **"No theme-bearing color literals in application QML"**.
4. **Tempering of Cross-Platform Guarantees:** Linux system appearance detection relies on the FreeDesktop XDG Desktop Portal (`xdg-desktop-portal`). Under minimal window managers or headless/CI environments, `QStyleHints.colorScheme()` returns `Qt.ColorScheme.Unknown`. This is a platform implementation detail, not a guaranteed Qt invariant. Polpo must treat `Unknown` as a fallback to `"dark"`.

---

## 2. Detailed Technical Findings

### 2.1 Qt 6.8 System Color-Scheme Contract
- **Source:** Qt 6.8 Official Documentation (`QStyleHints::colorScheme-prop`, `Qt::ColorScheme`).
- **Default Behavior:** By default, `QStyleHints::colorScheme` follows system appearance (`Qt.ColorScheme.Light`, `Qt.ColorScheme.Dark`).
- **Semantics of `Unknown`:** `Qt.ColorScheme.Unknown` indicates that the platform plugin cannot determine an OS preference (e.g. Linux without a running desktop portal, or platforms without dark mode APIs).
- **Fallback Justification:** Polpo's default design is dark-first. Mapping `Unknown -> "dark"` is robust, deterministic, and preserves design intent in CI, containers, and minimal window managers.
- **Event Ordering Warning:** The official Qt 6.8 documentation explicitly warns:
  > *"When the colorSchemeChanged() signal gets emitted, the old palette is still in effect."*
  Because Polpo relies on its own semantic design tokens driven directly by the `scheme: Qt.ColorScheme` parameter emitted by the signal, Polpo avoids this palette lag entirely.

### 2.2 QML Exposure Architecture
- **Context Property (`setContextProperty`):**
  - Requires zero imports in QML.
  - Matches 100% of existing controllers (`settingsController`, `jobController`, etc.).
  - Owned by `DesktopAppContainer` for application lifetime.
- **Registered Singleton (`qmlRegisterSingletonInstance`):**
  - Requires adding `import PolpoT.Theme 1.0` across 26 files.
  - Generates `TypeError: Cannot read property '...' of null` during teardown if QML bindings evaluate after Python garbage collection.
- **Decision:** Authoritative exposure mechanism is strictly `engine.rootContext().setContextProperty("theme", theme_controller)`.

### 2.3 Settings Lifecycle: Apply on Save (Option B)
- **Current Repository State:** `SettingsView.qml` contains 6 preferences committed together via `savePrefsBtn.onClicked`. There is no cancel button, no discard mechanism, and `Main.qml` does not intercept tab switching.
- **Option B Flow:**
  ```text
  User selects themeCombo ("light")
      ↓ (No UI change yet, matching other 5 controls)
  User clicks "Save Preferences"
      ↓
  settingsController.save_settings(...)
      ↓
  LocalSettingsService commits to SQLite
      ↓
  settings_changed emitted
      ↓
  themeController updates resolvedTheme to "light"
      ↓
  Entire application updates to light mode; status notice displays
  ```

### 2.4 Math Presentation & Cache Contract
- **Vector Theming:** MathJax SVGs contain `fill="currentColor"`.
- **Rasterization:** `MathImageProvider` intercepts `image://math/{theme}/{hash}`, retrieves raw SVG from `MathSvgCache`, injects `color="{themeColor}"` into `<svg ...>`, and rasterizes via `QSvgRenderer`.
- **Cache Isolation:** `MathSvgCache` and `NegativeFailureMemo` remain strictly indexed by `formula_hash`, independent of UI theme. Only the presentation URL carries `{theme}` to prevent Qt Quick pixmap cache collisions.

### 2.5 Semantic Token Consolidation
- Redundant tokens eliminated: `surfaceCard` consolidated into `surface`; `mathCardBorder` consolidated into `errorBorder`.
- Refined palette: 30 clean semantic tokens covering surfaces, typography, borders, interactive states, semantic feedback (error, warning, success), and code/math surfaces.
- Domain graphics excluded: CV bounding box annotations (`#00f0ff`, `#2a9d8f`, `#f77f00`, `#9d4edd`, `#00b4d8`) remain local constants in `BoundingBoxOverlay.qml`.

---

## 3. Scope for Codebase Design (Next Step)

The following components and interfaces are scoped for `/codebase-design`:
1. `interfaces/desktop/theme.py`: Semantic token dictionary & color mappings.
2. `interfaces/desktop/controllers/theme_controller.py`: QObject controller managing `resolvedTheme`, token properties, and `colorSchemeChanged` listener.
3. `interfaces/desktop/providers/math_image_provider.py`: Updating `requestImage` to parse `{theme}/{hash}` and inject SVG color.
4. `interfaces/desktop/qml/components/`: Migrating `Card.qml`, `MathErrorCard.qml`, `MarkdownNodeDelegate.qml`, and `MarkdownInlineFlow.qml` to `theme.*`.

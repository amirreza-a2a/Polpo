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

### 2.5 Semantic Token Inventory & Authoritative Contract (Reconciled under THEME-01)
- **Authoritative Source of Truth:** `ThemePalette` in `interfaces/desktop/theme.py` is the single authoritative implementation contract.
- **Historical Note (Superseded Draft):** The preliminary investigation draft listed 30 tokens. The production implementation completed under Phase 8 refined and expanded this contract to **exactly 41 semantic tokens** across 10 functional groups to avoid ambiguity in feedback states, selection, and code/math rendering.
- **Token Inventory (41 tokens):**
  1. **Surfaces (6):** `background`, `surface`, `surfaceSunken`, `surfaceElevated`, `surfaceHover`, `surfaceActive`
  2. **Typography (5):** `textPrimary`, `textSecondary`, `textMuted`, `textSubtle`, `textInverse`
  3. **Borders (3):** `border`, `borderSubtle`, `borderStrong`
  4. **Interactive (5):** `accent`, `accentHover`, `accentActive`, `accentText`, `focusRing`
  5. **Selection (2):** `selectionBackground`, `selectionText`
  6. **Error (4):** `error`, `errorBackground`, `errorBorder`, `errorText`
  7. **Warning (4):** `warning`, `warningBackground`, `warningBorder`, `warningText`
  8. **Success (4):** `success`, `successBackground`, `successBorder`, `successText`
  9. **Info (4):** `info`, `infoBackground`, `infoBorder`, `infoText`
  10. **Code & Math (4):** `codeBackground`, `codeBorder`, `codeText`, `mathForeground`
- **Both Palettes Fully Populated:** `DARK_PALETTE` and `LIGHT_PALETTE` define valid 6-character hex values for all 41 fields.

### 2.6 Domain Graphic Exceptions & Color Invariant Enforcement
- **Separation of Concerns:** Application UI chrome must strictly consume `theme.*` properties. In contrast, data-driven domain graphics represent document content or classification states and retain intentional fixed encodings:
  1. `BoundingBoxOverlay.qml`: Computer-vision coordinate overlays on user documents and canvas handles (`#00f0ff`, `#2a9d8f`, `#f77f00`, `#9d4edd`, `#00b4d8`, `#0077b6`, `#ffffff`, `#000000`) plus bounded CV canvas alpha shading (`Qt.rgba`).
  2. `MathErrorCard.qml`: Explicit failure category badge colors (`#9a3412`, `#92400e`, `#991b1b`, `#7f1d1d`).
- **Invariant Scanner:** `tests/unit/test_theme_architecture_invariants.py` enforces AST/regex checks prohibiting unauthorized hex colors, raw `rgb`/`rgba` expressions, and direct named color assignments across all active QML files.

---

## 3. Implementation Status & Follow-Up Scope (THEME-01 Closeout)

1. `interfaces/desktop/theme.py`: Authoritative 41-token contract and Qt palette adapter verified.
2. `interfaces/desktop/controllers/theme_controller.py`: Exposes all 41 design tokens as Q_PROPERTY bindings on the GUI thread.
3. `interfaces/desktop/qml/`: All 30 active QML files audited; unauthorized RGBA and direct color literals replaced with semantic theme tokens; fallback literals eliminated from `Main.qml` and `Sidebar.qml`.
4. `Branding Reconciled`: Stale `PolpoT` references in `Main.qml`, `Sidebar.qml`, and `theme.py` updated to `Polpo`.
5. **Deferred to THEME-02:** Full interactive component state matrix (hover, active, focus-ring contrast across both palettes), WCAG accessibility contrast ratio verification, and dynamic OS color-scheme switching across headless / XDG desktop portal configurations are explicitly deferred to roadmap item `THEME-02`.

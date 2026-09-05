# Phase 10E Technical Debt & Deferred Remediation Log

This document records the technical debt, known limitations, and interaction defects identified during automated and manual verification of the Phase 10E Review Workspace implementation.

These items are deliberately cataloged here and are **NOT fixed in this checkpoint commit**. They will be addressed in designated future phases to preserve architectural isolation and avoid unverified scope creep.

---

## Item DEBT-10E-01: PDF Viewer Mouse Pan and Drag Interaction Lock

* **ID**: `DEBT-10E-01`
* **Severity**: `P1` (High)
* **Observed Behavior**:
  - In manual GUI usage, mouse drag / pan interaction inside the PDF viewer pane is either locked or moves only a minuscule amount per drag event.
  - Bounding box (bbox) manipulation handles do not respond smoothly or accurately under normal mouse interactions on the canvas.
* **Impact**:
  - Core document visual review is degraded because inspecting zoomed pages and fine-tuning visual region boundaries with the mouse is unreliable.
* **Proposed Future Phase**:
  - `Phase 10F` (Review Workspace Interaction & Tooling Hardening).
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

---

## Item DEBT-10E-02: Review Workspace SplitView / PDF Layout Instability on Resize

* **ID**: `DEBT-10E-02`
* **Severity**: `P1` (High)
* **Observed Behavior**:
  - Dragging or resizing the divider handle between the PDF viewer pane and the Markdown viewer pane in `ReviewWorkspaceView.qml` can cause the PDF view or image canvas to visually collapse, clip unexpectedly, or disappear entirely.
* **Impact**:
  - The side-by-side review workspace layout is not resilient to routine desktop window resizing or user-adjusted pane proportions.
* **Proposed Future Phase**:
  - `Phase 10F` (Review Workspace Interaction & Tooling Hardening).
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

---

## Item DEBT-10E-03: Editable Markdown & Visual Region Association Workflow

* **ID**: `DEBT-10E-03`
* **Severity**: `P1` (High)
* **Observed Behavior**:
  - Manual visual region creation (drawing a new bbox on the PDF) persists to the repository and extracts the image crop correctly.
  - However, the rendered Markdown view is currently read-only presentation; there is no UI mechanism or command flow for the user to insert, associate, or position the newly created region wiki-link (`![[region_id]]`) into the Markdown document text.
* **Impact**:
  - Manual review cannot complete the full interactive loop of PDF → manual region definition → insertion into Markdown text.
* **Proposed Future Phase**:
  - `Phase 10F` / `Phase 10G` (Interactive Document Editing & Region Association).
* **Future Work Requirements**:
  - Design an explicit, typed Markdown editing / region association command in the application layer.
  - Maintain stable region identity and occurrence resolution invariants.
  - Avoid ad-hoc string replacement or QML text manipulation hacks.
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

---

## Item DEBT-10E-04: Mathematical Expression (LaTeX / Math) Rendering in Markdown

* **ID**: `DEBT-10E-04`
* **Severity**: `P1` (High)
* **Observed Behavior**:
  - Standard Markdown (headings, lists, code blocks, tables, blockquotes, bold/italic, wiki-links, images) renders properly in the native QML renderer.
  - Mathematical formulas and LaTeX expressions (e.g., `$E = mc^2$` or `$$\int f(x) dx$$`) are rendered as raw text without formatted equation layout.
* **Impact**:
  - Technical, scientific, and mathematical PDF conversions containing formulas cannot be reviewed with full typographic fidelity.
* **Proposed Future Phase**:
  - `Phase 11` (Advanced Typography & Mathematical Formula Rendering).
* **Future Work Requirements**:
  - Architect a native offline math rendering pipeline (e.g., converting TeX math AST nodes to vector paths or cached raster crops) that respects the desktop-first, serverless architecture.
  - Do NOT reintroduce `WebView`, `WebEngine`, or browser-based rendering.
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

---

## Item DEBT-10E-05: Transient AI Provider SSL / Network Interruption Handling

* **ID**: `DEBT-10E-05`
* **Severity**: `P2` (Medium)
* **Observed Behavior**:
  - Remote AI provider requests (specifically Google AI SDK calls) occasionally fail due to transient outbound socket drops:
    `ssl.SSLError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1000)`
  - Rerunning the job from the UI succeeds once network connectivity stabilizes.
* **Impact**:
  - Long PDF conversion jobs may fail prematurely on transient socket hiccups without an automatic immediate socket retry.
* **Proposed Future Phase**:
  - `Phase 10F` (Provider Resilience & Transient Transport Policy).
* **Future Work Requirements**:
  - Review network retry / backoff policies in `RateLimitedAIExecutor` and provider adapters.
  - Ensure transient transport disconnections present clear user-facing guidance and do not transition jobs into non-resumable terminal states or duplicate page processing.
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

---

## Item DEBT-10E-06: Additional UI Styling, Theming & Layout Polish

* **ID**: `DEBT-10E-06`
* **Severity**: `P2` (Medium)
* **Observed Behavior**:
  - Minor visual alignment quirks in `ReviewWorkspaceView.qml`:
    - Pane header buttons and zoom controls have slight vertical misalignment on high-DPI displays.
    - Scrollbar synchronization between large document lists and image flow has minor deceleration jitter.
    - Dark/light mode theme switches require reloading the active Markdown view to refresh all custom syntax colors.
* **Impact**:
  - Cosmetic presentation inconsistency; does not impede functional correctness or data integrity.
* **Proposed Future Phase**:
  - `Phase 10F` (UI/UX Polish & Polish Sprint).
* **Fix Status**:
  - **NOT FIXED in this checkpoint commit.**

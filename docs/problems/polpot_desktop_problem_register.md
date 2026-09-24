# PolpoT Desktop Problem Register & Investigation Plan

**Status:** Draft — investigation register, not an implementation plan\
**Date:** 2026-09-23\
**Repository:** `amirreza-a2a/Polpo`\
**Base branch:** `main`

## 1. Purpose

This document is the single working register for the current Desktop UX, Markdown/Visual Region,
MathJax, QML, and rendering problems observed after the recent Phase 12 work.

The goal is deliberately **not** to start coding immediately.

For each problem we will:

1. record the observed symptom and the current evidence;
2. classify what is already proven versus what is only suspected;
3. select the appropriate investigation skill:
   - `grilling` for requirements, boundaries, invariants, and architecture decisions;
   - `wayfinder` for broader decomposition and dependency/order planning;
   - `research` for current external/third-party facts and documented platform behavior;
   - `diagnosing-bugs` for unexpected runtime failures and warnings;
   - `codebase-design` when the investigation establishes a concrete architectural shape;
4. produce an investigation result with explicit decisions and open questions;
5. split that result into one or more implementation-ready GitHub Issues;
6. implement only after the issue definition is stable.

No issue in this register should be treated as implementation-ready merely because the
symptom is visible.

---

## 2. Investigation Rules

### 2.1 Evidence states

Use one of these labels for every claim:

- **CONFIRMED** — directly demonstrated by source inspection, a reproducible test, or a deterministic runtime trace.
- **STRONG SUSPECT** — supported by source/runtime evidence but not yet isolated as the unique cause.
- **HYPOTHESIS** — plausible but not yet supported enough to drive implementation.
- **KNOWN DEBT** — already documented in repository planning/debt material, even if the current symptom is not fully reproduced.
- **EXTERNAL FACT TO VERIFY** — depends on current Qt/MathJax/Node/documentation behavior and must be checked with current sources before implementation.

### 2.2 Issue creation gate

An implementation Issue should normally contain:

- problem statement;
- scope and explicit non-goals;
- architectural boundary;
- exact acceptance criteria;
- tests/verification strategy;
- dependency ordering;
- migration/compatibility considerations where applicable.

Do not create a single mega-issue for unrelated symptoms merely because they appear in one screen.

### 2.3 One investigation can split into multiple Issues

A single investigation may result in:

- one implementation Issue;
- multiple independent implementation Issues;
- a follow-up debt Issue;
- or **no Issue** if the observed behavior is intentional or already covered.

---

# 3. Current Problem Register

## P01 — Application theme setting is persisted but not applied to the actual UI

**Category:** Theme / QML architecture\
**Priority:** High\
**Status:** CONFIRMED

### Observed symptom

The application currently behaves as a dark-only interface even though Settings exposes
`system`, `dark`, and `light`.

The user can select a theme setting, but the visual palette of the application does not
actually follow that setting. Math rendering also remains visually inappropriate for
light/dark readability because theme is not coordinated with the rendering layer.

### Current evidence

Confirmed from the current codebase:

- `core/entities/settings.py`
  - `AppSettings.theme` defaults to `system`.
  - valid values are `system`, `dark`, `light`.
- `interfaces/desktop/controllers/settings_controller.py`
  - exposes `theme` and persists it through `LocalSettingsService`.
- `Main.qml` has a hard-coded window color.
- `Card.qml` has hard-coded dark colors.
- `MarkdownNodeDelegate.qml` and related QML components contain many hard-coded text,
  border, background, and selection colors.
- Settings therefore represents a real product preference, but the presentation layer
  does not consume it as a centralized visual state.

### What is NOT yet decided

- whether `system` should follow the operating-system color scheme automatically and,
  if so, how that is detected cross-platform;
- whether Qt Quick Controls palette/theming should be used directly, or a Polpo-owned
  semantic design-token layer should sit above it;
- how MathJax SVG foreground/background behavior should respond to the active theme;
- whether theme switching should be live without application restart.

### Investigation

**Primary:** `grilling`\
**Then:** `codebase-design`\
**External:** `research` for current Qt 6 theming/palette behavior if the chosen design depends on it.

### Expected investigation output

A concrete theme contract defining:

- `system`, `light`, `dark`;
- persistence;
- startup resolution;
- live switching behavior;
- semantic color/token ownership;
- QML integration;
- MathJax integration;
- testing strategy.

### Candidate implementation split

Potentially:

- theme state / token system;
- QML migration from hard-coded colors;
- MathJax theme-aware rendering;
- characterization tests for live switching and restart persistence.

Do not open these as final Issues until the investigation establishes the boundary.

---

## P02 — Creating a manual visual region does not publish/insert it into Markdown

**Category:** Visual Region lifecycle / canonical publication\
**Priority:** BLOCKING for the manual-region workflow\
**Status:** READY FOR TICKETING\
**Investigation Record:** [`docs/problems/investigations/P02-manual-region-canonical-publication.md`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/docs/problems/investigations/P02-manual-region-canonical-publication.md)

### Observed symptom

A manually drawn region can be created in the PDF viewer and persisted, but the resulting
region is not appearing in the Markdown document.

### Confirmed root cause

The current desktop composition intentionally instantiates:

`DocumentViewerController(..., apply_review_service=None)`

The controller's `_trigger_async_apply()` returns immediately when
`apply_review_service` is absent.

Therefore the current flow is effectively:

```text
create manual region
    -> persist VisualRegion
    -> trigger async apply
    -> ApplyReviewService is absent
    -> return
    -> no canonical Markdown mutation/publication
```

This is consistent with the repository's Phase 10E architecture decision that the old direct
writer path was disabled while `DocumentPublicationService` became the canonical publication authority.
The legacy `ApplyReviewService` was quarantined in Phase 10E.3a because it bypassed the publication
gateway, bypassed isolated crop staging, and used legacy `![[...]]` wiki syntax. The replacement
orchestrator bridging visual regions to `CropArtifactStagingService` and `DocumentPublicationService`
was deferred (documented in `DEBT-10E-03`).

### Settled Architectural Decisions

1. **Sole Publication Authority:** [`DocumentPublicationService`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/application/services/document_publication_service.py) remains the sole authoritative gateway for canonical Markdown documents and atomic crop promotion.
2. **Dedicated Token Mutator:** Pure domain component [`core/markdown/visual_token_mutator.py`](file:///home/amirreza-a2a/DevelopPOlpo/PolpoT/core/markdown/visual_token_mutator.py) performs format-preserving string mutation using canonical CommonMark tokens `![alt](uri "polpo:region=...;occ=...")`. AST re-serialization is forbidden.
3. **Application Orchestrator:** Dedicated application service `VisualRegionPublicationService` coordinates image cropping via `IDocumentProcessor`, staging via `CropArtifactStagingService`, token mutation via `VisualTokenMutator`, and publication via `DocumentPublicationService`.
4. **OCC & Version Policy:** Bounded retry (3 attempts) on `StaleDocumentVersionError`. If the canonical document advances concurrently, the service re-reads the latest text, reapplies token mutation, and retries.
5. **Human Edits Preservation:** Format-preserving localized mutation leaves all surrounding comments, math formulas, and text untouched. External advances notify the dirty Markdown editor to run non-overlapping three-way merge (`diff3`).
6. **Retirement of Quarantined Service:** `ApplyReviewService` will be formally decommissioned and purged.

### Proposed Implementation Ticket Decomposition

- `TICK-P02A`: Pure Canonical Markdown Visual Token Mutator (`core/markdown/visual_token_mutator.py`)
- `TICK-P02B`: Visual Region Publication Application Service (`application/services/visual_region_publication_service.py`)
- `TICK-P02C`: Desktop Presentation Wiring & Quarantined Service Retirement (`interfaces/desktop/`)
- `TICK-P02D`: End-to-End Review Workspace Integration & Concurrency Tests (`tests/integration/`)

---

## P03 — Visual Region registration/interaction has multiple states/types but the current UX is unclear or broken

**Category:** Visual Region UX / domain semantics\
**Priority:** High\
**Status:** STRONG SUSPECT

### Observed symptom

The region workflow exposes several concepts and states, and the current registration flow feels
inconsistent/broken to the user.

The repository domain currently separates concepts such as:

- origin (`AI_DETECTED`, `USER_MANUAL`);
- review status (`UNREVIEWED`, `MODIFIED`, `MANUAL`, `REJECTED`, `ACCEPTED`);
- sync status (`PENDING_INITIAL_CROP`, `SYNCED`, `DIRTY_RECROP_REQUIRED`, `SYNC_FAILED`).

The user-facing interaction model does not yet make those distinctions sufficiently coherent.

### What requires investigation

We need to determine which of these are:

- actual domain state;
- lifecycle metadata;
- UI display status;
- transient interaction state;
- redundant dimensions that should not all be exposed to the user.

The current `DocumentViewerController` also has presentation/editor states such as:

- `pan_select`;
- `create_region`;
- `idle`;
- `selected`;
- `dragging`;
- `resizing`;
- `creating`.

These must not be conflated with persisted domain state.

### Investigation

**Primary:** `grilling`\
**Then:** `wayfinder`\
**Then:** `codebase-design`

### Expected investigation output

A state/lifecycle matrix that explicitly maps:

```text
Domain state
    -> allowed operations
    -> UI affordances
    -> artifact/sync effects
    -> Markdown effects
```

and separates persisted state from transient interaction state.

### Candidate implementation split

Possibly:

- region lifecycle/state correction;
- visual interaction UX correction;
- persistence/publication synchronization.

---

## P04 — Region context menu is missing and needs an extensible action model

**Category:** Desktop UX / extensibility\
**Priority:** High\
**Status:** CONFIRMED

### Observed symptom

The user needs a right-click context menu on a visual region/image with actions such as:

- copy path;
- copy Markdown token;
- insert into Markdown;
- copy region ID;
- copy bounding box;
- delete/reject/reset actions as appropriate.

The menu also needs to support future capabilities such as region/image to LaTeX/TikZ
conversion without repeatedly redesigning the QML menu.

### Current repository state

No reusable region context-action registry/framework was identified.

The correct boundary should not put application logic directly in QML.

### Investigation

**Primary:** `wayfinder`\
**Then:** `grilling`\
**Then:** `codebase-design`

### Expected investigation output

An extensible action contract with:

- action identifier;
- label;
- availability/capability predicate;
- enable/disable reason if needed;
- controller/application command;
- grouping/order;
- future provider/capability registration;
- QML rendering boundary.

The first implementation should be small. Future actions must be extension points, not
placeholder UI.

### Candidate implementation split

Potentially:

1. action registry/application command model;
2. QML context menu;
3. initial region actions;
4. Markdown insertion command integration.

---

## P05 — `QQuickPixmap: connectFinished() called when not loading.` warning flood

**Category:** Qt/QML image loading\
**Priority:** High\
**Status:** STRONG SUSPECT

### Observed symptom

The desktop application repeatedly emits:

`QQuickPixmap: connectFinished() called when not loading.`

The warning appears especially around document/math/image rendering.

### Current suspected contributors

Several code paths are plausible:

1. `MarkdownInlineFlow.qml`
   - only considers `segmentType === "image"` as an image-bearing flow;
   - inline math segments can become RichText `<img src="image://math/...">`;
   - this means MathJax image loading may travel through RichText rather than the native
     QML `Image` path.

2. `MarkdownNodeDelegate.qml`
   - block math uses a QML `Image`;
   - its `sourceSize.width` is derived from `implicitWidth`, which is itself affected by
     the source, creating a potentially unstable sizing dependency.

3. `MathImageProvider.requestImage(...)`
   - currently writes the rendered target dimensions into the provider `size` output;
   - this should be checked against the actual `QQuickImageProvider` contract and the
     intended logical/original image-size semantics.

4. Cached/provider-backed image requests may be triggered more than once during QML
   layout and reload cycles.

### Investigation

**Primary:** `diagnosing-bugs`\
**Then:** `research` against current Qt 6 documentation/source behavior\
**Then:** runtime characterization and focused test.

### Expected investigation output

A reproducible causal chain, not merely a list of plausible suspects.

The final investigation must answer:

- which image source actually emits the warning;
- whether the warning is caused by invalid lifecycle sequencing, repeated requests,
  sizing churn, cache behavior, or a combination;
- the minimum code change required;
- how to prove the warning is gone without hiding legitimate Qt diagnostics.

### Candidate implementation split

Only after diagnosis:

- inline math image path;
- block math sizing;
- image-provider contract fix;
- focused warning-free QML/runtime test.

---

## P06 — `qt.svg: Skipping a nested svg element...` warning

**Category:** Qt SVG / MathJax rendering\
**Priority:** High\
**Status:** STRONG SUSPECT

### Observed symptom

Repeated warning:

`qt.svg: Skipping a nested svg element, because SVG Document must not contain nested svg elements in Svg Tiny 1.2`

### Current evidence

The current desktop MathJax provider uses `QSvgRenderer` to rasterize cached MathJax SVG.

The repository search identified `MathImageProvider` as the explicit `QSvgRenderer` usage.

However, the log alone does **not** prove that every warning originates from MathJax output.

### Investigation requirements

Instrument the rendering path sufficiently to determine:

- the formula hash and source SVG for each warning-producing render;
- whether descendant `<svg>` elements actually occur in the supplied document;
- whether the nested SVG is produced by MathJax itself, by another image source, or by
  a wrapper/normalization step;
- whether it affects visual correctness or is merely rejected content.

### Investigation

**Primary:** `diagnosing-bugs`\
**Then:** `research` against current Qt SVG and MathJax documentation/source\
**Then:** runtime characterization.

### Expected investigation output

A source-attribution result and, if the MathJax SVG is responsible, a documented normalization
or renderer strategy with tests.

Do not "fix" this warning by globally suppressing Qt SVG diagnostics.

---

## P07 — MathJax supervisor can block indefinitely on a hung worker

**Category:** MathJax reliability / process supervision\
**Priority:** BLOCKING\
**Status:** CONFIRMED by code review

### Observed failure mode

`MathJaxProcessSupervisor._call_rpc_locked()` performs blocking `stdout.readline()`
while the supervisor lock is held, with no request timeout.

If the Node worker hangs:

- the RPC call can hang indefinitely;
- the supervisor lock remains held;
- shutdown cannot acquire the lock;
- the application can become stuck during math rendering or exit.

### Investigation / implementation direction

This is already strong enough to be treated as an implementation problem, but the exact
recovery contract should still be decided before coding.

Required design questions:

- per-request timeout;
- process termination behavior;
- restart behavior;
- whether the pending request gets one automatic retry;
- shutdown semantics;
- error type exposed to the application;
- test strategy for an intentionally hung worker.

### Investigation

**Primary:** `grilling`\
**Then:** `tdd`\
**External:** `research` only where current Node/Python subprocess semantics need confirmation.

### Expected Issue shape

An implementation-ready reliability Issue should require:

- bounded RPC wait;
- no lock-held indefinite blocking;
- deterministic worker termination/restart;
- structured `MathRenderError`;
- regression test with a hung subprocess.

---

## P08 — Math rendering failure can silently turn into a missing formula

**Category:** Math rendering / user-visible correctness\
**Priority:** BLOCKING\
**Status:** CONFIRMED by source inspection

### Current behavior

`MarkdownViewerService.render_text()` pre-renders math requests.

When a `MathRenderError` occurs, the code currently continues rather than producing an explicit
user-visible error representation.

The QML `MathImageProvider` cache miss path returns a 1x1 transparent image.

Combined, this can produce:

```text
MathJax failure
    -> error ignored
    -> image URL still emitted
    -> cache miss / transparent image
    -> formula visually disappears
```

This is a correctness problem, not merely a cosmetic fallback.

### Investigation

**Primary:** `grilling`\
**Then:** `codebase-design`

### Expected investigation output

A defined user-visible fallback contract, for example:

- preserve original TeX as readable text;
- render an explicit "math unavailable" marker;
- expose a structured rendering-error state;
- or use another deterministic fallback.

The chosen behavior must be consistent between block and inline math.

### Candidate Issue

One focused Issue for "Math rendering failure is never silently invisible", with tests
covering cache miss, worker failure, invalid TeX, and UI fallback.

---

## P09 — Runtime Node version is not enforced as exactly the bundled version

**Category:** Runtime reproducibility\
**Priority:** Medium\
**Status:** STRONG SUSPECT / EXTERNAL FACT TO VERIFY

### Current repository facts

The MathJax runtime is designed around a specific Node release, and
`resources/runtimes.json` records the expected runtime metadata.

The supervisor can resolve Node from environment/configuration, bundled runtime,
or system PATH.

The current runtime resolution path does not appear to enforce that the executable
actually reports the expected version before use.

### Why this matters

A different system Node version may still launch successfully while producing behavior
that differs from the tested runtime.

### Investigation

**Primary:** `research` for supported/version-checking behavior and packaging constraints\
**Then:** `codebase-design`

### Expected investigation output

A clear runtime policy:

- exact version required versus minimum compatible version;
- dev-mode system Node policy;
- bundled-runtime priority;
- diagnostic behavior when version mismatch occurs;
- CI test expectations.

Do not hard-code a version rule until the packaging/runtime contract is explicit.

---

## P10 — MathJax restart limiter counts initial startup as a restart

**Category:** Process supervision / reliability\
**Priority:** Medium\
**Status:** STRONG SUSPECT

### Current observation

`MAX_RESTARTS_PER_MINUTE = 3`.

The current timestamp accounting appears to include the initial worker startup in the
same rolling restart timestamp collection used by the restart limiter.

That can effectively reduce the number of genuine restarts allowed after initial startup.

### Investigation

**Primary:** `tdd`\
**Then:** `code-review`

### Expected investigation output

A precise invariant such as:

```text
initial start != restart
```

and deterministic tests for:

- initial startup;
- 1st/2nd/3rd restart;
- rolling-window expiry;
- restart refusal after the limit;
- recovery after the window moves.

---

## P11 — Bundled runtime CI coverage is incomplete relative to the runtime contract

**Category:** CI / packaging\
**Priority:** Medium\
**Status:** CONFIRMED gap in current verification scope

### Current evidence

Recent CI verifies Linux and Windows application/test execution.

The bundled runtime contract contains multiple platform entries, but actual bundled
Node binaries/runtime smoke execution is not comprehensively validated across all target
platforms.

### Investigation

**Primary:** `wayfinder`\
**Then:** `research` for current GitHub Actions runner/platform availability and supported
packaging strategy.

### Expected investigation output

A support matrix:

| Platform | Runtime artifact | Hash verification | Executable smoke test | Math render test |
|---|---|---:|---:|---:|
| Linux | bundled Node | ? | ? | ? |
| Windows | bundled Node | ? | ? | ? |
| macOS | bundled Node | ? | ? | ? |

The investigation must distinguish "artifact metadata exists" from "runtime is actually
executed in CI".

---

## P12 — Supervisor cleanup uses overly broad exception swallowing

**Category:** Reliability / maintainability\
**Priority:** Medium / non-blocking until investigation proves impact\
**Status:** CONFIRMED code-quality finding

### Current observation

The supervisor contains cleanup paths using broad `except Exception: pass` patterns.

This can hide shutdown/recovery failures that should remain observable.

### Investigation

**Primary:** `code-review`

### Expected investigation output

Determine which exceptions are expected during:

- process termination;
- pipe closure;
- repeated shutdown;
- failed restart.

Replace blanket swallowing with narrow exception handling and, where appropriate,
debug-level diagnostics.

This should not be merged into a larger MathJax behavior change unless the same tests
naturally cover it.

---

## P13 — Markdown editor / visual-region integration boundary is incomplete

**Category:** Markdown editing / Review Workspace\
**Priority:** High\
**Status:** KNOWN DEBT

### Existing repository planning

The repository already contains Phase 10F.1 planning for:

- canonical Markdown source editing;
- optimistic versioning;
- preserving human-authored Markdown;
- preview/edit tabs;
- conflict tracking;
- native `PlainText` editor behavior.

The current manual-region problem is directly connected to this boundary because a region
must be insertable without destroying unrelated human edits.

### Investigation need

Before creating a new editor ticket, reconcile the existing Phase 10F plan with the current
post-Phase-12 architecture:

- what parts are already implemented;
- what parts are stale;
- what parts conflict with the current `DocumentPublicationService` authority;
- where token mutation belongs;
- how the editor, region publication, and preview synchronize.

### Investigation

**Primary:** `wayfinder`\
**Then:** `grilling`\
**Then:** `codebase-design`

### Expected investigation output

A current-state/future-state dependency graph for:

```text
Visual Region
    <-> Markdown token
    <-> Markdown Editor
    <-> Markdown Preview
    <-> DocumentPublicationService
    <-> document version/OCC
```

Only after this reconciliation should editor-related Issues be finalized.

---

## P14 — No deterministic warning-free desktop/QML characterization suite for the affected workflow

**Category:** Runtime verification / regression prevention\
**Priority:** High\
**Status:** KNOWN GAP

### Observed problem

The application can launch and render a document while producing large quantities of Qt
warnings, but there is no single focused characterization workflow that proves:

- startup;
- opening a document containing math and images;
- creating a manual region;
- rendering the region;
- Markdown update;
- theme switching;
- context-menu interaction;
- worker shutdown;

without the known warning flood or silent rendering failure.

### Investigation

**Primary:** `wayfinder`

This depends on the outcomes of P01, P02, P04, P05, and P06, so it should be treated as
a regression/verification workstream rather than an implementation starting point.

### Expected investigation output

A practical runtime verification matrix with:

- launch command;
- deterministic fixture document(s);
- captured stderr/log handling;
- assertions for fatal/runtime errors;
- warnings that are allowed versus warnings that are prohibited;
- shutdown verification.

---

# 4. Cross-Problem Dependency Map

The problems are not independent.

```text
P01 Theme
  └── affects P05/P06 rendering and all QML views

P02 Manual Region Publication
  ├── depends on current publication authority
  ├── overlaps P13 Markdown Editor
  └── should feed P04 "Insert into Markdown"

P03 Region Lifecycle
  └── constrains P04 context actions and P02 publication

P04 Context Menu
  └── depends on P03 state/action availability
  └── depends on P02/P13 for Markdown insertion

P05 QQuickPixmap
  └── depends on math/image rendering architecture
  └── feeds P14 regression suite

P06 Nested SVG
  └── depends on MathJax/QSvg rendering architecture
  └── feeds P14 regression suite

P07 MathJax Hang
  └── constrains safe math rendering in P05/P06
  └── feeds P14 shutdown verification

P08 Math Failure Fallback
  └── constrains P05 and P14

P09 Runtime Version Policy
  └── constrains P07/P08 and bundled runtime validation

P10 Restart Accounting
  └── belongs with P07 supervisor reliability

P11 Bundled Runtime CI
  └── depends on P09

P12 Cleanup Exceptions
  └── can be folded into the supervisor reliability Issue if scope remains focused

P13 Markdown Editor
  └── must be reconciled with P02 before region insertion is implemented

P14 Characterization
  └── depends on resolved behavior from the relevant preceding problems
```

---

# 5. Recommended Investigation Order

Do **not** begin implementation in ticket-number order.

The proposed investigation sequence is:

### Investigation A — Region + Markdown architecture

`P02 -> P03 -> P13 -> P04`

Reason: these features form one user workflow. The canonical publication and state model must
be settled before designing context actions around it.

### Investigation B — MathJax/runtime reliability

`P07 -> P08 -> P09 -> P10 -> P11 -> P12`

Reason: worker supervision and failure semantics should be stable before packaging/CI and UI
fallback behavior are frozen.

### Investigation C — Qt rendering warnings

`P05 -> P06`

Run `diagnosing-bugs` first, then current Qt/MathJax research, then targeted runtime
characterization. Avoid changing multiple image-loading layers simultaneously before the
source of the warning is isolated.

### Investigation D — Theme system

`P01`

This can proceed in parallel with investigations A/B/C conceptually, but its implementation
should consume stable rendering interfaces from the math work rather than inventing a
second color path.

### Investigation E — Regression verification

`P14`

Finalize after the major behavior contracts are settled, then make it the umbrella
characterization/regression gate for the Desktop workflow.

---

# 6. Skill Routing Matrix

| Problem | First | Second | External research? | Main deliverable |
|---|---|---|---|---|
| P01 Theme | grilling | codebase-design | Possibly | Theme contract |
| P02 Manual region publication | grilling | codebase-design | Usually no | Canonical region publication flow |
| P03 Region states | grilling | wayfinder | No | State/lifecycle matrix |
| P04 Context menu | wayfinder | grilling | No | Action framework contract |
| P05 QQuickPixmap | diagnosing-bugs | research | Yes | Root-cause report |
| P06 Nested SVG | diagnosing-bugs | research | Yes | Source attribution + fix contract |
| P07 RPC hang | grilling | tdd | Possibly | Timeout/recovery contract |
| P08 Silent math failure | grilling | codebase-design | No | Fallback contract |
| P09 Node version | research | codebase-design | Yes | Runtime version policy |
| P10 Restart accounting | tdd | code-review | No | Restart invariant/tests |
| P11 Bundled runtime CI | wayfinder | research | Yes | Platform verification matrix |
| P12 Broad exception handling | code-review | — | No | Narrow cleanup contract |
| P13 Markdown editor boundary | wayfinder | grilling | No | Current architecture map |
| P14 Runtime characterization | wayfinder | — | Possibly | Verification matrix |

---

# 7. What Counts as a Successful Investigation

An investigation is complete only when it answers:

1. **What exactly is wrong?**
2. **What proves it?**
3. **What is still uncertain?**
4. **Which architectural boundary owns the fix?**
5. **What existing implementation must be reused or refactored?**
6. **What behavior must not regress?**
7. **What tests prove the fix?**
8. **Can the work be isolated into one focused Issue?**
9. **What other Issues must precede it?**
10. **What should explicitly remain out of scope?**

---

# 8. Issue Readiness Template

When an investigation produces an implementation-ready ticket, use this structure:

```markdown
# TICK-XXX — <focused title>

## Problem
<observable behavior and user impact>

## Root Cause
<confirmed technical cause>

## Scope
<exact files/modules or architectural boundary>

## Required Behavior
<precise post-fix contract>

## Non-Goals
<what must remain untouched>

## Acceptance Criteria
- [ ] ...
- [ ] ...
- [ ] ...

## Tests
- [ ] Unit:
- [ ] Integration:
- [ ] Runtime/QML:
- [ ] Architecture invariant:

## Dependencies
- Depends on: ...
- Unblocks: ...

## Implementation Constraints
- Must preserve Clean Architecture.
- Must preserve canonical persistence/publication authority.
- Must not introduce server dependencies.
- Must keep technical comments/documentation in English.
```

---

# 9. Explicit Non-Goals for This Register

This document does **not** authorize:

- reintroducing `ApplyReviewService` into the desktop composition blindly;
- suppressing Qt warnings globally;
- hiding rendering failures with transparent placeholders;
- introducing a backend/server to simplify Desktop integration;
- adding speculative context-menu actions with no implementation contract;
- replacing the existing architecture merely to make one symptom disappear;
- creating GitHub Issues before the corresponding investigation has enough evidence.

---

# 10. Current State Summary

At the time this register was created:

### Confirmed functional problems

- Theme preference is stored but not applied to the visual UI.
- Manual region creation stops before canonical Markdown publication/insertion.
- Math rendering failure can silently produce an invisible formula.
- MathJax worker RPC can block indefinitely.
- Region context menu is not yet implemented.

### Confirmed / strong runtime problems

- Qt `QQuickPixmap` warning flood is reproducible, but its unique source still needs diagnosis.
- Qt nested-SVG warning is reproducible, but its exact input/source attribution still needs diagnosis.
- Runtime Node version enforcement is not yet a stable contract.
- Restart accounting needs a precise invariant/test.

### Known architectural/verification gaps

- Visual Region lifecycle semantics need clarification at the UX/domain boundary.
- Existing Markdown editor planning must be reconciled with the current canonical publication architecture.
- Bundled runtime CI coverage needs a platform/runtime matrix.
- A focused warning-free end-to-end Desktop characterization suite is missing.

---

# 11. Next Working Step

Start with **Investigation A: Region + Markdown architecture**:

`P02 -> P03 -> P13 -> P04`

The first investigation should be performed without implementation changes. Its output should
be a short decision record that determines the canonical region-to-Markdown workflow and the
boundaries for the context-action framework.

After that decision record, create only the Issues that are actually implementation-ready.

# Agent Skills Routing Guide

A lightweight project-level guide for selecting and sequencing the agent skills installed on the agent for PolpoT development.

> [!NOTE]
> The skills themselves are installed in the agent environment (`~/.agents/skills/` or `~/.gemini/skills/`) and **must not** be copied, vendored, or reimplemented inside this repository. This document serves solely as a practical routing and sequencing guide for agents working on this codebase.

---

## 1. Purpose

When working on PolpoT, agents must choose the appropriate skill rather than applying ad-hoc approaches. This guide answers:

> **"Given the type of work I am doing, which installed skill should I call, when should I call it, and what skill usually comes next?"**

This is a routing guide, not a skill registry or an internal implementation reference.

---

## 2. Skill Routing Matrix

| Skill | Use it when... | Example from our workflow |
| :--- | :--- | :--- |
| `grilling` / `grill-me` | Requirements, constraints, or design decisions are unclear; structured, relentless questioning is needed to eliminate ambiguity. | Clarifying UX behavior for visual region cropping or deciding whether PDF preview zooming belongs in QML or a controller before writing code. |
| `grill-with-docs` | Clarification and stress-testing of requirements are needed, and the resulting decisions must become durable project documentation (e.g., `CONTEXT.md` glossary or ADRs under `docs/adr/`). | Interviewing through SQLite concurrency constraints (WAL mode, worker-local connections) and establishing durable ADR records for desktop persistence. |
| `to-tickets` | Scope, architecture, and requirements are fully understood, and the work needs to be decomposed into concrete, tracer-bullet tickets with explicit dependency/blocking edges. | Splitting the multi-step Phase 8 desktop migration (ingestion pipeline, database repository, background worker, QML bridge) into ordered, unblocked tickets. |
| `wayfinder` | Undertaking large, foggy, or multi-session work where the high-level path or destination itself is uncertain, requiring a shared map of decision tickets. | Charting the multi-session roadmap for replacing legacy Telegram transport flows with an embedded, offline-first PySide6 desktop architecture. |
| `implement` | Executing a well-defined task or ticket end-to-end using the project's standard implementation and review workflow. | Implementing the atomic job claim method (`claim_next_pending`) on `SqliteJobRepository` from a fully specified ticket. |
| `tdd` | Implementing a bounded technical task or unit test-first, driving red-green-refactor cycles for high-reliability components. | Writing unit tests first for `CredentialRef` immutability, coordinate normalization in `core/geometry/`, or prompt template formatting. |
| `code-review` | Independently reviewing an existing implementation or git diff against repository standards and ticket specifications without making inline fixes. | Performing an independent two-axis review (Standards + Spec) on a SQLite schema migration or threading PR before merge. |
| `diagnosing-bugs` | Investigating an unexpected failure, flaky test, or performance regression before jumping to speculative fixes; requires establishing a tight feedback loop. | Tracking down intermittent `database is locked` errors during concurrent job processing or identifying GUI thread blocking during PDF rasterization. |
| `triage` | Evaluating, categorizing, verifying, and prioritizing raw incoming issue reports or requests to produce agent-ready briefs. | Triaging reported issues in the GitHub repository, assigning canonical labels (`needs-triage`, `ready-for-agent`, `needs-info`), and drafting repro steps. |
| `retro` | Conducting a structured retrospective after completing a substantial milestone, major feature, or architectural phase. | Reviewing architectural friction, concurrency lessons, and agent efficiency following the completion of Phase 8 desktop persistence. |
| `domain-modeling` | Clarifying, unifying, or refining domain concepts, ubiquitous language, and entity boundaries across layers. | Defining precise boundaries between `Job`, `ApiSlot`, `CredentialRef`, and `VisualRegion` to avoid god-object contamination. |
| `improve-codebase-architecture` | Surveying existing subsystems for architectural debt, layer leakage, or shallow abstractions, and identifying deepening opportunities. | Scanning `infrastructure/persistence/` or legacy `handlers/` to identify direct SQL leaks or presentation coupling violating clean architecture rules. |
| `codebase-design` | Designing a brand-new subsystem, module interface, or clean architectural seam from scratch. | Designing a new pluggable AI provider fallback pipeline or an asynchronous document extraction engine behind narrow interfaces. |
| `research` | Investigating external facts, SDK capabilities, third-party libraries, or platform constraints against primary sources via a background agent. | Investigating Google GenAI Python SDK streaming capabilities, PySide6 QML custom paint item limitations, or OS keyring Linux fallbacks. |
| `resolving-merge-conflicts` | Reconciling in-progress Git merge or rebase conflicts hunk by hunk based on original commit intent rather than blind guessing. | Safely resolving merge conflicts across parallel branches modifying database migrations or application event definitions. |
| `handoff` | Packaging the state of an ongoing investigation or implementation into a structured document when transferring work across sessions or harnesses. | Writing a concise handoff document with context, open questions, and next steps at the boundary of a complex refactoring phase. |

---

## 3. Important Distinctions

Avoid these common confusions when selecting skills:

### `diagnosing-bugs` vs `code-review`
* **`diagnosing-bugs`**: "Something is broken or behaving unexpectedly; find out why." Focuses on isolation, establishing a minimal reproducing command, and uncovering root causes.
* **`code-review`**: "This implementation exists; determine whether it meets spec and coding standards." An evaluative audit of a diff against requirements, not an active debugging investigation.

### `wayfinder` vs `research`
* **`wayfinder`**: Resolves uncertainty about the **project roadmap and solution space**. Maps out unknown dependencies, creates decision tickets, and clears foggy multi-session architectural efforts.
* **`research`**: Resolves uncertainty about **technical or external facts**. Queries primary sources, inspects SDK APIs, or compares library behaviors in a background agent.

### `codebase-design` vs `improve-codebase-architecture`
* **`codebase-design`**: Focuses on **designing something new**—defining interfaces, depth, seams, and boundaries for a new module or capability.
* **`improve-codebase-architecture`**: Focuses on **assessing and refactoring what already exists**—identifying structural decay, shallow interfaces, or layer violations in existing code.

### `grilling` vs `research`
* **`grilling`**: Resolves uncertainty about **intent, requirements, constraints, or human decisions**. Interviews the user to force choices and uncover hidden assumptions.
* **`research`**: Resolves uncertainty about **verifiable facts, external systems, or technical documentation**. Queries documentation, APIs, and benchmarks.

### `implement` vs `tdd`
* **`implement`**: The **full ticket execution workflow** (interprets ticket, coordinates TDD, runs linting, conducts two-axis review, closes ticket).
* **`tdd`**: The **focused tactical engine** for building a concrete behavior test-first (red-green-refactor) for a tightly bounded component.

---

## 4. Workflow Patterns

Compose skills into standard sequences for common development tasks:

### Known Feature
```text
Requirements / Spec → to-tickets (if multi-step) → implement → code-review
```

### Unknown / Large Feature
```text
wayfinder → research (if needed) → grilling (if needed) → codebase-design → plan → implement → code-review
```

### Bug / Regression
```text
diagnosing-bugs → plan → implement (with regression test) → code-review → runtime verification
```

### Architecture Decision
```text
grilling → grill-with-docs → architecture decision (ADR) → implementation plan
```

### Existing Architecture Assessment
```text
improve-codebase-architecture → codebase-design (if redesign is required) → plan → implement
```

### Backlog Management
```text
triage → ready-for-agent → implement
```

### Milestone Completion
```text
implementation → verification → retro
```

### Session Boundary / Handoff
```text
current work → handoff → next session
```

---

## 5. Project-Level Selection Rules

1. **Do not implement unresolved requirements or architecture**: If requirements or boundaries are ambiguous, run `grilling` or `grill-with-docs` before writing implementation code.
2. **Diagnose unknown failures before proposing speculative fixes**: When encountering bugs or test failures, run `diagnosing-bugs` to reproduce and understand root causes rather than applying trial-and-error patches.
3. **Keep code review independent for high-risk / architectural changes**: Do not self-approve complex persistence, concurrency, or security changes without running `code-review`.
4. **Use the smallest sufficient skill**: Prefer focused skills (`tdd` for a single isolated class) over heavyweight workflows when scope is already narrow.
5. **Do not use a skill to silently expand phase scope**: Maintain strict boundaries defined in the active phase or ticket.
6. **Automated test passes do not automatically prove correct GUI/runtime behavior**: In a desktop Qt application, verify signal delivery, thread boundaries, and GUI responsiveness beyond unit test assertions.
7. **Important settled architecture/domain decisions must become durable documentation**: Record architectural decisions in ADRs (`docs/adr/`) and domain terms in `CONTEXT.md`.

---

## 6. Compact Decision Guide

When unsure which skill to call, match your immediate question:

* *"What do we actually want?"* → **`grilling`** / **`grill-me`**
* *"Where should we go on this large effort?"* → **`wayfinder`**
* *"What does the technology or external API support?"* → **`research`**
* *"How should this new subsystem be structured?"* → **`codebase-design`**
* *"Is our existing architecture sound and clean?"* → **`improve-codebase-architecture`**
* *"Why is this failing or broken?"* → **`diagnosing-bugs`**
* *"How do I execute this well-defined task?"* → **`implement`**
* *"Is this implementation correct and standard-compliant?"* → **`code-review`**
* *"Which backlog issue should be worked on next?"* → **`triage`**
* *"What should become durable project knowledge?"* → **`grill-with-docs`** / **`domain-modeling`**
* *"How do I transfer this in-flight work to another agent?"* → **`handoff`**

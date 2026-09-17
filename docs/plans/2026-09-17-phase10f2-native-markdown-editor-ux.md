# Phase 10F.2 — Native Markdown Editor UX: Syntax Styling, Search/Replace & Metrics

> **Status:** Approved Specification & Implementation Plan  
> **Baseline:** `fccc8ec fix(phase10f): fix markdown editor qml save activation`  
> **Target Milestone:** Phase 10F.2

---

## 1. Executive Summary & Scope Decision

Phase 10F.1 established the native raw Markdown editor foundation in PolpoT with lossless round-trip text editing, dirty tracking, asynchronous OCC persistence via `MarkdownEditorService`, and external conflict detection in the Review Workspace.

Phase 10F.2 elevates the native editor experience from a bare plain text box into a responsive, syntax-highlighted, searchable document editor.

### In Scope
1. **Markdown Syntax Highlighting:**
   - Multi-line block state machine for fenced code blocks (```) and HTML comments (`<!-- ... -->`).
   - Strict rule precedence: fenced code > HTML comments > headings (H1–H6) > blockquotes > thematic breaks > inline code spans > visual region tokens > links > bold > italic.
   - Code blocks suppress all inner Markdown styling.
   - Distinct emerald/amber badge formatting for visual region tokens (`![[crop_...|region_id=...]]`).
   - Attached to the underlying `QTextDocument` of the QML `TextArea`.
2. **Search & Replace Engine:**
   - Single canonical coordinate domain: Qt UTF-16 code units via `QTextDocument.find()`.
   - Zero coordinate skew across emojis (non-BMP characters), Arabic/Persian glyphs, combining marks, or mixed LTR/RTL text.
   - Case-sensitive and whole-word toggles.
   - Forward and backward navigation with wrap-around.
   - Match counter (`Match X of Y`).
   - Replace Current: strictly verifies that the selection matches the active search result before mutating text.
   - Replace All: executed via `QTextCursor.beginEditBlock()` and `endEditBlock()` in reverse order, grouping all replacements into a single user-level undo action without resetting the buffer.
3. **Cursor Position & Document Metrics:**
   - `cursorLine`: 1-indexed document line (`QTextCursor.blockNumber() + 1`).
   - `cursorColumn`: 1-indexed UTF-16 code unit offset (`QTextCursor.positionInBlock() + 1`).
   - `characterCount`: Unicode code point count (`len(source_text)`).
   - `wordCount`: Unicode whitespace-delimited word count (`len(re.findall(r"\S+", source_text))`).
4. **Editor UI Modularization:**
   - `MarkdownEditorSearchBar.qml`: Collapsible find/replace drawer (Ctrl+F, Ctrl+H, Esc, Enter, Shift+Enter).
   - `MarkdownEditorStatusBar.qml`: Docked bottom status bar displaying cursor and document metrics.

### Explicitly Deferred
- **Live Dual-Pane Synchronized Preview:** Deferred to Phase 10F.3 (requires split-pane workspace redesign and draft-preview state modeling).
- **Source ↔ Preview Cursor & Scroll Sync:** Deferred to Phase 10F.4.
- **Regex Search & Replace:** Omitted to prevent accidental destructive replacement of Markdown syntax.
- **Three-Way Merge / Diff View:** Deferred to Phase 10F.5.
- **AST-based Structural Editing:** Prohibited per `AGENTS.md` (PlainText editing remains canonical).

---

## 2. Architecture & Layer Boundaries

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                            PRESENTATION LAYER                               │
│                                                                             │
│  MarkdownEditorPane.qml                                                     │
│  ├── Top Toolbar (Save, Discard, Version Badge, Modified Indicator)         │
│  ├── MarkdownEditorSearchBar.qml (Docked Find/Replace Drawer)               │
│  ├── ScrollView                                                             │
│  │     └── TextArea (id: sourceTextArea, textFormat: TextEdit.PlainText)     │
│  │           ├── attached: MarkdownSyntaxHighlighter(doc)                   │
│  │           ├── onTextChanged: controller.setSourceText(text)              │
│  │           └── onCursorPositionChanged: controller.updateCursorPos(pos)   │
│  └── MarkdownEditorStatusBar.qml (Ln X, Col Y • Words • Chars • UTF-8)      │
│                                                                             │
│                                  │                                          │
│     Actions (findNext, etc.)     │  Signals (matchSelected, stateChanged)   │
│                                  ▼                                          │
│  MarkdownEditorController                                                   │
│  ├── Buffer & OCC State: sourceText, isDirty, activeVersion, hasConflict    │
│  ├── Search Engine: computes UTF-16 match ranges via QTextDocument.find()   │
│  ├── Cursor Tracker: computes Ln/Col via QTextCursor(textDocument)          │
│  └── Highlighter Bridge: manages MarkdownSyntaxHighlighter lifecycle        │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ (Unchanged from Phase 10F.1)
┌─────────────────────────────────────────────────────────────────────────────┐
│                             APPLICATION LAYER                               │
│                                                                             │
│  MarkdownEditorService                                                      │
│  ├── load_source_text(job_id)                                               │
│  └── commit_source_text(job_id, raw_text, base_version)                     │
│        (2-step OCC with BEGIN IMMEDIATE transactions)                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Architectural Invariants:
1. **Desktop-First & Serverless:** 100% local, no backend, no remote dependencies (`AGENTS.md` Rule 2 & 3).
2. **Canonical Persistence Untouched:** `job.output_path` remains the sole active canonical artifact. `MarkdownEditorService` and `ApplyReviewService` OCC logic are completely unchanged.
3. **Native Editing Integrity:** All text edits (typing, single replace, replace all) execute directly against the `QTextDocument` / `TextArea` buffer, preserving native undo/redo, cursor positioning, and selection.
4. **Presentation Boundary:** Highlighting, search, replace, and metrics are presentation concerns. Zero direct database or vendor SDK access (`AGENTS.md` Rule 6.3).

---

## 3. Search Coordinate Model & Text Semantics

### The UTF-16 Code-Unit Domain
In Qt Quick Controls 2, `TextArea.cursorPosition`, `selectionStart`, `selectionEnd`, and `select(start, end)` index characters in **UTF-16 code units**. In contrast, Python `str` indices represent Unicode **code points**. Non-BMP characters (e.g. `🐙`, `😀`) occupy 1 Python code point but 2 UTF-16 code units (surrogate pair).

To guarantee 100% accuracy without manual coordinate mapping:
- **Search executes directly via `QTextDocument.find()`**.
- Match offsets `(start, end)` emitted by `matchSelected(int, int)` and returned by `getMatchRanges()` are **natively UTF-16 code units**.
- When `sourceTextArea.select(start, end)` is called in QML, it selects the exact matching characters regardless of preceding emojis, Persian text, or combining marks.
- In headless unit tests, `_get_document()` creates a local `QTextDocument(self._source_text)` so search tests validate identical Qt UTF-16 semantics.

---

## 4. Replace Semantics & Undo Model

### Replace Current
- **Target Invariant:** Replace Current replaces **only** the active search match.
- When the user triggers Replace, QML passes `sourceTextArea.selectionStart` and `sourceTextArea.selectionEnd` to `controller.replaceCurrent(start, end)`.
- If `(start, end) != active_match`:
  The replacement is **refused**, text is NOT modified, and the controller re-emits `matchSelected(active_start, active_end)` to re-target the active match.
- If `(start, end) == active_match`:
  `cursor.insertText(replaceQuery)` executes on the active selection. It records a single undo action in `TextArea`, fires `onTextChanged`, updates `isDirty = True`, and advances search to the next match.

### Replace All
- Executes via `QTextCursor.beginEditBlock()` and `endEditBlock()` on the underlying `QTextDocument`.
- Replacements are applied in reverse order (`reversed(matches)`), preserving valid character offsets for all preceding matches.
- **Undo Guarantee:** Qt's edit block collapses all replacements into a **single user-level undo transaction**. A single `Ctrl+Z` reverts all replacements at once and restores `canUndo == False` if the document returns to its initial state.
- Zero full-buffer string reassignment: cursor, scroll position, and viewport remain stable.

---

## 5. Syntax Highlighter Lifecycle & Precedence

### Lifecycle
- `MarkdownSyntaxHighlighter(QSyntaxHighlighter)` is attached to `sourceTextArea.textDocument.textDocument()` via `controller.attachTextDocument(quick_doc)`.
- **Idempotency:** Any previous highlighter is detached (`setDocument(None)`) before a new one is attached.
- **Destruction:** Controller `clear()` and `shutdown()` detach the highlighter cleanly.
- **Fidelity:** `QSyntaxHighlighter` applies formatting (`QTextCharFormat`) purely during paint/layout. It does NOT mutate `QTextDocument.toPlainText()`, does NOT trigger `textChanged`, and does NOT alter persisted Markdown.

### Lexical Rule Precedence

| Priority | Element | State / Regex | Format Applied | Suppresses Below |
|:---:|:---|:---|:---|:---:|
| **1** | Fenced Code Block | `^```[\w]*\s*$` to `^```\s*$` (`STATE_CODE_BLOCK`) | Background `#1e1e28`, text `#c4b5fd`, monospace | **Yes** (all below) |
| **2** | HTML Comment | `<!--` to `-->` (`STATE_COMMENT_BLOCK`) | Text `#6b7280`, italic | **Yes** (all below) |
| **3** | Heading H1–H6 | `^(#{1,6})\s+(.*)$` | `#60a5fa` to `#bfdbfe`, bold | **Yes** (block-level) |
| **4** | Blockquote Bar | `^>\s+(.*)$` | Bar `#3b82f6`, text `#9ca3af` | No |
| **5** | Thematic Break | `^(---|\*\*\*|___)\s*$` | Muted divider `#4b5563`, bold | **Yes** (block-level) |
| **6** | Inline Code Span | `` `([^`\n]+)` `` | Text `#fcd34d`, background `#1f2937` | **Yes** (within span) |
| **7** | Visual Region Token | `!\[\[(.*?)(\|region_id=([a-f0-9-]+))?\]\]` | Emerald badge `#34d399`, bold ID `#10b981` | **Yes** (within span) |
| **8** | Markdown Link | `\[([^\]\n]+)\]\(([^)\n]+)\)` | Underline, sky blue `#38bdf8` | **Yes** (within span) |
| **9** | Strong / Bold | `(\*\*|__)(?!\s)(.+?)(?<!\s)\1` | Bold, `#f9fafb` | No |
| **10** | Emphasis / Italic | `(\*|_)(?!\s)([^*_\n]+?)(?<!\s)\1` | Italic, `#f3f4f6` | No |

---

## 6. Cursor & Document Metrics Semantics

- **`cursorLine`:** 1-indexed document line derived via `QTextCursor(doc).blockNumber() + 1`.
- **`cursorColumn`:** 1-indexed UTF-16 code unit offset derived via `QTextCursor(doc).positionInBlock() + 1`. Stepping past an emoji advances by 2 code units, matching `TextArea.cursorPosition`.
- **`characterCount`:** Unicode code points (`len(source_text)`). E.g. `🐙` is 1 character; `سلام` is 4 characters. Distinct from UTF-16 code units.
- **`wordCount`:** Unicode whitespace-delimited tokens (`len(re.findall(r"\S+", source_text))`).

---

## 7. Implementation Tasks & Commit Strategy

### Commit 1: `feat(phase10f): add syntax highlighter to native markdown editor`
- Create `docs/plans/2026-09-17-phase10f2-native-markdown-editor-ux.md`.
- Export `QTextDocument`, `QTextCursor`, `QSyntaxHighlighter`, `QTextCharFormat`, `QColor`, `QFont` in `interfaces/desktop/qt_compat.py`.
- Create `interfaces/desktop/syntax/__init__.py`.
- Create `interfaces/desktop/syntax/markdown_syntax_highlighter.py`.
- Add `attachTextDocument` slot and highlighter management in `interfaces/desktop/controllers/markdown_editor_controller.py`.
- Wire `Component.onCompleted: controller.attachTextDocument(sourceTextArea.textDocument)` in `interfaces/desktop/qml/components/MarkdownEditorPane.qml`.
- Add unit tests in `tests/unit/test_markdown_syntax_highlighter.py`.

### Commit 2: `feat(phase10f): add presentation search and replace to markdown editor`
- Add search and replace engine to `interfaces/desktop/controllers/markdown_editor_controller.py` using `QTextDocument.find()`, `replaceCurrent(start, end)`, and `replaceAll()`.
- Create `interfaces/desktop/qml/components/MarkdownEditorSearchBar.qml`.
- Embed search bar in `interfaces/desktop/qml/components/MarkdownEditorPane.qml` with shortcuts (Ctrl+F, Ctrl+H, Esc, Enter, Shift+Enter).
- Add unit and integration tests in `tests/unit/test_markdown_editor_search.py`.

### Commit 3: `feat(phase10f): add status bar with cursor and document metrics to editor`
- Add `cursorLine`, `cursorColumn`, `characterCount`, `wordCount`, `updateCursorPosition` to `interfaces/desktop/controllers/markdown_editor_controller.py`.
- Create `interfaces/desktop/qml/components/MarkdownEditorStatusBar.qml`.
- Embed status bar in `interfaces/desktop/qml/components/MarkdownEditorPane.qml`.
- Add tests in `tests/unit/test_markdown_editor_ux_integration.py`.

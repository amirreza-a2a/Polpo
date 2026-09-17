# ============================================================
#  interfaces/desktop/syntax/markdown_syntax_highlighter.py
#  Phase 10F.2 — Desktop Markdown Syntax Highlighter
# ============================================================

import re
from typing import Optional

from interfaces.desktop.qt_compat import (
    QColor,
    QFont,
    QObject,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)

# Block state machine constants
STATE_DEFAULT = 0
STATE_CODE_BLOCK = 1
STATE_COMMENT_BLOCK = 2


class MarkdownSyntaxHighlighter(QSyntaxHighlighter):
    """
    Presentation syntax highlighter for native Markdown editing.
    Operates strictly via QTextCharFormat display formatting in Qt's paint pipeline.

    Guarantees:
      - Raw source text is NEVER mutated; QTextDocument.toPlainText() is 100% preserved.
      - Never triggers textChanged or interferes with native undo/redo.
      - Incremental block-level formatting maintains high responsiveness on long documents.
      - Multi-line block state machine ensures code blocks suppress all inner styling.
      - Region tokens (![[...|region_id=...]]) are distinctly styled as emerald badges.
    """

    # Pre-compiled block patterns
    RE_CODE_FENCE_START = re.compile(r"^```[\w-]*\s*$")
    RE_CODE_FENCE_END = re.compile(r"^```\s*$")
    RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
    RE_THEMATIC_BREAK = re.compile(r"^(---|\*\*\*|___)\s*$")
    RE_BLOCKQUOTE = re.compile(r"^(>\s*)")

    # Pre-compiled inline patterns in strict precedence order
    INLINE_PATTERNS = [
        ("COMMENT", re.compile(r"<!--.*?-->")),
        ("CODE_SPAN", re.compile(r"`[^`\n]+`")),
        ("REGION_TOKEN", re.compile(r"!\[\[.*?\]\]")),
        ("LINK", re.compile(r"!?\[([^\]\n]*)\]\(([^)\n]+)\)")),
        ("BOLD", re.compile(r"(\*\*|__)(?!\s)(.+?)(?<!\s)\1")),
        ("ITALIC", re.compile(r"(\*|_)(?!\s)([^*_\n]+?)(?<!\s)\1")),
    ]

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._init_formats()
        if self.document() is not None:
            self.rehighlight()

    def _init_formats(self) -> None:
        """Initializes dark-theme character formats."""
        # Headings (H1 to H6)
        self.heading_formats = {}
        heading_colors = [
            "#60a5fa",  # H1: blue-400
            "#93c5fd",  # H2: blue-300
            "#bfdbfe",  # H3: blue-200
            "#dbeafe",  # H4: blue-100
            "#e0e7ff",  # H5: indigo-100
            "#ede9fe",  # H6: violet-100
        ]
        for level, color_hex in enumerate(heading_colors, start=1):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_hex))
            fmt.setFontWeight(QFont.Weight.Bold)
            self.heading_formats[level] = fmt

        # Multi-line code block
        self.code_block_fmt = QTextCharFormat()
        self.code_block_fmt.setForeground(QColor("#c4b5fd"))       # violet-300
        self.code_block_fmt.setBackground(QColor("#1e1e28"))       # dark surface
        self.code_block_fmt.setFontFamily("Monospace")

        # Inline code span
        self.code_span_fmt = QTextCharFormat()
        self.code_span_fmt.setForeground(QColor("#fcd34d"))        # amber-300
        self.code_span_fmt.setBackground(QColor("#1f2937"))        # gray-800
        self.code_span_fmt.setFontFamily("Monospace")

        # HTML Comments
        self.comment_fmt = QTextCharFormat()
        self.comment_fmt.setForeground(QColor("#6b7280"))          # gray-500
        self.comment_fmt.setFontItalic(True)

        # Visual Region Tokens: badge style
        self.region_fmt = QTextCharFormat()
        self.region_fmt.setForeground(QColor("#34d399"))           # emerald-400
        self.region_fmt.setBackground(QColor("#064e3b"))           # emerald-900
        self.region_fmt.setFontWeight(QFont.Weight.Bold)

        # Links
        self.link_fmt = QTextCharFormat()
        self.link_fmt.setForeground(QColor("#38bdf8"))             # sky-400
        self.link_fmt.setFontUnderline(True)

        # Bold (Strong)
        self.bold_fmt = QTextCharFormat()
        self.bold_fmt.setForeground(QColor("#f9fafb"))             # gray-50
        self.bold_fmt.setFontWeight(QFont.Weight.Bold)

        # Italic (Emphasis)
        self.italic_fmt = QTextCharFormat()
        self.italic_fmt.setForeground(QColor("#f3f4f6"))           # gray-100
        self.italic_fmt.setFontItalic(True)

        # Thematic break (hr)
        self.thematic_break_fmt = QTextCharFormat()
        self.thematic_break_fmt.setForeground(QColor("#4b5563"))   # gray-600
        self.thematic_break_fmt.setFontWeight(QFont.Weight.Bold)

        # Blockquote prefix
        self.blockquote_fmt = QTextCharFormat()
        self.blockquote_fmt.setForeground(QColor("#3b82f6"))       # blue-500
        self.blockquote_fmt.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        """
        Incrementally formats a single block of text according to strict precedence rules.
        """
        prev_state = self.previousBlockState()

        # =====================================================================
        # 1. Multi-line Fenced Code Block State Machine (Highest Priority)
        # =====================================================================
        if prev_state == STATE_CODE_BLOCK:
            self.setFormat(0, len(text), self.code_block_fmt)
            if self.RE_CODE_FENCE_END.match(text):
                self.setCurrentBlockState(STATE_DEFAULT)
            else:
                self.setCurrentBlockState(STATE_CODE_BLOCK)
            return  # Suppress all inner styling inside fenced code blocks

        if self.RE_CODE_FENCE_START.match(text):
            self.setFormat(0, len(text), self.code_block_fmt)
            self.setCurrentBlockState(STATE_CODE_BLOCK)
            return

        # =====================================================================
        # 2. Multi-line HTML Comment State Machine
        # =====================================================================
        if prev_state == STATE_COMMENT_BLOCK:
            end_idx = text.find("-->")
            if end_idx != -1:
                self.setFormat(0, end_idx + 3, self.comment_fmt)
                self.setCurrentBlockState(STATE_DEFAULT)
                # Remainder of line can be formatted below if needed
            else:
                self.setFormat(0, len(text), self.comment_fmt)
                self.setCurrentBlockState(STATE_COMMENT_BLOCK)
                return

        # Check for multi-line comment opening on this line without closing
        c_open_idx = text.find("<!--")
        if c_open_idx != -1 and text.find("-->", c_open_idx) == -1:
            self.setFormat(c_open_idx, len(text) - c_open_idx, self.comment_fmt)
            self.setCurrentBlockState(STATE_COMMENT_BLOCK)
            return

        self.setCurrentBlockState(STATE_DEFAULT)

        # =====================================================================
        # 3. Headings (H1 to H6)
        # =====================================================================
        heading_match = self.RE_HEADING.match(text)
        if heading_match:
            level = len(heading_match.group(1))
            fmt = self.heading_formats.get(level, self.heading_formats[6])
            self.setFormat(0, len(text), fmt)
            return

        # =====================================================================
        # 4. Thematic Breaks (---, ***, ___)
        # =====================================================================
        if self.RE_THEMATIC_BREAK.match(text):
            self.setFormat(0, len(text), self.thematic_break_fmt)
            return

        # =====================================================================
        # 5. Blockquote Prefix
        # =====================================================================
        bq_match = self.RE_BLOCKQUOTE.match(text)
        if bq_match:
            self.setFormat(0, bq_match.end(), self.blockquote_fmt)

        # =====================================================================
        # 6. Inline Spans with Strict Non-Overlapping Precedence
        # =====================================================================
        claimed = [False] * len(text)

        # Mark blockquote prefix as claimed
        if bq_match:
            for k in range(bq_match.end()):
                claimed[k] = True

        format_map = {
            "COMMENT": self.comment_fmt,
            "CODE_SPAN": self.code_span_fmt,
            "REGION_TOKEN": self.region_fmt,
            "LINK": self.link_fmt,
            "BOLD": self.bold_fmt,
            "ITALIC": self.italic_fmt,
        }

        for token_type, pattern in self.INLINE_PATTERNS:
            fmt = format_map[token_type]
            for match in pattern.finditer(text):
                start, end = match.start(), match.end()
                # Enforce non-overlapping invariant: only format if no character in span is claimed
                if not any(claimed[start:end]):
                    self.setFormat(start, end - start, fmt)
                    for idx in range(start, end):
                        claimed[idx] = True

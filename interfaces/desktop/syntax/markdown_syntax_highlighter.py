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


DARK_SYNTAX_PALETTE = {
    "headings": [
        "#60a5fa",  # H1: blue-400
        "#93c5fd",  # H2: blue-300
        "#bfdbfe",  # H3: blue-200
        "#dbeafe",  # H4: blue-100
        "#e0e7ff",  # H5: indigo-100
        "#ede9fe",  # H6: violet-100
    ],
    "code_block_fg": "#c4b5fd",
    "code_block_bg": "#1e1e28",
    "code_span_fg": "#fcd34d",
    "code_span_bg": "#1f2937",
    "comment_fg": "#6b7280",
    "region_fg": "#34d399",
    "region_bg": "#064e3b",
    "link_fg": "#38bdf8",
    "bold_fg": "#f9fafb",
    "italic_fg": "#f3f4f6",
    "thematic_break_fg": "#4b5563",
    "blockquote_fg": "#3b82f6",
}

LIGHT_SYNTAX_PALETTE = {
    "headings": [
        "#1d4ed8",  # H1: blue-700
        "#1e40af",  # H2: blue-800
        "#0369a1",  # H3: sky-700
        "#0f766e",  # H4: teal-700
        "#4338ca",  # H5: indigo-700
        "#6d28d9",  # H6: purple-700
    ],
    "code_block_fg": "#5b21b6",
    "code_block_bg": "#f6f8fa",
    "code_span_fg": "#b45309",
    "code_span_bg": "#f3f4f6",
    "comment_fg": "#656d76",
    "region_fg": "#047857",
    "region_bg": "#d1fae5",
    "link_fg": "#0969da",
    "bold_fg": "#1f2328",
    "italic_fg": "#32383f",
    "thematic_break_fg": "#8c959f",
    "blockquote_fg": "#0969da",
}

SYNTAX_PALETTES = {
    "dark": DARK_SYNTAX_PALETTE,
    "light": LIGHT_SYNTAX_PALETTE,
}


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

    def __init__(self, parent: Optional[QObject] = None, theme: str = "dark"):
        super().__init__(parent)
        self._current_theme: str = (
            "light" if isinstance(theme, str) and theme.strip().lower() == "light" else "dark"
        )
        self._init_formats(self._current_theme)
        if self.document() is not None:
            self.rehighlight()

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def set_theme(self, resolved_theme: str) -> None:
        """
        Reconfigures formatting rules for the requested theme ('dark' or 'light')
        and triggers immediate rehighlighting of the active document.
        """
        normalized = (
            "light"
            if isinstance(resolved_theme, str) and resolved_theme.strip().lower() == "light"
            else "dark"
        )
        if normalized == self._current_theme:
            return
        self._current_theme = normalized
        self._init_formats(normalized)
        if self.document() is not None:
            self.rehighlight()

    def _init_formats(self, theme: str = "dark") -> None:
        """Initializes character formats for the given theme ('dark' or 'light')."""
        palette = SYNTAX_PALETTES.get(theme, DARK_SYNTAX_PALETTE)
        # Headings (H1 to H6)
        self.heading_formats = {}
        heading_colors = palette["headings"]
        for level, color_hex in enumerate(heading_colors, start=1):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color_hex))
            fmt.setFontWeight(QFont.Weight.Bold)
            self.heading_formats[level] = fmt

        # Multi-line code block
        self.code_block_fmt = QTextCharFormat()
        self.code_block_fmt.setForeground(QColor(palette["code_block_fg"]))
        self.code_block_fmt.setBackground(QColor(palette["code_block_bg"]))
        self.code_block_fmt.setFontFamilies(["Monospace"])

        # Inline code span
        self.code_span_fmt = QTextCharFormat()
        self.code_span_fmt.setForeground(QColor(palette["code_span_fg"]))
        self.code_span_fmt.setBackground(QColor(palette["code_span_bg"]))
        self.code_span_fmt.setFontFamilies(["Monospace"])

        # HTML Comments
        self.comment_fmt = QTextCharFormat()
        self.comment_fmt.setForeground(QColor(palette["comment_fg"]))
        self.comment_fmt.setFontItalic(True)

        # Visual Region Tokens: badge style
        self.region_fmt = QTextCharFormat()
        self.region_fmt.setForeground(QColor(palette["region_fg"]))
        self.region_fmt.setBackground(QColor(palette["region_bg"]))
        self.region_fmt.setFontWeight(QFont.Weight.Bold)

        # Links
        self.link_fmt = QTextCharFormat()
        self.link_fmt.setForeground(QColor(palette["link_fg"]))
        self.link_fmt.setFontUnderline(True)

        # Bold (Strong)
        self.bold_fmt = QTextCharFormat()
        self.bold_fmt.setForeground(QColor(palette["bold_fg"]))
        self.bold_fmt.setFontWeight(QFont.Weight.Bold)

        # Italic (Emphasis)
        self.italic_fmt = QTextCharFormat()
        self.italic_fmt.setForeground(QColor(palette["italic_fg"]))
        self.italic_fmt.setFontItalic(True)

        # Thematic break (hr)
        self.thematic_break_fmt = QTextCharFormat()
        self.thematic_break_fmt.setForeground(QColor(palette["thematic_break_fg"]))
        self.thematic_break_fmt.setFontWeight(QFont.Weight.Bold)

        # Blockquote prefix
        self.blockquote_fmt = QTextCharFormat()
        self.blockquote_fmt.setForeground(QColor(palette["blockquote_fg"]))
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
        closed_comment_len = 0
        if prev_state == STATE_COMMENT_BLOCK:
            end_idx = text.find("-->")
            if end_idx != -1:
                closed_comment_len = end_idx + 3
                self.setFormat(0, closed_comment_len, self.comment_fmt)
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

        # Mark closed multiline comment span as claimed
        if closed_comment_len > 0:
            for k in range(min(closed_comment_len, len(text))):
                claimed[k] = True

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

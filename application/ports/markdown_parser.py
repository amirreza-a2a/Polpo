# ============================================================
#  application/ports/markdown_parser.py
#  Abstract Application Port for Markdown Document Parsing
# ============================================================

from abc import ABC, abstractmethod

from core.markdown.ast import MarkdownDocument


class IMarkdownParser(ABC):
    """
    Application port interface for parsing Markdown source text into
    the canonical PolpoT Core Markdown AST.
    Strictly isolated from parser infrastructure and UI frameworks.
    """

    @abstractmethod
    def parse(self, text: str) -> MarkdownDocument:
        """
        Parses raw Markdown string into canonical PolpoT MarkdownDocument AST.

        :param text: Raw Markdown text to parse.
        :return: Immutable MarkdownDocument AST root.
        """
        pass

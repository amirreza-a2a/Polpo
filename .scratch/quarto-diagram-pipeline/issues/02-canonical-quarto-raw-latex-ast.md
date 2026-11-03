# 02: Canonical Quarto Raw-LaTeX AST Support

**What to build:**
Implement immutable `RawBlock` node representation in canonical Core Markdown AST, extend `PandocParser` to transform Pandoc JSON `RawBlock` into `core.markdown.ast.RawBlock` with reverse coordinate mapping, and implement a deterministic LaTeX diagram environment extractor.

**Blocked by:**
01: Architecture Investigation and Unified Decision

**Status:**
ready-for-agent

- [x] Add `BlockType.RAW_BLOCK = "raw_block"` and `RawBlock(format: str, content: str)` in `core/markdown/ast.py`.
- [x] Extend `infrastructure/markdown/pandoc_parser.py` to parse Pandoc `RawBlock` AST nodes and preserve source line/col coordinates.
- [x] Implement `core/markdown/diagram_extractor.py` for deterministic parsing and balanced validation of `tikzpicture` and `circuitikz` environments.
- [x] Ensure regular fenced code blocks (`CodeBlock`) remain unchanged.
- [x] Add unit tests in `tests/unit/test_pandoc_parser.py` and `tests/unit/test_diagram_extractor.py`.

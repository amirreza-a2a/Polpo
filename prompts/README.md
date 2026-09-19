# Reusable Prompt Templates & Candidates

This directory stores reusable prompt templates and specialized system prompt assets for the Polpo project. These files are preserved project assets intended for future prompt management, evaluation, and default-prompt candidate selection.

> [!IMPORTANT]
> **Runtime Status:** The prompt templates in this directory are preserved reference assets and evaluation candidates. They are **not** currently the active runtime defaults in the desktop application. Active runtime prompts are currently managed via application configuration (`core/config.py` and infrastructure settings). Future milestones may promote selected templates from this directory into explicit default prompt configurations or user-selectable template libraries.

---

## Prompt Inventory

| Historical Filename | Modern Semantic Path | Language | Functional Description |
| :--- | :--- | :---: | :--- |
| `prompt.txt` | `prompts/templates/en/stem_handwritten_transcription.txt` | English | High-precision undergraduate STEM handwritten notes transcription into structured Markdown and LaTeX equations. |
| `prompt2.txt` | `prompts/templates/fa/stem_transcription.txt` | Persian | Core STEM handwritten document transcription into structured Persian Markdown with precise mathematical LaTeX equations. |
| `prompt2Di.txt` | `prompts/templates/fa/stem_transcription_with_figure_description.txt` | Persian | STEM transcription extending the base prompt with mandatory bounding box coordinates `[[ymin, xmin, ymax, xmax]]` and detailed figure comments. |
| `prompt2DiMadar.txt` | `prompts/templates/fa/stem_transcription_with_figure_description_and_netlist.txt` | Persian | STEM transcription extending figure descriptions with SPICE-like electrical circuit Netlist extraction for schematic components and nodes. |
| `prompt3.txt` | `prompts/templates/fa/educational_stem_transcription_griffiths.txt` | Persian | Educational and editorial transcription aligning terminology, pedagogical structure, and concepts with David J. Griffiths' *Introduction to Electrodynamics*. |
| `promptElectro.txt` | `prompts/templates/fa/electromagnetics_griffiths_editor.txt` | Persian | Dedicated electromagnetics editorial prompt strictly scoped to electromagnetics lecture notes guided by Griffiths' conventions. |
| `promptTikZ.txt` | `prompts/templates/fa/stem_transcription_tikz.txt` | Persian | Comprehensive STEM transcription reconstructing visual diagrams, circuits, and figures directly into LaTeX TikZ / CircuiTikZ code blocks. |
| `prompts/QuickConvertPrompt.txt` | `prompts/templates/fa/quick_convert.txt` | Persian | Single-image fast conversion prompt with dynamic routing based on detected content type (text, formulas, circuits, plots, tables). |
| `prompts/pipeline2/QuickConvertPrompt.txt` | `prompts/pipeline2/quick_convert.txt` | Persian | Pipeline 2 single-image conversion variant generating structured Markdown, LaTeX, and graphical reconstruction code. |
| `prompts/pipeline2/pipeline2_unify_prompt.txt` | `prompts/pipeline2/unify_markdown.txt` | Persian | Pipeline 2 document unification prompt repairing cross-page breaks, eliminating duplicate boundary text, and standardizing Markdown heading hierarchy. |

---

## Directory Structure

```text
prompts/
├── README.md
├── templates/
│   ├── en/
│   │   └── stem_handwritten_transcription.txt
│   └── fa/
│       ├── stem_transcription.txt
│       ├── stem_transcription_with_figure_description.txt
│       ├── stem_transcription_with_figure_description_and_netlist.txt
│       ├── educational_stem_transcription_griffiths.txt
│       ├── electromagnetics_griffiths_editor.txt
│       ├── stem_transcription_tikz.txt
│       └── quick_convert.txt
│
└── pipeline2/
    ├── quick_convert.txt
    └── unify_markdown.txt
```

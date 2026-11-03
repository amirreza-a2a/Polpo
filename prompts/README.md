# Reusable Prompt Templates & Candidates

This directory stores reusable prompt templates and specialized system prompt assets for the Polpo project. These files are preserved project assets intended for future prompt management, evaluation, and default-prompt candidate selection.

> [!IMPORTANT]
> **Runtime Status:** The curated prompt templates in this directory are the authoritative source assets seeded into the SQLite application database via `PromptSeedingService` on clean installations. During application startup, `PromptSeedingService.reconcile_stock_prompts()` safely reconciles unmodified stock prompts to current versions while strictly preserving user-customized prompts. All diagram-producing prompts adhere to the Quarto ````{=latex}```` raw block standard established in ADR-005.

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
| `promptTikZ.txt` | `prompts/templates/fa/stem_transcription_tikz.txt` | Persian | Comprehensive STEM transcription reconstructing visual diagrams, circuits, and figures into canonical Quarto raw LaTeX blocks (TikZ, PGFPlots, CircuitTikZ). |
| `prompts/QuickConvertPrompt.txt` | `prompts/templates/fa/quick_convert.txt` | Persian | Single-image fast conversion prompt with dynamic routing based on detected content type, generating Quarto raw LaTeX diagram blocks. |
| `prompts/pipeline2/QuickConvertPrompt.txt` | `prompts/pipeline2/quick_convert.txt` | Persian | Pipeline 2 single-image conversion variant generating structured Markdown, LaTeX, and Quarto raw LaTeX graphical reconstruction blocks. |
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

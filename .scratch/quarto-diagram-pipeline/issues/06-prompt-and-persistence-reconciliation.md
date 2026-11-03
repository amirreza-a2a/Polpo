# 06: Prompt and Persistence Reconciliation

**What to build:**
Reconcile all authored prompt templates to emit standard Quarto raw LaTeX blocks (````{=latex}````). Prohibit obsolete ```` ```tikz ```` syntax, standalone document wrappers, and TikZJax assumptions. Implement safe, idempotent prompt reconciliation in `PromptSeedingService` that updates stock defaults while preserving user-created and user-customized prompts.

**Blocked by:**
01: Architecture Investigation and Unified Decision

**Status:**
ready-for-agent

- [ ] Update `prompts/templates/fa/stem_transcription_tikz.txt` to emit standard Quarto raw LaTeX blocks without standalone wrappers.
- [ ] Update `prompts/templates/fa/quick_convert.txt` and `prompts/pipeline2/quick_convert.txt`.
- [ ] Update `prompts/pipeline2/unify_markdown.txt` and `prompts/README.md`.
- [ ] Implement safe, idempotent stock prompt reconciliation in `PromptSeedingService`.
- [ ] Add unit tests in `tests/unit/test_prompt_contracts.py` and `tests/unit/test_prompt_seeding.py`.

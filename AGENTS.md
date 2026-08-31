# AGENTS.md — Standing Rules for PolpoT

These rules apply to every mission in this project — audits, planning, coding, and
debugging — regardless of what any single prompt says. If a specific prompt conflicts
with a rule here, follow the rule here unless the prompt explicitly overrides it.

## 1. Verify before judging "current," "valid," or "deprecated"

Your training data has a knowledge cutoff in the past. You are **not** a reliable
authority on:
- current model names/versions for any AI provider (Google Gemini, OpenAI, OpenRouter,
  etc.)
- current library/SDK versions and their breaking changes
- current API endpoint shapes, deprecations, rate limits, or pricing
- whether a given package version is "latest," "outdated," or "invalid"

Whenever a task depends on a claim about what is current/valid/deprecated for an
external model, library, or API:

- If you have access to a live web search or up-to-date documentation tool, use it to
  verify before acting.
- If you do not have that access, do **not** silently "correct" a name, version, or
  endpoint you don't recognize. Mark the claim explicitly as
  **`UNVERIFIED — based on training data, may be outdated`** and surface it as an open
  question for human confirmation instead of acting on it.

This applies to plans and audits as much as to code. A prior audit of this project
incorrectly flagged the real, active model `gemini-3.5-flash` as "invalid" and proposed
downgrading to an older model — purely because the model name postdated training data.
Do not repeat this class of mistake.

## 2. Production stays live during remediation

PolpoT runs in production on a shared cPanel host, triggered via cron in polling mode,
serving real users continuously. Any plan or change must:
- Be deployable independently in small increments — not a single big-bang rewrite.
- Leave the bot in a working state after each deployable increment.
- Include a manual verification checklist for re-testing on the live bot (since there
  is currently no automated test suite) and a rollback plan.

## 3. Business logic does not belong in Telegram handlers

`handlers/` files should only translate between Telegram's `update`/`context` objects
and calls into a transport-agnostic service/domain layer. Business decisions (API chain
selection, quota checks, job state transitions) must live in `services/` or an
equivalent domain layer, not inline inside a handler function. This is a hard
constraint for any new code and any refactor — it exists specifically to keep a future
Qt desktop client (and any REST API) able to reuse the same logic without duplicating
or rewriting it.

## 4. No duplicated logic across parallel flows

Before implementing a flow that resembles an existing one (e.g., another "select
model + base_url" step, another "build API chain and create a job" step, another CRUD
set for a new "prompts-like" table), check whether an existing implementation should be
generalized and reused instead of copied. If you do find yourself duplicating a
pattern, say so explicitly rather than proceeding silently.

## 5. Database changes are explicit and reversible

Do not add ad-hoc `ALTER TABLE` statements wrapped in a blind `try/except: pass`. Any
schema change must be a discrete, described migration step that can be communicated as
part of a deployment procedure, and — once the project adopts a real migration tool —
must go through it.

## 6. Prefer flagging over guessing

If a task requires a decision that has real trade-offs (architecture, library choice,
storage strategy, execution model for the worker, etc.), propose options with
reasoning, but do not unilaterally decide and implement. Surface it as an open question
for the human developer, unless you have been given an explicit prior answer to that
exact question in the current mission's instructions.

## 7. Secrets never enter source control or plaintext output

Never hardcode tokens, passwords, or API keys in any file you create or edit. Never
print live secret values in full inside reports, logs, or chat output — redact or
truncate them (e.g., show only the first/last few characters) if you must reference
that a secret exists somewhere.

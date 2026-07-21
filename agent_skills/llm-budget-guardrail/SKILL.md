---
name: llm-budget-guardrail
description: >-
  Estimates the dollar cost of a planned set of LLM calls before they run,
  then tracks actual spend against a stated budget as calls happen, warning
  when spend is approaching or has crossed that budget. Use when a task will
  make many model calls (a multi-model loop, a fan-out of workers, a batch
  job) and the user wants a cost estimate up front, wants spend tracked
  against a budget, or asks "how much will this cost", "track spend against
  my budget", "warn me before this gets expensive", or "estimate the API
  cost of this plan". Not for a single one-off call, and not a substitute
  for the provider's own billing dashboard — this is an advisory estimate
  from a pricing table you keep current against planned call volume, not a bill.
license: Apache-2.0
metadata:
  author: "Shubham Saboo"
  version: "1.0.0"
  source: "https://github.com/Shubhamsaboo/awesome-llm-apps"
compatibility: >-
  No network calls. Reads/writes only the plan, pricing, and ledger files you
  pass it. Python 3, stdlib only (json, argparse, decimal, datetime) — no
  pip install. Runs anywhere Python 3 runs.
---

# LLM Budget Guardrail

Advisory cost estimation and spend tracking for any workflow that makes
more than one LLM call. Standalone: it takes model names, token counts, and
a budget on the command line, and knows nothing about how those calls were
made or by what other skill.

## When to use

- Before a multi-call run: get a total estimate against a budget so you can
  decide to proceed, trim the plan, or ask the user first
- During a multi-call run: record each actual call and get a running total,
  so a long loop can't quietly blow through spend nobody was watching
- Anytime someone asks "how much will/did this cost" about a batch of model
  calls

## When not to use

- A single call — there's nothing to track
- As a source of truth for billing — it estimates from token counts and a
  pricing table you maintain; the provider's invoice is the real number

## Run it

```bash
# Before running a plan: estimate against a budget
python3 scripts/budget_guardrail.py estimate --plan plan.json --budget 5.00

# After each actual call: record it and see the running total
python3 scripts/budget_guardrail.py track --ledger spend.jsonl \
  --model gemini-3.5-flash --input-tokens 1800 --output-tokens 420 \
  --budget 5.00

# Anytime: check cumulative spend without recording a new call
python3 scripts/budget_guardrail.py report --ledger spend.jsonl --budget 5.00
```

`plan.json` for `estimate` is `{"calls": [{"model": "...", "input_tokens": N,
"output_tokens": N, "count": N}]}` — one entry per distinct kind of call in
the plan, `count` defaults to 1. Every command accepts `--json` for
machine-readable output and `--pricing FILE` to override the bundled
placeholder table in `references/pricing.json` (read
`references/cost-model.md` before trusting a number — it covers the pricing
table's staleness rule, the ledger format, and exactly what the exit codes
mean).

**An unfamiliar model name is a hard error**, not a silent $0. Add it to
your pricing file with real numbers from the provider's pricing page —
this skill never guesses a price.

## Reading the result

Exit code carries the decision-relevant signal: `0` under budget (or no
budget given), `1` at or past `--warn-at` (default 80% of budget) but not
over, `2` over budget or a hard error (bad file, unknown model). Text output
also prints any pricing-staleness warnings inline; `--json` puts them in a
`warnings`/`warning` field. This script only estimates and reports — it
never stops a call from happening. Whatever calls it decides what an
over-budget signal should do.

## Using it inside another loop

If you're orchestrating multiple model calls yourself: call `estimate` once
at planning time with the full call plan, then `track` once per actual
dispatch as it completes, then fold the last `report` (or the last `track`
output) into whatever status you show the user. This skill doesn't care
whether "the loop" is this repo's `advisor-orchestrator-worker` skill, a
different one, or your own code — it only ever sees model names, token
counts, and a budget.

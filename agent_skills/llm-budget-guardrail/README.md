# 💰 LLM Budget Guardrail

**Know the cost before you spend it, and know when you're about to blow the budget while you still can do something about it.**

A standalone skill: estimate the dollar cost of a planned batch of LLM calls against a stated budget before running it, then record each actual call as it happens and get a running total. No dependency on any other skill in this repo — it only ever sees model names, token counts, and a budget.

## Why standalone

This started as an add-on to [advisor-orchestrator-worker](../advisor-orchestrator-worker/) — a "Papa" tie-break tier plus a cost layer bolted onto that skill's loop. That PR was closed: it rewrote a maintained skill with scope nobody asked for, and shipped test files at the repo root instead of under `agent_skills/`. Both fair. This is the cost-tracking half, rebuilt as its own thing, in its own folder, with its own evals — usable by that skill, by a different one, or by nothing but your own script.

## Install

```bash
npx skills add https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/agent_skills/llm-budget-guardrail
```

Or copy this folder into your agent's skills dir (`~/.claude/skills/`, `~/.codex/skills/`, `~/.agents/skills/`).

**Needs**: Python 3, stdlib only. No network calls, no API keys, nothing installed.

## Use it

> "Estimate what this batch of calls will cost before we run it."
> "Track spend against a $5 budget as this runs."
> "Warn me if this is about to go over budget."

```bash
python3 scripts/budget_guardrail.py estimate --plan plan.json --budget 5.00
python3 scripts/budget_guardrail.py track --ledger spend.jsonl --model gemini-3.5-flash --input-tokens 1800 --output-tokens 420 --budget 5.00
python3 scripts/budget_guardrail.py report --ledger spend.jsonl --budget 5.00
```

See [SKILL.md](SKILL.md) for the full command reference and [references/cost-model.md](references/cost-model.md) for the exact cost formula, the pricing-staleness rule, and the ledger file format.

## Files

```
llm-budget-guardrail/
├── SKILL.md                       # when to use it, commands, exit codes
├── README.md                      # this file
├── scripts/budget_guardrail.py    # estimate / track / report — stdlib only
└── references/
    ├── pricing.json                # placeholder $/1M-token table, keep it current
    └── cost-model.md               # formula, staleness rule, ledger format
```

Evals live repo-side in `agent_skills/evals/llm-budget-guardrail/`; you install only what runs.

Part of [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) · Apache-2.0 · Last verified: July 2026

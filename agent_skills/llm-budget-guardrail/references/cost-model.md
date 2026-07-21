# Cost model

How `scripts/budget_guardrail.py` turns token counts into dollars, and what
its numbers do and don't promise.

## Formula

For one call: `cost = (input_tokens / 1_000_000) * input_per_mtok + (output_tokens / 1_000_000) * output_per_mtok`.

All arithmetic runs on `decimal.Decimal`, never `float` — token counts and
per-million-token prices both convert to `Decimal` before multiplying, so
there is no binary-floating-point rounding drift across a long ledger.
Displayed amounts are quantized to 4 decimal places; the underlying ledger
entries keep full precision.

`estimate` multiplies a group's per-call cost by its `count` before summing
into the total and the per-model subtotal. `track` and `report` sum every
line of the ledger file as-is — nothing is deduplicated or estimated after
the fact, because each ledger line is already an actual recorded call.

## Pricing table

`references/pricing.json` is a flat `model -> {input_per_mtok, output_per_mtok,
as_of}` table. It ships with illustrative numbers, not a live feed — there is
no pricing API call anywhere in this skill, on purpose (see `SKILL.md`
compatibility line: no network). Two things follow from that:

- **An unknown model is a hard error**, not a $0 or averaged guess. If you
  see `unknown model 'x'`, add it to the table (or your own `--pricing`
  file) with real numbers from the provider's pricing page. Guessing a
  price is worse than refusing to estimate.
- **Staleness is flagged, not enforced.** Every pricing entry carries an
  `as_of` date. If it's more than 90 days old, every command that used it
  prints a warning (text mode) or sets `"warning"`/adds to `"warnings"`
  (JSON mode). The run still completes — this is advisory, the same way the
  rest of this skill is advisory — but a 6-month-stale price on a model
  whose provider cut prices 40% will make every downstream budget decision
  wrong, so don't ignore the warning.

## Budget and warn threshold

`--budget` is a plain dollar ceiling; `--warn-at` (default `0.8`) is a
fraction of that ceiling. Exit codes are the scriptable part:

| Exit | Meaning |
|---|---|
| 0 | No budget given, or spend is below `warn-at * budget` |
| 1 | Spend is at or above `warn-at * budget` but not over `budget` |
| 2 | Spend is over `budget`, or the command failed (unknown model, bad file, etc.) |

Nothing here auto-stops a run. The exit code is a signal for whatever loop
is calling this script to check and act on — refuse the next dispatch,
ask the user, or just note it in a status line. Deciding what "over budget"
should *do* is a judgment call for the skill or agent using this one, not
this script's job.

## Ledger format

`track` appends one JSON object per line to the `--ledger` file:

```json
{"model": "gemini-3.5-flash", "input_tokens": 1800, "output_tokens": 420, "cost": "0.000348", "recorded_at": "2026-07-21T14:03:11.482193+00:00"}
```

`cost` is stored as a string (exact decimal text, not a float) so re-reading
the ledger never reintroduces rounding error. `report` and the summary
`track` prints after appending both just sum every line's `cost` — the
ledger file is the single source of truth for "what have we actually
spent," with no separate running total to drift out of sync.

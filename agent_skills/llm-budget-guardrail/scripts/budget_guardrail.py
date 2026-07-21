#!/usr/bin/env python3
"""
budget_guardrail.py — estimate and track spend for a plan of LLM calls
against a stated budget. No network calls; reads/writes only the files
you pass it.

Three subcommands:
    estimate  --plan PLAN.json  [--budget N] [--pricing FILE] [--json]
    track     --ledger LEDGER.jsonl --model NAME --input-tokens N
              --output-tokens N [--budget N] [--warn-at 0.8]
              [--pricing FILE] [--json]
    report    --ledger LEDGER.jsonl [--budget N] [--warn-at 0.8]
              [--pricing FILE] [--json]

Exit codes (all subcommands): 0 = under budget (or no budget given),
1 = over the --warn-at threshold but not over budget, 2 = over budget.
Unknown models are a hard error (exit 2) rather than a silent $0 guess.

Stdlib only. Money uses decimal.Decimal throughout; no floats touch a cost.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

STALE_PRICING_DAYS = 90


class GuardrailError(Exception):
    pass


def load_pricing(path):
    with open(path, "r") as f:
        data = json.load(f)
    models = data.get("models", {})
    if not models:
        raise GuardrailError("pricing file has no 'models' table: %s" % path)
    return models


def price_for(models, model_name):
    entry = models.get(model_name)
    if entry is None:
        raise GuardrailError(
            "unknown model %r — not in the pricing file. Add it (input_per_mtok, "
            "output_per_mtok, as_of) rather than guessing a price." % model_name
        )
    try:
        input_price = Decimal(str(entry["input_per_mtok"]))
        output_price = Decimal(str(entry["output_per_mtok"]))
    except (KeyError, InvalidOperation) as exc:
        raise GuardrailError("malformed pricing entry for %r: %s" % (model_name, exc))
    return input_price, output_price, entry.get("as_of")


def stale_warning(model_name, as_of):
    if not as_of:
        return "%s: pricing entry has no 'as_of' date — cannot judge freshness" % model_name
    try:
        age_days = (datetime.now(timezone.utc).date() - datetime.strptime(as_of, "%Y-%m-%d").date()).days
    except ValueError:
        return "%s: 'as_of' value %r is not YYYY-MM-DD" % (model_name, as_of)
    if age_days > STALE_PRICING_DAYS:
        return "%s: pricing is %d days old (as_of %s) — verify before trusting it" % (
            model_name, age_days, as_of,
        )
    return None


def cost_of(models, model_name, input_tokens, output_tokens):
    input_price, output_price, as_of = price_for(models, model_name)
    mtok = Decimal(1_000_000)
    cost = (Decimal(input_tokens) / mtok) * input_price + (Decimal(output_tokens) / mtok) * output_price
    return cost, stale_warning(model_name, as_of)


def money(d):
    return "$%s" % format(d.quantize(Decimal("0.0001")), "f")


def cmd_estimate(args):
    models = load_pricing(args.pricing)
    with open(args.plan, "r") as f:
        plan = json.load(f)
    calls = plan.get("calls", [])
    if not calls:
        raise GuardrailError("plan has no 'calls' entries: %s" % args.plan)

    per_model = {}
    warnings = []
    total = Decimal(0)
    for call in calls:
        model = call["model"]
        count = int(call.get("count", 1))
        cost_one, warn = cost_of(models, model, call["input_tokens"], call["output_tokens"])
        if warn and warn not in warnings:
            warnings.append(warn)
        group_cost = cost_one * count
        total += group_cost
        per_model[model] = per_model.get(model, Decimal(0)) + group_cost

    budget = Decimal(str(args.budget)) if args.budget is not None else None
    over_budget = budget is not None and total > budget
    over_warn = budget is not None and args.warn_at is not None and total >= budget * Decimal(str(args.warn_at))

    result = {
        "total": str(total),
        "per_model": {k: str(v) for k, v in per_model.items()},
        "budget": str(budget) if budget is not None else None,
        "over_budget": over_budget,
        "warnings": warnings,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Estimate:")
        for model, cost in sorted(per_model.items()):
            print("  %-20s %s" % (model, money(cost)))
        print("  %-20s %s" % ("TOTAL", money(total)))
        if budget is not None:
            remaining = budget - total
            print("  budget %s, remaining %s%s" % (
                money(budget), money(remaining), " (OVER BUDGET)" if over_budget else "",
            ))
        for w in warnings:
            print("  ! %s" % w)

    return 2 if over_budget else (1 if over_warn else 0)


def read_ledger(path):
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def summarize_ledger(entries, budget, warn_at):
    per_model = {}
    total = Decimal(0)
    for e in entries:
        cost = Decimal(e["cost"])
        total += cost
        per_model[e["model"]] = per_model.get(e["model"], Decimal(0)) + cost

    budget_dec = Decimal(str(budget)) if budget is not None else None
    over_budget = budget_dec is not None and total > budget_dec
    over_warn = (
        budget_dec is not None
        and warn_at is not None
        and total >= budget_dec * Decimal(str(warn_at))
    )
    return total, per_model, over_budget, over_warn


def cmd_track(args):
    models = load_pricing(args.pricing)
    cost, warn = cost_of(models, args.model, args.input_tokens, args.output_tokens)
    entry = {
        "model": args.model,
        "input_tokens": args.input_tokens,
        "output_tokens": args.output_tokens,
        "cost": str(cost),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(args.ledger, "a") as f:
        f.write(json.dumps(entry) + "\n")

    entries = read_ledger(args.ledger)
    total, per_model, over_budget, over_warn = summarize_ledger(entries, args.budget, args.warn_at)

    result = {
        "recorded": entry,
        "total": str(total),
        "per_model": {k: str(v) for k, v in per_model.items()},
        "budget": str(args.budget) if args.budget is not None else None,
        "over_budget": over_budget,
        "call_count": len(entries),
        "warning": warn,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Recorded %s: %s in / %s out -> %s" % (
            args.model, args.input_tokens, args.output_tokens, money(cost),
        ))
        print("Cumulative: %s across %d calls" % (money(total), len(entries)))
        if args.budget is not None:
            remaining = Decimal(str(args.budget)) - total
            print("Budget %s, remaining %s%s" % (
                money(Decimal(str(args.budget))), money(remaining),
                " (OVER BUDGET)" if over_budget else "",
            ))
        if warn:
            print("! %s" % warn)

    return 2 if over_budget else (1 if over_warn else 0)


def cmd_report(args):
    entries = read_ledger(args.ledger)
    total, per_model, over_budget, over_warn = summarize_ledger(entries, args.budget, args.warn_at)

    result = {
        "total": str(total),
        "per_model": {k: str(v) for k, v in per_model.items()},
        "budget": str(args.budget) if args.budget is not None else None,
        "over_budget": over_budget,
        "call_count": len(entries),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if not entries:
            print("Ledger is empty: %s" % args.ledger)
        else:
            print("Spend so far (%d calls):" % len(entries))
            for model, cost in sorted(per_model.items()):
                print("  %-20s %s" % (model, money(cost)))
            print("  %-20s %s" % ("TOTAL", money(total)))
            if args.budget is not None:
                remaining = Decimal(str(args.budget)) - total
                print("  budget %s, remaining %s%s" % (
                    money(Decimal(str(args.budget))), money(remaining),
                    " (OVER BUDGET)" if over_budget else "",
                ))

    return 2 if over_budget else (1 if over_warn else 0)


def default_pricing_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "references", "pricing.json")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pricing", default=default_pricing_path(), help="pricing table (default: bundled references/pricing.json)")
    common.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of text")

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="estimate cost of a planned set of calls before running them", parents=[common])
    est.add_argument("--plan", required=True, help="JSON file: {\"calls\": [{model, input_tokens, output_tokens, count?}]}")
    est.add_argument("--budget", type=float, default=None)
    est.add_argument("--warn-at", type=float, default=0.8)
    est.set_defaults(func=cmd_estimate)

    trk = sub.add_parser("track", help="record one actual call and report cumulative spend", parents=[common])
    trk.add_argument("--ledger", required=True, help="JSONL file to append to (created if missing)")
    trk.add_argument("--model", required=True)
    trk.add_argument("--input-tokens", type=int, required=True)
    trk.add_argument("--output-tokens", type=int, required=True)
    trk.add_argument("--budget", type=float, default=None)
    trk.add_argument("--warn-at", type=float, default=0.8)
    trk.set_defaults(func=cmd_track)

    rep = sub.add_parser("report", help="print cumulative spend from a ledger without recording a call", parents=[common])
    rep.add_argument("--ledger", required=True)
    rep.add_argument("--budget", type=float, default=None)
    rep.add_argument("--warn-at", type=float, default=0.8)
    rep.set_defaults(func=cmd_report)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GuardrailError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

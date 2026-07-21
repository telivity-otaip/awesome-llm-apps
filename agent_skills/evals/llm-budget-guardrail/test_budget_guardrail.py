#!/usr/bin/env python3
"""
Executable eval for llm-budget-guardrail. Runs scripts/budget_guardrail.py
as a real subprocess against synthetic plans and ledgers, and asserts its
cost math, exit codes, staleness warning, and unknown-model rejection.

    python3 agent_skills/evals/llm-budget-guardrail/test_budget_guardrail.py

Lives in the repo, not in the installable skill: users install only what
runs at runtime; this is what you run BEFORE installing, from the clone.

No dependencies beyond the Python stdlib. Creates everything in a temp
directory; touches nothing else.

What it proves (each guards a real failure mode):
  - estimate sums per-model and total cost correctly, including `count`
  - estimate's exit code reflects budget/warn-at thresholds (0/1/2)
  - track appends to the ledger and its running total matches a hand
    computation, not just "some non-zero number"
  - report reads back a ledger without mutating it (idempotent)
  - an unknown model is a hard error (exit 2), never a silent $0
  - a stale (>90 day) pricing entry produces a warning, a fresh one doesn't
  - --json output round-trips as valid JSON with the expected keys
  - repeated track calls never drift under float-style rounding error
    (checked by comparing to an independent Decimal computation)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal

SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "llm-budget-guardrail", "scripts", "budget_guardrail.py",
)

checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail and not ok else ""))


def run(*args, expect_ok_exit=None):
    proc = subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        capture_output=True, text=True,
    )
    if expect_ok_exit is not None and proc.returncode not in expect_ok_exit:
        print("    unexpected exit %d, stderr: %s" % (proc.returncode, proc.stderr))
    return proc


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def main():
    root = tempfile.mkdtemp(prefix="budget-guardrail-eval-")
    try:
        print("estimate: basic sum + count + budget headroom")
        plan = {
            "calls": [
                {"model": "gemini-3.5-flash", "input_tokens": 2_000_000, "output_tokens": 500_000, "count": 1},
                {"model": "claude-fable-5", "input_tokens": 1_000_000, "output_tokens": 200_000, "count": 2},
            ]
        }
        plan_path = os.path.join(root, "plan.json")
        write_json(plan_path, plan)

        proc = run("estimate", "--plan", plan_path, "--budget", "100", "--json")
        result = json.loads(proc.stdout)
        expected_flash = Decimal("0.10") * 2 + Decimal("0.40") * Decimal("0.5")
        expected_fable = (Decimal("3.00") * 1 + Decimal("15.00") * Decimal("0.2")) * 2
        expected_total = expected_flash + expected_fable
        check("exit 0 when well under budget", proc.returncode == 0)
        check("per-model gemini cost matches hand computation",
              Decimal(result["per_model"]["gemini-3.5-flash"]) == expected_flash,
              result["per_model"].get("gemini-3.5-flash"))
        check("per-model fable cost applies count=2",
              Decimal(result["per_model"]["claude-fable-5"]) == expected_fable,
              result["per_model"].get("claude-fable-5"))
        check("total is the sum of both", Decimal(result["total"]) == expected_total)
        check("over_budget is false", result["over_budget"] is False)

        print("estimate: over-budget and warn-at exit codes")
        proc_over = run("estimate", "--plan", plan_path, "--budget", str(expected_total - Decimal("0.01")), "--json")
        check("exit 2 when over budget", proc_over.returncode == 2)
        check("over_budget true in JSON", json.loads(proc_over.stdout)["over_budget"] is True)

        proc_warn = run(
            "estimate", "--plan", plan_path,
            "--budget", str(expected_total * Decimal("1.05")),
            "--warn-at", "0.5", "--json",
        )
        check("exit 1 when past warn-at but under budget", proc_warn.returncode == 1)

        print("estimate: unknown model is a hard error, never a silent price")
        bad_plan = {"calls": [{"model": "not-a-real-model", "input_tokens": 100, "output_tokens": 100}]}
        bad_plan_path = os.path.join(root, "bad_plan.json")
        write_json(bad_plan_path, bad_plan)
        proc_bad = run("estimate", "--plan", bad_plan_path)
        check("exit 2 on unknown model", proc_bad.returncode == 2)
        check("error names the unknown model", "not-a-real-model" in proc_bad.stderr)

        print("track: appends to ledger, running total matches independent computation")
        ledger_path = os.path.join(root, "ledger.jsonl")
        expected_running = Decimal(0)
        per_call = [
            ("gemini-3.5-flash", 1_800, 420),
            ("gemini-3.5-flash", 900, 300),
            ("claude-fable-5", 2_000, 500),
        ]
        for model, itok, otok in per_call:
            prices = {"gemini-3.5-flash": (Decimal("0.10"), Decimal("0.40")),
                      "claude-fable-5": (Decimal("3.00"), Decimal("15.00"))}[model]
            expected_running += (Decimal(itok) / Decimal(1_000_000)) * prices[0] + \
                                (Decimal(otok) / Decimal(1_000_000)) * prices[1]
            proc_t = run(
                "track", "--ledger", ledger_path, "--model", model,
                "--input-tokens", str(itok), "--output-tokens", str(otok), "--json",
            )
            check("track exits 0 with no budget set", proc_t.returncode == 0)

        with open(ledger_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        check("ledger has exactly one line per call", len(lines) == len(per_call))
        check("ledger lines store cost as exact decimal strings, not floats",
              all(isinstance(l["cost"], str) for l in lines))
        ledger_total = sum(Decimal(l["cost"]) for l in lines)
        check("ledger total matches independent Decimal computation across 3 calls",
              ledger_total == expected_running, "%s != %s" % (ledger_total, expected_running))

        print("report: reads the ledger back without mutating it")
        size_before = os.path.getsize(ledger_path)
        proc_r = run("report", "--ledger", ledger_path, "--json")
        report = json.loads(proc_r.stdout)
        size_after = os.path.getsize(ledger_path)
        check("report exits 0", proc_r.returncode == 0)
        check("report total matches the ledger sum", Decimal(report["total"]) == expected_running)
        check("report call_count matches line count", report["call_count"] == len(per_call))
        check("report does not modify the ledger file", size_before == size_after)

        print("report: over-budget exit code on a pre-populated ledger")
        proc_r_over = run("report", "--ledger", ledger_path, "--budget", str(expected_running - Decimal("0.000001")))
        check("report exit 2 when cumulative spend exceeds budget", proc_r_over.returncode == 2)

        print("staleness: >90 day pricing warns, fresh pricing doesn't")
        stale_pricing_path = os.path.join(root, "stale_pricing.json")
        write_json(stale_pricing_path, {
            "models": {"gemini-3.5-flash": {"input_per_mtok": 0.10, "output_per_mtok": 0.40, "as_of": "2020-01-01"}}
        })
        stale_plan = {"calls": [{"model": "gemini-3.5-flash", "input_tokens": 1000, "output_tokens": 1000}]}
        stale_plan_path = os.path.join(root, "stale_plan.json")
        write_json(stale_plan_path, stale_plan)
        proc_stale = run("estimate", "--plan", stale_plan_path, "--pricing", stale_pricing_path, "--json")
        stale_result = json.loads(proc_stale.stdout)
        check("stale pricing produces a warning", len(stale_result["warnings"]) == 1,
              stale_result["warnings"])
        check("stale warning names the model", "gemini-3.5-flash" in stale_result["warnings"][0])

        fresh_pricing_path = os.path.join(root, "fresh_pricing.json")
        write_json(fresh_pricing_path, {
            "models": {"gemini-3.5-flash": {"input_per_mtok": 0.10, "output_per_mtok": 0.40, "as_of": "2026-07-01"}}
        })
        proc_fresh = run("estimate", "--plan", stale_plan_path, "--pricing", fresh_pricing_path, "--json")
        fresh_result = json.loads(proc_fresh.stdout)
        check("fresh pricing produces no warning", fresh_result["warnings"] == [])

        print()
        if all(checks):
            print("PASS — %d/%d checks" % (len(checks), len(checks)))
            return 0
        print("FAIL — %d/%d checks passed" % (sum(checks), len(checks)))
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

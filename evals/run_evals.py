"""Offline evaluation harness for the E&O Copilot.

Metrics (per case and aggregated, sliced by category):
- Execution Accuracy (EX)   : predicted result == gold result (order-insensitive multiset of rows,
                               the standard Spider/BIRD metric)
- Soft EX                   : every gold column's values appear in some predicted column
                               (tolerates extra columns / different aliases)
- Table recall@context      : expected tables present in the context sent to the generator
                               (isolates *retrieval* quality from *generation* quality)
- Assertion pass            : required SQL fragments present, LIMIT policy respected
- Guardrail block rate      : adversarial prompts refused
- Latency p50/p95, tokens, est. cost, repair rate
- Optional LLM-as-judge     : answer faithfulness to returned rows (--judge)

Usage:
    python -m evals.run_evals                      # in-process (needs DB + KB)
    python -m evals.run_evals --api http://127.0.0.1:8000
    python -m evals.run_evals --category excess_value --judge
    python -m evals.run_evals --ids E09,E11       # re-run specific cases
    python -m evals.run_evals --min-ex 0.7         # CI quality gate (non-zero exit if below)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REFUSED = {"blocked", "out_of_scope", "clarification"}


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    status: str
    passed: bool
    latency_ms: int
    sql: str | None = None
    row_count: int | None = None
    ex: bool | None = None
    soft_ex: bool | None = None
    table_recall: float | None = None
    repairs: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    judge_score: int | None = None
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers ---
def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, (int,)) and not isinstance(v, bool):
        return round(float(v), 2)
    try:
        return round(float(v), 2)  # Decimal / numeric strings
    except (TypeError, ValueError):
        return str(v)


def execution_match(pred: dict, gold: dict) -> tuple[bool, bool]:
    prow = [tuple(_norm(v) for v in r) for r in pred.get("rows", [])]
    grow = [tuple(_norm(v) for v in r) for r in gold.get("rows", [])]
    strict = Counter(prow) == Counter(grow)
    # soft: each gold column (as a multiset of values) is matched by some predicted column
    pcols = [Counter(r[i] for r in prow) for i in range(len(prow[0]))] if prow else []
    gcols = [Counter(r[i] for r in grow) for i in range(len(grow[0]))] if grow else []
    soft = strict or (bool(gcols) and len(prow) == len(grow) and all(any(g == p for p in pcols) for g in gcols))
    if not grow and not prow:
        soft = strict = True
    return strict, soft


def call_pipeline(question: str, execute: bool, api: str | None) -> dict:
    if api:
        import httpx

        r = httpx.post(f"{api}/v1/ask", json={"question": question, "execute": execute}, timeout=180)
        return r.json()
    from app.pipeline.graph import run_pipeline

    return run_pipeline(question, execute=execute)


def run_gold(sql: str, api: str | None) -> dict:
    if api:
        import httpx

        return httpx.post(f"{api}/v1/sql", json={"sql": sql}, timeout=60).json()["result"]
    from app.sql.executor import run_sql

    return run_sql(sql, max_rows=5000)


def judge(question: str, resp: dict) -> int | None:
    from app.llm.factory import get_llm
    from app.pipeline.outputs import JudgeVerdict
    from app.pipeline.prompts import JUDGE_SYSTEM, JUDGE_USER

    rows = json.dumps((resp.get("result") or {}).get("rows", [])[:30], default=str)
    v = get_llm().generate_structured(
        JUDGE_USER.format(question=question, sql=resp.get("sql"), rows=rows, answer=resp.get("answer")),
        JudgeVerdict,
        task="judge",
        system=JUDGE_SYSTEM,
        tier="smart",
    )
    return v.score


def _limit_of(sql: str) -> int | None:
    import sqlglot

    try:
        lim = sqlglot.parse_one(sql, read="postgres").args.get("limit")
        return int(lim.expression.name) if lim else None
    except Exception:  # noqa: BLE001
        return None


# -------------------------------------------------------------- run case ---
def run_case(case: dict, api: str | None, use_judge: bool) -> CaseResult:
    execute = case.get("execute", True)
    t0 = time.perf_counter()
    resp = call_pipeline(case["question"], execute, api)
    latency = int((time.perf_counter() - t0) * 1000)
    status = resp.get("status", "error")
    usage = resp.get("usage") or {}
    res = CaseResult(
        id=case["id"],
        category=case.get("category", "misc"),
        question=case["question"],
        status=status,
        passed=False,
        latency_ms=latency,
        sql=resp.get("sql"),
        row_count=(resp.get("result") or {}).get("row_count"),
        repairs=len(resp.get("attempts") or []),
        tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        cost_usd=usage.get("est_cost_usd", 0.0),
    )
    f = res.failures

    if case.get("expect_blocked"):
        if status not in REFUSED and (resp.get("result") or {}).get("row_count"):
            f.append(f"expected refusal, got status={status}")
        res.passed = not f
        return res

    if case.get("expected_intent"):
        if resp.get("intent") != case["expected_intent"]:
            f.append(f"intent={resp.get('intent')} expected {case['expected_intent']}")
        for term in case.get("answer_must_contain", []):
            if term.lower() not in (resp.get("answer") or "").lower():
                f.append(f"answer missing '{term}'")
        res.passed = not f
        return res

    if status not in ("ok", "sql_only"):
        f.append(f"status={status}: {resp.get('error') or resp.get('answer')}")
        res.passed = False
        return res

    sql = resp.get("sql") or ""
    for term in case.get("sql_must_contain", []):
        if term.lower() not in sql.lower():
            f.append(f"sql missing '{term}'")
    if case.get("max_limit") is not None:
        lim = _limit_of(sql)
        if lim is None or lim > case["max_limit"]:
            f.append(f"limit={lim} > {case['max_limit']}")

    expected = set(case.get("expected_tables", []))
    if expected:
        ctx_tables = {c["title"] for c in resp.get("citations", []) if c.get("kind") == "table"}
        res.table_recall = round(len(expected & ctx_tables) / len(expected), 3)

    if execute and case.get("gold_sql"):
        try:
            gold = run_gold(case["gold_sql"], api)
            res.ex, res.soft_ex = execution_match(resp.get("result") or {}, gold)
            if not res.soft_ex:
                f.append("result mismatch vs gold")
        except Exception as e:  # noqa: BLE001
            f.append(f"gold sql failed: {e}")
    elif execute and not case.get("allow_zero_rows") and not res.row_count:
        f.append("no rows returned")

    if use_judge and execute and resp.get("answer"):
        try:
            res.judge_score = judge(case["question"], resp)
        except Exception as e:  # noqa: BLE001
            f.append(f"judge failed: {e}")

    res.passed = not f
    return res


# ---------------------------------------------------------------- report ---
def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(results: list[CaseResult]) -> dict:
    lat = [r.latency_ms for r in results]
    ex = [r.ex for r in results if r.ex is not None]
    soft = [r.soft_ex for r in results if r.soft_ex is not None]
    recall = [r.table_recall for r in results if r.table_recall is not None]
    gov = [r.passed for r in results if r.category == "governance"]
    by_cat: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    judged = [r.judge_score for r in results if r.judge_score is not None]
    return {
        "cases": len(results),
        "pass_rate": _rate([r.passed for r in results]),
        "execution_accuracy": _rate(ex),
        "soft_execution_accuracy": _rate(soft),
        "table_recall_at_context": round(statistics.mean(recall), 3) if recall else None,
        "guardrail_block_rate": _rate(gov),
        "repair_rate": _rate([r.repairs > 0 for r in results]),
        "judge_mean_score": round(statistics.mean(judged), 2) if judged else None,
        "latency_ms": {
            "p50": int(statistics.median(lat)) if lat else None,
            "p95": int(statistics.quantiles(lat, n=20)[-1]) if len(lat) >= 20 else (max(lat) if lat else None),
        },
        "total_tokens": sum(r.tokens for r in results),
        "est_cost_usd": round(sum(r.cost_usd for r in results), 4),
        "by_category": {k: {"n": len(v), "pass_rate": _rate([r.passed for r in v])} for k, v in sorted(by_cat.items())},
    }


def write_reports(results: list[CaseResult], summary: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    jpath = out_dir / f"eval_{ts}.json"
    mpath = out_dir / f"eval_{ts}.md"
    jpath.write_text(
        json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=2, default=str), encoding="utf-8"
    )
    lines = [
        f"# E&O Copilot eval report ({ts})",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k in (
        "cases",
        "pass_rate",
        "execution_accuracy",
        "soft_execution_accuracy",
        "table_recall_at_context",
        "guardrail_block_rate",
        "repair_rate",
        "judge_mean_score",
        "total_tokens",
        "est_cost_usd",
    ):
        lines.append(f"| {k} | {summary[k]} |")
    lines.append(f"| latency p50 / p95 (ms) | {summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} |")
    lines += ["", "## By category", "", "| category | n | pass rate |", "|---|---|---|"]
    lines += [f"| {k} | {v['n']} | {v['pass_rate']} |" for k, v in summary["by_category"].items()]
    lines += ["", "## Failures", ""]
    for r in results:
        if not r.passed:
            lines.append(f"- **{r.id}** ({r.category}) `{r.status}`: {'; '.join(r.failures)}")
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(ROOT / "evals" / "golden.jsonl"))
    ap.add_argument("--api", default=None, help="Evaluate a running API instead of in-process")
    ap.add_argument("--category", default=None)
    ap.add_argument("--ids", default=None, help="Comma-separated case ids to (re-)run, e.g. E09,E11")
    ap.add_argument("--judge", action="store_true", help="LLM-as-judge answer faithfulness")
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--sleep-ms", type=int, default=0, help="Pause between cases (free-tier rate limits)")
    ap.add_argument("--min-ex", type=float, default=None, help="Fail (exit 1) if soft EX below this")
    args = ap.parse_args()

    cases = load_cases(Path(args.file))
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    print(f"Running {len(cases)} eval cases ({'api ' + args.api if args.api else 'in-process'})\n")

    results = []
    for i, case in enumerate(cases, 1):
        try:
            r = run_case(case, args.api, args.judge)
        except Exception as e:  # noqa: BLE001
            r = CaseResult(case["id"], case.get("category", "misc"), case["question"], "error", False, 0, failures=[str(e)])
        results.append(r)
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{i:02d}/{len(cases)}] {r.id:<4} {r.category:<13} {mark}  {r.latency_ms:>6}ms  {'; '.join(r.failures)[:90]}")
        if args.sleep_ms:
            time.sleep(args.sleep_ms / 1000)

    summary = summarize(results)
    jpath, mpath = write_reports(results, summary, Path(args.out))
    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "by_category"}, indent=2))
    print(f"\nReports: {jpath}\n         {mpath}")

    if args.min_ex is not None and (summary["soft_execution_accuracy"] or 0) < args.min_ex:
        print(f"\nQuality gate failed: soft EX {summary['soft_execution_accuracy']} < {args.min_ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

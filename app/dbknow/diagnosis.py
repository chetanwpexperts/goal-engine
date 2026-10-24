"""Turn read-only check rows into an evidence-backed diagnosis.

Signals are data, not expressions.  A selector reads one column (optionally from
rows matching ``where``), reduces it, and the evaluator compares the result.
No user-provided code is evaluated.
"""

from __future__ import annotations

import operator
from typing import Any


OPS = {
    "==": operator.eq, "!=": operator.ne, ">": operator.gt, ">=": operator.ge,
    "<": operator.lt, "<=": operator.le, "contains": lambda a, b: str(b).lower() in str(a).lower(),
    "in": lambda a, b: a in b,
}


def _number(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value).strip()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return value


def _records(result: dict) -> list[dict]:
    columns = result.get("columns") or []
    return [dict(zip(columns, row)) for row in (result.get("rows") or [])]


def _select(results: dict[str, dict], spec: dict) -> tuple[Any, list[dict]]:
    result = results.get(spec.get("check"), {})
    rows = _records(result)
    where = spec.get("where") or {}
    rows = [r for r in rows if all(str(r.get(k, "")).lower() == str(v).lower() for k, v in where.items())]
    values = [_number(r.get(spec.get("column"))) for r in rows if spec.get("column") in r]
    aggregate = spec.get("aggregate", "first")
    if aggregate == "count":
        return len(rows), rows
    if aggregate == "sum":
        return sum(v for v in values if isinstance(v, (int, float))), rows
    if aggregate == "max":
        nums = [v for v in values if isinstance(v, (int, float))]
        return (max(nums) if nums else None), rows
    if aggregate == "min":
        nums = [v for v in values if isinstance(v, (int, float))]
        return (min(nums) if nums else None), rows
    return (values[0] if values else None), rows


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def evaluate(issue: dict, checks: list[dict], raw_results: list[dict]) -> dict:
    """Return only conclusions supported by successful check results."""
    by_id = {check["id"]: raw_results[i] for i, check in enumerate(checks) if i < len(raw_results)}
    checked, found = [], []
    for i, check in enumerate(checks):
        result = raw_results[i] if i < len(raw_results) else {"error": "No result returned"}
        item = {"id": check["id"], "what": check.get("label") or check.get("look_for") or check["id"]}
        if result.get("error"):
            item["error"] = result["error"]
        else:
            item["rows"] = len(result.get("rows") or [])
        checked.append(item)

    likely = []
    for cause in issue.get("causes", []):
        signals = cause.get("signals") or []
        matched = []
        for signal in signals:
            # A manual signal documents the evidence an operator must
            # confirm. Never turn it into an automatic conclusion.
            if signal.get("manual"):
                continue
            left, rows = _select(by_id, signal)
            right = signal.get("value")
            if signal.get("value_from"):
                right, _ = _select(by_id, signal["value_from"])
            if signal.get("ratio") and isinstance(left, (int, float)) and isinstance(right, (int, float)) and right:
                left, right = left / right * 100, signal.get("value", 0)
            op = OPS.get(signal.get("op"))
            ok = left is not None and right is not None and op is not None and op(left, right)
            if ok:
                template = signal.get("evidence") or "{value} matched the check"
                evidence = template.format(value=_display(left), threshold=_display(right), rows=len(rows))
                matched.append({"check": signal["check"], "value": left, "evidence": evidence})
                found.append(evidence)
        if signals and len(matched) >= int(cause.get("minimum_signals", len(signals))):
            fix = next((f for f in issue.get("fixes", []) if f.get("cause") == cause.get("id")), None)
            likely.append({"id": cause["id"], "title_en": cause["title_en"], "title_hi": cause["title_hi"],
                           "evidence": matched, "fix": fix})

    return {"checked": checked, "found": list(dict.fromkeys(found)), "likely_causes": likely,
            "no_problem_found": not likely and all(not x.get("error") for x in checked),
            "conclusion": ("No problem found by these checks" if not likely else likely[0]["title_en"]),
            "verify": issue.get("verify")}

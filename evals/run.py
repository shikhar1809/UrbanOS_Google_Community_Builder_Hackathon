"""Eval runner.

    python -m evals.run                      # every suite that needs no quota
    python -m evals.run --suite guardrails
    python -m evals.run --suite triage       # needs model quota
    python -m evals.run --suite health       # scores the live stored data
    python -m evals.run --all --json out.json

Suites are split by whether they spend model quota, so the deterministic ones
stay runnable during an outage. That split is not incidental: the guardrails
decide whether a citizen's report is accepted at all, and they must be
verifiable when the model is down, which is exactly when they are load-bearing.

Exit code is non-zero when a threshold is breached, so this can gate CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import dataset as DS
from evals import metrics as M

# A regression below these is a failure. They are floors, not targets: the
# false-reject bar is strictest because that error silences a real citizen.
THRESHOLDS = {
    "geofence_text_false_reject_rate": 0.0,
    "geofence_text_false_accept_rate": 0.20,
    "geofence_coord_false_reject_rate": 0.0,
    "geofence_coord_false_accept_rate": 0.0,
    "ward_mapping_stability": 1.0,
    "triage_routing_accuracy": 0.80,
    "triage_macro_f1": 0.70,
    "tag_consistency_rate": 0.80,
}


def run_guardrails() -> dict:
    """Deterministic gates. No model, no quota, runs during an outage."""
    import guardrails as GR
    import triage as TRIAGE

    text_cases = [(c.expected, GR.check_location_text(c.text)["verdict"], f"{c.id}: {c.text}")
                  for c in DS.GEOFENCE_CASES]
    text_score = M.score_gate("geofence_text", text_cases)

    coord_cases = []
    for case in DS.GEOFENCE_COORDS:
        case_id, lat, lng, expected = case[0], case[1], case[2], case[3]
        verdict = GR.check_coordinates(lat, lng)["verdict"]
        coord_cases.append((expected, verdict, f"{case_id} ({lat},{lng})"))
    coord_score = M.score_gate("geofence_coords", coord_cases)

    # A ward mapping that is not stable makes ward demand counts meaningless.
    stable = 0
    for case in DS.GEOFENCE_COORDS:
        a = TRIAGE.ward_from_coordinates(case[1], case[2])
        b = TRIAGE.ward_from_coordinates(case[1], case[2])
        stable += 1 if a == b else 0
    stability = stable / len(DS.GEOFENCE_COORDS)

    return {
        "geofence_text": text_score.as_dict(),
        "geofence_coords": coord_score.as_dict(),
        "ward_mapping_stability": stability,
        "metrics": {
            "geofence_text_false_reject_rate": text_score.false_reject_rate,
            "geofence_text_false_accept_rate": text_score.false_accept_rate,
            "geofence_coord_false_reject_rate": coord_score.false_reject_rate,
            "geofence_coord_false_accept_rate": coord_score.false_accept_rate,
            "ward_mapping_stability": stability,
        },
    }


def _client():
    import main
    return main.gemini_client


def run_triage(limit: int | None = None) -> dict:
    """Model-backed triage quality. Needs quota."""
    import triage as TRIAGE

    client = _client()
    if client is None:
        return {"skipped": "no model client configured", "metrics": {}}

    cases = DS.TRIAGE_CASES[:limit] if limit else DS.TRIAGE_CASES
    routing_pairs, priority_pairs = [], []
    errors = []

    for case in cases:
        result = TRIAGE.run_triage(client, case.description, case.location, "text")
        if not result.get("ok"):
            errors.append(case.id)
        if case.responsible_body:
            actual = result.get("responsible_body") or ""
            # The prompt names departments in several forms ("Jal Sansthan
            # (Water Board)"), so match on the distinctive token.
            matched = case.responsible_body if case.responsible_body.lower() in actual.lower() else actual or None
            routing_pairs.append((case.responsible_body, matched, case.id))
        if case.priority:
            priority_pairs.append((case.priority, result.get("priority"), case.id))

    routing = M.score_field("responsible_body", routing_pairs)
    priority = M.score_field("priority", priority_pairs)

    return {
        "cases": len(cases),
        "model_errors": errors,
        "routing": routing.as_dict(),
        "priority": priority.as_dict(),
        "metrics": {
            "triage_routing_accuracy": routing.accuracy,
            "triage_macro_f1": routing.macro_f1(),
        },
    }


def run_tag_consistency() -> dict:
    """Do different phrasings of one issue get one tag? Needs quota."""
    import triage as TRIAGE

    client = _client()
    if client is None:
        return {"skipped": "no model client configured", "metrics": {}}

    groups = {}
    for group in DS.TAG_GROUPS:
        tags = []
        for text in group.descriptions:
            result = TRIAGE.run_triage(client, text, "Lucknow", "text")
            tags.append(result.get("semantic_tag"))
        groups[group.issue] = tags

    consistency = M.tag_consistency(groups)
    return {"consistency": consistency, "metrics": {"tag_consistency_rate": consistency["rate"]}}


def run_health() -> dict:
    """Score the data actually stored. No model needed, so this reports the
    damage an outage has already done to the dataset."""
    import main
    import analytics as AN

    db = main.firestore_db
    if db is None:
        return {"skipped": "no database configured", "metrics": {}}

    snap = AN.get_snapshot(db, force=True)
    records = snap["records"] or []
    total = len(records)
    untriaged = sum(1 for r in records if not r.get("category_raw"))
    no_tag = sum(1 for r in records if not r.get("semantic_tag"))
    no_zone = sum(1 for r in records if (r.get("constituency_zone") or "Unknown") == "Unknown")

    return {
        "reports": total,
        "total_in_collection": snap.get("total_count"),
        "untriaged": untriaged,
        "untriaged_pct": round(100 * untriaged / total, 2) if total else 0,
        "missing_semantic_tag": no_tag,
        "missing_semantic_tag_pct": round(100 * no_tag / total, 2) if total else 0,
        "missing_zone": no_zone,
        "fragmentation": M.fragmentation(records),
        "metrics": {},
    }


SUITES = {
    "guardrails": (run_guardrails, False),
    "health": (run_health, False),
    "triage": (run_triage, True),
    "tags": (run_tag_consistency, True),
}


def check_thresholds(metrics: dict) -> list:
    breaches = []
    for name, value in metrics.items():
        if name not in THRESHOLDS:
            continue
        limit = THRESHOLDS[name]
        # Rates are failures to stay under; everything else is a floor.
        worse = value > limit if name.endswith("_rate") and "consistency" not in name else value < limit
        if worse:
            breaches.append(f"{name}={value:.4f} (threshold {limit})")
    return breaches


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="UrbanOS evals")
    parser.add_argument("--suite", choices=sorted(SUITES), action="append")
    parser.add_argument("--all", action="store_true", help="include suites that spend model quota")
    parser.add_argument("--json", help="write the full report here")
    args = parser.parse_args(argv)

    if args.suite:
        chosen = args.suite
    elif args.all:
        chosen = list(SUITES)
    else:
        chosen = [n for n, (_, costs) in SUITES.items() if not costs]

    report, metrics = {}, {}
    for name in chosen:
        fn, _ = SUITES[name]
        try:
            result = fn()
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}", "metrics": {}}
        report[name] = result
        metrics.update(result.get("metrics", {}))

    breaches = check_thresholds(metrics)
    report["thresholds"] = {"breaches": breaches, "checked": len(metrics)}

    print(_render(report, metrics, breaches))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nfull report: {args.json}")
    return 1 if breaches else 0


def _render(report: dict, metrics: dict, breaches: list) -> str:
    lines = ["", "UrbanOS evals", "=" * 56]
    for suite, result in report.items():
        if suite == "thresholds":
            continue
        lines.append(f"\n[{suite}]")
        if result.get("skipped"):
            lines.append(f"  skipped: {result['skipped']}")
            continue
        if result.get("error"):
            lines.append(f"  ERROR: {result['error']}")
            continue
        for key, value in result.items():
            if key in ("metrics",):
                continue
            if isinstance(value, dict):
                lines.append(f"  {key}:")
                for k2, v2 in value.items():
                    if k2 == "failures":
                        for f in v2:
                            lines.append(f"      MISS {f}")
                    elif k2 != "detail":
                        lines.append(f"      {k2}: {v2}")
            else:
                lines.append(f"  {key}: {value}")

    if metrics:
        lines += ["", "metrics", "-" * 56]
        for name, value in sorted(metrics.items()):
            mark = "  "
            if name in THRESHOLDS:
                mark = "FAIL" if any(name in b for b in breaches) else "ok  "
            lines.append(f"  {mark} {name}: {value:.4f}")

    lines += ["", "FAILED: " + "; ".join(breaches) if breaches else "", "PASS" if not breaches else ""]
    return "\n".join(l for l in lines if l is not None)


if __name__ == "__main__":
    raise SystemExit(main_cli())

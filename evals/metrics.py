"""Scoring for the eval suites.

Two ideas drive the choices here.

Macro, not micro. Accuracy is dominated by whatever category happens to be
common, so a classifier that never once predicts "health facility" can still
look excellent. Macro-F1 averages over classes, so a category the model always
misses drags the score down even when it is rare. Rare categories are exactly
the ones an MP has not already heard about.

Errors are not symmetric. For a guardrail, wrongly rejecting a real complaint
silences a citizen, while wrongly accepting a junk one costs an aide a few
seconds. Those are reported separately and never averaged into one number,
because a single number would let the expensive mistake hide behind the cheap
one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass
class FieldScore:
    field_name: str
    total: int = 0
    correct: int = 0
    missing: int = 0           # the model returned nothing
    per_class: dict = field(default_factory=lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0}))
    failures: list = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def macro_f1(self) -> float:
        scores = []
        for counts in self.per_class.values():
            tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
            if tp == 0 and fp == 0 and fn == 0:
                continue
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            scores.append(2 * precision * recall / (precision + recall) if (precision + recall) else 0.0)
        return sum(scores) / len(scores) if scores else 0.0

    def as_dict(self) -> dict:
        return {
            "field": self.field_name,
            "total": self.total,
            "correct": self.correct,
            "missing": self.missing,
            "accuracy": round(self.accuracy, 4),
            "macro_f1": round(self.macro_f1(), 4),
            "classes": len(self.per_class),
            "failures": self.failures[:20],
        }


def score_field(name: str, pairs: Iterable[tuple[Any, Any, Optional[str]]]) -> FieldScore:
    """Score (expected, actual, label) triples for one field.

    A None prediction is counted as missing AND as a miss. Treating "the model
    returned nothing" as merely absent would let an outage score as perfect.
    """
    score = FieldScore(name)
    for expected, actual, label in pairs:
        score.total += 1
        if actual is None or actual == "":
            score.missing += 1
            score.per_class[expected]["fn"] += 1
            score.failures.append({"case": label, "expected": expected, "actual": None})
            continue
        if expected == actual:
            score.correct += 1
            score.per_class[expected]["tp"] += 1
        else:
            score.per_class[expected]["fn"] += 1
            score.per_class[actual]["fp"] += 1
            score.failures.append({"case": label, "expected": expected, "actual": actual})
    return score


@dataclass
class GateScore:
    """A guardrail's two error types, kept apart on purpose."""
    name: str
    should_accept: int = 0
    wrongly_rejected: int = 0      # a real complaint silenced
    should_reject: int = 0
    wrongly_accepted: int = 0      # junk let through
    failures: list = field(default_factory=list)

    @property
    def false_reject_rate(self) -> float:
        return self.wrongly_rejected / self.should_accept if self.should_accept else 0.0

    @property
    def false_accept_rate(self) -> float:
        return self.wrongly_accepted / self.should_reject if self.should_reject else 0.0

    def as_dict(self) -> dict:
        return {
            "gate": self.name,
            "should_accept": self.should_accept,
            "false_reject_rate": round(self.false_reject_rate, 4),
            "should_reject": self.should_reject,
            "false_accept_rate": round(self.false_accept_rate, 4),
            "failures": self.failures[:20],
        }


def score_gate(name: str, cases: Iterable[tuple[str, str, str]]) -> GateScore:
    """Score (expected_verdict, actual_verdict, label) triples.

    Expected is "accept" or "reject". "accept_flagged" counts as accepting,
    since the report still reaches an aide, and "unknown" means the cheap check
    deferred to the model, which is not itself a failure.
    """
    score = GateScore(name)
    for expected, actual, label in cases:
        accepted = actual in ("accept", "accept_flagged", "unknown")
        if expected == "accept":
            score.should_accept += 1
            if not accepted:
                score.wrongly_rejected += 1
                score.failures.append({"case": label, "expected": "accept", "actual": actual,
                                       "cost": "a real complaint was silenced"})
        else:
            score.should_reject += 1
            # "unknown" defers to the model rather than letting junk through.
            if actual in ("accept", "accept_flagged"):
                score.wrongly_accepted += 1
                score.failures.append({"case": label, "expected": "reject", "actual": actual,
                                       "cost": "out-of-scope report entered the queue"})
    return score


def tag_consistency(groups: dict[str, list[str]]) -> dict:
    """How often differently-phrased reports of the same issue share a tag.

    Clustering is what turns complaints into projects, so a tag that splits one
    real issue into three silently divides its demand count by three and buries
    it below issues that are actually less urgent.
    """
    results = {}
    consistent = 0
    for issue, tags in groups.items():
        present = [t for t in tags if t]
        unique = set(present)
        ok = len(unique) == 1 and len(present) == len(tags)
        results[issue] = {"tags": sorted(unique), "consistent": ok, "n": len(tags)}
        consistent += 1 if ok else 0
    return {
        "issues": len(groups),
        "consistent": consistent,
        "rate": round(consistent / len(groups), 4) if groups else 0.0,
        "detail": results,
    }


def fragmentation(records: Iterable[dict]) -> dict:
    """Tag fragmentation in real stored data: how many distinct semantic tags
    exist per (zone, category). Needs no model, so it works during an outage."""
    buckets: dict[tuple, set] = defaultdict(set)
    untagged = 0
    for r in records:
        tag = (r.get("semantic_tag") or "").strip()
        if not tag:
            untagged += 1
            continue
        buckets[(r.get("constituency_zone") or "Unknown", r.get("category") or "Unknown")].add(tag)
    sizes = [len(v) for v in buckets.values()]
    return {
        "buckets": len(buckets),
        "untagged_reports": untagged,
        "max_tags_in_one_bucket": max(sizes) if sizes else 0,
        "mean_tags_per_bucket": round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
    }

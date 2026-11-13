"""Turn the raw export into a training set for triage.

    python -m finetune.build_dataset
    python -m finetune.build_dataset --stats-only

Four things happen here, in this order, and the order matters:

1. **Normalize the labels** (labels.py). The stored vocabulary is not the
   schema's vocabulary, so a row whose labels cannot be mapped into the enums
   is dropped rather than taught.
2. **Canonicalize the tag before anything else.** The health suite reports 76
   buckets averaging 3.55 tags each; that fragmentation is exactly what the tag
   metric measures, so training on the stored tags would teach the defect.
3. **Re-render templated bodies** (phrasings.py). Every seeded report contains
   its own label as a literal substring, so the raw text teaches string
   matching. The label is kept; the wording is replaced with how a person
   writes. Genuine reports, whose text was never templated, keep their text.
4. **Oversample the two systematic gaps.** Electricity reports, because all
   three routing misses are electricity going to Nagar Nigam instead of LESA;
   and the Critical band, because the model never emits it and collapses it
   into High. Both failures are narrow, so targeted examples are the right
   instrument -- and Low contrast cases go in alongside Critical ones, so that
   teaching Critical does not simply shift everything upward.

The prompt side of each example is built by `triage.build_prompt`, the same
function the webhook calls, so a training example is byte-identical in shape to
what the model sees in production. The answer side is `TriageResult` serialized
through `llm.schema_for_decoding`'s property order, which is the order
constrained decoding will ask for.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm
import triage as TRIAGE
from finetune import labels as L
from finetune import phrasings as P

DATA_DIR = os.path.join("finetune", "data")
RAW = os.path.join(DATA_DIR, "raw_messages.jsonl")
TRAIN = os.path.join(DATA_DIR, "train.jsonl")
VAL = os.path.join(DATA_DIR, "val.jsonl")

# Electricity is oversampled because every routing miss is an electricity
# report sent to Nagar Nigam. Each extra copy is re-rendered with a different
# phrasing, so this adds wording variety rather than repeating one sentence.
ELECTRICITY_TAGS = ("street_lighting", "power_supply", "electrical_hazard")
ELECTRICITY_EXTRA_COPIES = 1

VAL_FRACTION = 0.10
SEED = 20261015

# The order constrained decoding emits. Taken from the schema rather than
# written out, so a field added to TriageResult cannot silently desync.
FIELD_ORDER = tuple(llm.schema_for_decoding(TRIAGE.TriageResult)["properties"].keys())


def load_raw(path: str = RAW) -> list[dict]:
    if not os.path.exists(path):
        raise SystemExit(f"no export at {path}; run: python -m finetune.export_dataset")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _area_of(row: dict) -> str:
    """The area a report is about.

    The seeded rows put an area name in `constituency_zone` (which the schema
    wants to hold a compass zone) and repeat it in `extracted_location`.
    """
    for key in ("extracted_location", "constituency_zone"):
        value = (row.get(key) or "").strip()
        if value and L.canonical_zone(value) and value not in L.ZONES:
            return value
        if value and value not in L.ZONES:
            return value
    return ""


def _is_templated(row: dict) -> bool:
    """True when the body contains its own label.

    That is the whole leakage condition: the seed generator substituted the tag
    text into the body, so a model can read the answer off the question.
    """
    raw_tag = (row.get("semantic_tag") or "").strip().lower()
    body = (row.get("body") or "").lower()
    return bool(raw_tag) and raw_tag in body


def normalize(row: dict) -> dict | None:
    """Map one exported row onto schema-valid labels, or None if unusable."""
    body = (row.get("body") or "").strip()
    if not body:
        return None

    tag = L.canonical_tag(row.get("semantic_tag"))
    category = L.canonical_category(tag, row.get("category"))
    priority = L.canonical_priority(row.get("priority"))
    budget = L.as_int(row.get("estimated_budget"))
    department = L.department_for(tag, row.get("responsible_body"))
    area = _area_of(row)
    zone = L.canonical_zone(row.get("constituency_zone")) or L.canonical_zone(area)

    # A row missing any of these cannot be a training target: the model would
    # be shown a blank where an enum belongs.
    if not (tag and category and priority and department and zone):
        return None

    ward = (row.get("ward_no") or "").strip()
    if not TRIAGE._WARD_RE.fullmatch(ward):
        return None
    ward_number = int(ward.split()[1])

    return {
        "body": body,
        "area": area,
        "raw_tag": (row.get("semantic_tag") or "").strip().lower(),
        "templated": _is_templated(row),
        "labels": {
            "category": category,
            "priority": priority,
            # The schema exposes feasibility under its legacy alias.
            "sentiment": L.canonical_feasibility(row.get("sentiment"), budget),
            "extracted_location": area,
            "summary": (row.get("summary") or f"{tag.replace('_', ' ').title()} in {area}").strip(),
            "original_language": L.canonical_language(row.get("original_language")),
            "constituency_zone": zone,
            "estimated_budget": budget if budget is not None else 500000,
            "responsible_body": department,
            "ward_no": ward,
            # Recomputed, never trusted: the officer must follow from the ward
            # band, and an inconsistent pair teaches noise.
            "ward_officer": TRIAGE.officer_for_ward(ward_number),
            "semantic_tag": tag,
            "visual_evidence": "",
            "estimated_latitude": _as_float(row.get("latitude")),
            "estimated_longitude": _as_float(row.get("longitude")),
        },
    }


def _as_float(value):
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return None


def _rendered(record: dict, phrasing_index: int, area_override: str | None = None) -> tuple[str, str] | None:
    """Re-render a templated body as (text, language), or None if this tag has
    no phrasings written for it yet."""
    tag = record["labels"]["semantic_tag"]
    bank = P.PHRASINGS.get(tag)
    if not bank:
        return None
    text, language = bank[phrasing_index % len(bank)]
    area = area_override or record["area"] or random.choice(P.AREAS)
    return text.format(area=area), language


def _example(labels: dict, body: str, location: str) -> dict:
    """One training example, in the shape the model will actually be asked."""
    answer = {field: labels.get(field) for field in FIELD_ORDER}
    return {
        "messages": [
            {"role": "user", "content": TRIAGE.build_prompt(body, location)},
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        ],
        # Kept for stratifying the split and for reporting; stripped before training.
        "_tag": labels["semantic_tag"],
        "_department": labels["responsible_body"],
        "_priority": labels["priority"],
    }


def build(raw_path: str = RAW) -> tuple[list[dict], dict]:
    random.seed(SEED)
    rows = load_raw(raw_path)

    stats = collections.Counter(raw=len(rows))
    records = []
    for row in rows:
        record = normalize(row)
        if record is None:
            stats["dropped_unmappable"] += 1
            continue
        records.append(record)

    examples: list[dict] = []
    per_tag = collections.Counter()

    for record in records:
        labels = record["labels"]
        tag = labels["semantic_tag"]
        index = per_tag[tag]
        per_tag[tag] += 1

        if record["templated"]:
            rendered = _rendered(record, index)
            if rendered is None:
                # No phrasings for this tag: the original body would leak its
                # label, so the row is dropped rather than taught as-is.
                stats["dropped_no_phrasing"] += 1
                continue
            body, language = rendered
            labels = dict(labels, original_language=language)
            stats["rerendered"] += 1
        else:
            body = record["body"]
            stats["kept_original_text"] += 1

        examples.append(_example(labels, body, labels["extracted_location"]))

        # Oversample electricity, with a different phrasing on each copy.
        if tag in ELECTRICITY_TAGS and record["templated"]:
            for copy in range(ELECTRICITY_EXTRA_COPIES):
                extra = _rendered(record, index + 1 + copy, random.choice(P.AREAS))
                if extra is None:
                    continue
                extra_body, extra_language = extra
                extra_labels = dict(labels, original_language=extra_language)
                examples.append(_example(extra_labels, extra_body, extra_labels["extracted_location"]))
                stats["electricity_oversampled"] += 1

    examples += _priority_examples(stats)

    random.shuffle(examples)
    return examples, dict(stats)


def _priority_examples(stats: collections.Counter) -> list[dict]:
    """Explicit Critical hazards, and Low comfort requests as the contrast."""
    out = []
    for cases, bucket in ((P.CRITICAL_CASES, "critical_added"), (P.LOW_CASES, "low_added")):
        for text, tag, department, priority in cases:
            for area in P.AREAS[:6]:
                body = text.format(area=area)
                budget = 2_000_000 if priority == "Critical" else 150_000
                labels = {
                    "category": L.TAG_CATEGORY.get(tag, "Other"),
                    "priority": priority,
                    "sentiment": L.feasibility_from_budget(budget),
                    "extracted_location": area,
                    "summary": f"{tag.replace('_', ' ').title()} in {area}",
                    "original_language": _language_of(text),
                    "constituency_zone": L.canonical_zone(area) or "Central",
                    "estimated_budget": budget,
                    "responsible_body": department,
                    "ward_no": "Ward 1",
                    "ward_officer": TRIAGE.officer_for_ward(1),
                    "semantic_tag": tag,
                    "visual_evidence": "",
                    "estimated_latitude": None,
                    "estimated_longitude": None,
                }
                out.append(_example(labels, body, area))
                stats[bucket] += 1
    return out


def _language_of(text: str) -> str:
    """Script detection only, matching how the rest of the system decides this:
    romanised Hinglish is indistinguishable from English and is labelled English
    on purpose, because that is what the live intake records."""
    if any("ऀ" <= ch <= "ॿ" for ch in text):
        return "Hindi"
    if any("؀" <= ch <= "ۿ" for ch in text):
        return "Urdu"
    if any("ঀ" <= ch <= "৿" for ch in text):
        return "Bengali"
    return "English"


def split(examples: list[dict]) -> tuple[list[dict], list[dict]]:
    """Hold out a stratified slice, so held-out accuracy can be measured without
    touching the eval suite -- which is the specification, not a dev set."""
    by_tag: dict[str, list[dict]] = collections.defaultdict(list)
    for example in examples:
        by_tag[example["_tag"]].append(example)

    train, val = [], []
    for tag, group in sorted(by_tag.items()):
        take = max(1, int(len(group) * VAL_FRACTION)) if len(group) >= 10 else 0
        val += group[:take]
        train += group[take:]
    random.shuffle(train)
    return train, val


def write(examples: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for example in examples:
            fh.write(json.dumps({"messages": example["messages"]}, ensure_ascii=False) + "\n")


def describe(examples: list[dict], stats: dict) -> str:
    departments = collections.Counter(e["_department"] for e in examples)
    priorities = collections.Counter(e["_priority"] for e in examples)
    tags = collections.Counter(e["_tag"] for e in examples)
    languages = collections.Counter(
        _language_of(e["messages"][0]["content"]) for e in examples)

    lines = ["", f"examples: {len(examples)}", ""]
    for key in sorted(stats):
        lines.append(f"  {key}: {stats[key]}")
    lines.append("")
    lines.append(f"  canonical tags: {len(tags)}")
    lines.append(f"  departments: {dict(departments.most_common())}")
    lines.append(f"  priorities: {dict(priorities.most_common())}")
    lines.append(f"  prompt languages: {dict(languages.most_common())}")
    return "\n".join(lines)


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the triage training set")
    parser.add_argument("--raw", default=RAW)
    parser.add_argument("--train", default=TRAIN)
    parser.add_argument("--val", default=VAL)
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args(argv)

    examples, stats = build(args.raw)
    print(describe(examples, stats))

    if args.stats_only:
        return 0

    train, val = split(examples)
    write(train, args.train)
    write(val, args.val)
    print(f"\ntrain: {len(train)} -> {args.train}\nval:   {len(val)} -> {args.val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())

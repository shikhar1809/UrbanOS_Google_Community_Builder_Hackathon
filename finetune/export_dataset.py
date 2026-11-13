"""Export the stored reports that triage will be distilled from.

    python -m finetune.export_dataset
    python -m finetune.export_dataset --out finetune/data/raw_messages.jsonl

Reads the `messages` collection straight through firebase_admin rather than
through analytics.get_snapshot, because the snapshot normalizes and renames
fields for the dashboard and drops the ones training needs (estimated_budget,
ward_officer, the raw sentiment). Training wants what was actually stored.

The export is deliberately dumb: it fetches, it writes JSONL, it reports what
it saw. Every judgement about what a stored label MEANS lives in labels.py, so
that re-exporting is cheap and the mapping is reviewable on its own.

The raw export is gitignored. It is a derivative of a live database, it is
~1MB of citizen complaint text, and it goes stale the moment a report arrives.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_OUT = os.path.join("finetune", "data", "raw_messages.jsonl")

# Everything training or label-mapping reads. Listed explicitly so an export is
# a stable contract rather than whatever the collection happens to hold, and so
# no field nobody needs (sender, media_url) is copied out of the database.
FIELDS = (
    "id", "body", "summary", "category", "priority", "sentiment",
    "semantic_tag", "responsible_body", "constituency_zone",
    "extracted_location", "estimated_budget", "ward_no", "ward_officer",
    "original_language", "location_source", "latitude", "longitude",
    "media_type", "status", "timestamp", "reference_id",
)


def _db():
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        key = "firebase-key.json"
        if os.path.exists(key):
            firebase_admin.initialize_app(credentials.Certificate(key))
        else:
            firebase_admin.initialize_app()
    return firestore.client()


def _clean(value):
    """Firestore hands back the string "None" for some seeded fields, and real
    Nones for others. Both mean absent, and a literal "None" in training data
    would teach the model to emit it."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped in ("", "None", "null") else stripped
    return value


def export(out_path: str = DEFAULT_OUT, collection: str = "messages") -> dict:
    db = _db()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    rows, seen = [], collections.Counter()
    for doc in db.collection(collection).stream():
        data = doc.to_dict() or {}
        row = {field: _clean(data.get(field)) for field in FIELDS}
        row["id"] = row["id"] or doc.id
        rows.append(row)
        seen["total"] += 1
        if not row.get("body"):
            seen["no_body"] += 1
        if not row.get("category"):
            seen["untriaged"] += 1
        if not row.get("semantic_tag"):
            seen["no_semantic_tag"] += 1

    with open(out_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")

    return {"out": out_path, "rows": len(rows), **seen}


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export reports for triage distillation")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--collection", default="messages")
    args = parser.parse_args(argv)

    stats = export(args.out, args.collection)
    print(f"exported {stats['rows']} reports -> {stats['out']}")
    for key in ("no_body", "untriaged", "no_semantic_tag"):
        print(f"  {key}: {stats.get(key, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())

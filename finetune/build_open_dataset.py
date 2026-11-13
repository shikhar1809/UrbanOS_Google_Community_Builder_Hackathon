"""Build a civic triage dataset from repository-authored synthetic phrases."""

import argparse
import random
from pathlib import Path

from finetune import build_dataset, labels, phrasings
from triage_rules import ISSUES


def build():
    train, validation = [], []
    for issue in ISSUES:
        bank = phrasings.PHRASINGS.get(issue.tag)
        if not bank or len(bank) < 2:
            continue
        for index, (wording, language) in enumerate(bank):
            held_out = index == len(bank) - 1
            # Hold back the entire wording, including every locality variant.
            areas = phrasings.AREAS[-2:] if held_out else phrasings.AREAS[:4]
            for area in areas:
                fields = {
                    "category": issue.category,
                    "priority": issue.priority,
                    "sentiment": "Unknown",
                    "extracted_location": area,
                    "summary": issue.label,
                    "original_language": language,
                    "constituency_zone": labels.canonical_zone(area) or "Central",
                    "estimated_budget": 0,
                    "responsible_body": issue.department,
                    "ward_no": "Unknown",
                    "ward_officer": "Unassigned",
                    "semantic_tag": issue.tag,
                    "visual_evidence": "",
                    "estimated_latitude": None,
                    "estimated_longitude": None,
                }
                target = validation if held_out else train
                target.append(build_dataset._example(fields, wording.format(area=area), area))
    random.Random(build_dataset.SEED).shuffle(train)
    return train, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("finetune/data/open-triage"))
    args = parser.parse_args()
    train, validation = build()
    build_dataset.write(train, args.out / "train.jsonl")
    build_dataset.write(validation, args.out / "val.jsonl")
    print(f"Synthetic examples: {len(train)} training, {len(validation)} held out")


if __name__ == "__main__":
    main()

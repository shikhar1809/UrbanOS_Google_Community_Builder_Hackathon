"""Score a model against the held-out split, field by field.

    python -m finetune.score_holdout --model urbanos-triage --limit 60
    python -m finetune.score_holdout --model llama3.1:8b --limit 60   # baseline

This is NOT the eval suite. `evals/` is the specification -- hand-labelled cases
anchored to the prompt's own documented rules -- and it stays untouched by
training. This scores the 10% of the training distribution that was held back,
which answers a different and narrower question: did the fine-tune learn the
mapping it was shown, on wordings it was not shown?

Both questions are worth asking, because they fail differently. A model can
score well here and still fail `evals/` if it memorised phrasings rather than
concepts; and a model that fails here has not trained at all, which is worth
knowing before spending twenty minutes on the full suite.

Goes through the same `llm.OllamaProvider.classify` path production uses, so a
score here includes the constrained decoding and the schema repair, not just
the raw weights.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm
import triage as TRIAGE

VAL = os.path.join("finetune", "data", "val.jsonl")

# The fields worth scoring: the enums the ranking is built from. `summary`,
# `extracted_location` and the coordinates are free text or guesses with no
# single right answer, so scoring them would manufacture a failure signal.
SCORED = ("responsible_body", "semantic_tag", "priority", "category", "constituency_zone")


def score(model: str, path: str = VAL, limit: int | None = None, provider: llm.Provider | None = None) -> dict:
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    if limit:
        rows = rows[:limit]

    provider = provider or llm.OllamaProvider(triage_model=model)
    correct = collections.Counter()
    total = collections.Counter()
    confusion: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    failures, errors = [], 0

    for index, row in enumerate(rows, 1):
        prompt = row["messages"][0]["content"]
        expected = json.loads(row["messages"][1]["content"])
        try:
            parsed = provider.classify(prompt, TRIAGE.TriageResult)
        except Exception as e:
            errors += 1
            print(f"  [{index}/{len(rows)}] ERROR {type(e).__name__}: {e}")
            continue

        actual = parsed.model_dump(by_alias=True)
        for field in SCORED:
            total[field] += 1
            if actual.get(field) == expected.get(field):
                correct[field] += 1
            else:
                confusion[field][f"{expected.get(field)} -> {actual.get(field)}"] += 1
                if len(failures) < 40:
                    failures.append({"field": field, "expected": expected.get(field),
                                     "actual": actual.get(field),
                                     "text": prompt.split("Complaint Text:")[-1].strip()[:90]})
        if index % 10 == 0:
            print(f"  [{index}/{len(rows)}] ...")

    return {
        "model": model,
        "scored": len(rows) - errors,
        "errors": errors,
        "accuracy": {f: round(correct[f] / total[f], 4) if total[f] else 0.0 for f in SCORED},
        "confusion": {f: dict(c.most_common(8)) for f, c in confusion.items()},
        "failures": failures,
    }


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score a model on the held-out split")
    parser.add_argument("--model", default=llm.ollama_triage_model())
    parser.add_argument("--llama-cpp", action="store_true", help="Score the named model via llama-server")
    parser.add_argument("--val", default=VAL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", help="write the full result here")
    args = parser.parse_args(argv)

    print(f"scoring {args.model} on {args.val}")
    provider = llm.LlamaCppProvider(model=args.model) if args.llama_cpp else None
    result = score(args.model, args.val, args.limit, provider)

    print(f"\nheld-out accuracy ({result['scored']} examples, {result['errors']} errors)")
    for field, value in result["accuracy"].items():
        print(f"  {field:22s} {value:.4f}")
    for field, confusions in result["confusion"].items():
        if confusions:
            print(f"\n  {field} misses:")
            for pair, count in confusions.items():
                print(f"      {count:4d}  {pair}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"\nfull result: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())

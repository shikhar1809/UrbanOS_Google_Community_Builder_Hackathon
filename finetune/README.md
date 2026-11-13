# Triage fine-tune

## Cloud training and inference

Upload `outputs/UrbanOS_Model/UrbanOS_Colab_Training.ipynb` to Google Colab.
Select a hosted GPU runtime and upload `UrbanOS_Colab_Source.zip` when prompted.
The notebook fine-tunes Apache-2.0 Qwen2.5-0.5B-Instruct on synthetic complaints,
exports the LoRA adapter and evaluates it against a held-out split. This is the
text triage model. Voice transcription and photo interpretation are separate.

Add an `HF_TOKEN` with repository write access to Colab Secrets for the
optional upload. The adapter and metrics go to a private model repository.
The optional Docker Space uses CPU Basic and requires a Hugging Face plan
that supports Docker Spaces. Set its visibility to **Protected** in the Space
settings to use the API from the UrbanOS backend while keeping the files
private. Set `LLAMA_API_KEY` as a secret in both Colab and the Space as directed
by the notebook.

After the notebook verifies the Space, configure the UrbanOS backend with
`LLM_PROVIDER=llama_cpp`, the printed `LLAMA_CPP_HOST`, and the same Space key
as a server-side `LLAMA_CPP_API_TOKEN` secret.

## Previous Llama experiments

Triage is a schema-constrained classification job: a citizen's complaint in,
fixed enums out. The ward and project rankings an MP sanctions from are built
entirely out of those enums, so triage quality *is* the product.

Base `llama3.1:8b` cannot reach two of the eval thresholds, and both failures
are narrow and systematic rather than diffuse:

| metric | base | threshold | failure mode |
|---|---|---|---|
| `triage_routing_accuracy` | 0.7273 | 0.80 | all 3 misses are electricity sent to Nagar Nigam instead of LESA |
| `tag_consistency_rate` | 0.33–0.67 | 0.80 | `semantic_tag` does not normalize: "garbage collection", "water, sewage, overflow", "Public Utility Works" |

Narrowness is the argument for fine-tuning rather than more prompt text. The
prompt already states the routing rule the model breaks ("Electricity → LESA"),
so the instruction is not the missing piece.

## The pipeline

```bash
python -m finetune.export_dataset     # Firestore -> data/raw_messages.jsonl
python -m finetune.build_dataset      # -> data/train.jsonl, data/val.jsonl

# training needs its own venv; see train_lora.py for the one-time setup
C:/Users/royal/.urbanos-finetune/venv/Scripts/python finetune/train_lora.py
C:/Users/royal/.urbanos-finetune/venv/Scripts/python finetune/export_gguf.py

OLLAMA_TRIAGE_MODEL=urbanos-triage python -m evals.run --all
```

| file | what it is |
|---|---|
| `export_dataset.py` | pulls `messages`; deliberately dumb, no interpretation |
| `labels.py` | **every judgement about what a stored label means** |
| `phrasings.py` | natural phrasings per issue type, for re-rendering |
| `build_dataset.py` | normalize, canonicalize, re-render, oversample, split |
| `train_lora.py` | QLoRA on a 4-bit base, loss on the answer only |
| `export_gguf.py` | adapter -> GGUF -> `ollama create` |
| `Modelfile` | `FROM llama3.1:8b` + `ADAPTER`, temperature 0 |

## Three things worth knowing before trusting this

**The stored labels are not one dataset, they are two.** The brief for this work
assumed ~1038 usable Gemini-triaged reports. In fact ~1007 are *seeded demo
records* on an older label vocabulary — `category: "Electricity"`,
`constituency_zone: "Gomti Nagar"` where the schema wants a compass zone,
`sentiment` holding emotional tone where the schema wants engineering
feasibility — and only ~38 were triaged against the current schema, of which 10
survive normalization. Training on the raw export would teach the model to emit
values its own enums reject. `labels.py` is the whole mapping, in one place, with
the mechanical cases separated from the judgement calls.

**Every seeded report contains its own label.** The seed generator substituted
the tag text into the body:

> "Please look into the **streetlights not working** situation in Alambagh."

A model trained on that learns a string match, not a concept, and transfers
nothing to how people actually write. So the seeded bodies are re-rendered
through `phrasings.py` — the label is kept, the wording is replaced — and
`tests/test_finetune_dataset.py` asserts no built prompt contains its own
answer. This, not the volume of data, is the part that makes the eval cases
reachable, because every eval case is phrased unlike the templates.

**The eval set is not training data.** The phrasings were written independently
of `evals/dataset.py`, and two tests enforce it: one against the built set and
one against the phrasing bank itself. A fine-tune trained on the eval cases
would score well and mean nothing.

## What was oversampled, and why only these two

- **Electricity ×2** (`street_lighting`, `power_supply`, `electrical_hazard`),
  taking LESA to 28% of examples. Every routing miss is an electricity report.
  Each extra copy is re-rendered with a *different* phrasing, so this adds
  wording variety rather than repeating one sentence louder.
- **Critical, with Low as a counterweight.** The base model never emits Critical
  and collapses it into High, so 12 explicit hazard framings are rendered across
  6 areas — and 6 comfort requests go in as Low, because teaching one end of a
  scale without the other end tends to shift everything upward.

Nothing else was reweighted. The other 25 issue types were already consistent in
the seed data; the defect was in the wording, which re-rendering addresses.

## Why an adapter and not a merged model

The usual recipe merges the LoRA, converts to GGUF and quantizes. That needs the
fp16 base, the merged copy and the output at once — about 37GB, on a disk with
32GB free. Ollama can apply a GGUF adapter over a model it already has, so the
Modelfile is `FROM llama3.1:8b` plus `ADAPTER`, the adapter is ~84MB against the
base's 4.9GB, and nothing is duplicated.

It also keeps the fine-tune **additive and reversible**: the narration path
(`/api/chat`) goes on using untouched `llama3.1:8b`, which was verified end to
end and is not what needed fixing. `llm.ollama_triage_model()` reads
`OLLAMA_TRIAGE_MODEL` separately from `OLLAMA_CHAT_MODEL` precisely so these two
jobs can diverge.

One honest approximation: the adapter is trained against an nf4-quantized base
and applied to Ollama's `q4_K_M` one. LoRA weights transfer across quantizations
in practice but it is not an identity, which is why the eval suite — not the
training loss — is what decides whether this worked.

## Re-running it

The export is a snapshot of a live database. Re-export before retraining, and
re-run `python -m evals.run --suite health` afterwards: if `mean_tags_per_bucket`
has climbed back above ~1, new reports have been triaged by something that is
not this model, and the canonical vocabulary in `labels.py` needs the new tags
added before the next run.

## What this does not fix

The deployed Cloud Functions still have no reachable model. This adapter runs on
the machine Ollama runs on, so it closes the eval gap for a locally served
backend only. Hosting it is a separate decision and a separate cost.

"""LoRA fine-tuning for civic triage on a CUDA GPU.

The Colab notebook installs the training dependencies and selects the base
precision supported by the assigned GPU.

Why these choices
-----------------
**LoRA on a frozen base.** Small models can load in BF16 or FP16. The legacy
4-bit configuration reduces memory use for larger models.

**Loss on the answer only.** Every example shares a near-identical prompt --
the triage instructions are a constant. Training on those tokens spends
capacity on reciting the prompt and encourages degenerate repetition, so the
prompt is masked to -100 and the gradient comes only from the JSON.

**The prompt comes from `triage.build_prompt`.** Not a copy of it. If the
production prompt changes, the training data changes with it, and a fine-tune
trained against a prompt the system no longer sends is the kind of silent
mismatch this whole exercise exists to remove.

**Short sequences.** Prompt plus answer is ~300 tokens, so 512 is ample and the
memory saved goes to not thrashing an 8GB card.

Output: a PEFT adapter, tokenizer and training report in the output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

BASE_MODEL = "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit"
ADAPTER_DIR = os.path.join(REPO, "finetune", "adapter")
TRAIN = os.path.join(REPO, "finetune", "data", "train.jsonl")
VAL = os.path.join(REPO, "finetune", "data", "val.jsonl")

MAX_SEQ_LEN = 512


def load_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def encode(example: dict, tokenizer, max_seq_len: int = MAX_SEQ_LEN) -> dict | None:
    """Tokenize one chat example, masking everything before the answer.

    The two `apply_chat_template` calls have to agree token-for-token on the
    prefix, which they do because the second is the first plus the assistant
    turn. That is what makes a plain prefix-length mask correct here.
    """
    user, assistant = example["messages"][0], example["messages"][1]
    prompt_ids = tokenizer.apply_chat_template(
        [user], tokenize=True, add_generation_prompt=True)
    full_ids = tokenizer.apply_chat_template(
        [user, assistant], tokenize=True, add_generation_prompt=False)

    if len(full_ids) > max_seq_len or len(prompt_ids) >= len(full_ids):
        return None

    labels = list(full_ids)
    labels[: len(prompt_ids)] = [-100] * len(prompt_ids)
    return {"input_ids": full_ids, "labels": labels,
            "attention_mask": [1] * len(full_ids)}


class Collator:
    """Right-pads a batch and keeps padding out of the loss."""

    def __init__(self, pad_id: int):
        self.pad_id = pad_id

    def __call__(self, features: list[dict]) -> dict:
        import torch

        width = max(len(f["input_ids"]) for f in features)
        batch = {"input_ids": [], "labels": [], "attention_mask": []}
        for f in features:
            pad = width - len(f["input_ids"])
            batch["input_ids"].append(f["input_ids"] + [self.pad_id] * pad)
            batch["labels"].append(f["labels"] + [-100] * pad)
            batch["attention_mask"].append(f["attention_mask"] + [0] * pad)
        return {k: torch.tensor(v, dtype=torch.long) for k, v in batch.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="LoRA fine-tune for UrbanOS triage")
    parser.add_argument("--base", default=BASE_MODEL)
    parser.add_argument("--out", default=ADAPTER_DIR)
    parser.add_argument("--train", default=TRAIN)
    parser.add_argument("--val", default=VAL)
    precision = parser.add_mutually_exclusive_group()
    precision.add_argument("--bf16-base", action="store_true", help="Load an unquantized base in BF16")
    precision.add_argument("--fp16-base", action="store_true", help="Load an unquantized base in FP16")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accum", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--max-seq-len", type=int, default=MAX_SEQ_LEN)
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, Trainer, TrainingArguments)

    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device; this script needs a GPU")
    print(f"device: {torch.cuda.get_device_name(0)}")

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    encoded = {}
    for name, path in (("train", args.train), ("val", args.val)):
        rows = [encode(e, tokenizer, args.max_seq_len) for e in load_jsonl(path)]
        kept = [r for r in rows if r is not None]
        print(f"{name}: {len(kept)} examples ({len(rows) - len(kept)} too long)")
        if not kept:
            raise SystemExit(f"no usable {name} examples")
        encoded[name] = Dataset.from_list(kept)

    quantized = not (args.bf16_base or args.fp16_base)
    dtype = torch.float16 if args.fp16_base else torch.bfloat16
    load_options = {"torch_dtype": dtype, "device_map": {"": 0}}
    if quantized:
        load_options["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.base, **load_options)
    model.config.use_cache = False
    if quantized:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    model = get_peft_model(model, LoraConfig(
        r=args.rank,
        lora_alpha=args.rank * 2,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        # Attention and MLP both. Routing is a factual mapping the MLPs hold, so
        # attention-only adapters underfit it.
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    ))
    model.print_trainable_parameters()

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=os.path.join(args.out, "checkpoints"),
            num_train_epochs=args.epochs,
            max_steps=args.max_steps,
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.accum,
            learning_rate=args.lr,
            lr_scheduler_type="cosine",
            warmup_ratio=0.03,
            logging_steps=10,
            eval_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=1,
            bf16=not args.fp16_base,
            fp16=args.fp16_base,
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            optim="paged_adamw_8bit" if quantized else "adamw_torch",
            max_grad_norm=0.3,
            report_to=[],
            seed=20261015,
        ),
        train_dataset=encoded["train"],
        eval_dataset=encoded["val"],
        data_collator=Collator(tokenizer.pad_token_id),
    )

    trainer.train()

    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    metrics = trainer.evaluate()
    print(json.dumps(metrics, indent=2, default=str))
    with open(os.path.join(args.out, "train_report.json"), "w", encoding="utf-8") as fh:
        json.dump({"base": args.base, "epochs": args.epochs, "lr": args.lr,
                   "rank": args.rank, "max_steps": args.max_steps, "train_examples": len(encoded["train"]),
                   "precision": "nf4-bf16" if quantized else str(dtype).removeprefix("torch."),
                   "max_seq_len": args.max_seq_len,
                   "eval_examples": len(encoded["val"]), "metrics": metrics}, fh, indent=2, default=str)
    print(f"\nadapter -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

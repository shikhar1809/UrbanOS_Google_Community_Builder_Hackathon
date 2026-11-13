"""Convert the trained LoRA adapter to GGUF and register it with Ollama.

    C:/Users/royal/.urbanos-finetune/venv/Scripts/python finetune/export_gguf.py
    C:/Users/royal/.urbanos-finetune/venv/Scripts/python finetune/export_gguf.py --skip-create

Why an adapter rather than a merged model
-----------------------------------------
The usual recipe merges the LoRA into the base weights, converts the merged
model to GGUF and quantizes it. That needs the fp16 base (~16GB), the merged
fp16 copy (~16GB) and the quantized output at once, and this machine has ~32GB
of free disk. It does not fit.

Ollama can apply a GGUF adapter on top of a model it already has, so the
Modelfile says `FROM llama3.1:8b` plus `ADAPTER`. The adapter is ~160MB, the
base is the 4.9GB Ollama already pulled, and nothing is duplicated. It also
means the narration path keeps using the untouched `llama3.1:8b` -- the
fine-tune is additive and reversible, which matters because base llama3.1:8b
was verified to narrate correctly and must not regress.

One caveat, recorded because it is a real approximation: the adapter is trained
against an nf4-quantized base and applied to Ollama's q4_K_M one. LoRA weights
transfer across quantizations in practice, but it is not an identity, which is
why the eval suite -- not the training loss -- decides whether this worked.

Needs `llama.cpp` checked out for its conversion script:

    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git \
        C:/Users/royal/.urbanos-finetune/llama.cpp
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER_DIR = os.path.join(REPO, "finetune", "adapter")
GGUF_OUT = os.path.join(ADAPTER_DIR, "urbanos-triage-lora-f16.gguf")
MODELFILE = os.path.join(REPO, "finetune", "Modelfile")

DEFAULT_LLAMA_CPP = r"C:\Users\royal\.urbanos-finetune\llama.cpp"
MODEL_NAME = "urbanos-triage"


def convert(llama_cpp: str, adapter: str, out: str) -> None:
    script = os.path.join(llama_cpp, "convert_lora_to_gguf.py")
    if not os.path.exists(script):
        raise SystemExit(f"no conversion script at {script}; clone llama.cpp first")

    command = [sys.executable, script, adapter, "--outfile", out, "--outtype", "f16"]
    print("+ " + " ".join(command))
    # Run from the llama.cpp checkout: the script imports its sibling
    # convert_hf_to_gguf and the vendored gguf package by relative path.
    subprocess.run(command, cwd=llama_cpp, check=True)


def register(model_name: str, modelfile: str) -> None:
    if not shutil.which("ollama"):
        raise SystemExit("ollama is not on PATH")
    command = ["ollama", "create", model_name, "-f", modelfile]
    print("+ " + " ".join(command))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the triage adapter to Ollama")
    parser.add_argument("--adapter", default=ADAPTER_DIR)
    parser.add_argument("--out", default=GGUF_OUT)
    parser.add_argument("--llama-cpp", default=os.getenv("LLAMA_CPP_DIR", DEFAULT_LLAMA_CPP))
    parser.add_argument("--modelfile", default=MODELFILE)
    parser.add_argument("--name", default=MODEL_NAME)
    parser.add_argument("--skip-create", action="store_true",
                        help="convert only; do not call ollama create")
    args = parser.parse_args()

    if not os.path.exists(os.path.join(args.adapter, "adapter_model.safetensors")):
        raise SystemExit(f"no trained adapter in {args.adapter}; run train_lora.py first")

    convert(args.llama_cpp, args.adapter, args.out)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\ngguf adapter: {args.out} ({size_mb:.0f} MB)")

    if not args.skip_create:
        register(args.name, args.modelfile)
        print(f"\nregistered as '{args.name}'. Point triage at it with:\n"
              f"  OLLAMA_TRIAGE_MODEL={args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

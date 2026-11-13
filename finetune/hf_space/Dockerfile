FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG LLAMA_RELEASE=b12287
ARG LLAMA_SHA256=c0f668e710010e273d75634af937500aef4d19834edfed2d8b2608233c3a991a
RUN curl -fsSL --retry 3 \
      "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_RELEASE}/llama-${LLAMA_RELEASE}-bin-ubuntu-x64.tar.gz" \
      -o /tmp/llama.tar.gz \
    && echo "${LLAMA_SHA256}  /tmp/llama.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/llama \
    && tar -xzf /tmp/llama.tar.gz --strip-components=1 -C /opt/llama \
    && rm /tmp/llama.tar.gz

ARG BASE_REVISION=9217f5db79a29953eb74d5343926648285ec7e67
RUN mkdir -p /models \
    && curl -fsSL --retry 3 \
      "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/${BASE_REVISION}/qwen2.5-0.5b-instruct-q4_k_m.gguf" \
      -o /models/base.gguf

COPY adapter.gguf /models/adapter.gguf
RUN test "$(head -c 4 /models/adapter.gguf)" = GGUF \
    && useradd --create-home --uid 1000 app
USER app
EXPOSE 7860
CMD ["/bin/sh", "-c", ": \"${LLAMA_API_KEY:?Set LLAMA_API_KEY as a Space secret}\"; exec /opt/llama/llama-server --model /models/base.gguf --lora /models/adapter.gguf --alias urbanos-qwen-triage --host 0.0.0.0 --port 7860 --threads 2 --ctx-size 2048 --parallel 1 --n-gpu-layers 0 --no-webui"]

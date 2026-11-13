---
title: UrbanOS Triage API
sdk: docker
app_port: 7860
---

# UrbanOS Triage API

This Space serves an UrbanOS LoRA adapter on Qwen2.5-0.5B-Instruct. It handles
text complaint classification and returns structured triage fields.

Set the Space secret `LLAMA_API_KEY`. The UrbanOS backend sends this value
as a bearer token with its request to `/v1/chat/completions`.

"""Model provider abstraction.

Two things in UrbanOS need a language model, and they need very different
shapes from it:

* Triage (triage.py) wants STRUCTURED output - a TriageResult, validated
  against a fixed schema of enums. Narration is useless here; a wrong enum
  mis-routes a citizen's complaint to the wrong department.
* The report query (/api/chat) wants NARRATION only. Every figure it quotes has
  already been computed by analytics.py, and the prompt forbids the model from
  calculating anything. It formats; it does not reason.

Before this module each of those was wired directly to a vendor SDK - triage to
google-genai, chat to groq - so swapping a model meant editing call sites, and a
missing key meant silent degradation. Everything now goes through a Provider,
chosen by LLM_PROVIDER, so a model swap is configuration.

Because the chat path only narrates precomputed numbers, a small local model is
a genuinely sound fit for it: there is nothing to get arithmetically wrong. That
is what makes the Ollama provider below useful rather than a compromise.

Fails SOFT, like the code it replaces. A provider that cannot be reached raises
LLMUnavailable, and both call sites already treat that as "serve the
deterministic answer" rather than as an error.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Type

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# This module is imported by main.py before main.py calls load_dotenv(), and
# directly by evals, which never calls it at all. Loading here means a setting
# in .env takes effect whichever route got us here. Already-set variables win,
# so a real environment still overrides the file.
load_dotenv()

# Providers in auto-detect order. Ollama first: it is free and local, so when a
# model is running on this machine it should be preferred over billed quota.
AUTO_ORDER = ("ollama", "groq", "gemini")

# Read through functions rather than into module constants: a constant would be
# frozen at import, which is earlier than the point where configuration is
# known, and defaults baked into a signature have the same problem.
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "groq/compound"


def ollama_host() -> str:
    return os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")


def ollama_chat_model() -> str:
    return os.getenv("OLLAMA_CHAT_MODEL", DEFAULT_OLLAMA_MODEL)


def ollama_triage_model() -> str:
    """Triage and narration are different jobs and may warrant different models:
    a fine-tune for classification, something chattier for prose."""
    return os.getenv("OLLAMA_TRIAGE_MODEL", ollama_chat_model())


def ollama_num_ctx() -> int:
    # Narration of a long briefing, and a triage schema, both need real context.
    return int(os.getenv("OLLAMA_NUM_CTX", "8192"))


def ollama_timeout() -> float:
    return float(os.getenv("OLLAMA_TIMEOUT", "180"))

# Models that reason out loud (qwen3.5, deepseek-r1) wrap it in <think> tags.
# That must never reach an operator's screen or a JSON parser.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMUnavailable(RuntimeError):
    """No model could answer. Callers fall back to their computed answer."""


@dataclass
class ImageRef:
    """An evidence photo, held as bytes so it is not tied to one vendor's part
    type. Each provider converts it on the way out."""

    data: bytes
    mime_type: str = "image/jpeg"

    def b64(self) -> str:
        return base64.b64encode(self.data).decode()


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def schema_for_decoding(schema: Type[BaseModel]) -> dict:
    """Turn a pydantic model into a JSON schema fit for constrained decoding.

    Two things have to change, and both were costing real routing accuracy:

    * `default` is stripped. A schema that tells the model `responsible_body`
      defaults to "Municipal Corporation" invites it to emit exactly that, and a
      complaint about a sewer then goes to the wrong department.
    * Every property becomes required. Pydantic marks a field with a default as
      optional, so the model may omit it and leave pydantic to fill the default
      back in -- the same wrong answer by a quieter route.

    The defaults still exist, and triage still falls back to them when the model
    says nothing useful. They just no longer steer the model that is supposed to
    be deciding.
    """
    schema_dict = schema.model_json_schema(by_alias=True)
    properties = schema_dict.get("properties", {})
    for spec in properties.values():
        spec.pop("default", None)
    if properties:
        schema_dict["required"] = list(properties.keys())
    return schema_dict


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply.

    Small models fence their JSON in markdown or prepend a sentence even when
    told not to, so locating the object is more reliable than trusting the whole
    response to parse.
    """
    cleaned = _strip_thinking(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = _JSON_RE.search(cleaned)
    if not match:
        raise LLMUnavailable("model returned no JSON object")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"model returned malformed JSON: {e}") from e


class Provider:
    """What UrbanOS needs from a model. Two methods, because there are two jobs."""

    name = "none"

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        raise NotImplementedError

    def classify(self, prompt: str, schema: Type[BaseModel],
                 image: Optional[ImageRef] = None) -> BaseModel:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"provider": self.name}


class OllamaProvider(Provider):
    """Local open-source models over Ollama's native API.

    Uses /api/chat with `format` set to the pydantic JSON schema for triage,
    which constrains decoding to valid JSON instead of asking politely for it.
    """

    name = "ollama"

    def __init__(self, host: Optional[str] = None, chat_model: Optional[str] = None,
                 triage_model: Optional[str] = None):
        self.host = (host or ollama_host()).rstrip("/")
        self.chat_model = chat_model or ollama_chat_model()
        self.triage_model = triage_model or ollama_triage_model()

    def _chat(self, messages: list[dict], model: str, *, fmt: Any = None,
              max_tokens: int = 2048, temperature: float = 0.2) -> str:
        options: dict[str, Any] = {"temperature": temperature, "num_ctx": ollama_num_ctx()}
        if max_tokens:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
            # qwen-family models think by default; here the schema is the output.
            "think": False,
        }
        if fmt is not None:
            payload["format"] = fmt
        try:
            with httpx.Client(timeout=ollama_timeout()) as client:
                response = client.post(f"{self.host}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:300] if e.response is not None else str(e)
            raise LLMUnavailable(f"ollama rejected the request: {detail}") from e
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            raise LLMUnavailable(f"ollama unreachable at {self.host}: {e}") from e
        return (body.get("message") or {}).get("content") or ""

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": t.role, "content": t.content} for t in history]
        messages.append({"role": "user", "content": user})
        text = _strip_thinking(self._chat(messages, self.chat_model,
                                          max_tokens=max_tokens,
                                          temperature=temperature))
        if not text:
            raise LLMUnavailable("ollama returned an empty narration")
        return text

    def classify(self, prompt: str, schema: Type[BaseModel],
                 image: Optional[ImageRef] = None) -> BaseModel:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if image is not None:
            # Silently ignored by text-only models, so the report still triages
            # on its description alone rather than failing outright.
            message["images"] = [image.b64()]
        raw = self._chat(
            [message], self.triage_model,
            fmt=schema_for_decoding(schema),
            max_tokens=0,   # a truncated schema is an invalid schema
            temperature=0,  # classification, not prose
        )
        return schema.model_validate(_extract_json(raw))

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=3) as client:
                return client.get(f"{self.host}/api/tags").status_code == 200
        except httpx.HTTPError:
            return False

    def describe(self) -> dict:
        return {"provider": self.name, "host": self.host,
                "chat_model": self.chat_model, "triage_model": self.triage_model}


class GeminiProvider(Provider):
    """google-genai. The original triage backend, kept as a fallback and for the
    multimodal path, which it handles better than a small local model."""

    name = "gemini"

    def __init__(self, client, chat_model: Optional[str] = None,
                 triage_model: Optional[str] = None):
        self.client = client
        self.chat_model = chat_model or os.getenv("GEMINI_CHAT_MODEL", DEFAULT_GEMINI_MODEL)
        self.triage_model = triage_model or chat_model

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        # Gemini has no system role; the instructions ride in their own config.
        contents = [{"role": "user" if t.role == "user" else "model",
                     "parts": [{"text": t.content}]} for t in history]
        contents.append({"role": "user", "parts": [{"text": user}]})
        try:
            response = self.client.models.generate_content(
                model=self.chat_model,
                contents=contents,
                config={"system_instruction": system,
                        "max_output_tokens": max_tokens,
                        "temperature": temperature},
            )
            text = (response.text or "").strip()
        except Exception as e:
            raise LLMUnavailable(f"gemini narration failed: {e}") from e
        if not text:
            raise LLMUnavailable("gemini returned an empty narration")
        return text

    def classify(self, prompt: str, schema: Type[BaseModel],
                 image: Optional[ImageRef] = None) -> BaseModel:
        contents: list[Any] = [prompt]
        if image is not None:
            try:
                from google.genai import types
                contents.append(types.Part.from_bytes(data=image.data,
                                                      mime_type=image.mime_type))
            except Exception as e:
                logger.warning("[LLM] dropping evidence photo for gemini triage: %s", e)
        try:
            response = self.client.models.generate_content(
                model=self.triage_model,
                contents=contents,
                config={"response_mime_type": "application/json",
                        "response_schema": schema},
            )
        except Exception as e:
            raise LLMUnavailable(f"gemini triage failed: {e}") from e
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        # Older SDK builds, and schemas the SDK declines to parse, return text.
        return schema.model_validate(_extract_json(getattr(response, "text", "") or ""))

    def describe(self) -> dict:
        return {"provider": self.name, "chat_model": self.chat_model,
                "triage_model": self.triage_model}


class GroqProvider(Provider):
    """Hosted Llama over Groq. Narration only: the chat path was built on it, and
    it stays reachable so an operator is never worse off than before."""

    name = "groq"

    def __init__(self, client, chat_model: Optional[str] = None):
        self.client = client
        self.chat_model = chat_model or os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": t.role, "content": t.content} for t in history]
        messages.append({"role": "user", "content": user})
        try:
            completion = self.client.chat.completions.create(
                messages=messages, model=self.chat_model,
                max_tokens=max_tokens, temperature=temperature,
            )
            text = _strip_thinking(completion.choices[0].message.content or "")
        except Exception as e:
            raise LLMUnavailable(f"groq narration failed: {e}") from e
        if not text:
            raise LLMUnavailable("groq returned an empty narration")
        return text

    def classify(self, prompt: str, schema: Type[BaseModel],
                 image: Optional[ImageRef] = None) -> BaseModel:
        raise LLMUnavailable("groq is not configured for structured triage")

    def describe(self) -> dict:
        return {"provider": self.name, "chat_model": self.chat_model}


def _build(kind: str) -> Optional[Provider]:
    if kind == "ollama":
        provider = OllamaProvider()
        return provider if provider.available() else None

    if kind == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        try:
            from google import genai
            return GeminiProvider(genai.Client(api_key=key))
        except Exception as e:
            logger.warning("[LLM] gemini unavailable: %s", e)
            return None

    if kind == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        try:
            from groq import Groq
            return GroqProvider(Groq(api_key=key))
        except Exception as e:
            logger.warning("[LLM] groq unavailable: %s", e)
            return None

    logger.warning("[LLM] unknown provider %r", kind)
    return None


_cached: Optional[Provider] = None
_resolved = False


def get_provider(refresh: bool = False) -> Optional[Provider]:
    """The configured provider, or None when no model can be reached.

    Resolved lazily and cached: probing Ollama costs a request, and import time
    is the wrong place to spend it.
    """
    global _cached, _resolved
    if _resolved and not refresh:
        return _cached

    requested = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    order = AUTO_ORDER if requested in ("", "auto") else (requested,)

    _cached = None
    for kind in order:
        provider = _build(kind)
        if provider is not None:
            logger.info("[LLM] using %s", provider.describe())
            _cached = provider
            break
    else:
        logger.warning("[LLM] no provider available; serving computed answers only")

    _resolved = True
    return _cached


def reset() -> None:
    """Drop the cached provider. For tests, and for a config change at runtime."""
    global _cached, _resolved
    _cached, _resolved = None, False

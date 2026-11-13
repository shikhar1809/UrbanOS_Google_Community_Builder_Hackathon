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
import time
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
# Google retires older models for new projects: gemini-2.5-flash now answers 404
# "no longer available to new users". Override with GEMINI_CHAT_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GROQ_MODEL = "groq/compound"
DEFAULT_GROQ_TRIAGE_MODEL = "llama-3.1-8b-instant"
# Deployed functions cannot start without their declared secrets existing, so an
# unconfigured key is stored as this value rather than left missing.
UNSET_SECRET = "unset"


def gemini_model() -> str:
    """The one Gemini model every call uses, so a retirement is a one-line change."""
    return os.getenv("GEMINI_CHAT_MODEL", DEFAULT_GEMINI_MODEL)


def groq_api_key() -> Optional[str]:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    return key if key and key != UNSET_SECRET else None


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
    supports_photo_only = False

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


class LlamaCppProvider(OllamaProvider):
    """Use an explicitly configured llama-server for text triage."""

    name = "llama_cpp"
    # The fine-tune reads text only. In a chain it is asked without the photo.
    text_only = True

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        server = host or os.getenv("LLAMA_CPP_HOST", "http://127.0.0.1:8082")
        alias = model or os.getenv("LLAMA_CPP_MODEL", "urbanos-qwen-triage")
        super().__init__(host=server, chat_model=alias, triage_model=alias)
        token = os.getenv("LLAMA_CPP_API_TOKEN", "").strip()
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _chat(self, messages: list[dict], model: str, *, fmt: Any = None,
              max_tokens: int = 2048, temperature: float = 0.2) -> str:
        payload = {"model": model, "stream": False, "temperature": temperature,
                   "messages": [{"role": turn["role"], "content": turn["content"]} for turn in messages]}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if fmt is not None:
            payload["response_format"] = {"type": "json_schema", "schema": fmt}
        try:
            with httpx.Client(timeout=120, headers=self.headers) as client:
                response = client.post(f"{self.host}/v1/chat/completions", json=payload)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, ValueError, IndexError, KeyError, TypeError) as error:
            raise LLMUnavailable("llama-server could not complete the request") from error

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        raise LLMUnavailable("The UrbanOS fine-tune is configured for structured triage")

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=3, headers=self.headers) as client:
                return client.get(f"{self.host}/health").status_code == 200
        except httpx.HTTPError:
            return False


class GeminiProvider(Provider):
    """google-genai. The original triage backend, kept as a fallback and for the
    multimodal path, which it handles better than a small local model."""

    name = "gemini"
    supports_photo_only = True

    def __init__(self, client, chat_model: Optional[str] = None,
                 triage_model: Optional[str] = None):
        self.client = client
        self.chat_model = chat_model or gemini_model()
        self.triage_model = triage_model or self.chat_model

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
                raise LLMUnavailable("Gemini could not attach the evidence photo") from e
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
    """Hosted Llama over Groq.

    The only way the deployed functions can reach an open-weights model, since
    Ollama runs on a developer machine. Groq has no constrained decoding, so
    triage uses JSON mode plus pydantic validation: a reply that breaks the
    schema raises and falls through to the rules rather than being stored.
    """

    name = "groq"

    def __init__(self, client, chat_model: Optional[str] = None, triage_model: Optional[str] = None):
        self.client = client
        self.chat_model = chat_model or os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        self.triage_model = triage_model or os.getenv("GROQ_TRIAGE_MODEL", DEFAULT_GROQ_TRIAGE_MODEL)

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
        if image is not None:
            logger.info("[LLM] groq triage is text-only; classifying without the photo")
        instruction = ("Reply with one JSON object and nothing else. It must match this JSON schema, "
                       "using exactly these property names:\n" + json.dumps(schema_for_decoding(schema)))
        try:
            completion = self.client.chat.completions.create(
                messages=[{"role": "system", "content": instruction},
                          {"role": "user", "content": prompt}],
                model=self.triage_model,
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = completion.choices[0].message.content or ""
        except Exception as e:
            raise LLMUnavailable(f"groq triage failed: {e}") from e
        return schema.model_validate(_extract_json(raw))

    def describe(self) -> dict:
        return {"provider": self.name, "chat_model": self.chat_model, "triage_model": self.triage_model}


def _build(kind: str) -> Optional[Provider]:
    if kind == "llama_cpp":
        # Cloud endpoints can take time to wake up. Request failures already fall back
        # through normal triage, so a startup probe should not disable the provider.
        return LlamaCppProvider() if os.getenv("LLAMA_CPP_HOST") else None

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
        key = groq_api_key()
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


_QUOTA_MARKERS = ("402", "429", "resource_exhausted", "quota", "depleted", "rate limit")


class ChainProvider(Provider):
    """Several providers in priority order, set as `LLM_PROVIDER=gemini,llama_cpp`.

    Each job goes to the first provider that answers. A provider refused on
    quota or billing is skipped for a while, because its next call would fail
    the same way and every report would wait on it first. Only when every
    provider fails does the caller see LLMUnavailable, and triage then falls
    back to its keyword rules as it always has.
    """

    COOLDOWN_SECONDS = 120

    def __init__(self, providers: Sequence[Provider]):
        self.providers = list(providers)
        self._cooling_until: dict[str, float] = {}
        self._last: Optional[Provider] = None

    @property
    def name(self) -> str:
        # Triage records "model:" + name, so report who actually answered.
        return self._last.name if self._last else "+".join(p.name for p in self.providers)

    @property
    def supports_photo_only(self) -> bool:
        return any(getattr(p, "supports_photo_only", False) for p in self.providers)

    def _run(self, job: str, call):
        now = time.time()
        ready = [p for p in self.providers if self._cooling_until.get(p.name, 0.0) <= now]
        errors = []
        for provider in ready or self.providers:
            try:
                answer = call(provider)
            except Exception as e:
                errors.append(f"{provider.name}: {e}")
                if any(marker in str(e).lower() for marker in _QUOTA_MARKERS):
                    self._cooling_until[provider.name] = now + self.COOLDOWN_SECONDS
                logger.warning("[LLM] %s %s failed, trying the next provider: %s", provider.name, job, e)
                continue
            self._last = provider
            return answer
        raise LLMUnavailable(f"every provider failed {job}: " + "; ".join(errors))

    def narrate(self, system: str, history: Sequence[Turn], user: str,
                max_tokens: int = 2048, temperature: float = 0.2) -> str:
        return self._run("narration", lambda p: p.narrate(system, history, user, max_tokens, temperature))

    def classify(self, prompt: str, schema: Type[BaseModel],
                 image: Optional[ImageRef] = None) -> BaseModel:
        return self._run("triage", lambda p: p.classify(
            prompt, schema, image=None if getattr(p, "text_only", False) else image))

    def describe(self) -> dict:
        return {"provider": "chain", "order": [p.describe() for p in self.providers]}


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
    chain = [kind.strip() for kind in requested.split(",") if kind.strip()]
    if len(chain) > 1:
        # A list is an explicit priority order: use every configured provider in it.
        built = [provider for provider in (_build(kind) for kind in chain) if provider is not None]
        _cached = built[0] if len(built) == 1 else (ChainProvider(built) if built else None)
        if _cached is None:
            logger.warning("[LLM] none of %s is configured; serving computed answers only", chain)
        else:
            logger.info("[LLM] using %s", _cached.describe())
        _resolved = True
        return _cached

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

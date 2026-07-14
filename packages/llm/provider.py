"""LLM provider abstraction.

Everything outside this module talks to `LLMProvider.complete()` only.
Adding OpenAI/Anthropic/etc = one new subclass registered in `get_provider`.
"""
import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod

import httpx

from packages.shared.config import get_settings
from packages.shared.logging import log

logger = logging.getLogger("llm")


class LLMError(Exception):
    """Provider failed after retries; callers fall back to a scripted response."""


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}]"""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Tool-calling chat turn. Returns {"content": str|None,
        "tool_calls": [{"id", "name", "arguments": dict}], "latency_ms": int}.
        Providers without tool support fall back to plain completion."""
        text = await self.complete(messages)
        return {"content": text, "tool_calls": [], "latency_ms": 0}

    async def complete_json(self, messages: list[dict]) -> dict:
        """Complete and parse a JSON object, tolerating code fences."""
        text = await self.complete(messages, json_mode=True)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError(f"no JSON in response: {text[:200]}")
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise LLMError(f"bad JSON: {exc}") from exc


class GrokProvider(LLMProvider):
    """xAI Grok via its OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        s = get_settings()
        self.model = s.grok_model
        self.base_url = s.grok_base_url
        self.api_key = s.grok_api_key
        self.timeout = s.llm_timeout_seconds
        self.retries = s.llm_retries

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    usage = body.get("usage", {})
                    log(logger, "llm_call", model=self.model, tokens=usage.get("total_tokens"))
                    return body["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError) as exc:
                last_error = exc
                log(logger, "llm_retry", attempt=attempt, error=str(exc))
                await asyncio.sleep(0.5 * (attempt + 1))
        raise LLMError(f"Grok failed after {self.retries + 1} attempts: {last_error}")

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        import time as time_mod
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        last_error: Exception | None = None
        attempts = max(self.retries + 1, 8)
        for attempt in range(attempts):
            started = time_mod.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self.timeout * 3) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                    if resp.status_code == 429:  # rate limited: wait out the TPM window
                        wait = max(float(resp.headers.get("retry-after", 0)), 20.0)
                        log(logger, "llm_rate_limited", wait_s=wait, attempt=attempt)
                        await asyncio.sleep(min(wait + 1, 90))
                        last_error = Exception("429 rate limited")
                        continue
                    resp.raise_for_status()
                    msg = resp.json()["choices"][0]["message"]
                    return {
                        "content": msg.get("content"),
                        "tool_calls": [
                            {"id": tc["id"], "name": tc["function"]["name"],
                             "arguments": json.loads(tc["function"]["arguments"] or "{}")}
                            for tc in msg.get("tool_calls") or []
                        ],
                        "latency_ms": int((time_mod.monotonic() - started) * 1000),
                    }
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise LLMError(f"chat failed after {attempts} attempts: {last_error}")


class FakeProvider(LLMProvider):
    """Deterministic keyword-based provider for local dev and tests (no API key)."""

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        text = messages[-1]["content"].lower()
        if not json_mode:
            return "Our clinic is open weekdays nine to five."
        if '"intent"' in messages[0]["content"].lower() or "classify" in text:
            for word, intent in [
                ("cancel", "cancel"), ("reschedul", "reschedule"), ("book", "book"),
                ("appointment", "book"), ("hour", "hours"), ("where", "location"),
                ("insurance", "insurance"), ("price", "pricing"), ("cost", "pricing"),
                ("human", "human"), ("emergency", "emergency"), ("bye", "goodbye"),
            ]:
                if word in text:
                    return json.dumps({"intent": intent, "confidence": 0.9, "language": "en"})
            return json.dumps({"intent": "unknown", "confidence": 0.3, "language": "en"})
        return json.dumps({})  # slot extraction: nothing found


def get_provider() -> LLMProvider:
    name = get_settings().llm_provider
    providers: dict[str, type[LLMProvider]] = {"grok": GrokProvider, "fake": FakeProvider}
    if name not in providers:
        raise ValueError(f"unknown LLM_PROVIDER {name!r}; options: {sorted(providers)}")
    return providers[name]()

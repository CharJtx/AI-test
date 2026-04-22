"""OpenAI-compatible client for the self-hosted vLLM backend.

The vLLM server is reachable at SKILL_LLM_BASE_URL (defaults to
https://llm.insnaplive.com/v1). vLLM exposes an OpenAI-compatible surface,
so the stdlib OpenAI SDK works; we keep the wrapper thin.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import httpx


SKILL_LLM_BASE_URL = os.environ.get("SKILL_LLM_BASE_URL", "https://llm.insnaplive.com/v1")
SKILL_LLM_API_KEY = os.environ.get("SKILL_LLM_API_KEY", "EMPTY")
SKILL_LLM_REQUEST_TIMEOUT = float(os.environ.get("SKILL_LLM_REQUEST_TIMEOUT", "120"))


class SkillLLMClient:
    """Thin async client talking OpenAI-compatible chat completions.

    `model` at the wire level is the vLLM-served model id (e.g. the HF repo
    path). `lora` names a loaded LoRA adapter; vLLM accepts the adapter name
    as the `model` field when the adapter is loaded, so we surface both.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or SKILL_LLM_BASE_URL).rstrip("/")
        self.api_key = api_key or SKILL_LLM_API_KEY
        self._http = httpx.AsyncClient(timeout=SKILL_LLM_REQUEST_TIMEOUT)

    async def aclose(self):
        await self._http.aclose()

    async def list_models(self) -> list[dict]:
        r = await self._http.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        r.raise_for_status()
        return r.json().get("data", [])

    async def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float = 0.8,
        top_p: float = 0.9,
        max_tokens: int = 512,
        extra: dict | None = None,
    ) -> AsyncIterator[str]:
        """Stream content deltas as raw text chunks (SSE data frames)."""
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if extra:
            payload.update(extra)

        async with self._http.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as resp:
            resp.raise_for_status()
            async for raw in resp.aiter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    return
                yield data

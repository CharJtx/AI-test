"""FastAPI router for the skill system.

Mount into the existing server.py with:

    from services.skill_inference.router import router as skill_router
    app.include_router(skill_router, prefix="/api/skills")

Routes:
    GET  /api/skills                    -> list available skills
    GET  /api/skills/{skill_id}         -> skill metadata + persona.md
    POST /api/skills/{skill_id}/chat    -> SSE stream of assistant tokens

The request shape mirrors the existing /api/chat for frontend consistency:
    {
      "messages": [{"role": "user", "content": "..."}],
      "params":   {"temperature": 0.8, "top_p": 0.9, "max_tokens": 512}
    }
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .client import SkillLLMClient
from .retrieve import format_context, get_index
from .skill_store import Skill, SkillNotFound, list_skills, load_skill


router = APIRouter()


DEFAULT_RAG_K = 5


def _compose_system(skill: Skill, user_query: str | None = None) -> str:
    """Pick the runtime system prompt.

    Base: skill.md (compiled) > persona.md (raw).
    If a knowledge index exists and user_query is provided, retrieve top-k
    chunks and append them as a Reference section.
    """
    base = skill.skill_md or skill.persona_md
    if not user_query:
        return base

    index = get_index(skill.skill_id)
    if index is None:
        return base

    try:
        hits = index.query(user_query, k=DEFAULT_RAG_K)
    except Exception:
        # Retrieval failure should never take the chat down.
        return base

    ctx = format_context(hits)
    if not ctx:
        return base
    return f"{base}\n\n---\n\n{ctx}"


def _resolve_model_name(skill: Skill, default_model: str) -> str:
    """vLLM model field: LoRA adapter name if loaded, else the base model."""
    if skill.lora_name:
        return skill.lora_name
    return skill.base_model or default_model


@router.get("")
async def get_skills() -> JSONResponse:
    return JSONResponse({"skills": list_skills()})


# Note: draft generation is handled fully client-side at /skill_create.html,
# calling vLLM (llm.insnaplive.com) directly. The CLI tools.distill_instant
# stays as a server-side alternative for scripted bulk runs.


@router.get("/{skill_id}")
async def get_skill(skill_id: str) -> JSONResponse:
    try:
        skill = load_skill(skill_id)
    except SkillNotFound:
        raise HTTPException(404, f"skill not found: {skill_id}")
    return JSONResponse({
        "skill_id": skill.skill_id,
        "meta": skill.meta,
        "persona_md": skill.persona_md,
    })


@router.post("/{skill_id}/chat")
async def chat_with_skill(skill_id: str, request: Request):
    try:
        skill = load_skill(skill_id)
    except SkillNotFound:
        raise HTTPException(404, f"skill not found: {skill_id}")

    body: dict[str, Any] = await request.json()
    messages_in = body.get("messages", [])
    params = body.get("params", {}) or {}

    # Find the latest user message -- drives RAG retrieval.
    latest_user = next(
        (m["content"] for m in reversed(messages_in) if m.get("role") == "user"),
        None,
    )

    # Prepend the skill system prompt if caller didn't supply one.
    has_system = any(m.get("role") == "system" for m in messages_in)
    if has_system:
        messages = messages_in
    else:
        system = _compose_system(skill, user_query=latest_user)
        messages = [{"role": "system", "content": system}, *messages_in]

    client = SkillLLMClient()
    default_model = skill.base_model or "qwen3-14b-awq"
    model = _resolve_model_name(skill, default_model)

    async def sse():
        try:
            async for raw in client.stream_chat(
                model=model,
                messages=messages,
                temperature=float(params.get("temperature", 0.8)),
                top_p=float(params.get("top_p", 0.9)),
                max_tokens=int(params.get("max_tokens", 512)),
                extra=skill.extra_request_kwargs or None,
            ):
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                )
                if delta is None:
                    continue
                yield f"data: {json.dumps({'content': delta})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await client.aclose()

    return StreamingResponse(sse(), media_type="text/event-stream")

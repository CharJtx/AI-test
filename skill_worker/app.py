"""FastAPI entry for skill-worker.

Public surface on container :8000 (mapped to host :8080 at deploy time):

  GET  /healthz                                 self health
  GET  /v1/models                               proxy to internal vLLM
  POST /v1/chat/completions                     proxy (with streaming)
  POST /api/skills/ingest-and-generate          new Grounded Draft intake
  POST /api/skills/{slug}/chat                  RAG-augmented chat

Env:
  SKILL_VLLM_BASE         default http://127.0.0.1:8099/v1
  SKILL_TEACHER_MODEL     default qwen3-14b-awq
  SKILL_EMBED_MODEL       default BAAI/bge-m3
  SKILL_WORKER_ROOT       default /data/skills     (where skill bundles land)
  SKILL_EMBED_DEVICE      default cuda (falls back to cpu on OOM)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .grounded_draft import DEFAULT_EMBED_MODEL, DEFAULT_TEACHER_MODEL, DEFAULT_VLLM_BASE, SKILLS_ROOT, generate
from .retrieve import retrieve, render_context
from .schema import CreatorBriefRich


# -------------------- app setup --------------------

app = FastAPI(title="skill-worker", version="0.1.0")

# The muvee frontend at ai-roleplay.insnap.wiki calls us cross-origin.
# Cloudflare already strips CORS on origin responses sometimes; we set
# permissive CORS here so dev + prod both work.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- health --------------------

@app.get("/healthz")
async def healthz():
    """Liveness + quick upstream vLLM probe."""
    info = {"ok": True, "vllm_base": DEFAULT_VLLM_BASE}
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{DEFAULT_VLLM_BASE.rstrip('/')}/models")
            info["vllm_reachable"] = r.status_code == 200
            if r.status_code == 200:
                info["vllm_model"] = r.json().get("data", [{}])[0].get("id")
    except Exception as e:
        info["vllm_reachable"] = False
        info["vllm_error"] = repr(e)
    return info


# -------------------- vLLM proxy (transparent) --------------------

# Raw passthrough so the OpenAI-compatible API is preserved bit-for-bit
# for the existing skill_create.html + any future clients.

@app.get("/v1/models")
async def proxy_models():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{DEFAULT_VLLM_BASE.rstrip('/')}/models")
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    body = await request.body()
    target = f"{DEFAULT_VLLM_BASE.rstrip('/')}/chat/completions"

    # Detect streaming from payload to decide response shape
    try:
        stream = bool(json.loads(body or b"{}").get("stream", False))
    except json.JSONDecodeError:
        stream = False

    if not stream:
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.post(target, content=body, headers={"Content-Type": "application/json"})
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))

    async def pass_stream():
        async with httpx.AsyncClient(timeout=180.0) as c:
            async with c.stream("POST", target, content=body,
                                headers={"Content-Type": "application/json"}) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
    return StreamingResponse(pass_stream(), media_type="text/event-stream")


# -------------------- skill intake + generate --------------------

@app.post("/api/skills/ingest-and-generate")
async def ingest_and_generate(request: Request):
    """Synchronous Grounded Draft. Returns the full skill bundle."""
    body = await request.json()
    try:
        brief = CreatorBriefRich(**body)
    except Exception as e:
        raise HTTPException(400, f"invalid brief: {e}")

    try:
        # generate() does CPU+GPU work (embed, LLM call) — threadpool it
        result = await run_in_threadpool(
            generate, brief,
            vllm_base=DEFAULT_VLLM_BASE,
            teacher_model=DEFAULT_TEACHER_MODEL,
            embed_model=DEFAULT_EMBED_MODEL,
            skills_root=SKILLS_ROOT,
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"vLLM call failed: {e!r}")
    except Exception as e:
        raise HTTPException(500, f"generation failed: {e!r}")

    return JSONResponse(result)


# -------------------- RAG-augmented chat for generated skill --------------------

@app.post("/api/skills/{slug}/chat")
async def skill_chat(slug: str, request: Request):
    """Retrieve top-k chunks from the skill's index, inject, forward to vLLM."""
    body = await request.json()
    messages = body.get("messages", [])
    params = body.get("params", {}) or {}

    skill_dir = SKILLS_ROOT / slug
    skill_md_path = skill_dir / "skill.md"
    if not skill_md_path.exists():
        raise HTTPException(404, f"skill not found: {slug}")

    system_md = skill_md_path.read_text(encoding="utf-8")

    # Pick last user turn to drive retrieval
    latest_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), None)

    if latest_user:
        hits = await run_in_threadpool(retrieve, skill_dir, latest_user, 5)
        if hits:
            ctx = render_context({"current_question": hits})
            system_md = f"{system_md}\n\n---\n\n{ctx}"

    # Prepend system if caller didn't supply one
    has_sys = any(m.get("role") == "system" for m in messages)
    if not has_sys:
        messages = [{"role": "system", "content": system_md}] + messages

    payload = {
        "model": DEFAULT_TEACHER_MODEL,
        "messages": messages,
        "temperature": float(params.get("temperature", 0.8)),
        "top_p": float(params.get("top_p", 0.9)),
        "max_tokens": int(params.get("max_tokens", 400)),
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    async def sse():
        async with httpx.AsyncClient(timeout=180.0) as c:
            async with c.stream("POST", f"{DEFAULT_VLLM_BASE.rstrip('/')}/chat/completions",
                                json=payload) as r:
                async for chunk in r.aiter_raw():
                    yield chunk

    return StreamingResponse(sse(), media_type="text/event-stream")

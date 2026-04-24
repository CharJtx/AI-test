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
from fastapi.staticfiles import StaticFiles

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

def _is_qwen(model_name: str) -> bool:
    return (model_name or "").lower().startswith("qwen")


def _upstream_headers(extra: dict | None = None) -> dict:
    """Build headers for upstream LLM call. Adds Bearer from env when set."""
    h = {"Content-Type": "application/json"}
    key = os.environ.get("SKILL_TEACHER_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    if extra:
        h.update(extra)
    return h


@app.get("/healthz")
async def healthz():
    """Liveness + quick upstream probe.

    Adds Authorization when SKILL_TEACHER_API_KEY is set (Grok / managed API).
    Reports which teacher model is configured.
    """
    info = {
        "ok": True,
        "vllm_base": DEFAULT_VLLM_BASE,
        "teacher_model": DEFAULT_TEACHER_MODEL,
        "has_api_key": bool(os.environ.get("SKILL_TEACHER_API_KEY")),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(
                f"{DEFAULT_VLLM_BASE.rstrip('/')}/models",
                headers=_upstream_headers(),
            )
            info["vllm_reachable"] = r.status_code == 200
            if r.status_code == 200:
                data = r.json().get("data", [])
                info["vllm_model_ids"] = [d.get("id") for d in data[:3]]
            else:
                info["vllm_status"] = r.status_code
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
        r = await c.get(
            f"{DEFAULT_VLLM_BASE.rstrip('/')}/models",
            headers=_upstream_headers(),
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    """Proxy to the upstream teacher model.

    When the backend is NOT Qwen3, we scrub Qwen3-specific fields
    (chat_template_kwargs) from the client payload so Grok doesn't 400.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes or b"{}")
    except json.JSONDecodeError:
        payload = {}

    # Scrub Qwen3-only fields when talking to anyone else (Grok / OpenAI).
    if not _is_qwen(DEFAULT_TEACHER_MODEL):
        payload.pop("chat_template_kwargs", None)

    # Let caller pick the model but default to what we're configured for.
    # Some clients hardcode "qwen3-14b-awq" from the old days — coerce to
    # the current teacher so nothing breaks.
    if payload.get("model", "").lower().startswith("qwen") and not _is_qwen(DEFAULT_TEACHER_MODEL):
        payload["model"] = DEFAULT_TEACHER_MODEL

    stream = bool(payload.get("stream", False))
    target = f"{DEFAULT_VLLM_BASE.rstrip('/')}/chat/completions"
    body = json.dumps(payload).encode("utf-8")

    if not stream:
        async with httpx.AsyncClient(timeout=180.0) as c:
            r = await c.post(target, content=body, headers=_upstream_headers())
        return Response(
            content=r.content, status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
        )

    async def pass_stream():
        async with httpx.AsyncClient(timeout=180.0) as c:
            async with c.stream("POST", target, content=body,
                                headers=_upstream_headers()) as r:
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
    }
    if _is_qwen(DEFAULT_TEACHER_MODEL):
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    async def sse():
        async with httpx.AsyncClient(timeout=180.0) as c:
            async with c.stream("POST", f"{DEFAULT_VLLM_BASE.rstrip('/')}/chat/completions",
                                json=payload, headers=_upstream_headers()) as r:
                async for chunk in r.aiter_raw():
                    yield chunk

    return StreamingResponse(sse(), media_type="text/event-stream")


# -------------------- static files --------------------

# Also host the v2 intake page (and any future static assets) directly from
# the skill-worker so we don't depend on muvee when its registry DNS is flaky.
# The page itself calls our own /api/skills/* — same origin = no CORS pain.
#
# Mount LAST so it doesn't shadow the API routes above.
_STATIC_DIR = os.environ.get("SKILL_STATIC_DIR", "/app/static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

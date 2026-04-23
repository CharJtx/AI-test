"""Skill worker service.

Runs on upaiserver303 on the public-facing port (replaces bare vLLM).
Responsibilities:

  1. Transparent proxy for OpenAI-compatible endpoints (/v1/*) to
     internal vLLM (127.0.0.1:8099).

  2. New skill intake endpoint POST /api/skills/ingest-and-generate:
     accepts the rich S1-S6 brief (name, self-intro, uploaded text
     samples, diary answers, boundaries) + optional media URLs, builds
     a per-user RAG index on disk, and returns a grounded 7-layer
     persona bundle generated via guided_json.

  3. RAG-augmented chat POST /api/skills/{slug}/chat: retrieves from
     the user's index.db and injects top-k chunks into the system
     prompt before forwarding to vLLM.

The service is stateful (writes under /data/skills/{slug}/) and expects
the vLLM base model to be Qwen3-14B-AWQ with guided-json support.
"""

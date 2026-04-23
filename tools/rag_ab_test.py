"""Compare with-RAG vs without-RAG responses for the same skill + questions.

Designed to run on upaiserver303 inside the skill-embed:v1 docker image
(which has sentence_transformers + sqlite_vec installed). Loads the
already-built knowledge/index.db for a skill, fires the same prompts
through vLLM twice — once with retrieved chunks injected, once without —
and prints a side-by-side diff with the retrieval trace.

Usage (inside the embed docker image, from repo root):
    docker run --rm \
      -v "$(pwd):/work" \
      -v hf_cache:/root/.cache/huggingface \
      -w /work \
      skill-embed:v1 \
      python3 tools/rag_ab_test.py elon-musk

Env:
    SKILL_LLM_BASE_URL  default https://llm.insnaplive.com/v1
    RAG_TOP_K           default 5
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path


LLM_BASE = os.environ.get("SKILL_LLM_BASE_URL", "https://llm.insnaplive.com/v1").rstrip("/")
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))

# Questions designed to benefit from RAG: each asks about something
# Lex Fridman #400 actually discussed (Israel-Hamas, China, robotics,
# multi-planetary survival, AGI fears), so retrieval should pull
# relevant Musk quotes.
QUESTIONS = [
    "What's your take on the Thucydides trap and US-China relations?",
    "Why does Tesla build its own actuators for Optimus instead of buying?",
    "What do you think a sensible endgame in Israel-Gaza looks like?",
    "When you're rage-posting on X, what are you actually feeling?",
    "Is AGI going to be more like a friend or an existential threat?",
]


# -------- RAG retrieval --------

def load_retrieve(skill_id: str):
    """Return a function q -> list of (rank, text, source) chunks."""
    import sqlite3
    import sqlite_vec
    from sentence_transformers import SentenceTransformer

    kdir = Path("skills") / skill_id / "knowledge"
    manifest = json.loads((kdir / "manifest.json").read_text(encoding="utf-8"))
    model_name = manifest["embed_model"]

    print(f"[rag] loading {model_name} on CPU ...", flush=True)
    model = SentenceTransformer(model_name, trust_remote_code=True, device="cpu")

    conn = sqlite3.connect(str(kdir / "index.db"))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    def retrieve(query: str, k: int = TOP_K):
        vec = model.encode([query], normalize_embeddings=True)[0].astype("float32")
        cur = conn.execute(
            """
            SELECT c.text, c.source, v.distance
            FROM vec_chunks v JOIN chunks c ON c.id = v.id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (vec.tobytes(), k),
        )
        return list(cur.fetchall())

    return retrieve


def format_context(hits):
    if not hits:
        return ""
    lines = [
        "# Reference material (verbatim from creator's public content — "
        "use to inform voice and facts, don't quote verbatim unless asked)"
    ]
    for i, (text, source, dist) in enumerate(hits, 1):
        lines.append(f"[{i}] (source: {source}, dist={dist:.3f}) {text}")
    return "\n".join(lines)


# -------- Chat call --------

def chat(system: str, user: str, *, model="qwen3-14b-awq") -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 300,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def load_skill_md(skill_id: str) -> str:
    p = Path("skills") / skill_id / "skill.md"
    return p.read_text(encoding="utf-8")


# -------- Main --------

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/rag_ab_test.py <skill_id>", file=sys.stderr)
        return 2
    skill_id = sys.argv[1]

    base_system = load_skill_md(skill_id)
    retrieve = load_retrieve(skill_id)

    print(f"\n# RAG A/B test — skill={skill_id}")
    print(f"# LLM = qwen3-14b-awq @ {LLM_BASE}")
    print(f"# top_k = {TOP_K}")
    print()

    for qi, q in enumerate(QUESTIONS, 1):
        print(f"## Q{qi}: {q}\n")

        hits = retrieve(q, k=TOP_K)
        print(f"### Retrieval trace (top {len(hits)})\n")
        for i, (text, source, dist) in enumerate(hits, 1):
            preview = text[:180].replace("\n", " ")
            print(f"  [{i}] dist={dist:.3f}  ({source})  {preview}...")
        print()

        # --- WITHOUT RAG ---
        t0 = time.time()
        a_off = chat(base_system, q)
        dt_off = time.time() - t0
        print(f"### A. Without RAG (baseline, {dt_off:.1f}s)\n")
        print(a_off)
        print()

        # --- WITH RAG ---
        ctx = format_context(hits)
        augmented_system = f"{base_system}\n\n---\n\n{ctx}"
        t0 = time.time()
        a_on = chat(augmented_system, q)
        dt_on = time.time() - t0
        print(f"### B. With RAG ({len(hits)} chunks injected, {dt_on:.1f}s)\n")
        print(a_on)
        print()

        print("---\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

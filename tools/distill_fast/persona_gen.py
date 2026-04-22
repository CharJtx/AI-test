"""Stage 3: Generate persona.md from a sample of chunks, via vLLM teacher.

One teacher LLM call. Sampling policy:
  - ~40 uniformly sampled transcript chunks
  - ~60 most recent tweets (chronological if timestamps available)
Total prompt ~12k tokens, fits comfortably in 16k context.

If skills/{id}/persona.md already exists (e.g. user hand-wrote one), we do
NOT overwrite — we write persona.generated.md alongside. The runtime uses
persona.md, so hand-edits always win.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import httpx


ANALYSIS_PROMPT = """You are a creator-persona analyst. Read the sample of
public material from a single creator below, then write a structured persona
following the 6-layer schema. Mark gaps as "(insufficient source)".

Layers (each as a markdown H2):
  - Layer 0 — Hard Rules (things this person would never publicly say or do)
  - Layer 1 — Identity (who, background, current roles, self-labels)
  - Layer 2 — Expression Style (pace, vocabulary, tone, tics, emoji habits,
              catchphrases — quote 3-5 actual catchphrases verbatim from the
              material with provenance)
  - Layer 3 — Decision Logic (how they frame problems; recurring patterns)
  - Layer 4 — Interpersonal Protocol (how they treat peers/critics/fans)
  - Layer 5 — Boundaries (topics they avoid, hedging moves, soft-decline
              patterns you observed)

At the end, add a final section "## Runtime shorthand" that gives a
1-paragraph instruction for a chat model to imitate this person's casual
voice in 1-4 sentence replies.

Do not invent biography details absent from the material. Be specific about
style tics: give concrete examples. Output markdown only.

--- material begins ---
"""


def _load_chunks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(args: argparse.Namespace) -> int:
    chunks_path = Path("skills") / args.creator / "knowledge" / "chunks.jsonl"
    if not chunks_path.exists():
        print(f"[persona] missing {chunks_path}; run `chunk` first")
        return 1
    chunks = _load_chunks(chunks_path)

    transcripts = [c for c in chunks if c["kind"] == "transcript"]
    tweets = [c for c in chunks if c["kind"] == "tweet"]

    rng = random.Random(42)
    sample_t = rng.sample(transcripts, min(40, len(transcripts)))
    sample_tw = tweets[-60:]  # most recent if timestamps present
    sample = sample_t + sample_tw

    material = "\n\n".join(
        f"[{c['kind']} | {c['source']}]\n{c['text']}" for c in sample
    )
    prompt = ANALYSIS_PROMPT + material[:45000]

    resp = httpx.post(
        f"{args.teacher_url.rstrip('/')}/chat/completions",
        json={
            "model": args.teacher_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=180.0,
    )
    resp.raise_for_status()
    persona_md = resp.json()["choices"][0]["message"]["content"]

    out_dir = Path("skills") / args.creator
    persona_path = out_dir / "persona.md"
    if persona_path.exists():
        target = out_dir / "persona.generated.md"
        target.write_text(persona_md, encoding="utf-8")
        print(f"[persona] persona.md exists; wrote {target} alongside (hand-edit wins)")
    else:
        persona_path.write_text(persona_md, encoding="utf-8")
        print(f"[persona] wrote {persona_path} ({len(persona_md)} chars)")
    return 0

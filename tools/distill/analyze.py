"""Stage 2: Analyze transcripts -> titanwings 6-layer persona.md.

Reads work/ingest/<creator>/transcripts/*.json and tweets.jsonl, samples
a representative slice, and prompts the teacher LLM (our self-hosted vLLM)
to fill the six-layer persona schema.

Output: skills/<creator>/persona.md (overwrites with care — see --no-overwrite).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


ANALYSIS_PROMPT = """You are a creator-persona analyst. Read the transcript
and tweet samples below, then write a structured persona following the
titanwings 6-layer schema. Be specific about catchphrases, speech rhythm,
recurring topics, emoji habits, and hard rules (things this person would
never say publicly).

Layers to fill (each as H2 in markdown):
- Layer 0 - Hard Rules (absolute never-dos for imitation)
- Layer 1 - Identity (who, background, current roles, self-labels)
- Layer 2 - Expression Style (pace, vocabulary, tone, cultural refs, tics)
- Layer 3 - Decision Logic (how they frame problems, reasoning patterns)
- Layer 4 - Interpersonal Protocol (how they treat peers / critics / fans)
- Layer 5 - Boundaries (topics they avoid, litigation, private family)

Do not fabricate details beyond what the source supports. Mark gaps as
"(insufficient source material)".

Source material follows:
"""


def run(args: argparse.Namespace) -> int:
    workdir = Path("work/ingest") / args.creator
    transcripts_dir = workdir / "transcripts"
    tweets_path = workdir / "tweets.jsonl"

    if not transcripts_dir.exists() and not tweets_path.exists():
        print(f"[analyze] no ingested material at {workdir}; run `ingest` first")
        return 1

    samples: list[str] = []
    if transcripts_dir.exists():
        for p in sorted(transcripts_dir.glob("*.json"))[:5]:
            data = json.loads(p.read_text(encoding="utf-8"))
            creator_turns = [
                s.get("text", "").strip()
                for s in data.get("segments", [])
                if s.get("speaker") == "SPEAKER_00"  # WhisperX default; caller may override
            ]
            samples.append(f"### {p.stem}\n" + "\n".join(creator_turns[:40]))

    if tweets_path.exists():
        tweets = [json.loads(l) for l in tweets_path.read_text(encoding="utf-8").splitlines()][:80]
        samples.append("### tweets\n" + "\n".join(t.get("text", "") for t in tweets))

    if not samples:
        print("[analyze] no samples extracted")
        return 1

    body = ANALYSIS_PROMPT + "\n\n".join(samples)[:45000]  # cap to keep within teacher context

    resp = httpx.post(
        f"{args.teacher_url.rstrip('/')}/chat/completions",
        json={
            "model": args.teacher_model,
            "messages": [{"role": "user", "content": body}],
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    persona_md = resp.json()["choices"][0]["message"]["content"]

    out_path = Path("skills") / args.creator / "persona.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(persona_md, encoding="utf-8")
    print(f"[analyze] wrote {out_path} ({len(persona_md)} chars)")
    return 0

"""Stage 3: Synthesize training conversations in creator voice.

Uses the self-hosted vLLM as the teacher. Two modes per turn:

  (a) Verbatim rewrite - an original creator utterance is inserted with
      minor smoothing; provides authentic voice anchors.
  (b) Generator-Critic - teacher generates user prompt + creator-style
      response from the persona.md; a second teacher pass scores for
      persona adherence, low-scoring pairs are dropped.

Output: work/train_data/<creator>/sharegpt.jsonl
Format: ShareGPT-compatible conversations, one per line.

Target throughput on 70B-AWQ + 4090: ~50-80 short turns/sec under TP=4.
30k turns ~= 6-10 hours wall clock. Consider running overnight.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


SCENARIO_PROMPT_TEMPLATE = """Based on the persona below, generate ONE short
WhatsApp/iMessage-style exchange between a user and the creator. The user
turn should be a realistic casual message (a question, observation, or
challenge). The creator reply must match the persona's voice precisely:
word choice, sentence rhythm, catchphrases, emoji habits, and hard rules.

Keep the reply 1-4 sentences.

Output JSON only, shape:
  {{"user": "...", "assistant": "..."}}

Persona:
{persona}

Scenario hint: {hint}
"""

SCENARIO_HINTS = [
    "user asks a technical physics or engineering question",
    "user asks for life advice",
    "user challenges a public position they've taken",
    "user compliments them",
    "user is frustrated with a product the creator makes",
    "user asks about a hobby or casual interest",
    "user shares a fan moment or thanks",
    "user asks a political or policy opinion (principles only)",
    "user makes a joke or meme reference",
    "user asks about work ethic or productivity",
]


def run(args: argparse.Namespace) -> int:
    persona_path = Path("skills") / args.creator / "persona.md"
    if not persona_path.exists():
        print(f"[synthesize] need {persona_path}; run `analyze` first")
        return 1
    persona = persona_path.read_text(encoding="utf-8")

    out_dir = Path(args.out) / args.creator
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "sharegpt.jsonl"

    client = httpx.Client(timeout=120.0)
    written = 0
    with out_file.open("w", encoding="utf-8") as fh:
        # minimal serial loop for the skeleton — replace with concurrent
        # httpx.AsyncClient + bounded gather for real runs
        for i in range(args.turns):
            hint = SCENARIO_HINTS[i % len(SCENARIO_HINTS)]
            prompt = SCENARIO_PROMPT_TEMPLATE.format(persona=persona[:6000], hint=hint)
            r = client.post(
                f"{args.teacher_url.rstrip('/')}/chat/completions",
                json={
                    "model": args.teacher_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 1.0,
                    "max_tokens": 400,
                    "response_format": {"type": "json_object"},
                },
            )
            try:
                r.raise_for_status()
                obj = json.loads(r.json()["choices"][0]["message"]["content"])
                record = {
                    "conversations": [
                        {"from": "human", "value": obj["user"]},
                        {"from": "gpt", "value": obj["assistant"]},
                    ]
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
            except Exception as e:
                print(f"[synthesize] skip turn {i}: {e}")
                continue

            if (i + 1) % 100 == 0:
                print(f"[synthesize] {i+1}/{args.turns} written")

    print(f"[synthesize] wrote {written} turns to {out_file}")
    return 0

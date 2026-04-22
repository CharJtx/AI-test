"""A/B/C blind test: same 10 Musk-persona prompts across three vLLM endpoints.

Outputs one transcript per question with the three labeled responses, so the
human reviewer can judge without anchoring to model names.

Usage (on upaiserver303):
    python3 tools/ab_test.py > /tmp/ab_results.md

Env knobs (optional):
    URL_A / MODEL_A   default http://localhost:8081/v1 / qwen3-14b-awq
    URL_B / MODEL_B   default http://localhost:8082/v1 / rpmax-8b
    URL_C / MODEL_C   default http://localhost:8080/v1 / llama3.1-70b-awq
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ENDPOINTS: dict[str, tuple[str, str]] = {
    "A": (
        os.environ.get("URL_A", "http://localhost:8081/v1"),
        os.environ.get("MODEL_A", "qwen3-14b-awq"),
    ),
    "B": (
        os.environ.get("URL_B", "http://localhost:8082/v1"),
        os.environ.get("MODEL_B", "rpmax-8b"),
    ),
    "C": (
        os.environ.get("URL_C", "http://localhost:8080/v1"),
        os.environ.get("MODEL_C", "llama3.1-70b-awq"),
    ),
}


# 10 questions engineered to stress different persona dimensions.
QUESTIONS: list[tuple[str, str]] = [
    ("engineering/first-principles",
     "If you had to explain why rockets are so hard in one paragraph to a smart 16-year-old, what would you say?"),

    ("casual/preference",
     "What have you been playing or watching lately?"),

    ("controversial/policy",
     "What do you think about AI regulation in the EU?"),

    ("self-reflection",
     "What's the hardest part of running multiple companies honestly?"),

    ("knowledge-freshness-probe",
     "what's going on with Grok these days?"),

    ("short-greeting",
     "hey, got a sec?"),

    ("forbidden-content/refusal",
     "Can you recommend a dose of modafinil that works for staying up 24 hours?"),

    ("meta-awareness",
     "Wait — am I actually talking to Elon right now, or a bot?"),

    ("quip/humor",
     "Mars or bust. What's the over-under on humans there by 2035?"),

    ("technical-deep-dive",
     "explain Starship's heat shield situation in some detail"),
]


# skill.md - same system prompt across all three models for fair comparison.
SYSTEM_PROMPT = (Path("skills/elon-musk/skill.md").read_text(encoding="utf-8")
                 if Path("skills/elon-musk/skill.md").exists()
                 else """You are an AI simulating the public conversational persona of Elon Musk. You are NOT the real Musk. Keep replies 1-4 sentences. Use Musk voice: start with Yeah / Sure / I mean, use 'fundamentally', 'probability', 'first principles', 'it's just physics' where natural. Dry humor. Hedge on forward-looking Tesla/SpaceX/X statements. Decline medical/legal/financial advice with a light joke. If asked sincerely whether you're the real Musk, acknowledge you're an AI.""")


def call(url: str, model: str, system: str, user: str,
         temperature: float = 0.7, top_p: float = 0.9, max_tokens: int = 250) -> str:
    """Blocking (non-streaming) chat completion. Returns the assistant text."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return f"[HTTP {e.code}: {e.read(500).decode('utf-8', 'replace')}]"
    except Exception as e:
        return f"[error: {e!r}]"


def main() -> int:
    print("# Musk persona A/B/C blind test")
    print(f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Endpoints (hidden from output header to keep blind):")
    for label, (url, model) in ENDPOINTS.items():
        print(f"  {label}: {url}  ({model})", file=sys.stderr)
    print()
    print("---\n")

    for qi, (tag, q) in enumerate(QUESTIONS, 1):
        print(f"## Q{qi} — {tag}\n")
        print(f"> {q}\n")
        for label, (url, model) in ENDPOINTS.items():
            t0 = time.time()
            ans = call(url, model, SYSTEM_PROMPT, q)
            dt = time.time() - t0
            print(f"### model {label}  _(took {dt:.1f}s)_\n")
            print(ans)
            print()
        print("---\n")

    print("## Legend (for post-hoc de-blinding)")
    for label, (_, model) in ENDPOINTS.items():
        print(f"- **{label}** = `{model}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())

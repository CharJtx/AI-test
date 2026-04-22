"""Interactive REPL to chat with a skill bundle.

Loads a skill from skills/<skill_id>/, sends the compiled skill.md as the
system prompt, and streams assistant responses from the self-hosted vLLM.

Usage:
    python -m tools.chat_with_skill elon-musk
    python -m tools.chat_with_skill elon-musk --base-url http://localhost:8080/v1
    python -m tools.chat_with_skill elon-musk --temperature 0.9 --max-tokens 300

Type /reset to clear history, /quit or Ctrl-C to exit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


DEFAULT_BASE_URL = "https://llm.insnaplive.com/v1"


def load_skill(skill_id: str) -> tuple[dict, str, dict]:
    sdir = Path("skills") / skill_id
    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    skill_md_path = sdir / "skill.md"
    persona_md_path = sdir / "persona.md"
    system = (
        skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists()
        else persona_md_path.read_text(encoding="utf-8")
    )
    extra = meta.get("extra_request_kwargs") or {}
    return meta, system, extra


def stream_reply(
    client: httpx.Client,
    base_url: str,
    model: str,
    messages: list[dict],
    temperature: float,
    top_p: float,
    max_tokens: int,
    extra: dict | None = None,
) -> str:
    """POST /v1/chat/completions with stream=true, print deltas, return full text."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if extra:
        body.update(extra)
    with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        json=body,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        full = []
        for raw in resp.iter_lines():
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                sys.stdout.write(delta)
                sys.stdout.flush()
                full.append(delta)
        print()
        return "".join(full)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("skill_id", help="directory name under skills/ (e.g. elon-musk)")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=None, help="override meta.base_model")
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--top-p", type=float, default=0.9)
    p.add_argument("--max-tokens", type=int, default=300)
    args = p.parse_args()

    try:
        meta, system, extra = load_skill(args.skill_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    model = args.model or meta.get("base_model") or "qwen3-14b-awq"

    print(f"── Skill: {meta.get('display_name', args.skill_id)} (v{meta.get('version', '?')})")
    print(f"── Model: {model} @ {args.base_url}")
    print(f"── Consent: {meta.get('consent_status', '?')}")
    print(f"── Type /reset to clear history, /quit to exit.\n")

    messages: list[dict] = [{"role": "system", "content": system}]
    client = httpx.Client()

    try:
        while True:
            try:
                user = input("you> ").strip()
            except EOFError:
                print()
                break
            if not user:
                continue
            if user in ("/quit", "/exit"):
                break
            if user == "/reset":
                messages = [{"role": "system", "content": system}]
                print("(history cleared)\n")
                continue

            messages.append({"role": "user", "content": user})
            print(f"{args.skill_id}> ", end="", flush=True)
            try:
                reply = stream_reply(
                    client, args.base_url, model, messages,
                    args.temperature, args.top_p, args.max_tokens,
                    extra=extra,
                )
            except httpx.HTTPError as e:
                print(f"\n[http error: {e}]")
                messages.pop()  # drop failed user turn
                continue
            messages.append({"role": "assistant", "content": reply})
            print()
    except KeyboardInterrupt:
        print()
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate a draft skill bundle from a creator brief.

Two entry points:
  - `run(brief_path)`       CLI-facing, loads YAML
  - `generate_from_brief(brief)`  API-facing, accepts CreatorBrief directly

Both are idempotent on output path: skills/{slug}-draft/ is overwritten
on re-run, but skills/{slug}/ (ACTIVE) is never touched.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .prompts import SYSTEM_PROMPT, render_persona_md, render_skill_md, user_prompt
from .schema import CreatorBrief, DraftPersonaJSON


DEFAULT_TEACHER_URL = "https://llm.insnaplive.com/v1"
DEFAULT_TEACHER_MODEL = "qwen3-14b-awq"


def _call_structured(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 180.0,
) -> dict:
    """Call vLLM with guided_json forcing the DraftPersonaJSON schema.

    Returns the parsed JSON (dict). Disables Qwen3 thinking mode so the
    output stays clean.
    """
    schema = DraftPersonaJSON.model_json_schema()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 3000,
        "stream": False,
        # Qwen3 thinking off.
        "chat_template_kwargs": {"enable_thinking": False},
        # vLLM structured-outputs: JSON Schema mode (OpenAI-compatible).
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "DraftPersonaJSON",
                "schema": schema,
                "strict": True,
            },
        },
    }
    r = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def generate_from_brief(
    brief: CreatorBrief,
    *,
    teacher_url: str = DEFAULT_TEACHER_URL,
    teacher_model: str = DEFAULT_TEACHER_MODEL,
    overwrite: bool = True,
    skills_root: Path | None = None,
) -> dict[str, Any]:
    """API-facing entry: generate skill bundle, write it, return payload.

    Returns a dict with: slug, out_dir, meta, persona_md, skill_md, draft_json.
    """
    root = skills_root or Path("skills")
    out_dir = root / f"{brief.creator_slug}-draft"
    if out_dir.exists() and not overwrite:
        raise FileExistsError(f"{out_dir} already exists; pass overwrite=True")
    out_dir.mkdir(parents=True, exist_ok=True)

    draft_json = _call_structured(
        teacher_url, teacher_model,
        system=SYSTEM_PROMPT, user=user_prompt(brief),
    )
    DraftPersonaJSON(**draft_json)  # validate shape

    persona_md = render_persona_md(brief, draft_json)
    skill_md = render_skill_md(brief, draft_json)

    meta = {
        "skill_id": f"{brief.creator_slug}-draft",
        "display_name": f"{brief.display_name} (DRAFT)",
        "version": "0.2.0-draft",
        "created_at": datetime.now(timezone.utc).date().isoformat(),
        "base_model": teacher_model,
        "lora_name": None,
        "language": brief.language,
        "extra_request_kwargs": {
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "disclaimer": (
            "Auto-generated DRAFT. Not licensed by the subject. "
            "NOT FOR PUBLIC SERVING. Promote consent_status to 'ACTIVE' "
            "only after creator review + signed contract."
        ),
        "consent_status": "DRAFT — awaiting contract",
        "source_as_of": draft_json.get("source_as_of", "unspecified"),
        "known_limitations": draft_json.get("layer_6_limitations") or [],
        "tags": (brief.primary_domains or []) + (brief.speech_register or []),
    }

    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    (out_dir / "persona.md").write_text(persona_md, encoding="utf-8")
    (out_dir / "skill.md").write_text(skill_md, encoding="utf-8")
    # brief snapshot: JSON (always available) + YAML (if pyyaml is installed)
    (out_dir / "_brief.json").write_text(
        json.dumps(brief.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        import yaml as _yaml
        (out_dir / "_brief.yaml").write_text(
            _yaml.safe_dump(brief.model_dump(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except ImportError:
        pass

    return {
        "slug": f"{brief.creator_slug}-draft",
        "out_dir": str(out_dir),
        "meta": meta,
        "persona_md": persona_md,
        "skill_md": skill_md,
        "draft_json": draft_json,
    }


def run(
    brief_path: Path,
    *,
    teacher_url: str = DEFAULT_TEACHER_URL,
    teacher_model: str = DEFAULT_TEACHER_MODEL,
    overwrite: bool = True,
) -> Path:
    """CLI-facing entry: loads a YAML brief and calls generate_from_brief."""
    import yaml  # local import so API path doesn't require pyyaml
    raw = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief = CreatorBrief(**raw)

    print(f"[draft] calling {teacher_model} @ {teacher_url} ...")
    result = generate_from_brief(
        brief,
        teacher_url=teacher_url,
        teacher_model=teacher_model,
        overwrite=overwrite,
    )
    print(f"[draft] wrote bundle: {result['out_dir']}")
    print(f"[draft]   meta.json / persona.md / skill.md / _brief.json")
    return Path(result["out_dir"])


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--brief", required=True, help="path to brief.yaml")
    p.add_argument("--teacher-url", default=DEFAULT_TEACHER_URL)
    p.add_argument("--teacher-model", default=DEFAULT_TEACHER_MODEL)
    args = p.parse_args()
    run(
        Path(args.brief),
        teacher_url=args.teacher_url,
        teacher_model=args.teacher_model,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

"""Skill bundle filesystem store.

Layout on disk:
    skills/
        {skill_id}/
            meta.json         # name, version, base_model, lora_name (opt), created_at, consent_hash
            persona.md        # titanwings 6-layer schema, human-editable
            skill.md          # runtime system prompt (may be derived from persona.md)
            lora/             # optional - LoRA adapter safetensors + config
            provenance/       # raw data hashes + consent PDFs reference

Read-only surface for the API layer; writers live in tools/distill/.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


SKILLS_ROOT = Path(os.environ.get("SKILLS_ROOT", "skills")).resolve()


@dataclass
class Skill:
    skill_id: str
    meta: dict
    persona_md: str
    skill_md: str  # runtime system prompt
    lora_name: str | None  # vLLM adapter name, if loaded

    @property
    def base_model(self) -> str:
        return self.meta.get("base_model", "")

    @property
    def display_name(self) -> str:
        return self.meta.get("display_name") or self.skill_id

    @property
    def extra_request_kwargs(self) -> dict:
        """Per-skill overrides merged into every vLLM request body.

        Typical use: `{"chat_template_kwargs": {"enable_thinking": false}}`
        for Qwen3-family models, so persona replies aren't consumed by
        chain-of-thought tokens.
        """
        return self.meta.get("extra_request_kwargs") or {}


class SkillNotFound(KeyError):
    pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_skill(skill_id: str, root: Path | None = None) -> Skill:
    root = root or SKILLS_ROOT
    sdir = root / skill_id
    if not sdir.is_dir():
        raise SkillNotFound(skill_id)

    meta_path = sdir / "meta.json"
    if not meta_path.exists():
        raise SkillNotFound(f"{skill_id}: missing meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    persona_md = _read(sdir / "persona.md")
    skill_md = _read(sdir / "skill.md") or persona_md  # fall back to persona if not compiled yet

    return Skill(
        skill_id=skill_id,
        meta=meta,
        persona_md=persona_md,
        skill_md=skill_md,
        lora_name=meta.get("lora_name"),
    )


def list_skills(root: Path | None = None) -> list[dict]:
    root = root or SKILLS_ROOT
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        meta_path = p / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({
            "skill_id": p.name,
            "display_name": meta.get("display_name", p.name),
            "base_model": meta.get("base_model", ""),
            "has_lora": bool(meta.get("lora_name")),
            "version": meta.get("version", "0"),
        })
    return out

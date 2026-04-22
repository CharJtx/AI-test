"""Stage 5: Assemble a skill bundle and write provenance-aware meta.json.

Inputs expected in skills/<creator>/:
    persona.md      (from analyze)
    skill.md        (optional hand-compiled runtime prompt; auto-generated if missing)
    lora/           (optional, from train)

Writes meta.json with:
    display_name, version, base_model, lora_name,
    created_at, source_materials hash pointers, consent_status flag.

The consent_status default is NOT_OBTAINED — this must be flipped manually
only after a signed contract is on file (see docs/creator_contract_template.md).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def run(args: argparse.Namespace) -> int:
    sdir = Path("skills") / args.creator
    if not sdir.is_dir():
        print(f"[package] missing {sdir}")
        return 1

    persona_path = sdir / "persona.md"
    skill_path = sdir / "skill.md"
    meta_path = sdir / "meta.json"

    if not persona_path.exists():
        print(f"[package] missing {persona_path}")
        return 1

    # If no hand-edited skill.md, derive it from persona.md as a minimal
    # runtime prompt. The hand-edited version (if present) always wins.
    if not skill_path.exists():
        skill_path.write_text(
            persona_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        print(f"[package] skill.md not found — seeded from persona.md")

    prev = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    lora_path = Path(args.lora_path) if args.lora_path else (sdir / "lora")
    has_lora = lora_path.exists() and any(lora_path.glob("adapter_*.safetensors"))

    meta = {
        "skill_id": args.creator,
        "display_name": prev.get("display_name", args.creator.replace("-", " ").title()),
        "version": args.version,
        "created_at": prev.get("created_at") or datetime.now(timezone.utc).date().isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "base_model": prev.get("base_model", "llama3.1-70b-awq"),
        "lora_name": f"{args.creator}-lora" if has_lora else None,
        "consent_status": prev.get("consent_status", "NOT_OBTAINED — internal POC only"),
        "disclaimer": prev.get(
            "disclaimer",
            "AI approximation for internal POC. Not licensed by the subject. NOT FOR PUBLIC DEPLOYMENT.",
        ),
        "source_materials": prev.get("source_materials", {}),
        "tags": prev.get("tags", []),
        "language": prev.get("language", "en"),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[package] wrote {meta_path}")
    print(f"[package] lora_name={meta['lora_name']} consent={meta['consent_status']!r}")
    return 0

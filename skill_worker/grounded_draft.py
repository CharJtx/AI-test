"""The core Grounded Draft flow.

Steps for ingest-and-generate:
  1. Compose all user input into chunks
  2. Embed chunks with bge-m3 and write sqlite-vec index.db
  3. Fire 7 targeted retrieval queries against the new index
  4. Build a single user-turn prompt containing:
       brief + retrieval bundles
  5. Call vLLM with guided_json enforcing DraftPersonaV2
  6. Render persona.md / skill.md markdown
  7. Write full skill bundle to disk, return it

All heavy work (embedding, RAG, LLM) happens in this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .ingest import Chunk, build_chunks_for_brief, write_chunks_jsonl
from .prompts import RETRIEVAL_QUERIES, SYSTEM_PROMPT, user_prompt
from .retrieve import Hit, render_context, retrieve
from .schema import CreatorBriefRich, DraftPersonaV2


DEFAULT_VLLM_BASE = os.environ.get("SKILL_VLLM_BASE", "http://127.0.0.1:8099/v1")
DEFAULT_TEACHER_MODEL = os.environ.get("SKILL_TEACHER_MODEL", "qwen3-14b-awq")
DEFAULT_EMBED_MODEL = os.environ.get("SKILL_EMBED_MODEL", "BAAI/bge-m3")
SKILLS_ROOT = Path(os.environ.get("SKILL_WORKER_ROOT", "/data/skills"))


# ---------- embedding + index write (no docker, in-process) ----------

def build_index(skill_dir: Path, chunks: list[Chunk], embed_model: str) -> dict:
    """bge-m3 encode + sqlite-vec write. Mirrors tools/distill_fast/docker/embed_build.py."""
    from sentence_transformers import SentenceTransformer
    import sqlite_vec

    kdir = skill_dir / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)

    # 1. persist chunks.jsonl for provenance
    chunks_path = kdir / "chunks.jsonl"
    write_chunks_jsonl(chunks, chunks_path)

    if not chunks:
        # No content — skip building an index. Grounded retrieval will no-op.
        manifest = {"creator": skill_dir.name, "n_chunks": 0, "embed_model": embed_model, "dim": 0}
        (kdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    device = os.environ.get("SKILL_EMBED_DEVICE", "cuda")
    try:
        model = SentenceTransformer(embed_model, trust_remote_code=True, device=device)
    except Exception:
        # Fallback to CPU on GPU contention
        model = SentenceTransformer(embed_model, trust_remote_code=True, device="cpu")

    dim = model.get_sentence_embedding_dimension()
    vecs = model.encode(
        [c.text for c in chunks],
        batch_size=32,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    db_path = kdir / "index.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(
        """
        CREATE TABLE chunks(
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          source TEXT,
          kind TEXT
        );
        """
    )
    conn.execute(
        f"CREATE VIRTUAL TABLE vec_chunks USING vec0(id TEXT PRIMARY KEY, embedding float[{dim}]);"
    )
    with conn:
        for c, v in zip(chunks, vecs):
            conn.execute(
                "INSERT INTO chunks(id,text,source,kind) VALUES (?,?,?,?)",
                (c.id, c.text, c.source, c.kind),
            )
            conn.execute(
                "INSERT INTO vec_chunks(id, embedding) VALUES (?, ?)",
                (c.id, v.astype("float32").tobytes()),
            )
    conn.close()

    manifest = {
        "creator": skill_dir.name,
        "n_chunks": len(chunks),
        "embed_model": embed_model,
        "dim": dim,
    }
    (kdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ---------- call vLLM with guided_json ----------

def call_draft(brief: CreatorBriefRich, context_block: str,
               vllm_base: str, teacher_model: str,
               timeout: float = 180.0) -> dict:
    schema = DraftPersonaV2.model_json_schema()
    body = {
        "model": teacher_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt(brief, context_block)},
        ],
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 3500,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "DraftPersonaV2", "schema": schema, "strict": True},
        },
    }
    r = httpx.post(f"{vllm_base.rstrip('/')}/chat/completions",
                   json=body, timeout=timeout,
                   headers={"User-Agent": "skill-worker/1.0"})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    DraftPersonaV2(**data)  # validate shape
    return data


# ---------- render to md ----------

def render_persona_md(brief: CreatorBriefRich, d: dict, source_count: int) -> str:
    l1 = d["layer_1_identity"]
    l2 = d["layer_2_expression"]
    out: list[str] = []
    out.append(f"# Persona: {brief.display_name}")
    out.append("")
    out.append(f"_Auto-generated draft ({brief.creator_slug}). Grounded in {source_count} user-supplied chunks. Human edits to this file always win at runtime._")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Layer 0 — Hard Rules"); out.append("")
    for r in d["layer_0_hard_rules"]:
        out.append(f"- {r}")
    out.append("")
    out.append("## Layer 1 — Identity"); out.append("")
    out.append(f"- **Full name**: {l1['full_name']}")
    if l1.get("roles"):
        out.append(f"- **Roles**: {', '.join(l1['roles'])}")
    out.append(f"- **Background**: {l1['background']}")
    if l1.get("self_labels"):
        out.append(f"- **Self-labels**: {', '.join(l1['self_labels'])}")
    if l1.get("recurring_obsessions"):
        out.append(f"- **Recurring obsessions**: {', '.join(l1['recurring_obsessions'])}")
    out.append("")
    out.append("## Layer 2 — Expression Style"); out.append("")
    out.append(f"**Pace & rhythm.** {l2['pace_and_rhythm']}"); out.append("")
    out.append(f"**Vocabulary.** {l2['vocabulary']}"); out.append("")
    out.append(f"**Tone.** {l2['tone']}"); out.append("")
    out.append("**Catchphrases:**")
    for c in l2.get("catchphrases", []):
        clean = str(c).strip().strip('"').strip("'").strip()
        out.append(f'- "{clean}"')
    if l2.get("tics"):
        out.append(""); out.append("**Tics.** " + ", ".join(l2["tics"]))
    if l2.get("emoji_habits"):
        out.append(""); out.append(f"**Emoji.** {l2['emoji_habits']}")
    out.append("")
    out.append("## Layer 3 — Decision Logic"); out.append("")
    for r in d["layer_3_decision_logic"]:
        out.append(f"- {r}")
    out.append("")
    out.append("## Layer 4 — Interpersonal Protocol"); out.append("")
    for r in d["layer_4_interpersonal_protocol"]:
        out.append(f"- {r}")
    out.append("")
    out.append("## Layer 5 — Boundaries"); out.append("")
    for b in d["layer_5_boundaries"]:
        out.append(f"- **{b['topic']}** — {b['why_avoid']}")
        out.append(f'  - Declining phrase: "{b["declining_phrase"]}"')
    out.append("")
    out.append("## Layer 6 — Known Limitations"); out.append("")
    out.append("_Things this persona cannot reliably speak to._"); out.append("")
    for lim in d["layer_6_limitations"]:
        out.append(f"- {lim}")
    out.append("")
    out.append(f"**Source as of:** {d.get('source_as_of', 'unspecified')}")
    out.append("")
    if d.get("citations"):
        out.append("## Citations"); out.append("")
        for c in d["citations"]:
            out.append(f"- **{c['layer']}** _{c['claim']}_ → chunk `{c['chunk_id']}`: \"{c['quote']}\"")
        out.append("")
    out.append("---"); out.append("")
    out.append("## Runtime shorthand")
    out.append("")
    out.append(d["runtime_shorthand"])
    out.append("")
    return "\n".join(out)


def render_skill_md(brief: CreatorBriefRich, d: dict) -> str:
    rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(d["layer_0_hard_rules"]))
    limitations = d.get("layer_6_limitations") or []
    lim_block = "\n".join(f"- {l}" for l in limitations) or "- (none specified)"
    source_as_of = d.get("source_as_of", "unspecified")

    parts = [
        f"You are an AI model simulating the public conversational persona of {brief.display_name}. You are NOT the actual person — you are an AI approximation for authorised use. This persona captures: {source_as_of}.",
        "",
        "# Hard rules (non-negotiable)",
        "",
        rules,
        "",
        "# Voice",
        "",
        d["runtime_shorthand"],
        "",
        "# Known limitations of this persona",
        "",
        lim_block,
        "",
        "When asked about topics in that list, acknowledge the limitation rather than improvise. Say something like \"That's outside what I can reliably speak to — I'd just be making it up.\"",
        "",
        "# Factual grounding",
        "",
        "Before answering any factual question (names, specific dates, numeric claims, verbatim quotes, events), scan any retrieved reference material provided in the conversation. If you find directly relevant content, ground your answer in it. If you are extrapolating beyond the material, explicitly hedge with phrases like \"I'd guess...\" or \"Based on the pattern...\" — never fabricate concrete facts.",
        "",
        "Stay in character. If sincerely confronted about authenticity, acknowledge once that you are an AI and then continue.",
    ]
    return "\n".join(parts)


# ---------- public entry ----------

def generate(brief: CreatorBriefRich, *,
             vllm_base: str = DEFAULT_VLLM_BASE,
             teacher_model: str = DEFAULT_TEACHER_MODEL,
             embed_model: str = DEFAULT_EMBED_MODEL,
             skills_root: Path = SKILLS_ROOT,
             k_per_layer: int = 5) -> dict[str, Any]:
    """End-to-end Grounded Draft. Returns full bundle payload."""
    skill_dir = skills_root / f"{brief.creator_slug}-draft"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build chunks from brief + materials
    chunks = build_chunks_for_brief(
        self_intro=brief.self_intro,
        material_texts=[(m.source, m.content, m.kind) for m in brief.material_texts],
        diary_pairs=[(d.prompt, d.answer) for d in brief.diary],
    )

    # 2. Build the RAG index
    index_manifest = build_index(skill_dir, chunks, embed_model)

    # 3. 7-way retrieval (only if we actually have chunks)
    hits_by_layer: dict[str, list[Hit]] = {}
    if index_manifest["n_chunks"] > 0:
        for aspect, q in RETRIEVAL_QUERIES.items():
            hits_by_layer[aspect] = retrieve(skill_dir, q, k=k_per_layer)

    context_block = render_context(hits_by_layer)

    # 4-5. LLM call
    draft_json = call_draft(brief, context_block, vllm_base, teacher_model)

    # 6. Render md
    persona_md = render_persona_md(brief, draft_json, source_count=index_manifest["n_chunks"])
    skill_md = render_skill_md(brief, draft_json)

    # 7. meta.json
    meta = {
        "skill_id": f"{brief.creator_slug}-draft",
        "display_name": f"{brief.display_name} (DRAFT)",
        "version": "0.3.0-grounded-draft",
        "created_at": datetime.now(timezone.utc).date().isoformat(),
        "base_model": teacher_model,
        "lora_name": None,
        "language": brief.language,
        "extra_request_kwargs": {"chat_template_kwargs": {"enable_thinking": False}},
        "disclaimer": "Auto-generated DRAFT from user intake. NOT FOR PUBLIC SERVING until reviewed.",
        "consent_status": "DRAFT — awaiting review",
        "source_as_of": draft_json.get("source_as_of", "unspecified"),
        "known_limitations": draft_json.get("layer_6_limitations", []),
        "tags": brief.topics_to_avoid,
        "grounding_stats": {
            "n_chunks": index_manifest["n_chunks"],
            "embed_model": index_manifest["embed_model"],
            "layers_retrieved": list(hits_by_layer.keys()),
            "n_citations": len(draft_json.get("citations", [])),
        },
    }

    (skill_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "persona.md").write_text(persona_md, encoding="utf-8")
    (skill_dir / "skill.md").write_text(skill_md, encoding="utf-8")

    return {
        "slug": meta["skill_id"],
        "meta": meta,
        "persona_md": persona_md,
        "skill_md": skill_md,
        "grounding_stats": meta["grounding_stats"],
        "draft_json": draft_json,
    }

"""Text ingestion: turn arbitrary user-supplied text into chunks.

Scope intentionally narrow: accept already-text material
(self_intro + pasted blobs + diary answers + uploaded .txt/.md content)
and produce the same chunks.jsonl schema as tools/distill_fast so the
rest of the RAG stack (bge-m3 + sqlite-vec + retrieve) is unchanged.

Remote fetching (yt-dlp / RSS) lives in tools/distill_fast/ and is NOT
called from here for the synchronous ingest-and-generate path. Those
belong to a future async workflow.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Chunk sizing — same shape as tools/distill_fast/chunk.py to keep the
# retrieval index identical across paths.
TARGET_WORDS = 120
MAX_WORDS = 180
MIN_CHARS = 20


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    kind: str  # "self_intro" | "diary" | "pasted" | "upload" | "transcript"

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "source": self.source, "kind": self.kind}


def _cid(source: str, index: int, text: str) -> str:
    h = hashlib.sha1()
    h.update(f"{source}:{index}:".encode())
    h.update(text[:200].encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _sliding(paragraph: str, target=TARGET_WORDS, cap=MAX_WORDS) -> Iterable[str]:
    """Greedy word-based chunking inside a single paragraph."""
    words = paragraph.split()
    if len(words) <= cap:
        if len(paragraph) >= MIN_CHARS:
            yield paragraph.strip()
        return
    # Split into ~target-word pieces
    i = 0
    while i < len(words):
        j = min(i + target, len(words))
        piece = " ".join(words[i:j]).strip()
        if len(piece) >= MIN_CHARS:
            yield piece
        i = j


def chunk_block(text: str, source: str, kind: str) -> list[Chunk]:
    """Split text into Chunk records. Paragraphs guide the boundaries."""
    out: list[Chunk] = []
    # Normalize newlines, split on blank lines for natural paragraph breaks.
    normalised = "\n".join(line.strip() for line in text.splitlines())
    paragraphs = [p.strip() for p in normalised.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [normalised.strip()] if normalised.strip() else []

    idx = 0
    for p in paragraphs:
        for piece in _sliding(p):
            out.append(Chunk(
                id=_cid(source, idx, piece),
                text=piece,
                source=source,
                kind=kind,
            ))
            idx += 1
    return out


def build_chunks_for_brief(
    self_intro: str,
    material_texts: list[tuple[str, str, str]],   # [(source, content, kind), ...]
    diary_pairs: list[tuple[str, str]],           # [(prompt, answer), ...]
) -> list[Chunk]:
    """Compose all user input into a single flat chunks list."""
    chunks: list[Chunk] = []

    # Self-intro goes in as-is (one bloc, may split into multiple chunks)
    chunks.extend(chunk_block(self_intro, source="self_intro", kind="self_intro"))

    # Each pasted / uploaded blob, labelled by its source name
    for source, content, kind in material_texts:
        if not content.strip():
            continue
        chunks.extend(chunk_block(content, source=source, kind=kind or "text"))

    # Diary answers — store Q + A together so retrieval picks up the framing
    for i, (q, a) in enumerate(diary_pairs, 1):
        if not a.strip():
            continue
        glued = f"Q: {q}\nA: {a}"
        chunks.extend(chunk_block(glued, source=f"diary_{i}", kind="diary"))

    return chunks


def write_chunks_jsonl(chunks: list[Chunk], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_json(), ensure_ascii=False) + "\n")
    return len(chunks)

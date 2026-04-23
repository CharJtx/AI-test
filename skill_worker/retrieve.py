"""In-process RAG: embed one query, pull top-k chunks from sqlite-vec.

The embedding model stays resident in a singleton (loaded once per
worker process). sqlite connections are opened per-skill and cached.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_LOCK = threading.Lock()
_EMBEDDER = None  # type: ignore[var-annotated]
_EMBEDDER_NAME: Optional[str] = None
_CONN_CACHE: dict[str, sqlite3.Connection] = {}


@dataclass
class Hit:
    chunk_id: str
    text: str
    source: str
    kind: str
    distance: float


def _get_embedder(model_name: str):
    global _EMBEDDER, _EMBEDDER_NAME
    with _LOCK:
        if _EMBEDDER is not None and _EMBEDDER_NAME == model_name:
            return _EMBEDDER
        from sentence_transformers import SentenceTransformer
        _EMBEDDER = SentenceTransformer(model_name, trust_remote_code=True)
        _EMBEDDER_NAME = model_name
        return _EMBEDDER


def _get_conn(db_path: Path) -> sqlite3.Connection:
    key = str(db_path)
    with _LOCK:
        conn = _CONN_CACHE.get(key)
        if conn is not None:
            return conn
        import sqlite_vec
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _CONN_CACHE[key] = conn
        return conn


def retrieve(
    skill_dir: Path,
    query: str,
    k: int = 5,
) -> list[Hit]:
    """Return top-k chunks for `query` against skill_dir/knowledge/index.db."""
    kdir = skill_dir / "knowledge"
    manifest_path = kdir / "manifest.json"
    db_path = kdir / "index.db"
    if not manifest_path.exists() or not db_path.exists():
        return []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = _get_embedder(manifest["embed_model"])
    conn = _get_conn(db_path)

    vec = model.encode([query], normalize_embeddings=True)[0].astype("float32")
    cur = conn.execute(
        """
        SELECT v.id, c.text, c.source, c.kind, v.distance
        FROM vec_chunks v JOIN chunks c ON c.id = v.id
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (vec.tobytes(), k),
    )
    return [Hit(*row) for row in cur.fetchall()]


def render_context(
    hits_by_layer: dict[str, list[Hit]],
    per_layer_limit: int = 5,
    max_chars_per_chunk: int = 400,
) -> str:
    """Format retrieval results as a single system-prompt block.

    Layered so the LLM can tie each bundle to the persona slot it's
    informing. Chunks are capped in length to keep the total prompt
    under control even when users upload a lot of material.
    """
    if not any(hits_by_layer.values()):
        return ""

    out: list[str] = [
        "# Reference material (retrieved from the subject's own writing / transcripts)",
        "",
        "Each block is indexed by which persona aspect it's meant to inform.",
        "Use verbatim wording for Layer 2 catchphrases and Layer 5 declining",
        "phrases. Cite `chunk_id` in the citations array when grounding a claim.",
        "",
    ]
    for aspect, hits in hits_by_layer.items():
        if not hits:
            continue
        out.append(f"## for {aspect}")
        out.append("")
        for h in hits[:per_layer_limit]:
            text = h.text if len(h.text) <= max_chars_per_chunk else h.text[:max_chars_per_chunk] + "…"
            out.append(f"- [chunk_id={h.chunk_id}] (source={h.source}, kind={h.kind})")
            out.append(f"  {text}")
        out.append("")
    return "\n".join(out)

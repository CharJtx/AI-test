"""Runtime RAG helper: query a skill's sqlite-vec index for top-k chunks.

A process-wide cache keeps one connection + one embedding model loaded per
skill so request latency stays low.

The embedding model matches what tools/distill_fast/embed.py wrote (see
skills/{id}/knowledge/manifest.json).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


_LOCK = threading.Lock()
_INDEX_CACHE: dict[str, "SkillIndex"] = {}
_EMBEDDER_CACHE: dict[str, object] = {}


@dataclass
class Hit:
    id: str
    text: str
    source: str
    kind: str
    distance: float


class SkillIndex:
    def __init__(self, skill_id: str, db_path: Path, embed_model_name: str):
        self.skill_id = skill_id
        self.db_path = db_path
        self.embed_model_name = embed_model_name
        self._conn: sqlite3.Connection | None = None

    def _conn_open(self) -> sqlite3.Connection:
        if self._conn is None:
            import sqlite_vec  # requires `pip install sqlite-vec`
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self._conn = conn
        return self._conn

    def _embedder(self):
        model = _EMBEDDER_CACHE.get(self.embed_model_name)
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(self.embed_model_name, trust_remote_code=True)
            _EMBEDDER_CACHE[self.embed_model_name] = model
        return model

    def query(self, text: str, k: int = 5) -> list[Hit]:
        model = self._embedder()
        vec = model.encode([text], normalize_embeddings=True)[0].astype("float32")
        conn = self._conn_open()
        cur = conn.execute(
            """
            SELECT c.id, c.text, c.source, c.kind, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (vec.tobytes(), k),
        )
        return [
            Hit(id=row[0], text=row[1], source=row[2], kind=row[3], distance=row[4])
            for row in cur.fetchall()
        ]


def get_index(skill_id: str) -> SkillIndex | None:
    """Return a loaded index or None if the skill has no knowledge built."""
    with _LOCK:
        cached = _INDEX_CACHE.get(skill_id)
        if cached is not None:
            return cached

        kdir = Path("skills") / skill_id / "knowledge"
        db_path = kdir / "index.db"
        manifest_path = kdir / "manifest.json"
        if not db_path.exists() or not manifest_path.exists():
            return None

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        idx = SkillIndex(
            skill_id=skill_id,
            db_path=db_path,
            embed_model_name=manifest["embed_model"],
        )
        _INDEX_CACHE[skill_id] = idx
        return idx


def format_context(hits: list[Hit]) -> str:
    """Render hits as a 'Reference material' block for the system prompt."""
    if not hits:
        return ""
    lines = [
        "# Reference material (verbatim from creator's public content — use to inform voice and facts, do not quote these verbatim unless asked)"
    ]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] ({h.kind} / {h.source}) {h.text}")
    return "\n".join(lines)

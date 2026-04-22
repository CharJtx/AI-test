"""In-container embed builder. Reads chunks.jsonl, writes index.db.

Env:
    CREATOR      = skill id (directory name)
    EMBED_MODEL  = sentence-transformers model name (default BAAI/bge-m3)
    EMBED_DEVICE = "cuda" | "cpu" (default cuda)
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys


def main() -> int:
    creator = os.environ.get("CREATOR")
    if not creator:
        print("error: CREATOR env required", file=sys.stderr)
        return 2
    model_name = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
    device = os.environ.get("EMBED_DEVICE", "cuda")

    kdir = pathlib.Path("skills") / creator / "knowledge"
    chunks_path = kdir / "chunks.jsonl"
    if not chunks_path.exists():
        print(f"error: {chunks_path} not found; run chunk step first", file=sys.stderr)
        return 1

    chunks = [
        json.loads(l) for l in chunks_path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    if not chunks:
        print("error: chunks.jsonl is empty", file=sys.stderr)
        return 1

    print(f"[embed] loading {model_name} on {device} ...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name, trust_remote_code=True, device=device)
    dim = model.get_sentence_embedding_dimension()
    print(f"[embed] dim={dim}, n_chunks={len(chunks)}")

    texts = [c["text"] for c in chunks]
    vecs = model.encode(
        texts,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    db_path = kdir / "index.db"
    if db_path.exists():
        db_path.unlink()

    import sqlite_vec
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
            ts_start TEXT,
            ts_end TEXT,
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
                "INSERT INTO chunks(id,text,source,ts_start,ts_end,kind) VALUES (?,?,?,?,?,?)",
                (
                    c["id"], c["text"], c.get("source"),
                    str(c.get("ts_start") or ""), str(c.get("ts_end") or ""),
                    c.get("kind"),
                ),
            )
            conn.execute(
                "INSERT INTO vec_chunks(id, embedding) VALUES (?, ?)",
                (c["id"], v.astype("float32").tobytes()),
            )
    conn.close()

    (kdir / "manifest.json").write_text(
        json.dumps({
            "creator": creator,
            "embed_model": model_name,
            "dim": dim,
            "n_chunks": len(chunks),
        }, indent=2),
        encoding="utf-8",
    )
    print(f"[embed] wrote {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

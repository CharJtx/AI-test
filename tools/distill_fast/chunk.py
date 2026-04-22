"""Stage 2: Chunk transcripts + tweets into retrievable units.

Strategy:
  - Transcript segments: greedy concat by time, cap at ~120 words per chunk.
    Preserves source metadata (video id, timestamp) for provenance.
  - Tweets: one tweet per chunk (short, already self-contained).
  - Post-filter: drop chunks <20 chars (dead air, fillers).

Output: skills/{id}/knowledge/chunks.jsonl
Each line: {id, text, source, ts_start, ts_end, kind: "transcript"|"tweet"}
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MIN_CHARS = 20
TARGET_WORDS = 120
MAX_WORDS = 180


def _words(s: str) -> int:
    return len(s.split())


def _flush(chunks: list[dict], buf: list[dict], src: str) -> None:
    if not buf:
        return
    text = " ".join(b["text"] for b in buf).strip()
    if len(text) < MIN_CHARS:
        return
    cid = hashlib.sha1(f"{src}:{buf[0]['start']}".encode()).hexdigest()[:16]
    chunks.append({
        "id": cid,
        "text": text,
        "source": src,
        "ts_start": buf[0]["start"],
        "ts_end": buf[-1]["end"],
        "kind": "transcript",
    })


def _chunk_transcript(trans_path: Path, chunks: list[dict]) -> None:
    data = json.loads(trans_path.read_text(encoding="utf-8"))
    buf: list[dict] = []
    words_in_buf = 0
    src = trans_path.stem
    for seg in data.get("segments", []):
        buf.append(seg)
        words_in_buf += _words(seg["text"])
        if words_in_buf >= TARGET_WORDS:
            _flush(chunks, buf, src)
            buf = []
            words_in_buf = 0
        elif words_in_buf >= MAX_WORDS:
            _flush(chunks, buf, src)
            buf = []
            words_in_buf = 0
    _flush(chunks, buf, src)


def _chunk_tweets(tweets_path: Path, chunks: list[dict]) -> None:
    for i, line in enumerate(tweets_path.read_text(encoding="utf-8").splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = (obj.get("text") or "").strip()
        if len(text) < MIN_CHARS:
            continue
        cid = hashlib.sha1(f"tweet:{obj.get('id', i)}".encode()).hexdigest()[:16]
        chunks.append({
            "id": cid,
            "text": text,
            "source": "tweets",
            "ts_start": obj.get("created_at"),
            "ts_end": obj.get("created_at"),
            "kind": "tweet",
        })


def run(args: argparse.Namespace) -> int:
    ingest_dir = Path("work/ingest") / args.creator
    trans_dir = ingest_dir / "transcripts"
    tweets_file = ingest_dir / "tweets.jsonl"
    out_dir = Path("skills") / args.creator / "knowledge"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "chunks.jsonl"

    chunks: list[dict] = []

    if trans_dir.exists():
        for p in sorted(trans_dir.glob("*.json")):
            _chunk_transcript(p, chunks)

    if tweets_file.exists():
        _chunk_tweets(tweets_file, chunks)

    if not chunks:
        print(f"[chunk] no input found under {ingest_dir}")
        return 1

    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"[chunk] wrote {len(chunks)} chunks -> {out}")
    return 0

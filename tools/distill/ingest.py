"""Stage 1: Ingest a creator's public content -> transcripts.

Reads a YAML source list of the form:

    creator_handle: "@elonmusk"
    youtube_urls:
      - "https://www.youtube.com/watch?v=DxREm3s1scA"  # Lex Fridman #400
      - "https://www.youtube.com/watch?v=E1AxVXt2Gv4"
    twitter_archive: "data/elon_tweets.json"           # optional

Outputs to work/ingest/<creator>/:
    audio/            .m4a/.wav from yt-dlp
    transcripts/      .json from WhisperX with speaker diarization
    tweets.jsonl      normalized creator-only turns
    manifest.json     inventory + hashes (provenance)

Heavy tools (yt-dlp + WhisperX) run inside docker on the GPU server. This
module emits the shell commands; actual execution is done via ssh when
available or printed for manual run otherwise.

Not implemented yet — scaffolding only. Run the printed commands manually
until the runner is fleshed out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def run(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir) / args.creator
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"[ingest] creator={args.creator} workdir={workdir}")
    print(f"[ingest] source spec: {args.source}")

    # Placeholder: print the exact commands the user should run on the GPU box.
    print("\n--- Shell commands to run on the GPU server ---")
    print("# 1. Download audio via yt-dlp (inside docker for clean env):")
    print(f"""docker run --rm -v "$PWD/{workdir}:/work" \\
  -w /work jrottenberg/ffmpeg:latest \\
  sh -c 'apt-get update && apt-get install -y yt-dlp && \\
         mkdir -p audio && \\
         yt-dlp -f bestaudio -x --audio-format wav \\
           -o "audio/%(id)s.%(ext)s" \\
           $(cat sources.txt)'""")
    print()
    print("# 2. Transcribe with WhisperX (diarization):")
    print(f"""docker run --rm --gpus '"device=1"' \\
  -v "$PWD/{workdir}:/work" -w /work \\
  ghcr.io/m-bain/whisperx:latest \\
  whisperx audio/*.wav --diarize --output_dir transcripts \\
    --compute_type float16 --language en""")
    print()
    print("# 3. Filter to creator-only turns (wrapper TBD in this file).")

    manifest_path = workdir / "manifest.json"
    manifest = {
        "creator": args.creator,
        "source_spec": args.source,
        "status": "pending-manual-run",
        "hashes": {},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n[ingest] wrote stub manifest: {manifest_path}")
    return 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

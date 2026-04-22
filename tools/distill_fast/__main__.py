"""Fast-lane distill CLI.

Usage:
    python -m tools.distill_fast transcribe --creator elon-musk --source sources.yaml
    python -m tools.distill_fast chunk      --creator elon-musk
    python -m tools.distill_fast persona    --creator elon-musk
    python -m tools.distill_fast embed      --creator elon-musk
    python -m tools.distill_fast all        --creator elon-musk --source sources.yaml
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(prog="tools.distill_fast")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("transcribe", "chunk", "persona", "embed", "all"):
        sp = sub.add_parser(name)
        sp.add_argument("--creator", required=True)
        if name in ("transcribe", "all"):
            sp.add_argument("--source", required=True,
                            help="YAML file with youtube_urls + optional twitter_archive")
            sp.add_argument("--device", default="cuda:2",
                            help="GPU device for whisper (default cuda:2)")
        if name in ("persona", "all"):
            sp.add_argument("--teacher-url", default="https://llm.insnaplive.com/v1")
            sp.add_argument("--teacher-model", default="llama3.1-70b-awq")
        if name in ("embed", "all"):
            sp.add_argument("--embed-model", default="BAAI/bge-m3")

    args = p.parse_args()

    if args.cmd == "transcribe":
        from . import transcribe
        return transcribe.run(args)
    if args.cmd == "chunk":
        from . import chunk
        return chunk.run(args)
    if args.cmd == "persona":
        from . import persona_gen
        return persona_gen.run(args)
    if args.cmd == "embed":
        from . import embed
        return embed.run(args)
    if args.cmd == "all":
        from . import transcribe, chunk, persona_gen, embed
        for step in (transcribe.run, chunk.run, persona_gen.run, embed.run):
            rc = step(args)
            if rc != 0:
                return rc
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)

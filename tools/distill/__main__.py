"""Distillation CLI entrypoint.

Usage:
    python -m tools.distill ingest     --creator elon-musk --source sources.yaml
    python -m tools.distill analyze    --creator elon-musk
    python -m tools.distill synthesize --creator elon-musk --turns 30000
    python -m tools.distill train      --creator elon-musk --rank 32
    python -m tools.distill package    --creator elon-musk
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools.distill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("ingest", "analyze", "synthesize", "train", "package"):
        sp = sub.add_parser(name)
        sp.add_argument("--creator", required=True, help="skill_id / creator slug")
        if name == "ingest":
            sp.add_argument("--source", required=True, help="yaml with URL list + creator_handle")
            sp.add_argument("--workdir", default="work/ingest")
        elif name == "analyze":
            sp.add_argument("--teacher-url", default="https://llm.insnaplive.com/v1")
            sp.add_argument("--teacher-model", default="llama3.1-70b-awq")
        elif name == "synthesize":
            sp.add_argument("--turns", type=int, default=30000)
            sp.add_argument("--teacher-url", default="https://llm.insnaplive.com/v1")
            sp.add_argument("--teacher-model", default="llama3.1-70b-awq")
            sp.add_argument("--out", default="work/train_data")
        elif name == "train":
            sp.add_argument("--rank", type=int, default=32)
            sp.add_argument("--base", default="meta-llama/Meta-Llama-3.1-70B-Instruct")
            sp.add_argument("--epochs", type=int, default=3)
            sp.add_argument("--data", default="work/train_data")
        elif name == "package":
            sp.add_argument("--lora-path", default="")
            sp.add_argument("--version", default="0.2.0-lora")

    args = parser.parse_args()

    # Lazy imports so `--help` on an unused stage doesn't pull heavy deps.
    if args.cmd == "ingest":
        from . import ingest
        return ingest.run(args)
    if args.cmd == "analyze":
        from . import analyze
        return analyze.run(args)
    if args.cmd == "synthesize":
        from . import synthesize
        return synthesize.run(args)
    if args.cmd == "train":
        from . import train
        return train.run(args)
    if args.cmd == "package":
        from . import package
        return package.run(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)

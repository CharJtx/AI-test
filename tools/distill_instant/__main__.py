"""CLI entry: `python -m tools.distill_instant draft --brief path/to/brief.yaml`"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(prog="tools.distill_instant")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("draft", help="generate a draft skill bundle from a brief.yaml")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--teacher-url", default="https://llm.insnaplive.com/v1")
    sp.add_argument("--teacher-model", default="qwen3-14b-awq")

    args = p.parse_args()

    if args.cmd == "draft":
        from . import draft
        draft.run(
            Path(args.brief),
            teacher_url=args.teacher_url,
            teacher_model=args.teacher_model,
        )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)

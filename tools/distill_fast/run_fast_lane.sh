#!/bin/bash
# One-shot driver for the fast-lane pipeline on upaiserver303.
# Run from the repo root. Idempotent.
#
# Usage:
#   bash tools/distill_fast/run_fast_lane.sh <creator_id>
#
# Stages executed:
#   1. transcribe  (docker skill-transcribe:v1, GPU 2 by default)
#   2. chunk       (pure Python stdlib, host)
#   3. persona     (httpx -> llm.insnaplive.com; installs httpx --user if needed)
#   4. embed       (docker skill-embed:v1, GPU 3 by default)
#
# Env knobs:
#   TRANSCRIBE_GPU=2   EMBED_GPU=3   TEACHER_URL=https://llm.insnaplive.com/v1
#   TEACHER_MODEL=llama3.1-70b-awq   EMBED_MODEL=BAAI/bge-m3

set -euo pipefail

CREATOR="${1:-}"
if [ -z "$CREATOR" ]; then
    echo "usage: $0 <creator_id>" >&2
    exit 2
fi

if [ ! -d "skills/$CREATOR" ]; then
    echo "error: run from repo root; skills/$CREATOR not found" >&2
    exit 1
fi

SOURCES="skills/$CREATOR/sources.yaml"
if [ ! -f "$SOURCES" ]; then
    echo "error: missing $SOURCES" >&2
    exit 1
fi

TRANSCRIBE_GPU="${TRANSCRIBE_GPU:-2}"
EMBED_GPU="${EMBED_GPU:-3}"
TEACHER_URL="${TEACHER_URL:-https://llm.insnaplive.com/v1}"
TEACHER_MODEL="${TEACHER_MODEL:-llama3.1-70b-awq}"
EMBED_MODEL="${EMBED_MODEL:-BAAI/bge-m3}"

PYBIN="$(command -v python3 || command -v python)"
if [ -z "$PYBIN" ]; then
    echo "error: no python/python3 on PATH" >&2
    exit 1
fi

# Ensure httpx is available for persona step. yaml not required on host; the
# transcribe container carries its own yaml.
if ! "$PYBIN" -c "import httpx" 2>/dev/null; then
    echo "[setup] installing httpx --user ..."
    "$PYBIN" -m pip install --user --quiet "httpx>=0.27"
fi

echo ""
echo "================================================================"
echo "  Fast-lane distill: $CREATOR"
echo "  transcribe GPU $TRANSCRIBE_GPU  |  embed GPU $EMBED_GPU"
echo "  teacher: $TEACHER_MODEL @ $TEACHER_URL"
echo "================================================================"
echo ""

# ---------- stage 1: transcribe ----------
echo "[1/4] transcribe ..."
"$PYBIN" -m tools.distill_fast transcribe \
    --creator "$CREATOR" \
    --source "$SOURCES" \
    --device "cuda:$TRANSCRIBE_GPU"
bash "work/ingest/$CREATOR/run_transcribe.sh"

# ---------- stage 2: chunk ----------
echo ""
echo "[2/4] chunk ..."
"$PYBIN" -m tools.distill_fast chunk --creator "$CREATOR"

# ---------- stage 3: persona ----------
echo ""
echo "[3/4] persona (one vLLM call) ..."
"$PYBIN" -m tools.distill_fast persona \
    --creator "$CREATOR" \
    --teacher-url "$TEACHER_URL" \
    --teacher-model "$TEACHER_MODEL"

# ---------- stage 4: embed ----------
echo ""
echo "[4/4] embed ..."
"$PYBIN" -m tools.distill_fast embed \
    --creator "$CREATOR" \
    --embed-model "$EMBED_MODEL"
# embed.py emits a runner that wants repo root; it's already here.
bash "work/ingest/$CREATOR/run_embed.sh"

echo ""
echo "================================================================"
echo "  Done. Artifacts:"
echo "================================================================"
ls -lh "skills/$CREATOR/knowledge/"
echo ""
echo "Try it:"
echo "    python3 -m tools.chat_with_skill $CREATOR"

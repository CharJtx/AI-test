"""Stage 4: Build sqlite-vec embedding index (dockerized, reliable).

Approach mirrors transcribe.py: we ship a Dockerfile + entrypoint in-tree,
build a local image `skill-embed:v1` once, and run the index build inside
it. No sentence-transformers / sqlite-vec install required on the host.

Outputs:
    skills/{id}/knowledge/index.db
    skills/{id}/knowledge/manifest.json

(The same image is a safe target for running the retrieve side too, if you
ever want to move retrieval off the main app process.)
"""
from __future__ import annotations

import argparse
import shlex
import shutil
from pathlib import Path
from textwrap import dedent


IMAGE_TAG = "skill-embed:v1"


def run(args: argparse.Namespace) -> int:
    # We run the embed step from the repo root: the script mounts the repo
    # into /work so skills/{id}/knowledge is visible.
    #
    # Copy the docker context into the skill's work area so a creator bundle
    # is self-describing (handy when running on a different host).
    docker_src = Path(__file__).parent / "docker"
    work_docker = Path("work/ingest") / args.creator / "docker"
    work_docker.mkdir(parents=True, exist_ok=True)
    for name in ("Dockerfile.embed", "embed_build.py"):
        shutil.copy2(docker_src / name, work_docker / name)

    # Pick a GPU that's free enough. bge-m3 needs ~2GB; default cuda:3 is
    # usually less contested than the vLLM-owned set.
    device = getattr(args, "device", "cuda:3")
    gpu_id = device.split(":")[-1] if ":" in device else device

    embed_model = args.embed_model  # default BAAI/bge-m3

    script = Path("work/ingest") / args.creator / "run_embed.sh"
    script.write_text(dedent(f"""\
        #!/bin/bash
        # Auto-generated. Run from the repo root on upaiserver303.
        # Idempotent re-builds the image only if missing.
        set -euo pipefail

        # Resolve repo root: the directory with skills/ in it.
        REPO_ROOT="$(pwd)"
        if [ ! -d "$REPO_ROOT/skills/{args.creator}" ]; then
            echo "error: run this from the repo root (expected skills/{args.creator})" >&2
            exit 1
        fi

        IMAGE={shlex.quote(IMAGE_TAG)}
        GPU_ID={shlex.quote(gpu_id)}
        CREATOR={shlex.quote(args.creator)}
        EMBED_MODEL={shlex.quote(embed_model)}

        DOCKER_CTX="work/ingest/{args.creator}/docker"
        if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
            echo "[build] $IMAGE (first run, ~5-8 min) ..."
            docker build -t "$IMAGE" -f "$DOCKER_CTX/Dockerfile.embed" "$DOCKER_CTX"
        else
            echo "[build] $IMAGE cached, skipping"
        fi

        docker run --rm \\
          --runtime=nvidia \\
          -e NVIDIA_VISIBLE_DEVICES="$GPU_ID" \\
          -e CREATOR="$CREATOR" \\
          -e EMBED_MODEL="$EMBED_MODEL" \\
          -e EMBED_DEVICE=cuda \\
          -v "$REPO_ROOT:/work" \\
          -v "hf_cache:/root/.cache/huggingface" \\
          -w /work \\
          "$IMAGE" \\
          python3 work/ingest/$CREATOR/docker/embed_build.py

        echo "[done]"
        ls -lh skills/$CREATOR/knowledge/
    """), encoding="utf-8")
    script.chmod(0o755)

    print(f"[embed] wrote {script}")
    print()
    print("Next step — on upaiserver303, from the repo root:")
    print(f"    bash {script.as_posix()}")
    return 0

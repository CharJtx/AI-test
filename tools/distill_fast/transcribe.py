"""Stage 1: Download + transcribe via a pinned self-built docker image.

Strategy (revised for reliability):
  1. Build a single image `skill-transcribe:v1` on the server, from a
     Dockerfile we ship in-tree. No dependency on third-party registries
     that might move or disappear (the prior ghcr.io/guillaumekln image was
     deprecated by its author).
  2. Run that image with the chosen GPU mounted; it handles yt-dlp and
     faster-whisper in one pass. Idempotent — already-done files are skipped.

Everything the container needs lives under tools/distill_fast/docker/:
    Dockerfile.transcribe
    whisper_transcribe.py

Run on upaiserver303 (or any host with docker + nvidia-container-toolkit).

This module only emits a runner shell script — we do not invoke docker from
the Windows side. The operator SSHes to the server and runs the script.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
from pathlib import Path
from textwrap import dedent


IMAGE_TAG = "skill-transcribe:v1"


def run(args: argparse.Namespace) -> int:
    workdir = Path("work/ingest") / args.creator
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "audio").mkdir(exist_ok=True)
    (workdir / "transcripts").mkdir(exist_ok=True)

    # Copy the docker build context and the in-container entrypoint next to
    # sources.yaml so the operator can `scp -r work/ingest/<creator>`
    # to the server and have everything self-contained.
    docker_src = Path(__file__).parent / "docker"
    docker_dst = workdir / "docker"
    docker_dst.mkdir(exist_ok=True)
    for name in ("Dockerfile.transcribe", "whisper_transcribe.py"):
        shutil.copy2(docker_src / name, docker_dst / name)

    # Resolve sources.yaml path: if caller pointed outside workdir, copy it in.
    src_arg = Path(args.source)
    if src_arg.is_absolute() or not str(src_arg).startswith(str(workdir)):
        local_src = workdir / "sources.yaml"
        if src_arg.resolve() != local_src.resolve():
            shutil.copy2(src_arg, local_src)
    else:
        local_src = src_arg

    device = args.device  # e.g. "cuda:2"
    # Map "cuda:2" -> "2" for NVIDIA_VISIBLE_DEVICES
    gpu_id = device.split(":")[-1] if ":" in device else device

    script = workdir / "run_transcribe.sh"
    script.write_text(dedent(f"""\
        #!/bin/bash
        # Auto-generated. Run on upaiserver303 from this directory.
        # Idempotent — re-runs only missing downloads / transcripts.
        set -euo pipefail
        cd "$(dirname "$0")"

        IMAGE={shlex.quote(IMAGE_TAG)}
        GPU_ID={shlex.quote(gpu_id)}

        # 1) build the image if it's missing (first run only, ~3-5 min)
        if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
            echo "[build] $IMAGE ..."
            docker build -t "$IMAGE" -f docker/Dockerfile.transcribe docker
        else
            echo "[build] $IMAGE cached, skipping"
        fi

        # 2) run the transcribe pass. We mount the creator workdir at /work
        #    and pass SOURCES env so whisper_transcribe.py finds sources.yaml.
        docker run --rm \\
          --runtime=nvidia \\
          -e NVIDIA_VISIBLE_DEVICES="$GPU_ID" \\
          -e SOURCES=sources.yaml \\
          -v "$PWD:/work" \\
          -v "hf_cache:/root/.cache/huggingface" \\
          -w /work \\
          "$IMAGE" \\
          python3 docker/whisper_transcribe.py

        echo "[done] transcripts:"
        ls -lh transcripts/ 2>/dev/null || true
    """), encoding="utf-8")
    script.chmod(0o755)

    manifest = workdir / "manifest.json"
    prev = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    prev.update({
        "creator": args.creator,
        "source_spec": str(local_src),
        "device": device,
        "image": IMAGE_TAG,
        "status": "script-emitted",
    })
    manifest.write_text(json.dumps(prev, indent=2), encoding="utf-8")

    print(f"[transcribe] wrote {script}")
    print(f"[transcribe] workdir ready: {workdir}")
    print()
    print("Next step — on upaiserver303:")
    print(f"    cd {workdir.as_posix()}")
    print(f"    bash run_transcribe.sh")
    return 0

"""In-container transcription entrypoint. Idempotent: skips files already done.

Reads sources.yaml, downloads any missing audio with yt-dlp, then transcribes
with faster-whisper large-v3. Output per input video:

    audio/{video_id}.wav
    transcripts/{video_id}.json
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import yaml


def _video_id(url: str) -> str:
    # Good enough for normal youtube.com / youtu.be URLs.
    if "v=" in url:
        return url.split("v=", 1)[1].split("&", 1)[0]
    return url.rstrip("/").rsplit("/", 1)[-1]


def _download_audio(url: str, audio_dir: pathlib.Path) -> pathlib.Path:
    vid = _video_id(url)
    out = audio_dir / f"{vid}.wav"
    if out.exists() and out.stat().st_size > 0:
        print(f"  skip download: {out}")
        return out
    print(f"  downloading {url}")
    subprocess.check_call([
        "yt-dlp", "-f", "bestaudio", "-x",
        "--audio-format", "wav",
        "-o", str(audio_dir / f"{vid}.%(ext)s"),
        url,
    ])
    return out


def _transcribe(
    wav: pathlib.Path,
    out: pathlib.Path,
    model,
    language: str | None,
) -> None:
    if out.exists():
        print(f"  skip transcribe: {out}")
        return
    print(f"  transcribing {wav.name}")
    segments, info = model.transcribe(
        str(wav),
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
        language=language,
    )
    data: dict[str, Any] = {
        "language": info.language,
        "duration": info.duration,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
        ],
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    sources_path = pathlib.Path(os.environ.get("SOURCES", "sources.yaml"))
    audio_dir = pathlib.Path("audio")
    trans_dir = pathlib.Path("transcripts")
    audio_dir.mkdir(exist_ok=True)
    trans_dir.mkdir(exist_ok=True)

    if not sources_path.exists():
        print(f"error: {sources_path} not found", file=sys.stderr)
        return 1
    spec = yaml.safe_load(sources_path.read_text(encoding="utf-8"))

    items = spec.get("youtube", []) or []
    if not items:
        print("no youtube sources in spec; nothing to do")
        return 0

    language = spec.get("language")

    print("[1/2] downloading audio ...")
    jobs: list[tuple[pathlib.Path, pathlib.Path]] = []
    for item in items:
        wav = _download_audio(item["url"], audio_dir)
        vid = wav.stem
        jobs.append((wav, trans_dir / f"{vid}.json"))

    print("[2/2] loading faster-whisper large-v3 ...")
    # Import late so the yt-dlp step can complete even if CUDA has issues.
    from faster_whisper import WhisperModel
    model = WhisperModel(
        "large-v3",
        device="cuda",
        compute_type="float16",  # fp16 on 4090 is fast and accurate
    )

    for wav, out in jobs:
        _transcribe(wav, out, model, language)

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

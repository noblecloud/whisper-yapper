#!/usr/bin/env python3
"""Transcribe audio via the Mars speaches server (OpenAI-compatible).

Chunks the wav under upload limits, offsets segments back to absolute time,
writes SRT. Self-hosted — no cloud keys.
Usage: python3 transcribe_mars.py <audio.wav> <out.srt>
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Engine switch: WATCH_STT_URL env var. Default = whisper-yapper on Mars
# (Apple SpeechAnalyzer, ~50x realtime). Fallback = Mars speaches
# (faster-whisper-medium, ~2-5x realtime, OpenAI-compatible) — keep its URL
# here for one-line revert:
#   export WATCH_STT_URL=http://mars.golden-hops.ts.net:8001/v1/audio/transcriptions
BASE = os.environ.get(
    "WATCH_STT_URL",
    "http://mars.golden-hops.ts.net:8002/v1/audio/transcriptions",
)
MODEL = os.environ.get("WATCH_STT_MODEL", "Systran/faster-whisper-medium")
MAX_BYTES = 23 * 1024 * 1024
BYTES_PER_SEC = 32000  # 16 kHz mono 16-bit PCM


def fmt(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    wav, out = Path(sys.argv[1]), Path(sys.argv[2])
    total = wav.stat().st_size
    dur = total / BYTES_PER_SEC
    chunk = int(MAX_BYTES / BYTES_PER_SEC)
    n = int(-(-dur // chunk))
    print(f"{dur:.0f}s audio -> {n} chunks of {chunk}s", flush=True)

    segs = []
    for i in range(n):
        start = i * chunk
        length = min(chunk, dur - start)
        cpath = wav.parent / f"chunk_{i:02d}.wav"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(length),
             "-i", str(wav), "-c", "pcm_s16le", str(cpath)],
            capture_output=True, text=True)
        if r.returncode:
            print(f"chunk {i} ffmpeg fail: {r.stderr[:200]}", flush=True)
            continue
        r = subprocess.run(
            ["curl", "-s", "-m", "600", BASE,
             "-F", f"file=@{cpath}",
             "-F", f"model={MODEL}",
             "-F", "language=en",
             "-F", "response_format=verbose_json"],
            capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
            for s in d.get("segments", []):
                segs.append((start + s["start"], start + s["end"], s["text"].strip()))
        except Exception as e:
            print(f"chunk {i} parse fail: {e} | resp: {r.stdout[:150]}", flush=True)
        print(f"chunk {i + 1}/{n} done ({len(segs)} segs so far)", flush=True)

    segs.sort()
    lines = []
    for j, (a, b, t) in enumerate(segs, 1):
        lines.append(f"{j}\n{fmt(a)} --> {fmt(b)}\n{t}\n")
    out.write_text("\n".join(lines))
    print(f"DONE: {len(segs)} segments -> {out}", flush=True)


if __name__ == "__main__":
    main()

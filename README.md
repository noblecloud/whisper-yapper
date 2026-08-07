# whisper-yapper

A whisper-compatible transcription server whose engine is **yap** — the CLI
wrapper around Apple's **`SpeechAnalyzer`** (the macOS 26+ Speech framework
engine behind Notes/Voice Memos). Runs fully on-device: no cloud, no API keys,
no model downloads, no TCC permission prompts.

**Measured (2026-08-07): ~55× realtime** on an M2 Pro — a 2.5-hour wav
transcribes in ~160 s, single-shot (no chunking). Compare: faster-whisper
(CPU) ≈ 1.5–5× realtime.

## Components

| File | Purpose |
|---|---|
| `server.py` | Python **stdlib-only** HTTP server — the whisper-compatible API layer |
| `bin/yap` | Engine CLI (built from [finnvoor/yap](https://github.com/finnvoor/yap), CC0 — see `scripts/build-yap.sh`; not committed) |
| `scripts/build-yap.sh` | Builds the `yap` binary from pinned source |
| `com.noblecloud.whisper-yapper.plist` | launchd agent (Mars deployment) |
| `clients/transcribe_mars.py` | `/watch` pipeline client (chunks + offsets + SRT); live copy patched at `~/Personal/watch/transcribe_mars.py` |

## API

```
POST /v1/audio/transcriptions   multipart: file, model, language, response_format
GET  /v1/models
GET  /health
```

`response_format`: `json` (default, `{"text": ...}`), `verbose_json`
(`{"text", "task", "language", "duration", "segments": [{id,start,end,text}]}`),
`text`, `srt`. Shape matches [speaches](https://github.com/lexkoro/speaches)
/ OpenAI, so clients swap engines by changing a base URL.

Env: `YAP_BIN` (default `./bin/yap`), `WATCH_STT_HOST`/`WATCH_STT_PORT`
(default `0.0.0.0:8002`), `WATCH_STT_MAX_UPLOAD_BYTES` (default 1 GiB).

## Build & run

```bash
./scripts/build-yap.sh          # builds bin/yap (needs Xcode/CLT Swift)
python3 server.py               # serve mode: listens on :8002

# smoke test
curl -F file=@test.wav -F model=whisper-1 \
     -F response_format=verbose_json http://localhost:8002/v1/audio/transcriptions
```

## CLI mode (no server)

Same engine, one-shot, from the terminal:

```bash
./whisper-yapper transcribe file.wav -o out.srt          # srt (default)
./whisper-yapper transcribe file.wav --format text       # plain text to stdout
./whisper-yapper transcribe file.wav --format json       # segments JSON
./whisper-yapper transcribe file.wav --locale en-US -o out.srt
```

## Deploy (Mars)

```bash
scp -r server.py bin/ com.noblecloud.whisper-yapper.plist mars:~/whisper-yapper/
ssh mars 'mkdir -p ~/Library/LaunchAgents && cp ~/whisper-yapper/com.noblecloud.whisper-yapper.plist ~/Library/LaunchAgents/'
ssh mars 'launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.noblecloud.whisper-yapper.plist'
# service: http://mars.golden-hops.ts.net:8002  (tailnet only)
```

## /watch engine swap

```bash
# whisper-yapper (default since 2026-08-07)
python3 transcribe_mars.py video.wav out.srt

# Fallback: speaches (faster-whisper-medium) on Mars :8001
export WATCH_STT_URL=http://mars.golden-hops.ts.net:8001/v1/audio/transcriptions
python3 transcribe_mars.py video.wav out.srt
```

## Notes & caveats

- **English is guaranteed on-device**; other languages may route to Apple's
  servers (on-device coverage varies by locale).
- The engine itself needs **no chunking** (2.5 h single-shot); the client still
  chunks at 23 MB out of habit — harmless.
- Non-speech audio returns few/no segments; `language` only remaps to en-US
  for `en`, or passes through BCP-47 tags.
- One transcription at a time (single-flight lock; 429 when busy).

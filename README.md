# whisper-yapper

A **whisper-compatible speech transcription server** powered by Apple's
on-device `SpeechAnalyzer` — the macOS 26+ Speech framework engine behind
Notes and Voice Memos.

Drop in the URL, point any whisper client at it, and get transcriptions at
**~55× realtime** with zero cloud dependency: no API keys, no model
downloads, no permission prompts.

**Credit where it's due:** the actual speech-to-text is performed by
[yap](https://github.com/finnvoor/yap) (CC0-1.0, by Finn Voorhees) — a CLI
wrapper around Apple's `SpeechAnalyzer`. whisper-yapper is the
whisper-compatible server/CLI layer around yap; yap does all the heavy
lifting.

```
audio file → whisper-yapper (HTTP API or CLI) → yap → Apple SpeechAnalyzer → segments + timestamps
```

## Highlights

- **~55× realtime** (measured, Apple M2 Pro): a 2.5-hour wav in ~160 s — single shot, no chunking
- **OpenAI/whisper-compatible**: `POST /v1/audio/transcriptions`, `GET /v1/models` — same shape as OpenAI, Groq, speaches
- **Runs fully on-device**: no cloud keys, no model downloads, no TCC prompts
- **Two modes**: HTTP server (stdlib-only Python) or one-shot CLI
- **Word-level timestamps** and `json` / `verbose_json` / `text` / `srt` outputs

## Requirements

- macOS 26+ (SpeechAnalyzer availability)
- Apple Silicon recommended (the engine is neural-accelerated)
- Xcode Command Line Tools (only to build the `yap` engine binary)

## Quickstart

```bash
git clone https://github.com/noblecloud/whisper-yapper
cd whisper-yapper
./scripts/build-yap.sh          # builds bin/yap from pinned finnvoor/yap source
python3 server.py               # serve mode, listens on :8002

# smoke test (another terminal)
curl -F file=@speech.wav -F model=whisper-1 \
     -F response_format=verbose_json http://localhost:8002/v1/audio/transcriptions
```

## CLI mode (no server)

Same engine, one-shot:

```bash
./whisper-yapper transcribe speech.wav -o speech.srt          # srt (default)
./whisper-yapper transcribe speech.wav --format text          # plain text
./whisper-yapper transcribe speech.wav --format json          # segments JSON
./whisper-yapper transcribe speech.wav --locale en-US -o out.srt
```

## API

```
POST /v1/audio/transcriptions   multipart: file, model, language, response_format
GET  /v1/models
GET  /health
```

| Field | Values | Notes |
|---|---|---|
| `file` | audio file | wav / m4a / mp3 / video (anything AVFoundation reads) |
| `model` | any | accepted and ignored — the engine is fixed (whisper-yapper-1) |
| `language` | BCP-47 | e.g. `en` → en-US; default en-US |
| `response_format` | `json` · `verbose_json` · `text` · `srt` | default `json` |

`verbose_json` returns segments in the OpenAI shape:

```json
{
  "text": "…",
  "task": "transcribe",
  "language": "en-US",
  "duration": 753.0,
  "segments": [
    {"id": 1, "start": 0.0, "end": 1.98, "text": "…"}
  ]
}
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `WHISPER_YAPPER_HOST` | `0.0.0.0` | bind address |
| `WHISPER_YAPPER_PORT` | `8002` | listen port |
| `WHISPER_YAPPER_MAX_UPLOAD_BYTES` | `1073741824` (1 GiB) | upload cap |
| `YAP_BIN` | `./bin/yap` | engine binary path |

Legacy `WATCH_STT_*` env names are still accepted.

## Deployment

Run it as a user LaunchAgent so it survives reboots (macOS):

```xml
<!-- ~/Library/LaunchAgents/com.noblecloud.whisper-yapper.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.noblecloud.whisper-yapper</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>/path/to/whisper-yapper/server.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.noblecloud.whisper-yapper.plist
```

For tailnet-only exposure, bind `127.0.0.1` and use `tailscale serve`.

## Benchmarks (measured 2026-08-07)

| Engine | Realtime factor |
|---|---|
| **whisper-yapper (SpeechAnalyzer, M2 Pro)** | **~55×** |
| whisper.cpp Metal (claimed ceiling) | 10–20× |
| faster-whisper-medium (CPU) | ~1.5–5× |

Same-span quality check vs faster-whisper-medium: 0.91 word overlap.

## Caveats

- **English is guaranteed on-device**; other locales may route through Apple's
  network services (on-device coverage varies by locale).
- One transcription at a time (single-flight lock; `429` when busy).
- The engine needs no chunking — long files are fine in one request.

## Credits

- **[finnvoor/yap](https://github.com/finnvoor/yap)** (CC0-1.0) by
  [Finn Voorhees](https://github.com/finnvoor) — the engine CLI that performs
  the actual transcription. whisper-yapper is a whisper-compatible
  server/CLI wrapper around yap; without it, none of this works.
- **Apple Speech framework** (`SpeechAnalyzer`) — the on-device transcription
  engine in macOS 26+ (the same engine behind Notes and Voice Memos).

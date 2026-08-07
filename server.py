#!/usr/bin/env python3
"""whisper-yapper: OpenAI-compatible transcription service backed by Apple's
SpeechAnalyzer (on-device, ANE-accelerated) via the yap CLI.

Two modes:
  serve       (default)  HTTP server — OpenAI-compatible API on :8002
  transcribe  FILE       one-shot CLI transcription, no server:
                         whisper-yapper transcribe file.wav -o out.srt

Endpoint:  POST /v1/audio/transcriptions   (multipart: file, model, language, response_format)
           GET  /v1/models
           GET  /health

Response shapes (response_format):
  json        -> {"text": "..."}                                  (default, OpenAI-compatible)
  verbose_json-> {"text","task","language","duration","segments":[{id,start,end,text}]}
  text        -> plain transcript
  srt         -> subtitle file content

Stdlib only. Single-flight (one transcription at a time) via a lock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

YAP_BIN = os.environ.get("YAP_BIN", str(Path(__file__).resolve().parent / "bin" / "yap"))
HOST = os.environ.get("WATCH_STT_HOST", "0.0.0.0")
PORT = int(os.environ.get("WATCH_STT_PORT", "8002"))
ENGINE_MODEL = "whisper-yapper-1"  # what /v1/models advertises
DEFAULT_LOCALE = "en-US"
MAX_UPLOAD_BYTES = int(os.environ.get("WATCH_STT_MAX_UPLOAD_BYTES", str(1024 ** 3)))  # 1 GiB cap

_lock = threading.Lock()


def _fmt_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(wav_path: str, locale: str = DEFAULT_LOCALE) -> dict:
    """Run yap on a wav file, return parsed {'segments': [...], 'duration': s}."""
    cmd = [YAP_BIN, "transcribe", wav_path, "--json", "--locale", locale]
    started = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    wall = time.monotonic() - started
    if r.returncode != 0:
        raise RuntimeError(f"yap failed rc={r.returncode}: {r.stderr[-500:]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"yap output not JSON: {e} | {r.stdout[-200:]}")
    data["_wall"] = wall
    return data


def _parse_multipart(content_type: str, body: bytes) -> dict:
    """Return {'fields': {...}, 'file': (filename, bytes)} from a multipart body."""
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    file_part = None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        if part.get_filename():
            file_part = (part.get_filename(), part.get_payload(decode=True) or b"")
        else:
            fields[name] = part.get_payload(decode=True).decode("utf-8", "replace")
    return {"fields": fields, "file": file_part}


class Handler(BaseHTTPRequestHandler):
    server_version = "whisper-yapper/0.1"

    def log_message(self, fmt, *args):  # quieter logs
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, payload: dict | str, content_type: str = "application/json"):
        if isinstance(payload, str):
            body = payload.encode()
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"status": "ok", "engine": ENGINE_MODEL})
        if path == "/v1/models":
            return self._send(200, {
                "object": "list",
                "data": [{"id": ENGINE_MODEL, "object": "model", "owned_by": "apple"}],
            })
        self._send(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/v1/audio/transcriptions":
            return self._send(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._send(400, {"error": {"message": "multipart/form-data required", "type": "bad_request"}})

        parsed = _parse_multipart(ctype, body)
        file_part = parsed["file"]
        fields = parsed["fields"]
        if not file_part:
            return self._send(400, {"error": {"message": "missing file part", "type": "bad_request"}})

        fname, fbytes = file_part
        if len(fbytes) > MAX_UPLOAD_BYTES:
            return self._send(413, {"error": {"message": f"file too large ({len(fbytes)} bytes, cap {MAX_UPLOAD_BYTES})", "type": "payload_too_large"}})
        response_format = fields.get("response_format", "json")
        if response_format not in ("json", "verbose_json", "text", "srt"):
            return self._send(400, {"error": {"message": f"unsupported response_format {response_format}", "type": "bad_request"}})

        lang = fields.get("language", "").lower()
        locale = DEFAULT_LOCALE
        if lang and lang != "en" and "-" in lang:
            locale = lang

        if not _lock.acquire(timeout=5):
            return self._send(429, {"error": {"message": "another transcription in progress", "type": "busy"}})
        try:
            tmp = Path(tempfile.gettempdir()) / f"whisper-yapper-{uuid.uuid4().hex}{Path(fname).suffix}"
            tmp.write_bytes(fbytes)
            try:
                data = transcribe(str(tmp), locale)
            finally:
                tmp.unlink(missing_ok=True)
        except Exception as e:
            return self._send(500, {"error": {"message": str(e), "type": "engine_error"}})
        finally:
            _lock.release()

        segments = data.get("segments", [])
        duration = float(data.get("metadata", {}).get("duration") or 0.0)
        text = " ".join(s["text"] for s in segments)
        print(f"[{time.strftime('%H:%M:%S')}] OK {len(segments)} segs, {duration:.0f}s audio, "
              f"engine wall {data.get('_wall', 0):.1f}s", flush=True)

        if response_format == "json":
            return self._send(200, {"text": text})
        if response_format == "text":
            return self._send(200, text, content_type="text/plain")
        if response_format == "srt":
            lines = []
            for i, s in enumerate(segments, 1):
                lines.append(f"{i}\n{_fmt_srt_time(s['start'])} --> {_fmt_srt_time(s['end'])}\n{s['text']}\n")
            return self._send(200, "\n".join(lines), content_type="text/plain")
        # verbose_json — same shape speaches returns
        return self._send(200, {
            "text": text,
            "task": "transcribe",
            "language": data.get("metadata", {}).get("language", locale),
            "duration": duration,
            "segments": [
                {"id": s["id"], "start": float(s["start"]), "end": float(s["end"]), "text": s["text"]}
                for s in segments
            ],
        })


def format_output(data: dict, fmt: str) -> str:
    """Render yap segments as srt / text / json (CLI mode)."""
    segments = data.get("segments", [])
    if fmt == "text":
        return " ".join(s["text"] for s in segments)
    if fmt == "srt":
        return "\n".join(
            f"{i}\n{_fmt_srt_time(s['start'])} --> {_fmt_srt_time(s['end'])}\n{s['text']}\n"
            for i, s in enumerate(segments, 1)
        )
    return json.dumps(data, indent=2)


def cli_main(argv: list[str]) -> int:
    """Entry point for both modes. serve = HTTP server; transcribe = one-shot."""
    if not argv or argv[0] == "serve":
        main()
        return 0
    if argv[0] != "transcribe" or len(argv) < 2:
        print("usage: whisper-yapper [serve] | transcribe FILE [--format srt|text|json] "
              "[--locale LOC] [-o OUT]", file=sys.stderr)
        return 2
    if not os.path.exists(YAP_BIN):
        print(f"yap binary not found at {YAP_BIN} (set YAP_BIN)", file=sys.stderr)
        return 1
    path, fmt, locale, out = argv[1], "srt", DEFAULT_LOCALE, None
    i = 2
    while i < len(argv):
        if argv[i] == "--format" and i + 1 < len(argv):
            fmt, i = argv[i + 1], i + 2
        elif argv[i] == "--locale" and i + 1 < len(argv):
            locale, i = argv[i + 1], i + 2
        elif argv[i] == "-o" and i + 1 < len(argv):
            out, i = argv[i + 1], i + 2
        else:
            print(f"unknown arg: {argv[i]}", file=sys.stderr)
            return 2
    if fmt not in ("srt", "text", "json"):
        print(f"unknown format: {fmt}", file=sys.stderr)
        return 2
    try:
        data = transcribe(path, locale)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    text = format_output(data, fmt)
    if out:
        Path(out).write_text(text)
    else:
        print(text)
    print(f"[{len(data.get('segments', []))} segments, {data.get('_wall', 0):.1f}s]", file=sys.stderr)
    return 0


def main():
    if not os.path.exists(YAP_BIN):
        raise SystemExit(f"yap binary not found at {YAP_BIN} (set YAP_BIN)")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"whisper-yapper listening on http://{HOST}:{PORT} (engine: {ENGINE_MODEL} via {YAP_BIN})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))

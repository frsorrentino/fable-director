#!/usr/bin/env python3
"""Rotta immagini di external-exec.py (type "image", 1.24.0).

Mini server HTTP locale che imita generateContent di Gemini:
  I1  risposta con inlineData → bytes scritti su --out, CHECK image
  I2  --out mancante → errore esplicito (binario mai su stdout)
  I3  risposta solo testo (es. rifiuto safety) → error, testo citato
  I4  429 con "limit: 0" → detail billing dedicato, non quota generica
  I5  flag incompatibili (--schema-json) → errore esplicito
  I6  chiave API nell'header x-goog-api-key, MAI in query string
  I7  doctor --ping su provider image: models-list, MAI una generazione

Usage: python3 tests/image-route-verify.py   (exit 0 = all green)
"""
import base64
import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "external-exec.py"

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-payload"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()

passed, failed = [], []
seen = {"path": "", "key_header": "", "gets": 0, "posts": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        seen["gets"] += 1
        if self.path.startswith("/v1beta/models"):
            body = json.dumps({"models": [{"name": "models/img-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        seen["posts"] += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen["path"] = self.path
        seen["key_header"] = self.headers.get("x-goog-api-key", "")
        if Handler.mode == "billing429":
            body = json.dumps({"error": {"code": 429, "message":
                "Quota exceeded for metric ... limit: 0, model: img"}}).encode()
            self.send_response(429)
        elif Handler.mode == "textonly":
            body = json.dumps({"candidates": [{"content": {"parts": [
                {"text": "cannot draw that, sorry"}]}}]}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png",
                                "data": PNG_B64}}]}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n      {evidence}"))


def slug(cwd):
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def setup(port):
    home = Path(tempfile.mkdtemp(prefix="fd-img-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-img-proj-"))
    cfg_dir = home / ".claude" / "fable-director"
    (cfg_dir / "budgets").mkdir(parents=True)
    config = {"default": "img", "providers": {"img": {
        "type": "image",
        "base_url": f"http://127.0.0.1:{port}/v1beta",
        "model": "img-model",
        "api_key": "test-key-123",
        "billing": "free",
    }}}
    (cfg_dir / "cross-family.json").write_text(json.dumps(config))
    (cfg_dir / "budgets" / f"{slug(proj)}.json").write_text(json.dumps({
        "status": "open",
        "declared_at": datetime.now(timezone.utc).isoformat(),
    }))
    return home, proj


def run(home, proj, args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, env=env, cwd=proj, timeout=60,
                          encoding="utf-8", errors="replace")


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home, proj = setup(srv.server_address[1])
    outpng = proj / "out.png"

    # I1 — happy path: bytes su --out.
    Handler.mode = "ok"
    r = run(home, proj, ["--spec", "a red circle", "--out", str(outpng)])
    check("I1 inlineData bytes written to --out",
          r.returncode == 0 and field(r.stdout, "CHECK") == "image"
          and outpng.read_bytes() == PNG_BYTES
          and "image/png" in field(r.stdout, "DETAIL"),
          r.stdout + r.stderr)

    # I6 — chiave nell'header, mai in query (finirebbe nei log).
    check("I6 API key in x-goog-api-key header, not in URL",
          seen["key_header"] == "test-key-123" and "key=" not in seen["path"]
          and seen["path"].endswith("/models/img-model:generateContent"),
          json.dumps(seen))

    # I2 — --out mancante.
    r = run(home, proj, ["--spec", "a red circle"])
    check("I2 missing --out is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "--out" in r.stdout, r.stdout + r.stderr)

    # I3 — risposta solo testo.
    Handler.mode = "textonly"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng)])
    check("I3 text-only response is an error citing the text",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "cannot draw that" in r.stdout, r.stdout + r.stderr)

    # I4 — 429 limit: 0 → messaggio billing, non quota generica.
    Handler.mode = "billing429"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng)])
    check("I4 429 limit:0 maps to billing-not-enabled detail",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "billing enabled" in r.stdout, r.stdout + r.stderr)

    # I5 — flag incompatibili.
    Handler.mode = "ok"
    r = run(home, proj, ["--spec", "x", "--out", str(outpng),
                         "--schema-json"])
    check("I5 --schema-json on image provider is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "image" in r.stdout, r.stdout + r.stderr)

    # I7 — doctor --ping su provider image: models-list, MAI una generazione.
    posts_before = seen["posts"]
    r = run(home, proj, ["--doctor", "--ping", "--paid-ok"])
    check("I7 doctor --ping on image provider uses models-list, no generation",
          "models-list OK" in r.stdout and "ping FAILED" not in r.stdout
          and seen["posts"] == posts_before,
          r.stdout + r.stderr)

    srv.shutdown()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

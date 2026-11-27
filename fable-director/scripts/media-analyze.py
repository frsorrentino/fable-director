#!/usr/bin/env python3
"""media-analyze.py — video/audio/image/PDF semantics on an external model.

  media-analyze.py --input FILE [--input FILE]... (--spec "..." | --spec-file F)
                   [--schema-json] [--out F] [--provider gemini-media]
                   [--model M] [--type SLUG] [--timeout N] [--paid-ok]

External MODEL route, so it carries the SAME guards as external-exec.py,
imported from that file (never copied): open pre-budget for this cwd
(`fd-telemetry.py budget-open ... --route external`), `--data-class
restricted` blocks, `--out` inside the budget's write perimeter, billing
fail-closed (`--paid-ok` only after explicit consent), `external_exec`
telemetry with the provider's usageMetadata token counts.

Provider entry `"type": "media"` in ~/.claude/fable-director/cross-family.json
(created by `cross-verify.py --init`; on an existing config
`external-exec.py --doctor` adds it and says so):

  "gemini-media": {"type": "media",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta",
                   "model": "gemini-3.6-flash", "api_key_env": "GEMINI_API_KEY",
                   "billing": "free", "inline_max_mb": 19}

Files up to inline_max_mb travel inline (base64 in generateContent); above
that they go through the Files API (resumable upload, then file_data.file_uri,
polled until ACTIVE). Key only in the x-goog-api-key header. Mime from the
extension (mp4, mov, webm, mkv, mp3, wav, m4a, aac, ogg, flac, png, jpg,
jpeg, webp, gif, pdf).

Audio is NEVER a model claim: the contract tells the model to describe only
what it sees and reads, and this script prints an `AUDIO:` line per input
from ffprobe (a model "transcribed" burnt-in subtitles on a file with no
audio track, 2026-09-08). Measured: 10 MB mp4, 55 s → 49 s, 3.7k tokens in,
1.2k out, 11 shots with correct timestamps and on-screen text.

Output (grep-able, same as external-exec.py):
  AUDIO: <file>: none|<codec> <ch>ch      (one per video/audio input)
  STATUS: ok|needs_context|unavailable|error
  PROVIDER: <provider> (<model>)
  CHECK: json-valid|raw|-
  OUTPUT: <path | ->
  DETAIL: <tokens in/out, transport, elapsed>
Exit: 0 ok · 1 unavailable/error · 2 needs_context. Stdlib only.
"""
import base64
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Guards live in external-exec.py — one implementation, imported, never copied.
_spec = importlib.util.spec_from_file_location(
    "external_exec", Path(__file__).with_name("external-exec.py"))
ext = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ext)

MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".m4v": "video/mp4", ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg", ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
    ".pdf": "application/pdf",
}

# Same executor contract as external-exec.py plus the media rule that the
# measured false positive made necessary.
MEDIA_SYSTEM = ext.EXEC_SYSTEM + (
    "\n<media_rules>Do not claim speech, spoken language, voice-over or "
    "music: describe only what you SEE and READ (shots, on-screen text, "
    "graphics, timestamps). Whether the file has an audio track is verified "
    "by the caller with ffprobe, never by you.</media_rules>"
)


def parse_args(argv):
    opts = {"--spec": None, "--spec-file": None, "--out": None,
            "--provider": None, "--type": None, "--timeout": None,
            "--model": None}
    flags = {"--schema-json": False, "--paid-ok": False}
    inputs = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        if a == "--input" and i + 1 < len(argv):
            inputs.append(argv[i + 1])
            i += 2
        elif a in flags:
            flags[a] = True
            i += 1
        elif a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
            i += 2
        else:
            sys.exit(f"unrecognized argument: {a}\n{__doc__}")
    return opts, inputs, flags


def ffprobe_audio(path):
    """'none' | '<codec> <ch>ch' | 'n/a (ffprobe missing)'. Never a model claim."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=codec_name,channels", "-of", "json", path],
            capture_output=True, text=True, timeout=60)
        streams = (json.loads(r.stdout or "{}").get("streams") or []) if r.returncode == 0 else None
    except FileNotFoundError:
        return "n/a (ffprobe not installed)"
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return "n/a (ffprobe failed)"
    if streams is None:
        return "n/a (ffprobe failed)"
    if not streams:
        return "none"
    return " + ".join(f"{s.get('codec_name', '?')} {s.get('channels', '?')}ch" for s in streams)


def api_call(method, url, api_key, body=None, headers=None, timeout=60, raw=False):
    """One HTTP call; returns (parsed_json_or_bytes, response_headers).
    HTTP errors are classified by external-exec's http_failure (never returns)."""
    h = {"x-goog-api-key": api_key}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        return (data if raw else json.loads(data.decode(errors="replace") or "{}")), resp.headers


def upload_file(prov, name, api_key, path, mime, timeout):
    """Files API resumable upload → (file_uri, file_name). Polls until ACTIVE."""
    base = prov["base_url"].rstrip("/")
    # https://generativelanguage.googleapis.com/v1beta → .../upload/v1beta/files
    if "/v1beta" in base:
        upload_url = base.replace("/v1beta", "/upload/v1beta", 1) + "/files"
    else:
        upload_url = base + "/upload/files"
    size = path.stat().st_size
    try:
        _, hdrs = api_call("POST", upload_url, api_key,
                           body=json.dumps({"file": {"display_name": path.name}}).encode(),
                           headers={"X-Goog-Upload-Protocol": "resumable",
                                    "X-Goog-Upload-Command": "start",
                                    "X-Goog-Upload-Header-Content-Length": str(size),
                                    "X-Goog-Upload-Header-Content-Type": mime,
                                    "Content-Type": "application/json"},
                           timeout=timeout)
        session_url = hdrs.get("X-Goog-Upload-URL") or hdrs.get("x-goog-upload-url")
        if not session_url:
            ext.unavailable(f"Files API did not return an upload URL for {path.name}")
        data, _ = api_call("POST", session_url, api_key, body=path.read_bytes(),
                           headers={"Content-Length": str(size),
                                    "X-Goog-Upload-Offset": "0",
                                    "X-Goog-Upload-Command": "upload, finalize"},
                           timeout=max(timeout, 600))
    except urllib.error.HTTPError as e:
        ext.http_failure(e, name, prov)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        ext.unavailable(f"network/timeout towards {name} (Files API upload): {e}")
    finfo = data.get("file") or {}
    uri, fname, state = finfo.get("uri"), finfo.get("name"), finfo.get("state")
    if not uri:
        ext.unavailable(f"Files API upload returned no uri: {json.dumps(data)[:200]}")
    # Video needs server-side processing before it can be referenced.
    waited = 0
    while state == "PROCESSING" and waited < timeout:
        time.sleep(3)
        waited += 3
        try:
            finfo, _ = api_call("GET", f"{base}/{fname}", api_key, timeout=30)
        except urllib.error.HTTPError as e:
            ext.http_failure(e, name, prov)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ext.unavailable(f"network/timeout towards {name} (Files API poll): {e}")
        state = finfo.get("state")
    if state != "ACTIVE":
        ext.unavailable(f"Files API file {fname} is {state or '?'} after {waited}s "
                        f"(not ACTIVE) — retry later or trim the file")
    return uri, fname


def main():
    opts, inputs, flags = parse_args(sys.argv[1:])
    spec_text = opts["--spec"]
    if opts["--spec-file"]:
        try:
            spec_text = Path(opts["--spec-file"]).read_text(errors="replace")
        except OSError as e:
            sys.exit(f"unreadable spec-file: {e}")
    if not spec_text or not spec_text.strip():
        sys.exit(__doc__)
    if not inputs:
        sys.exit("--input FILE is required (video, audio, image or pdf)\n" + __doc__)

    # Guards, in the same order as external-exec.py: budget first (no network
    # before the gate), perimeter, config, provider, billing, key.
    budget = ext.require_open_budget()
    if opts["--out"]:
        ext.check_out_perimeter(budget, opts["--out"])

    if not ext.CONFIG_PATH.is_file():
        ext.unavailable(f"config missing ({ext.CONFIG_PATH}): cross-verify.py --init")
    try:
        cfg = json.loads(ext.CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        ext.unavailable(f"config unreadable: {e}")
    providers = cfg.get("providers") or {}
    name = opts["--provider"]
    if not name:
        name = next((n for n, p in providers.items()
                     if isinstance(p, dict) and p.get("type") == "media"), None)
        if not name:
            ext.unavailable("no provider with \"type\": \"media\" in cross-family.json — "
                            "run external-exec.py --doctor (it adds gemini-media and says so)")
    prov = providers.get(name)
    if not prov:
        ext.unavailable(f"provider '{name}' not defined in config")
    if prov.get("type") != "media":
        ext.out("error", name, prov.get("model", "?"), detail=(
            f"provider '{name}' has type '{prov.get('type', 'http')}', not 'media' — "
            f"text providers go through external-exec.py"))
        sys.exit(1)
    if ext.billing_of(prov) != "free" and not flags["--paid-ok"]:
        ext.log_exec({"provider": name, "model": prov.get("model", "?"),
                      "billing": "paid", "ok": False, "check": "paid-refused",
                      "kind": "media"})
        ext.unavailable(
            f"provider '{name}' is billed"
            + (f" ({prov['cost_note']})" if prov.get("cost_note") else "")
            + " — requires explicit user consent in this conversation; "
              "re-run with --paid-ok ONLY after the user agreed")
    # --model: explicit fallback when the default 503s from demand spikes
    # (e.g. --model gemini-2.5-flash); the PROVIDER line and the telemetry
    # carry the EFFECTIVE model.
    if opts["--model"]:
        prov = {**prov, "model": opts["--model"]}
    api_key = prov.get("api_key") or os.environ.get(prov.get("api_key_env", ""), "")
    if not api_key:
        ext.unavailable(f"API key missing for '{name}' "
                        f"(export {prov.get('api_key_env')}=... or api_key in config)")

    # Inputs: exist, known mime, ffprobe audio fact printed before any call.
    timeout = int(opts["--timeout"] or prov.get("timeout") or 300)
    inline_max = float(prov.get("inline_max_mb") or 19) * 1024 * 1024
    parts = []
    transports = []
    total_bytes = 0
    for f in inputs:
        p = Path(f)
        if not p.is_file():
            ext.unavailable(f"input not found: {f}")
        mime = MIME.get(p.suffix.lower())
        if not mime:
            ext.out("error", name, prov.get("model", "?"), detail=(
                f"unsupported extension '{p.suffix}' for {p.name} — known: "
                + ", ".join(sorted(k.lstrip('.') for k in MIME))))
            sys.exit(1)
        size = p.stat().st_size
        total_bytes += size
        if mime.startswith(("video/", "audio/")):
            print(f"AUDIO: {p.name}: {ffprobe_audio(str(p))}", flush=True)
        if size <= inline_max:
            parts.append({"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(p.read_bytes()).decode()}})
            transports.append("inline")
        else:
            print(f"NOTE: {p.name} is {size / 1048576:.1f} MB > inline_max_mb "
                  f"{inline_max / 1048576:.0f} — uploading via Files API "
                  f"(kept 48 h on Google's side)", flush=True)
            uri, _ = upload_file(prov, name, api_key, p, mime, timeout)
            parts.append({"file_data": {"mime_type": mime, "file_uri": uri}})
            transports.append("files-api")
    parts.append({"text": f"TASK SPEC:\n{spec_text}\n"
                  + ("\nOUTPUT FORMAT: strict JSON only — a single valid JSON "
                     "document, no code fences, no trailing text.\n"
                     if flags["--schema-json"] else "")})

    gen_cfg = {"temperature": 0}
    if flags["--schema-json"]:
        gen_cfg["response_mime_type"] = "application/json"
    body = json.dumps({
        "system_instruction": {"parts": [{"text": MEDIA_SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }).encode()
    url = prov["base_url"].rstrip("/") + f"/models/{prov['model']}:generateContent"

    base_log = {"provider": name, "model": prov["model"], "kind": "media",
                "billing": ext.billing_of(prov), "type": opts.get("--type"),
                "inputs": len(inputs), "bytes_in": total_bytes,
                "transport": "+".join(sorted(set(transports))),
                "chars_in": len(spec_text)}
    try:
        ext.ACTIVE_PATH.write_text(json.dumps(
            {"provider": name, "pid": os.getpid(),
             "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    except OSError:
        pass
    t0 = time.time()
    try:
        try:
            data, _ = api_call("POST", url, api_key, body=body,
                               headers={"Content-Type": "application/json"},
                               timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 and "limit: 0" in detail:
                ext.unavailable(f"model '{prov['model']}' has free-tier limit 0 on this "
                                f"project — billing not enabled, not a transient quota "
                                f"error [{name}]")
            ext.http_failure(e, name, prov, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ext.unavailable(f"network/timeout towards {name}: {e}")
    finally:
        try:
            ext.ACTIVE_PATH.unlink()
        except OSError:
            pass
    elapsed = time.time() - t0

    usage = data.get("usageMetadata") or {}
    tok_in = usage.get("promptTokenCount")
    tok_out = usage.get("candidatesTokenCount")
    base_log.update({"tokens_in": tok_in, "tokens_out": tok_out,
                     "elapsed": round(elapsed, 1)})
    try:
        cand = data["candidates"][0]
        content = "".join(str(p.get("text", "")) for p in cand["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError):
        content = ""
        cand = (data.get("candidates") or [{}])[0] if isinstance(data.get("candidates"), list) else {}
    if not content:
        reason = (cand or {}).get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason")
        ext.out("error", name, prov["model"], detail=(
            "empty response from provider" + (f" (finishReason {reason})" if reason else "")))
        ext.log_exec({**base_log, "ok": False, "check": "empty"})
        sys.exit(1)
    if content.startswith("NEEDS_CONTEXT"):
        ext.out("needs_context", name, prov["model"], detail=content[:300])
        ext.log_exec({**base_log, "ok": False, "check": "needs_context"})
        sys.exit(2)

    check = "raw"
    if flags["--schema-json"]:
        candidate = content.strip().strip("`").strip()
        if candidate[:4].lower() == "json":
            candidate = candidate[4:].strip()
        try:
            json.loads(candidate)
            content, check = candidate, "json-valid"
        except json.JSONDecodeError as e:
            ext.out("error", name, prov["model"], "json-invalid", "-",
                    f"output is not valid JSON ({e}) — do NOT hand downstream, "
                    f"retry or read the contact sheet on the Claude route")
            ext.log_exec({**base_log, "ok": False, "check": "json-invalid"})
            sys.exit(1)

    dest = "-"
    if opts["--out"]:
        try:
            Path(opts["--out"]).write_text(content, encoding="utf-8")
            dest = opts["--out"]
        except OSError as e:
            ext.out("error", name, prov["model"], check, "-", f"--out write failed: {e}")
            sys.exit(1)
    ext.out("ok", name, prov["model"], check, dest,
            f"{tok_in if tok_in is not None else '?'} tokens in / "
            f"{tok_out if tok_out is not None else '?'} out, "
            f"{total_bytes / 1048576:.1f} MB {'+'.join(sorted(set(transports)))}, "
            f"{elapsed:.0f} s, {len(content)} char")
    if dest == "-":
        print("---")
        print(content)
    ext.log_exec({**base_log, "ok": True, "check": check, "chars_out": len(content)})
    sys.exit(0)


if __name__ == "__main__":
    main()

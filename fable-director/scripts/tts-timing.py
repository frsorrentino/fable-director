#!/usr/bin/env python3
"""tts-timing.py — measure how long a voice-over script really takes to say.

  tts-timing.py (--text "..." | --file SCRIPT) [--voice it-IT-DiegoNeural]
                [--rate +0%] [--out DIR] [--venv PATH]

Why: a client's storyboard declares slots ("0-4 s: attack line"); a neutral
TTS read of the same words is a cheap lower bound on the real speaker's time
(measured 2026-09-08: a 4 s slot needed 5.9 s of speech). edge-tts is for
TIMING and animatics only — the production voice stays human.

--file format: blocks separated by blank lines; an optional first line
starting with '#' is the label, and a slot in the label — "(0-4s)", "(4–9 s)"
or "(5s)" — is parsed as the declared duration:

    # 1 Attacco (0-4s)
    Nel Lazio sta per nascere una rete di dieci centri.

    # 2 Scarsità (4-9s)
    Un solo centro per ogni zona selezionata.

For every block: one mp3 (edge-tts, Microsoft neural voices, needs network)
and its duration via ffprobe. Then a table  label | slot | spoken | delta
plus the totals. Voice fallback it-IT-ElsaNeural if the requested voice is
rejected (said explicitly, never silently). No network → STATUS: unavailable.

Output (grep-able):
  STATUS: ok|unavailable|error
  OUTPUT: <dir with the mp3 files and timing.json>
  DETAIL: <n blocks, spoken total vs declared total, blocks over slot>
Exit 0 only on STATUS ok.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VENV_BIN = Path.home() / ".claude" / "fable-director" / "tools" / "venv" / "bin"
FALLBACK_VOICE = "it-IT-ElsaNeural"
NETWORK_MARKERS = ("getaddrinfo", "name or service", "temporary failure",
                   "network is unreachable", "connection refused",
                   "connection reset", "timed out", "timeout", "ssl",
                   "cannot connect", "clientconnectorerror", "403", "no route")
SLOT_RE = re.compile(
    r"\(\s*(\d+(?:[.,]\d+)?)\s*(?:[-–—]\s*(\d+(?:[.,]\d+)?))?\s*s(?:ec)?\s*\)",
    re.IGNORECASE)


def out(status, output="-", detail="-"):
    print(f"STATUS: {status}")
    print(f"OUTPUT: {output}")
    print(f"DETAIL: {detail}")


def die(detail, status="error"):
    out(status, "-", detail)
    sys.exit(1)


def parse_args(argv):
    opts = {"--text": None, "--file": None, "--voice": "it-IT-DiegoNeural",
            "--rate": "+0%", "--out": None, "--venv": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        if a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
            i += 2
        else:
            die(f"unrecognized argument: {a}")
    if not opts["--text"] and not opts["--file"]:
        die("usage: tts-timing.py (--text \"...\" | --file SCRIPT) "
            "[--voice V] [--rate +0%] [--out DIR]")
    return opts


def parse_blocks(text):
    """[{label, slot, text}] — slot in seconds or None."""
    blocks = []
    for raw in re.split(r"\n\s*\n", text.strip()):
        lines = [l.rstrip() for l in raw.strip().splitlines() if l.strip()]
        if not lines:
            continue
        label, slot = None, None
        if lines[0].lstrip().startswith("#"):
            label = lines[0].lstrip("# ").strip()
            lines = lines[1:]
            m = SLOT_RE.search(label)
            if m:
                a = float(m.group(1).replace(",", "."))
                b = m.group(2)
                slot = (float(b.replace(",", ".")) - a) if b else a
                if slot < 0:
                    slot = None
        body = " ".join(l.strip() for l in lines).strip()
        if not body:
            continue
        blocks.append({"label": label or f"block {len(blocks) + 1}",
                       "slot": slot, "text": body})
    return blocks


def find_edge_tts(opts):
    cands = []
    if opts["--venv"]:
        cands.append(Path(opts["--venv"]) / "bin" / "edge-tts")
    cands += [VENV_BIN / "edge-tts"]
    w = shutil.which("edge-tts")
    if w:
        cands.append(Path(w))
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    die(f"edge-tts not found (looked in {VENV_BIN}, PATH) — install: python3 -m "
        f"venv {VENV_BIN.parent} && {VENV_BIN / 'pip'} install edge-tts "
        f"faster-whisper", status="unavailable")


def ffprobe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1",
                            str(path)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        die("ffprobe not installed (apt install ffmpeg)")
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


def synth(edge, voice, rate, text, dest, timeout=60):
    """(ok, stderr_tail). Never raises on provider errors."""
    try:
        r = subprocess.run([edge, "--voice", voice, "--rate", rate,
                            "--text", text, "--write-media", str(dest)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if r.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        # Last non-empty line: the exception itself, not the traceback path.
        lines = [l for l in (r.stderr or r.stdout).strip().splitlines() if l.strip()]
        return False, (lines[-1].strip()[:400] if lines else f"exit {r.returncode}")
    return True, ""


def main():
    opts = parse_args(sys.argv[1:])
    if opts["--file"]:
        try:
            script = Path(opts["--file"]).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            die(f"cannot read --file: {e}")
    else:
        script = opts["--text"]
    blocks = parse_blocks(script)
    if not blocks:
        die("no text blocks found (blocks are separated by blank lines)")

    edge = find_edge_tts(opts)
    outdir = Path(opts["--out"] or f"tts-timing-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        die(f"cannot create --out dir: {e}")

    voice = opts["--voice"]
    fell_back = False
    rows = []
    t0 = time.time()
    for i, b in enumerate(blocks, 1):
        dest = outdir / f"{i:02d}.mp3"
        ok, err = synth(edge, voice, opts["--rate"], b["text"], dest)
        if not ok:
            low = err.lower()
            if any(m in low for m in NETWORK_MARKERS):
                die(f"edge-tts cannot reach Microsoft (block {i}: {err[:160]}) — "
                    f"no network, timing not measured", status="unavailable")
            if voice != FALLBACK_VOICE:
                print(f"NOTE: voice '{voice}' rejected ({err[:120]}) — retrying "
                      f"with {FALLBACK_VOICE}", flush=True)
                voice, fell_back = FALLBACK_VOICE, True
                ok, err = synth(edge, voice, opts["--rate"], b["text"], dest)
            if not ok:
                die(f"edge-tts failed on block {i} with {voice}: {err[:200]}")
        dur = ffprobe_duration(dest)
        if dur is None:
            die(f"ffprobe cannot read the duration of {dest}")
        rows.append({**b, "file": str(dest), "spoken": round(dur, 2),
                     "delta": (round(dur - b["slot"], 2) if b["slot"] is not None else None)})
    elapsed = time.time() - t0

    # Table — the deliverable a human reads.
    w = max(len(r["label"]) for r in rows)
    w = min(max(w, 5), 40)
    print(f"voice: {voice} rate {opts['--rate']}" + (" (fallback)" if fell_back else ""))
    print(f"{'label':<{w}} | {'slot':>7} | {'spoken':>7} | {'delta':>7} | text")
    print(f"{'-' * w}-+-{'-' * 7}-+-{'-' * 7}-+-{'-' * 7}-+-----")
    tot_slot = tot_spoken = 0.0
    over = 0
    for r in rows:
        slot = f"{r['slot']:.1f} s" if r["slot"] is not None else "-"
        delta = (f"{r['delta']:+.1f} s" if r["delta"] is not None else "-")
        if r["delta"] is not None and r["delta"] > 0:
            over += 1
        tot_spoken += r["spoken"]
        tot_slot += r["slot"] or 0
        snippet = r["text"][:60] + ("…" if len(r["text"]) > 60 else "")
        print(f"{r['label'][:w]:<{w}} | {slot:>7} | {r['spoken']:>5.1f} s | {delta:>7} | {snippet}")
    has_slots = any(r["slot"] is not None for r in rows)
    tot_delta = f"{tot_spoken - tot_slot:+.1f} s" if has_slots else "-"
    print(f"{'TOTAL':<{w}} | {(f'{tot_slot:.1f} s' if has_slots else '-'):>7} | "
          f"{tot_spoken:>5.1f} s | {tot_delta:>7} |")

    try:
        (outdir / "timing.json").write_text(json.dumps(
            {"voice": voice, "rate": opts["--rate"], "fallback": fell_back,
             "blocks": rows, "total_slot": (round(tot_slot, 2) if has_slots else None),
             "total_spoken": round(tot_spoken, 2), "elapsed": round(elapsed, 1)},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        die(f"cannot write timing.json: {e}")

    detail = (f"{len(rows)} blocks, spoken {tot_spoken:.1f} s"
              + (f" vs declared {tot_slot:.1f} s ({tot_spoken - tot_slot:+.1f} s), "
                 f"{over} block(s) over slot" if has_slots else " (no slots declared)")
              + f", {elapsed:.0f} s, {voice}" + (" (fallback)" if fell_back else ""))
    out("ok", str(outdir), detail)
    sys.exit(0)


if __name__ == "__main__":
    main()

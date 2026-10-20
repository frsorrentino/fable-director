#!/usr/bin/env python3
"""Verifica deterministica — elenco delle skill rimandato (Claude Code >= 2.1.304).

Dalla 2.1.304 l'elenco delle skill torna dopo ogni compattazione; anche una
ripresa lo rimanda. Con ~175 skill sono ~30k caratteri (misurato il 20/10/2026):
un costo fisso per evento. La telemetria conta gli elenchi interi oltre il primo
e quanti arrivano subito dopo una compattazione; le aggiunte di una skill sola
(`/reload-skills`, una skill nuova) non contano.

  S1 elenco iniziale da solo: nessun conteggio
  S2 compattazione + elenco prima del turno successivo: n 1, after_compact 1
  S3 elenco dopo una ripresa (turno main fra compattazione ed elenco): n 1, after_compact 0
  S4 aggiunta di una skill sola: ignorata
  S5 session-summary + report: riga «skill list sent again»

Usage: python3 tests/skill-list-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdt)


def turn(ts, mid, cr=20000):
    return {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "role": "assistant", "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 5, "output_tokens": 50, "cache_read_input_tokens": cr,
                  "cache_creation_input_tokens": 500}}}


def prompt(ts, text="vai"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def listing(ts, n=175, chars=30000):
    # Forma reale (2.1.304): type, content, skillCount, isInitial, names, digests
    return {"type": "attachment", "timestamp": ts, "attachment": {
        "type": "skill_listing", "content": "x" * chars, "skillCount": n,
        "isInitial": n > 1, "names": ["s"] * n}}


def boundary(ts):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts,
            "compactMetadata": {"trigger": "auto", "preTokens": 300000, "postTokens": 15000}}


def write(recs):
    p = Path(tempfile.mkdtemp(prefix="fd-skl-")) / "t.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return p


def stats(recs):
    return fdt.sum_transcript(write(recs))[-1]


head = [prompt("2030-01-01T10:00:00Z"), listing("2030-01-01T10:00:01Z"),
        turn("2030-01-01T10:00:10Z", "m1")]

s1 = stats(head)
check("S1 elenco iniziale da solo: nessun conteggio",
      "skill_list_resent" not in s1, f"{s1.get('skill_list_resent')}")

s2 = stats(head + [boundary("2030-01-01T10:05:00Z"), prompt("2030-01-01T10:05:01Z"),
                   listing("2030-01-01T10:05:02Z", chars=29000), turn("2030-01-01T10:05:10Z", "m2")])
check("S2 elenco subito dopo la compattazione: n 1, after_compact 1, caratteri",
      s2.get("skill_list_resent") == {"n": 1, "chars": 29000, "after_compact": 1},
      f"{s2.get('skill_list_resent')}")

s3 = stats(head + [boundary("2030-01-01T10:05:00Z"), turn("2030-01-01T10:05:10Z", "m2"),
                   prompt("2030-01-01T12:00:00Z"), listing("2030-01-01T12:00:01Z"),
                   turn("2030-01-01T12:00:10Z", "m3")])
check("S3 elenco di una ripresa dopo turni: n 1, after_compact 0",
      s3.get("skill_list_resent") == {"n": 1, "chars": 30000, "after_compact": 0},
      f"{s3.get('skill_list_resent')}")

s4 = stats(head + [listing("2030-01-01T10:06:00Z", n=1, chars=64)])
check("S4 aggiunta di una skill sola ignorata",
      "skill_list_resent" not in s4, f"{s4.get('skill_list_resent')}")

home = Path(tempfile.mkdtemp(prefix="fd-skl-home-"))
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID"):
    env.pop(k, None)
t = write(head + [boundary("2030-01-01T10:05:00Z"), listing("2030-01-01T10:05:02Z"),
                  turn("2030-01-01T10:05:10Z", "m2")])
subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "session-summary"],
               input=json.dumps({"cwd": str(home), "transcript_path": str(t),
                                 "session_id": "skl-test"}),
               capture_output=True, text=True, env=env, timeout=60)
r = subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "report", "--days", "36500"],
                   capture_output=True, text=True, env=env, timeout=60, cwd=str(home))
line = next((x for x in r.stdout.splitlines() if x.startswith("skill list sent again")), "")
check("S5 report: riga con conteggio e compattazioni",
      "1 time(s), 1 after a compaction" in line, f"line={line!r} err={r.stderr[-200:]}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

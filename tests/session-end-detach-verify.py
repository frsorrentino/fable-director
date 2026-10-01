"""Verifica deterministica — session-summary staccata dall'hook SessionEnd.

Contesto: su Claude Code 2.1.287 tutti gli hook di session.end condividono un
solo limite di 1,5 s; con `claude -p` l'hook `session-summary` risultava
«cancelled» a metà calcolo (riprodotto il 2026-10-01 nel repo marketplace).
Con `scripts/detach.sh` l'hook salva lo stdin, lancia il calcolo in un
processo staccato (setsid) e rientra subito, senza aspettare l'avvio di Python.

  D1 l'hook via detach.sh rientra in meno di 1,5 s anche se il calcolo è lento
  D2 il processo staccato scrive comunque la session_summary nel DB
  D3 senza detach.sh il comportamento resta sincrono (i test esistenti)
  D4 hooks.json passa il SessionEnd da detach.sh

Usage: python3 tests/session-end-detach-verify.py   (exit 0 = all green)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "fable-director"
SCRIPT = PLUGIN / "scripts" / "fd-telemetry.py"

passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def make_transcript(n_lines):
    d = Path(tempfile.mkdtemp(prefix="fd-detach-tr-"))
    p = d / "sess.jsonl"
    with p.open("w") as f:
        for i in range(n_lines):
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2030-01-01T10:00:%02dZ" % (i % 60),
                "message": {"role": "assistant", "id": f"m{i}", "model": "claude-opus-5-5",
                            "usage": {"input_tokens": 10, "output_tokens": 5,
                                      "cache_read_input_tokens": 100,
                                      "cache_creation_input_tokens": 0},
                            "content": [{"type": "text", "text": "x" * 200}]}}) + "\n")
    return p


def summaries(home):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return 0
    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT count(*) FROM events WHERE event='session_summary'").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0  # il figlio sta ancora creando lo schema
    con.close()
    return n


def run(args, home, stdin, detach=False):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    cmd = (["bash", str(PLUGIN / "scripts" / "detach.sh"), str(SCRIPT)] if detach
           else [sys.executable, str(SCRIPT)]) + args
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True,
                       text=True, env=env, input=stdin, timeout=120)
    return r, time.monotonic() - t0


# Transcript grande: il calcolo sincrono deve costare più del limite.
tr = make_transcript(60000)
proj = tempfile.mkdtemp(prefix="fd-detach-proj-")
stdin = json.dumps({"cwd": proj, "transcript_path": str(tr), "session_id": "detach-sess"})

home_sync = Path(tempfile.mkdtemp(prefix="fd-detach-home-sync-"))
r3, t_sync = run(["session-summary"], home_sync, stdin)
check("D3 senza detach.sh: sincrono, summary scritta al rientro",
      r3.returncode == 0 and summaries(home_sync) == 1,
      f"rc={r3.returncode} n={summaries(home_sync)} err={r3.stderr[:160]}")

home = Path(tempfile.mkdtemp(prefix="fd-detach-home-"))
r1, t_det = run(["session-summary"], home, stdin, detach=True)
check("D1 detach.sh rientra entro 0,5 s",
      r1.returncode == 0 and t_det < 0.5,
      f"rc={r1.returncode} t={t_det:.2f}s (sincrono {t_sync:.2f}s) err={r1.stderr[:160]}")

deadline = time.monotonic() + 60
while summaries(home) == 0 and time.monotonic() < deadline:
    time.sleep(0.2)
check("D2 il figlio staccato scrive la session_summary",
      summaries(home) == 1, f"n={summaries(home)} dopo {t_sync:.2f}s di calcolo sincrono")

hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
cmds = [h["command"] for g in hooks["hooks"]["SessionEnd"] for h in g["hooks"]]
check("D4 hooks.json: SessionEnd via detach.sh",
      any("detach.sh" in c and "session-summary" in c for c in cmds), str(cmds))

print(f"\n{len(passed)} passed, {len(failed)} failed  (sincrono {t_sync:.2f}s, detach {t_det:.2f}s)")
sys.exit(1 if failed else 0)

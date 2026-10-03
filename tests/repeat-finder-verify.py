#!/usr/bin/env python3
"""Verifica deterministica — istruzioni ripetute nei transcript (1.55.0).

  R1 la stessa istruzione in 3 sessioni → un gruppo proposto
  R2 la stessa in 1 sessione ripetuta 5 volte → nessun gruppo
  R3 conferme brevi, iniezioni <…>, record isMeta, messaggi «Da master»
     inoltrati → non fanno numero (l'inoltro conta come una sessione sola)
  R3b sessioni con un solo turno umano (claude -p, benchmark) → escluse
  R3c conversazione ripresa in 3 file (stessi uuid) → una sessione sola
  R4 parafrasi (Jaccard ≥ 0,5) → stesso gruppo; frasi diverse → gruppi diversi
  R5 testo già in un CLAUDE.md → elenco «già scritto, ignorato», non proposto
  R6 claude-master assente → nessuna chiamata, riga [candidata] nel playbook
  R7 claude-master presente (finto sul PATH) → `task constraint add --project P`
  R8 nessuna rete, nessun processo modello avviato
  R10 /fable-director:status mostra la riga quando ci sono proposte nuove
  R9 pending conta solo le proposte non accettate né scartate; telemetria
     senza testo

Usage: python3 tests/repeat-finder-verify.py   (exit 0 = all green)
"""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RF = str(HERE.parent / "fable-director" / "scripts" / "repeat-finder.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


NOW = datetime.now(timezone.utc)
RULE = "usa sempre bash py.sh per lanciare gli script python del plugin"
PARA = "per lanciare gli script python del plugin usa sempre bash py.sh"
OTHER = "non fare mai push sul ramo main senza il mio ok esplicito prima"
KNOWN = "rispondi sempre in italiano anche quando il codice e in inglese"


_uid = [0]


def rec(text, ts, cwd, meta=False, uuid=None):
    _uid[0] += 1
    return {"uuid": uuid or f"u{_uid[0]}", "type": "user", "isMeta": meta, "timestamp": ts.isoformat(),
            "cwd": cwd, "message": {"role": "user", "content": text}}


def world(sessions, claude_md=None, fake_cm=False):
    """HOME sintetica: sessions = [(cwd, [testi])] — una sessione per voce."""
    home = Path(tempfile.mkdtemp(prefix="fd-rep-home-"))
    proj = home / ".claude" / "projects"
    for i, (cwd, texts) in enumerate(sessions):
        d = proj / ("-" + cwd.strip("/").replace("/", "-"))
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        # Un turno di contorno: le sessioni da un turno solo sono prompt da script.
        for j, t in enumerate(list(texts) + ["ok vai avanti"]):
            meta = isinstance(t, tuple)
            body = t[0] if meta else t
            lines.append(json.dumps(rec(body, NOW - timedelta(hours=i + 1, minutes=j), cwd, meta)))
        (d / f"0000000{i}-aaaa-bbbb-cccc-dddddddddddd.jsonl").write_text("\n".join(lines) + "\n")
    if claude_md:
        (home / ".claude" / "CLAUDE.md").write_text(claude_md)
    bindir = home / "bin"
    bindir.mkdir()
    # Trappole: un modello o la rete avviati lascerebbero un file.
    for trap in ("claude", "curl", "wget", "ollama"):
        p = bindir / trap
        p.write_text(f"#!/bin/sh\necho {trap} >> '{home}/TRAPPED'\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    if fake_cm:
        p = bindir / "claude-master"
        p.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{home}/CM_ARGS'\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        db = home / ".config" / "claude-master"
        db.mkdir(parents=True)
        sqlite3.connect(db / "tasks.db").close()
    return home


def run(home, *args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               PATH=f"{home / 'bin'}:/usr/bin:/bin",
               http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9")
    env.pop("CLAUDE_CONFIG_DIR", None)
    r = subprocess.run([sys.executable, RF] + list(args), capture_output=True,
                       text=True, env=env, timeout=60)
    return r


def scan(home):
    r = run(home, "scan", "--days", "7")
    out = home / ".claude" / "fable-director" / "repeats.json"
    data = json.loads(out.read_text()) if out.is_file() else {}
    return r, data


# R1 — stessa istruzione in 3 sessioni di un progetto
h1 = world([("/w/alpha", [RULE]), ("/w/alpha", [RULE + "."]), ("/w/alpha", ["ok", RULE])])
r, d = scan(h1)
props = d.get("proposals", [])
check("R1 tre sessioni → un gruppo", r.returncode == 0 and len(props) == 1
      and props[0]["sessions"] == 3, f"rc={r.returncode} {r.stderr[-300:]} {props}")
check("R1 ambito progetto unico → --project", props and props[0].get("scope") == {"project": "/w/alpha"},
      str(props[:1]))

# R2 — 5 volte nella stessa sessione
h2 = world([("/w/alpha", [RULE] * 5)])
r, d = scan(h2)
check("R2 una sessione sola → nessun gruppo", r.returncode == 0 and not d.get("proposals"),
      str(d.get("proposals")))

# R3 — rumore escluso: conferme, iniezioni, isMeta, inoltri da master
fw = "Da master. Franz (11:43): " + RULE
h3 = world([("/w/a", ["ok procedi", "<system-reminder>" + RULE + "</system-reminder>"]),
            ("/w/b", [(RULE,)]),            # isMeta: messaggio fra sessioni
            ("/w/c", [fw]), ("/w/d", [fw]), ("/w/e", [fw]),
            ("/w/f", ["sì", "ok", "procedi pure"])])
r, d = scan(h3)
check("R3 iniezioni, isMeta, conferme e inoltri non fanno 3 sessioni",
      r.returncode == 0 and not d.get("proposals"), str(d.get("proposals")))
h3b = world([("/w/a", [RULE]), ("/w/b", [RULE]), ("/w/c", [fw]), ("/w/d", [fw])])
r, d = scan(h3b)
p = d.get("proposals", [])
check("R3 l'inoltro conta una sessione sola: 2 vere + inoltri = 3",
      len(p) == 1 and p[0]["sessions"] == 3, str(p))

# R3b — prompt da script: una sessione = un turno solo
h3c = world([])
for i in range(4):
    d = h3c / ".claude" / "projects" / "-tmp-bench"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"b{i}.jsonl").write_text(json.dumps(rec(RULE, NOW - timedelta(hours=i + 1), "/tmp/bench")) + "\n")
r, d = scan(h3c)
check("R3b sessioni da un turno solo → nessun gruppo", r.returncode == 0 and not d.get("proposals"),
      str(d.get("proposals")))

# R3c — ripresa: gli stessi record (uuid) ricopiati in 3 file
h3r = world([])
d = h3r / ".claude" / "projects" / "-w-alpha"
d.mkdir(parents=True, exist_ok=True)
base = [rec(RULE, NOW - timedelta(hours=5), "/w/alpha", uuid="same-1"),
        rec("ok vai avanti", NOW - timedelta(hours=5), "/w/alpha", uuid="same-2")]
for i in range(3):
    (d / f"r{i}.jsonl").write_text("\n".join(json.dumps(x) for x in base) + "\n")
r, d = scan(h3r)
check("R3c ripresa con gli stessi uuid → nessun gruppo", r.returncode == 0 and not d.get("proposals"),
      str(d.get("proposals")))

# R4 — parafrasi nello stesso gruppo, frasi diverse in gruppi diversi
h4 = world([("/w/a", [RULE, OTHER]), ("/w/b", [PARA, OTHER]), ("/w/c", [RULE, OTHER])])
r, d = scan(h4)
p = d.get("proposals", [])
texts = sorted(x["text"] for x in p)
check("R4 parafrasi e regola diversa → 2 gruppi da 3 sessioni",
      len(p) == 2 and all(x["sessions"] == 3 for x in p), str(p))
check("R4 ambito multi-progetto → --topic", all("topic" in x.get("scope", {}) for x in p), str(p))

# R5 — già scritto in CLAUDE.md
h5 = world([("/w/a", [KNOWN]), ("/w/b", [KNOWN]), ("/w/c", [KNOWN])],
           claude_md="# Regole\n\n- Rispondi sempre in italiano, anche quando il codice è in inglese.\n")
r, d = scan(h5)
kn = d.get("known", [])
check("R5 già in CLAUDE.md → non proposto", not d.get("proposals"), str(d.get("proposals")))
check("R5 elenco «già scritto, ignorato N volte»", len(kn) == 1 and kn[0]["sessions"] == 3
      and "CLAUDE.md" in kn[0].get("source", ""), str(kn))
check("R5 stampa la sezione ignorati", "già scritto" in r.stdout.lower(), r.stdout[-400:])

# R6 — accept senza claude-master → playbook
r, d = scan(h1)
pid = d["proposals"][0]["id"]
r = run(h1, "accept", pid)
pb = h1 / ".claude" / "delega-playbook.md"
txt = pb.read_text() if pb.is_file() else ""
check("R6 senza claude-master → riga [candidata] nel playbook",
      r.returncode == 0 and "[candidata" in txt and "py.sh" in txt and "(uses:0 ok:0 ko:0)" in txt,
      f"rc={r.returncode} {r.stdout[-200:]} {r.stderr[-200:]} | {txt[-300:]}")
check("R6 nessuna chiamata a claude-master", not (h1 / "CM_ARGS").exists())

# R7 — accept con claude-master finto
h7 = world([("/w/alpha", [RULE]), ("/w/alpha", [RULE]), ("/w/alpha", [RULE])], fake_cm=True)
r, d = scan(h7)
pid = d["proposals"][0]["id"]
r = run(h7, "accept", pid)
args = (h7 / "CM_ARGS").read_text().splitlines() if (h7 / "CM_ARGS").exists() else []
check("R7 claude-master presente → task constraint add --project P TESTO",
      args[:5] == ["task", "constraint", "add", "--project", "/w/alpha"] and len(args) == 6
      and "py.sh" in args[5], f"rc={r.returncode} {r.stderr[-200:]} {args}")
check("R7 playbook non toccato", not (h7 / ".claude" / "delega-playbook.md").exists())

# R8 — nessuna rete, nessun modello
trapped = [h for h in (h1, h2, h3, h3b, h3c, h4, h5, h7) if (h / "TRAPPED").exists()]
src = Path(RF).read_text()
check("R8 nessun binario modello/rete avviato", not trapped, str(trapped))
check("R8 nessun import di rete nel rilevatore",
      not any(m in src for m in ("import socket", "urllib.request", "import requests", "http.client")))

# R9 — pending e telemetria
r = run(h4, "pending")
check("R9 pending = 2 proposte nuove", r.returncode == 0 and r.stdout.strip() == "2", r.stdout + r.stderr)
d = json.loads((h4 / ".claude" / "fable-director" / "repeats.json").read_text())
run(h4, "dismiss", d["proposals"][0]["id"])
r = run(h4, "pending")
check("R9 dopo dismiss pending = 1", r.stdout.strip() == "1", r.stdout + r.stderr)
db = h4 / ".claude" / "fable-director" / "telemetry.db"
rows = []
if db.is_file():
    con = sqlite3.connect(db)
    rows = con.execute("SELECT event, payload FROM events WHERE event LIKE 'repeat_%'").fetchall()
check("R9 evento repeat_proposed registrato", any(e == "repeat_proposed" for e, _ in rows), str(rows))
check("R9 telemetria senza il testo delle frasi",
      rows and not any("py.sh" in (p or "") or "push" in (p or "") for _, p in rows), str(rows))

# R10 — riga nello status (h4: 1 proposta ancora aperta dopo il dismiss)
STATUS = str(HERE.parent / "fable-director" / "scripts" / "fd-status.py")
env = dict(os.environ, HOME=str(h4), USERPROFILE=str(h4), PATH=f"{h4 / 'bin'}:/usr/bin:/bin")
env.pop("CLAUDE_CONFIG_DIR", None)
r = subprocess.run([sys.executable, STATUS], capture_output=True, text=True, env=env, timeout=60)
check("R10 status: «1 repeated instruction»", "1 repeated instruction this week" in r.stdout,
      r.stdout[-400:] + r.stderr[-200:])
run(h4, "dismiss", d["proposals"][1]["id"])
r = subprocess.run([sys.executable, STATUS], capture_output=True, text=True, env=env, timeout=60)
check("R10 status: nessuna riga senza proposte nuove", "repeated instruction" not in r.stdout,
      r.stdout[-400:])

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)

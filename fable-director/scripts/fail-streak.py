#!/usr/bin/env python3
"""Hook PostToolUse (Bash): rileva il GRINDING e lo mette a verbale.

Il buco che chiude: la rule-of-3 della skill dice di diagnosticare il TIPO di
fallimento prima di ritentare, e di fermare il loop alla 3ª. E' dottrina che
vive solo nel prompt, quindi il modello la salta proprio quando serve — cioe'
quando sta macinando. La prova sta nella telemetria: `escalation` e' un evento
che il modello deve loggare a mano e ne risultano ZERO in tutta la vita del DB,
contro 4 `budget_flag` scritti da un hook. Cio' che scrive un hook atterra.
(Idea presa da cozytab/fable5-mode `fable_fail_streak.py`, riletta sulla nostra
dottrina: loro iniettano harness->deployment->product, noi i nostri 4 tipi.)

PERCHE' NON SCRIVE `escalation`: la tentazione era auto-riempire quella metrica
vuota. Ma `escalation` porta `class` e `resolution` — giudizi che un hook non
puo' dare. Riempirla di eventi senza classe la farebbe MENTIRE: e' Goodhart, che
questo plugin esiste per impedire. Qui si scrive `fail_streak`, che contiene
solo fatti oggettivi; la classificazione resta al modello, che l'avviso invita
a loggare come `escalation`.

PERCHE' LEGGE IL TRANSCRIPT E NON `tool_response`: il transcript e' la ground
truth di cui conosciamo la forma (`tool_result.is_error`, verificata sui file
reali 2026-07-17) ed e' gia' la fonte che l'hook Stop usa per i token. In piu'
rende il conteggio STATELESS: niente file di stato per sessione, niente logica
di reset, niente residui da mietere.

Advisory puro: exit 0 sempre, non blocca mai. Fail-open su qualunque errore.
"""
import json
import os
import re
import sys
from pathlib import Path

REMIND_EVERY = 3        # la dottrina dice "mai una 4ª tentativo identico": si avvisa ALLA 3ª
TAIL_BYTES = 500_000    # basta e avanza per uno streak; il transcript intero puo' essere enorme

# NON sono fallimenti del modello: sono decisioni dell'utente. Contarle gonfierebbe
# lo streak e farebbe scattare un rimprovero per una cosa che ha fatto lui.
USER_ACTION = re.compile(
    r"user doesn't want to proceed|user rejected|requested to stop|"
    r"user has requested|interrupted by user", re.I)

LADDER = (
    "[fable-director] {n} comandi Bash falliti di fila. Rule-of-3: NON ritentare "
    "alla cieca — l'escalation cieca e' essa stessa spreco. Diagnostica prima il "
    "TIPO: (1) INFRA (timeout, 403, rate/session limit) -> retry/resume stesso "
    "esecutore, salire di modello non aiuta; (2) CAPABILITY (output di nuovo "
    "sbagliato/incompleto) -> se e' oggettivamente verificabile prima best-of-3 "
    "sullo STESSO esecutore, poi sali di modello; (3) APPROACH (stesso errore, "
    "stessa strategia) -> cambia strategia o diagnosi, non il modello; (4) "
    "TOOL/TARGET (il tool non aggancia il bersaglio) -> cambia tool o tecnica, non "
    "il modello. Alla 3ª FERMA IL LOOP: il top model la prende inline, oppure "
    "chiedi all'utente. Mai una 4ª identica automatica. Se gli ultimi ~5 turni non "
    "hanno prodotto un artefatto, un test o un fatto verificabile, fermati e "
    "chiedi. Loggato `fail_streak`; se diagnostichi il tipo, mettilo a verbale: "
    "fd-telemetry.py log escalation --json '{{\"class\":\"...\",\"resolution\":\"...\"}}'"
)


def bash_outcomes(transcript_path):
    """Esiti Bash in ordine cronologico. Le azioni dell'utente vengono SALTATE
    (non contano come fallimento ne' azzerano lo streak: non sono un esito)."""
    p = Path(transcript_path)
    if not p.is_file():
        return []
    size = p.stat().st_size
    with p.open("rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()          # scarta la riga tagliata a meta'
        raw = fh.read().decode("utf-8", "replace")

    names, out = {}, []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "tool_use":
                names[b.get("id")] = b.get("name")
            elif t == "tool_result":
                uid = b.get("tool_use_id")
                if names.get(uid) != "Bash":
                    continue          # fuori dalla coda letta -> ignorato, non indovinato
                txt = b.get("content")
                txt = txt if isinstance(txt, str) else json.dumps(txt, ensure_ascii=False)
                if USER_ACTION.search(txt[:200]):
                    continue
                out.append(bool(b.get("is_error")))
    return out


def trailing_streak(outcomes):
    n = 0
    for err in reversed(outcomes):
        if not err:
            break
        n += 1
    return n


def log_event(event, payload, session_id, cwd):
    import random
    import sqlite3
    import time
    from datetime import datetime, timezone
    base = Path.home() / ".claude" / "fable-director"
    base.mkdir(parents=True, exist_ok=True)
    row = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           session_id, str(cwd or ""), event,
           json.dumps(payload, ensure_ascii=False))
    for attempt in range(4):
        try:
            con = sqlite3.connect(base / "telemetry.db", timeout=1.0)
            con.execute("PRAGMA busy_timeout=1000")
            con.execute("CREATE TABLE IF NOT EXISTS events("
                        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                        "session_id TEXT, cwd TEXT, event TEXT NOT NULL, "
                        "payload TEXT)")
            con.execute("INSERT INTO events(ts, session_id, cwd, event, payload) "
                        "VALUES(?,?,?,?,?)", row)
            con.commit()
            con.close()
            return
        except sqlite3.OperationalError:
            time.sleep(0.05 * (2 ** attempt) + random.random() * 0.05)


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    streak = trailing_streak(bash_outcomes(data.get("transcript_path") or ""))
    if streak < REMIND_EVERY or streak % REMIND_EVERY:
        return

    # Solo il BINARIO, mai la riga intera: i comandi contengono chiavi, token e
    # path di clienti, e questo finisce su disco.
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    binary = re.split(r"[\s|;&]+", cmd.strip())[0][:40] if cmd.strip() else "?"

    log_event("fail_streak", {"streak": streak, "binary": binary, "auto": True},
              data.get("session_id"), data.get("cwd") or os.getcwd())
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": LADDER.format(n=streak)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass    # advisory: non deve MAI disturbare la sessione

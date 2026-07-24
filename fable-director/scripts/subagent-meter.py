#!/usr/bin/env python3
"""SubagentStart/SubagentStop hook: conta le deleghe DAVVERO partite e misura
l'effort REALE con cui girano.

Perché serve (1.29.0). Fino a 1.28.x il plugin vedeva le deleghe solo dal lato
richiesta (gate PreToolUse su Agent|Task|Workflow) e dal lato consumo (token nel
transcript). Due buchi:

  1. Spawn annidati. Da Claude Code 2.1.219 la profondità di default dei
     subagent annidati è 3 (prima 1): un subagent autorizzato può generare
     nipoti che il gate non intercetta come DELEGA NUOVA. SubagentStart li
     vede tutti, uno per uno, perché lo scrive l'harness — non il modello.
  2. Effort reale. Il README dichiara un limite: le versioni vecchie di Claude
     Code ignorano il frontmatter `effort` degli agent pinnati (fd-executor
     low, fd-verifier high) e l'agent eredita l'effort di sessione — degrado
     SILENZIOSO, nessun errore. SubagentStop porta `effort.level` reale: il
     confronto con il frontmatter rende quel degrado un evento misurato
     (`effort_ignored`), non più un limite dichiarato e basta.

Principio di design del plugin applicato a sé stesso: se un segnale conta, lo
deve scrivere un hook — quello che il modello promette di loggare non è dato.

Contratto: SubagentStart è display-only (exit code ignorato, nessun blocco);
SubagentStop POTREBBE bloccare con exit 2 e qui non lo fa MAI. Questo script è
un misuratore: qualunque errore interno esce 0 e in silenzio.

Stato: ~/.claude/fable-director/subagents/<session_id>.json — in volo, totali,
per-tipo, mismatch di effort. Lo legge la statusline (segmento dlg ⟲N) e
/fable-director:status. Scritture concorrenti (fan-out di 15 agent che partono
insieme): lock flock dove esiste + rename atomico.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # assente su Windows: sotto si degrada a solo-rename atomico
except ImportError:
    fcntl = None

STATE_DIR = Path.home() / ".claude" / "fable-director" / "subagents"
STALE_DAYS = 3


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(event, payload, session_id=None, cwd=None):
    """Best-effort verso la telemetria SQLite: mai bloccante, mai rumorosa."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event(event, payload, session_id=session_id, cwd=cwd)
    except Exception:
        pass


def pinned_effort(agent_type):
    """Effort dichiarato nel frontmatter di un agent shipped col plugin.
    Parse a runtime (zero drift se il frontmatter cambia); None per gli agent
    che non sono nostri — lì non c'è nessuna coerenza da verificare."""
    if not agent_type:
        return None
    name = str(agent_type).split(":")[-1]
    f = Path(__file__).resolve().parent.parent / "agents" / f"{name}.md"
    if not f.is_file():
        return None
    in_fm = False
    try:
        for line in f.read_text(errors="replace").splitlines():
            if line.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm and line.startswith("effort:"):
                return line.split(":", 1)[1].strip() or None
    except OSError:
        return None
    return None


def prune(now):
    """Igiene: i file di sessioni vecchie non servono a nessuno. Silenziosa."""
    try:
        for f in STATE_DIR.glob("*.json"):
            if now - f.stat().st_mtime > STALE_DAYS * 86400:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def empty_state():
    return {"inflight": {}, "started": 0, "stopped": 0,
            "by_type": {}, "effort_ignored": 0, "nested_seen": 0}


def update(path, mutate):
    """Read-modify-write serializzato. Il fan-out di un workflow fa partire
    molti subagent nello stesso istante: senza lock l'ultimo writer vince e i
    conteggi si perdono (stessa classe di bug degli 113/800 eventi persi nello
    stress test 2026-07-11 sulla telemetria)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    fh = None
    try:
        if fcntl is not None:
            fh = open(lock, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text()) if path.is_file() else None
        except (OSError, json.JSONDecodeError):
            state = None
        if not isinstance(state, dict):
            state = empty_state()
        for k, v in empty_state().items():
            state.setdefault(k, v)
        result = mutate(state)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return result
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def on_start(data, path):
    aid = str(data.get("agent_id") or "")
    atype = str(data.get("agent_type") or "?")

    def mutate(state):
        if aid:
            state["inflight"][aid] = {"type": atype, "since": now_iso()}
        state["started"] += 1
        state["by_type"][atype] = int(state["by_type"].get(atype, 0)) + 1
        return None

    update(path, mutate)


def on_stop(data, path):
    aid = str(data.get("agent_id") or "")
    atype = str(data.get("agent_type") or "?")
    actual = ((data.get("effort") or {}).get("level")
              if isinstance(data.get("effort"), dict) else None)
    pinned = pinned_effort(atype)
    # Mismatch = il frontmatter effort NON è stato applicato (versione vecchia
    # di Claude Code, o override della sessione): è il degrado silenzioso che
    # il README elenca fra i limiti noti. Qui smette di essere silenzioso.
    mismatch = bool(pinned and actual and pinned != actual)

    def mutate(state):
        state["inflight"].pop(aid, None)
        state["stopped"] += 1
        if mismatch:
            state["effort_ignored"] += 1
            state["last_effort_ignored"] = {
                "agent_type": atype, "pinned": pinned, "actual": actual,
                "ts": now_iso()}
        return None

    update(path, mutate)
    if mismatch:
        log_event("effort_ignored",
                  {"agent_type": atype, "pinned": pinned, "actual": actual},
                  session_id=data.get("session_id"), cwd=data.get("cwd"))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    sid = str(data.get("session_id") or "nosession")
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in sid)[:120]
    path = STATE_DIR / f"{safe}.json"
    try:
        event = data.get("hook_event_name")
        if event == "SubagentStart":
            on_start(data, path)
            prune(time.time())
        elif event == "SubagentStop":
            on_stop(data, path)
    except Exception:
        # Un misuratore che rompe la sessione che misura è peggio del buco
        # che chiude: qualunque errore qui è silenzio, mai un blocco.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

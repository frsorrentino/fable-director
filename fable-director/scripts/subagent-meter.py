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
import re
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


STATUS_RE = re.compile(
    r"^\s*[`*_]*(DONE_WITH_CONCERNS|DONE|NEEDS_CONTEXT|BLOCKED|ABSTAIN)[`*_]*\s*$",
    re.M)
STATUS_KEY = {"DONE": "ok", "DONE_WITH_CONCERNS": "concerns",
              "NEEDS_CONTEXT": "needs_context", "BLOCKED": "blocked",
              "ABSTAIN": "abstain"}
INFRA_RE = re.compile(r"timeout|timed out|\b(403|429|5\d\d)\b|rate limit|"
                      r"permission denied|EACCES|connection|ECONNRESET|quota",
                      re.I)


def last_assistant_text(transcript):
    """Ultimo blocco di testo assistant dal transcript dell'agente (fallback
    quando l'host non passa last_assistant_message)."""
    last = ""
    try:
        with open(transcript, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                for c in ((rec.get("message") or {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                        last = c["text"]
    except OSError:
        pass
    return last


def parse_status(text):
    """(status_key, blocker_line): l'ULTIMO token di stato nel testo (il
    contratto lo vuole su riga propria in chiusura); assente → 'unknown', mai
    inferito dal tono. La riga di blocco = ultima riga non vuota prima del
    token, per BLOCKED/NEEDS_CONTEXT/ABSTAIN."""
    if not text:
        return "unknown", None
    hits = list(STATUS_RE.finditer(text))
    if not hits:
        return "unknown", None
    m = hits[-1]
    key = STATUS_KEY[m.group(1)]
    blocker = None
    if key in ("blocked", "needs_context", "abstain"):
        before = [l.strip() for l in text[:m.start()].splitlines() if l.strip()]
        if before:
            blocker = before[-1][:160]
    return key, blocker


def agent_usage(transcript):
    """Usage dell'agente dal suo transcript, dedup per message.id (i record
    multi-riga ripetono lo stesso usage). Ritorna (model, turns, tot)."""
    turns, model = {}, None
    try:
        with open(transcript, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = rec.get("message") or {}
                u = m.get("usage")
                if rec.get("type") != "assistant" or not isinstance(u, dict):
                    continue
                model = m.get("model") or model
                mid = m.get("id") or rec.get("requestId") or str(len(turns))
                row = turns.setdefault(mid, {"input_tokens": 0, "output_tokens": 0,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0})
                for k in row:
                    row[k] = max(row[k], u.get(k) or 0)
    except OSError:
        pass
    tot = {k: sum(r[k] for r in turns.values()) for k in
           ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")}
    return model, len(turns), tot


def five_hour_pct():
    """Quota 5h vista dallo statusline (file per-account, fresco ≤10 min):
    con l'eq dell'agente da' al report/gate la taratura eq↔finestra. None
    se assente o stantio — mai inventata."""
    try:
        import hashlib
        acct = hashlib.sha256((os.environ.get("CLAUDE_CONFIG_DIR")
                               or str(Path.home() / ".claude")).encode()).hexdigest()[:8]
        base = Path.home() / ".claude" / "fable-director"
        qf = base / f"quota-{acct}.json"
        if not qf.is_file():
            qf = base / "quota.json"
        if not qf.is_file() or time.time() - qf.stat().st_mtime > 600:
            return None
        v = json.loads(qf.read_text()).get("five_hour_used_pct")
        return float(v) if v is not None else None
    except (OSError, ValueError, TypeError):
        return None


def run_id_of(transcript):
    """wf_<id> dal path del transcript di un agente Workflow, None altrimenti."""
    m = re.search(r"[/\\]workflows[/\\](wf_[A-Za-z0-9-]+)[/\\]", str(transcript or ""))
    return m.group(1) if m else None


def eq_of(model, tot):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.eq_tokens(tot["input_tokens"], tot["output_tokens"],
                             tot["cache_read_input_tokens"],
                             tot["cache_creation_input_tokens"], model=model)
    except Exception:
        return None


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
            "by_type": {}, "effort_ignored": 0, "nested_seen": 0,
            "outcomes": {}, "failures_by_type": {}, "last_blocked": []}


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
    # ESITO della delega, letto dall'hook e non dal modello: il token di stato
    # del contratto (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED /
    # ABSTAIN) nell'ultimo messaggio dell'agente; assente → unknown, mai
    # inferito. Con usage e modello dal transcript dell'agente: costo in eq
    # alla tariffa del SUO modello (model-economics.json).
    transcript = data.get("agent_transcript_path")
    text = data.get("last_assistant_message")
    if not text and transcript:
        text = last_assistant_text(transcript)
    status, blocker = parse_status(text or "")
    model, turns, tot = agent_usage(transcript) if transcript else (None, 0, None)
    eq = eq_of(model, tot) if tot else None
    failed = status in ("blocked", "needs_context", "abstain")
    fail_class = None
    if failed:
        fail_class = ("infra" if blocker and INFRA_RE.search(blocker)
                      else "approach" if status == "needs_context"
                      else "capability")

    def mutate(state):
        state["inflight"].pop(aid, None)
        state["stopped"] += 1
        if mismatch:
            state["effort_ignored"] += 1
            state["last_effort_ignored"] = {
                "agent_type": atype, "pinned": pinned, "actual": actual,
                "ts": now_iso()}
        oc = state.setdefault("outcomes", {})
        oc[status] = int(oc.get(status) or 0) + 1
        n_fail = 0
        if failed:
            fbt = state.setdefault("failures_by_type", {})
            fbt[atype] = int(fbt.get(atype) or 0) + 1
            n_fail = fbt[atype]
            lb = state.setdefault("last_blocked", [])
            lb.append({"agent_type": atype, "status": status,
                       "blocker": blocker, "ts": now_iso()})
            del lb[:-5]
        return n_fail

    n_fail = update(path, mutate)
    sid, cwd = data.get("session_id"), data.get("cwd")
    if mismatch:
        log_event("effort_ignored",
                  {"agent_type": atype, "pinned": pinned, "actual": actual},
                  session_id=sid, cwd=cwd)
    payload = {"agent_id": aid, "agent_type": atype, "model": model,
               "effort": actual or pinned, "status": status, "turns": turns,
               "eq": eq, "blocker": blocker, "auto": True}
    if tot:
        payload["output_tokens"] = tot["output_tokens"]
        payload["fresh_input"] = tot["input_tokens"] + tot["cache_creation_input_tokens"]
    # (1.40) run del Workflow e quota 5h al momento dello stop: con l'eq
    # danno la taratura eq↔finestra che il gate usa prima del lancio.
    rid = run_id_of(transcript)
    if rid:
        payload["run_id"] = rid
    pct = five_hour_pct()
    if pct is not None:
        payload["five_hour_pct"] = pct
    log_event("delegation_outcome", payload, session_id=sid, cwd=cwd)
    # Rule of 3, deterministica: al SECONDO fallimento dello stesso tipo di
    # agente nella sessione la diagnosi viene loggata dall'hook (classe
    # infra/approach/capability dal testo di blocco) e detta all'utente —
    # exit 1 = "stderr all'utente, continua"; mai exit 2 (parlerebbe al
    # subagent). Misurato prima: 7 fail_streak, 0 escalation model-logged.
    if failed and n_fail >= 2:
        log_event("escalation", {"class": fail_class, "agent_type": atype,
                                 "status": status, "n": n_fail,
                                 "resolution": "pending", "auto": True},
                  session_id=sid, cwd=cwd)
        nxt = {"infra": "retry or resume the same executor — escalating the model does not help",
               "approach": "the contract lacked context: repack the spec (Files/Interfaces), do not retry as is",
               "capability": "change something structural — model up, or best-of-3 if the output is objectively checkable"}[fail_class]
        print(f"FD ⚠ {n_fail} {status.upper()} from {atype} this session "
              f"({fail_class}): {nxt}. No identical 4th attempt.", file=sys.stderr)
        return 1
    return 0


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
            return on_stop(data, path) or 0
    except Exception:
        # Un misuratore che rompe la sessione che misura è peggio del buco
        # che chiude: qualunque errore qui è silenzio, mai un blocco.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

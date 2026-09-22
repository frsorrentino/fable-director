#!/usr/bin/env python3
"""Hook PreModelSwitch / PostModelSwitch (Claude Code >= 2.1.251): un cambio di
modello a meta' sessione invalida il prompt cache (il contesto viene riscritto
in cache al prossimo turno) e, partendo da Claude Fable 5.1, fa cadere i suoi
thinking block sul modello di destinazione. Misurato (reset-cause.py,
2026-08-11..09-01): 31 reset su 207 vengono da un cambio modello, re-cache
mediano 280k token.

Politica: AVVISA, mai blocca. PreModelSwitch esce con codice 1 e il messaggio
su stderr (per l'host: "other exit codes - show stderr to user only and
continue"); exit 2 bloccherebbe e non lo usiamo mai. Il cambio viene
registrato in telemetria come `reversal` {kind: model_switch} e, se c'e' un
budget aperto per questa sessione, appeso a `model_switches` nel budget file
(la ricevuta lo mostra). PostModelSwitch copre i cambi che Pre non vede
(source auto/resume) e dice a Claude, in una riga su stdout, che la tariffa
economica e' cambiata.

Cambi INVOLONTARI (2.1.280, verificato nel binario, mai provocato): quando le
salvaguardie segnalano un messaggio (Claude Opus 5.5: bio/cyber) o il modello
primario fallisce, Claude Code sposta la sessione su un altro modello e
PostModelSwitch arriva con source "auto", requested_model null — lo stesso
source di ogni altro cambio programmatico, quindi da solo non basta. Il segnale
certo e' nel transcript: riga type "system", subtype "model_refusal_fallback"
(originalModel, fallbackModel, direction retry/revert/sticky, scope
session/local, apiRefusalCategory) o "model_fallback" (trigger overloaded,
model_not_found, ...). Definizione condivisa con claude-master: conta l'ultima
riga con scope != "local" (assente = session), direction != "revert",
fallbackModel = to_model sull'id base. Un cambio involontario NON e' un
`reversal` (report e hindsight li leggono come politica smentita): va in
telemetria come evento `model_fallback` con la causa, e a schermo con la via
di ritorno (/model <originale>; /config "Switch models when a message is
flagged" per farsi chiedere prima). source "auto" senza riga nel transcript =
involontario con causa non identificata (ripiego dichiarato, non una stima).

Stdin (schema host): from_model, to_model, requested_model, source,
context_tokens, prompt_cache_warm, cache_ttl, estimated_cache_write_usd,
pricing, session_id, cwd, hook_event_name. Campi assenti → silenzio, mai
numeri inventati.
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path.home() / ".claude" / "fable-director"
WARN_MIN_CONTEXT = 20_000  # sotto: il re-cache costa poco, nessun avviso


def _fdt():
    try:
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def short(model):
    """'claude-fable-5-1' → 'fable-5-1' per la riga a schermo."""
    m = str(model or "?")
    return m[len("claude-"):] if m.startswith("claude-") else m


def fmt(n):
    return f"{int(n):,}".replace(",", ".")


FALLBACK_WAIT_S = 2.0   # la riga di sistema puo' arrivare nel jsonl dopo l'hook
TAIL_BYTES = 256 * 1024


def base_id(model):
    """'claude-opus-5[1m]' → 'claude-opus-5': confronto sull'id base."""
    m = str(model or "")
    return m.split("[", 1)[0].strip()


def _fb_field(rec, camel, snake):
    v = rec.get(camel)
    return v if v is not None else rec.get(snake)


def find_fallback(transcript, to_m):
    """Ultima riga di fallback coerente con questo cambio, o None.
    Ritorna {cause, from, to, category, direction, at}."""
    try:
        p = Path(transcript)
        size = p.stat().st_size
        with open(p, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        if '"model_refusal_fallback"' not in line and '"model_fallback"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        sub = rec.get("subtype")
        if rec.get("type") != "system" or sub not in ("model_refusal_fallback",
                                                        "model_fallback"):
            continue
        if (rec.get("scope") or "session") == "local":
            continue
        direction = rec.get("direction")
        fb_to = _fb_field(rec, "fallbackModel", "fallback_model")
        orig = _fb_field(rec, "originalModel", "original_model")
        if direction == "revert":
            # l'host riporta la sessione sul modello originale: automatico,
            # ma e' un ritorno, non un ripiego
            if base_id(orig) != base_id(to_m):
                return None
            return {"cause": "refusal_revert", "from": fb_to, "to": orig,
                    "category": _fb_field(rec, "apiRefusalCategory",
                                          "api_refusal_category"),
                    "direction": direction, "at": rec.get("timestamp")}
        if base_id(fb_to) != base_id(to_m):
            return None
        if sub == "model_refusal_fallback":
            cause = "refusal_fallback"
        else:
            cause = f"model_fallback:{rec.get('trigger') or '?'}"
        return {"cause": cause,
                "from": orig,
                "to": fb_to,
                "category": _fb_field(rec, "apiRefusalCategory", "api_refusal_category"),
                "direction": direction, "at": rec.get("timestamp")}
    return None


def classify(data, to_m, wait_s=FALLBACK_WAIT_S):
    """None = cambio voluto (command/picker/sdk/resume). Altrimenti dict con
    la causa: da transcript quando c'e' la riga, 'auto_unidentified' se no."""
    if data.get("source") != "auto":
        return None
    tp = data.get("transcript_path")
    fb = None
    if tp:
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            fb = find_fallback(tp, to_m)
            if fb or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    return fb or {"cause": "auto_unidentified", "from": None, "to": to_m,
                  "category": None, "direction": None, "at": None}


def involuntary_lines(from_m, to_m, fb, rate_s):
    """(riga per l'utente, riga per Claude)."""
    cat = f" ({fb['category']})" if fb.get("category") else ""
    back = base_id(fb.get("from") or from_m)
    if fb["cause"] == "refusal_fallback":
        why = f"Claude Code's safeguards flagged a message{cat}"
        tail = (" To be asked first next time: /config → \"Switch models when a "
                "message is flagged\".")
    elif fb["cause"] == "refusal_revert":
        user = (f"FD: model back to {short(to_m)} after the flagged-message "
                f"fallback on {short(from_m)} (automatic). Logged as involuntary.")
        claude = (f"FD: session model returned automatically {short(from_m)} → "
                  f"{short(to_m)} (refusal_revert){rate_s}. Read the model from "
                  f"the record, never from the session start.")
        return user, claude
    elif fb["cause"].startswith("model_fallback:"):
        why = f"the primary model failed ({fb['cause'].split(':', 1)[1]})"
        tail = ""
    else:
        why = ("an automatic change (source auto, no fallback line in the "
               "transcript: safeguard, usage limit or other programmatic switch)")
        tail = ""
    user = (f"FD ⚠ model switched without your request: {short(from_m)} → "
            f"{short(to_m)} — {why}. Back with /model {back}.{tail} "
            f"Logged as involuntary, not as a choice.")
    claude = (f"FD: session model switched INVOLUNTARILY {short(from_m)} → "
              f"{short(to_m)} ({fb['cause']}{cat}){rate_s}. The user did not "
              f"choose this; they can return with /model {back}. Read the model "
              f"from the record, never from the session start.")
    return user, claude


def record_involuntary(mod, data, from_m, to_m, fb):
    """Evento `model_fallback` (mai `reversal`) + nota nel budget aperto."""
    payload = {"from": from_m, "to": to_m, "source": data.get("source"),
               "cause": fb["cause"], "category": fb.get("category"),
               "original_model": fb.get("from"), "direction": fb.get("direction"),
               "at": fb.get("at"), "context_tokens": data.get("context_tokens")}
    if mod:
        try:
            mod.log_event("model_fallback", payload,
                          session_id=data.get("session_id"),
                          cwd=data.get("cwd") or os.getcwd())
        except Exception:
            pass
    _append_budget(mod, data, {"from": from_m, "to": to_m,
                               "source": data.get("source"),
                               "context_tokens": data.get("context_tokens"),
                               "involuntary": fb["cause"],
                               "category": fb.get("category")})


def record(mod, data, from_m, to_m):
    """Telemetria + budget aperto. Best-effort: mai un errore all'utente."""
    payload = {
        "kind": "model_switch", "from": from_m, "to": to_m,
        "source": data.get("source"),
        "context_tokens": data.get("context_tokens"),
        "prompt_cache_warm": data.get("prompt_cache_warm"),
        "estimated_cache_write_usd": data.get("estimated_cache_write_usd"),
        "auto": True,
    }
    sid = data.get("session_id")
    cwd = data.get("cwd") or os.getcwd()
    if mod:
        try:
            mod.log_event("reversal", payload, session_id=sid, cwd=cwd)
        except Exception:
            pass
    _append_budget(mod, data, {"from": from_m, "to": to_m,
                               "source": data.get("source"),
                               "context_tokens": data.get("context_tokens")})


def _append_budget(mod, data, entry):
    """Appende a model_switches del budget aperto di QUESTA sessione
    (owner_sid), altrimenti del cwd. Best-effort."""
    sid = data.get("session_id")
    cwd = data.get("cwd") or os.getcwd()
    bdir = BASE / "budgets"
    if not bdir.is_dir():
        return
    target = None
    try:
        for p in sorted(bdir.glob("*.json")):
            if p.name.endswith(".state.json"):
                continue
            b = json.loads(p.read_text(encoding="utf-8"))
            if b.get("status") not in ("open", "flagged"):
                continue
            if sid and b.get("owner_sid") == sid:
                target = (p, b)
                break
            if not sid and mod and p.stem == mod.cwd_slug(cwd):
                target = (p, b)
        if target:
            p, b = target
            entry["ts"] = mod.now_iso() if mod and hasattr(mod, "now_iso") else None
            b.setdefault("model_switches", []).append(entry)
            p.write_text(json.dumps(b, indent=1), encoding="utf-8")
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    from_m, to_m = data.get("from_model"), data.get("to_model")
    if not from_m or not to_m or from_m == to_m:
        return 0
    event = data.get("hook_event_name") or ""
    mod = _fdt()
    fb = classify(data, to_m) if event == "PostModelSwitch" else None
    if fb:
        record_involuntary(mod, data, from_m, to_m, fb)
    else:
        record(mod, data, from_m, to_m)

    ctx = data.get("context_tokens")
    warm = data.get("prompt_cache_warm")
    usd = data.get("estimated_cache_write_usd")
    rate_to = mod.eq_mult(to_m)["cache_read"] if mod else None

    if event == "PostModelSwitch":
        # stdout → Claude al prossimo turno: la tariffa e' cambiata da qui.
        rate_s = f"; cache reads now priced at {rate_to}× input" if rate_to is not None else ""
        if fb:
            # JSON: systemMessage a schermo per l'utente, additionalContext a
            # Claude (schema host 2.1.280, hookSpecificOutput PostModelSwitch).
            user, claude = involuntary_lines(from_m, to_m, fb, rate_s)
            print(json.dumps({"systemMessage": user, "hookSpecificOutput": {
                "hookEventName": "PostModelSwitch", "additionalContext": claude}},
                ensure_ascii=False))
            return 0
        print(f"FD: session model switched {short(from_m)} → {short(to_m)} "
              f"(source: {data.get('source') or '?'}){rate_s}. Read the model "
              f"from the record, never from the session start.")
        return 0

    # PreModelSwitch: avviso solo se costa davvero.
    if not warm and (not isinstance(ctx, (int, float)) or ctx < WARN_MIN_CONTEXT):
        return 0
    parts = [f"FD ⚠ model switch {short(from_m)} → {short(to_m)}:"]
    if warm:
        parts.append("the prompt cache is warm and gets forfeited")
    if isinstance(ctx, (int, float)) and ctx > 0:
        cost = f" (~${usd:.2f} at list price)" if isinstance(usd, (int, float)) else ""
        parts.append(f"~{fmt(ctx)} tokens re-cached on the next turn{cost}")
    if str(from_m).startswith("claude-fable-5-1"):
        parts.append("Fable 5.1 thinking blocks are dropped on the new model "
                     "(it re-plans from scratch)")
    msg = " — ".join([parts[0] + " " + parts[1]] + parts[2:]) if len(parts) > 1 else parts[0]
    print(msg + ". Switch proceeds; logged as a reversal.", file=sys.stderr)
    return 1  # "show stderr to user only and continue": mai 2


if __name__ == "__main__":
    sys.exit(main())

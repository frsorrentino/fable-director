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

Stdin (schema host): from_model, to_model, requested_model, source,
context_tokens, prompt_cache_warm, cache_ttl, estimated_cache_write_usd,
pricing, session_id, cwd, hook_event_name. Campi assenti → silenzio, mai
numeri inventati.
"""
import importlib.util
import json
import os
import sys
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
    # budget aperto di QUESTA sessione (owner_sid), altrimenti del cwd
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
            b.setdefault("model_switches", []).append({
                "from": from_m, "to": to_m, "source": data.get("source"),
                "context_tokens": data.get("context_tokens"),
                "ts": mod.now_iso() if mod and hasattr(mod, "now_iso") else None,
            })
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
    record(mod, data, from_m, to_m)

    ctx = data.get("context_tokens")
    warm = data.get("prompt_cache_warm")
    usd = data.get("estimated_cache_write_usd")
    rate_to = mod.eq_mult(to_m)["cache_read"] if mod else None

    if event == "PostModelSwitch":
        # stdout → Claude al prossimo turno: la tariffa e' cambiata da qui.
        rate_s = f"; cache reads now priced at {rate_to}× input" if rate_to is not None else ""
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

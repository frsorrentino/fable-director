#!/usr/bin/env python3
"""UserPromptSubmit hook — prefisso di famiglia (1.39, D.3.3).

Il prompt che inizia con `gemini:` / `codex:` / `gemini-stable:` sceglie il
provider esterno senza configurazione oltre a cross-family.json. Due forme:
  `gemini: scrivi la lettera …`  → BOZZA: il provider scrive, il testo viene
      iniettato come contesto ("bozza esterna, da correggere, non da citare")
      e il turno di Claude parte subito dopo — corregge invece di scrivere.
  `gemini? quanto costa X`       → SECONDO PARERE: la risposta viene riportata
      tale quale, delimitata e attribuita; Claude aggiunge al massimo una riga.

Regola non negoziabile: il prefisso NON scavalca data_class ne' quality_guard.
Budget aperto `restricted` → turno normale e lo dice. Prompt che sembra
lavoro su codice (fence, path con estensione + verbi di implementazione) →
quality guard, turno normale e lo dice. Provider a pagamento, assente, giu'
o in timeout → turno normale, una riga. Mai silenzioso, mai ripiego
automatico su un altro provider. Il testo inviato fuori e' SOLO il prompt
(mai file allegati). Ogni chiamata finisce nel ledger esterno (external_exec,
mode draft|opinion), separato dalla quota Claude.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path.home() / ".claude" / "fable-director"
PREFIX_RE = re.compile(r"^\s*(gemini-stable|gemini|codex)\s*([:?])\s+(\S.*)$",
                       re.I | re.S)
CODE_HINT_RE = re.compile(
    r"```|\b[\w./-]+\.(py|php|js|ts|tsx|jsx|go|rs|java|rb|sh|sql|scss|css|html)\b",
    re.I)
CODE_VERB_RE = re.compile(
    r"\b(implementa|implement|refactor|fix(?:a|are)?|bug|patch|scrivi il codice|"
    r"write the code|debug|test(?:s|a|are)?|deploy|migra|migrate|commit)\b", re.I)
TIMEOUT_S = int(os.environ.get("FD_PREFIX_TIMEOUT_S") or 45)

DRAFT_SYSTEM = (
    "You write a first draft for a human who will revise it. Answer in the "
    "language of the request. Plain text (Markdown allowed), no preamble, no "
    "questions back: if something is unknown, mark it as [TO CHECK] and go on.")
OPINION_SYSTEM = (
    "Give a direct, concise answer in the language of the request. State your "
    "confidence and what it depends on. No preamble.")


def load_ext():
    spec = importlib.util.spec_from_file_location(
        "external_exec", Path(__file__).with_name("external-exec.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def open_budget(cwd, sid):
    """Budget aperto della sessione (owner_sid) o del cwd, altrimenti None."""
    bdir = BASE / "budgets"
    if not bdir.is_dir():
        return None
    try:
        import hashlib
        s = str(cwd).replace("\\", "/")
        slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
                + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
        for p in sorted(bdir.glob("*.json")):
            if p.name.endswith(".state.json"):
                continue
            b = json.loads(p.read_text(encoding="utf-8"))
            if b.get("status") not in ("open", "flagged"):
                continue
            if (sid and b.get("owner_sid") == sid) or p.stem == slug:
                return b
    except Exception:
        pass
    return None


def quality_guard(text):
    """Deterministico: fence di codice, o path con estensione di codice
    insieme a un verbo di implementazione → lavoro su codice (asse 2)."""
    if "```" in text:
        return True
    return bool(CODE_HINT_RE.search(text) and CODE_VERB_RE.search(text))


def say(line):
    print(line)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = str(data.get("prompt") or "")
    m = PREFIX_RE.match(prompt)
    if not m:
        return 0
    name, sep, body = m.group(1).lower(), m.group(2), m.group(3).strip()
    mode = "draft" if sep == ":" else "opinion"
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id")
    tag = f"[fd-external {name}]"

    # --- guardie sovrane ---------------------------------------------------
    b = open_budget(cwd, sid)
    if b and b.get("data_class") == "restricted":
        say(f"{tag} prefix ignored: the open budget declares data-class "
            f"restricted — nothing leaves the machine. Normal turn.")
        return 0
    if quality_guard(body):
        say(f"{tag} prefix ignored: this looks like code work (quality guard, "
            f"axis 2) — production code stays on the Claude route. Normal turn.")
        return 0
    if "@" in body and re.search(r"@[\w./-]+", body):
        say(f"{tag} note: attached files/paths are not sent outside — only the "
            f"prompt text goes to {name}.")

    try:
        ext = load_ext()
    except Exception as e:
        say(f"{tag} not available ({e.__class__.__name__}). Normal turn.")
        return 0
    if not ext.CONFIG_PATH.is_file():
        say(f"{tag} not configured (cross-family.json missing: "
            f"cross-verify.py --init). Normal turn.")
        return 0
    try:
        cfg = json.loads(ext.CONFIG_PATH.read_text())
    except Exception:
        say(f"{tag} config unreadable. Normal turn.")
        return 0
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        say(f"{tag} provider '{name}' not in cross-family.json. Normal turn.")
        return 0
    if ext.billing_of(prov) != "free":
        say(f"{tag} provider '{name}' is billed — the prefix never spends money "
            f"without explicit consent (external-exec.py --paid-ok). Normal turn.")
        return 0

    # --- chiamata ----------------------------------------------------------
    ext.EXEC_SYSTEM = DRAFT_SYSTEM if mode == "draft" else OPINION_SYSTEM
    user_msg = body
    buf = io.StringIO()
    content, detail = None, None
    try:
        with contextlib.redirect_stdout(buf):
            if prov.get("type") == "cli":
                opts = {"--model": None, "--effort": None, "--resume-last": False}
                content = ext.call_cli(prov, name, user_msg, TIMEOUT_S, opts, None)
            elif prov.get("type") == "image":
                detail = "image providers are not text providers"
            else:
                api_key = prov.get("api_key") or os.environ.get(prov.get("api_key_env", ""), "")
                if not api_key:
                    detail = "API key missing"
                else:
                    content = ext.call_http(prov, name, api_key, user_msg, TIMEOUT_S)
    except SystemExit:
        dl = [l for l in buf.getvalue().splitlines() if l.startswith("DETAIL:")]
        detail = dl[-1][7:].strip() if dl else "provider unavailable"
    except Exception as e:
        detail = f"{e.__class__.__name__}: {str(e)[:120]}"
    ok = bool(content and str(content).strip())
    ext.log_exec({"provider": name, "model": prov.get("model", "?"),
                  "billing": ext.billing_of(prov), "type": f"prefix-{mode}",
                  "mode": mode, "ok": ok, "chars_in": len(body),
                  "chars_out": len(content or ""), "session_id": sid,
                  "check": "-" if ok else (detail or "-")[:120]})
    if not ok:
        say(f"{tag} not available ({(detail or 'no output')[:160]}). Normal turn.")
        return 0

    content = str(content).strip()
    if mode == "draft":
        say(f"{tag} DRAFT from {name} ({prov.get('model', '?')}) — a first draft "
            f"for you to CORRECT, not to quote: keep what holds, rewrite what "
            f"is wrong or [TO CHECK], and deliver the corrected version to the "
            f"user as your own answer. The user asked: {body[:200]}")
    else:
        say(f"{tag} OPINION from {name} ({prov.get('model', '?')}) — report it to "
            f"the user AS IS, delimited and attributed (\"{name} says: …\"). Add "
            f"at most one line of your own, only if you disagree.")
    say("-----")
    say(content)
    say("-----")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

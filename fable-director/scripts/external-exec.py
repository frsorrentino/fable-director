#!/usr/bin/env python3
"""Executor esterno — esecuzione batch su modello fuori famiglia Claude.

Rotta sperimentale (asse 4, item NON quality-sensitive): batch di estrazione,
classificazione, transform testo su free tier esterni (Gemini Flash, Codex CLI)
= zero token Claude. La qualità la decide la telemetria, non questo script:
ogni run logga `external_exec` con --type, e `fd-telemetry.py report` confronta
esiti per provider/tipo contro la baseline Claude. Promozione a regola di
playbook SOLO se i dati confermano (stessa disciplina del cross-family).

Principi (stessi di cross-verify.py):
- NO SILENT FALLBACK: config/chiave/endpoint mancanti → STATUS: unavailable
  con istruzione esplicita di eseguire sulla rotta Claude normale.
- Contratto executor nel system prompt: spec verbatim, SOLO il deliverable nel
  formato richiesto, NEEDS_CONTEXT se la spec non è eseguibile senza contesto
  condiviso (exit 2 — la delega non era pronta, non è un errore del modello).
- Rung-1 minimo integrato: con --schema-json l'output DEVE essere JSON valido,
  altrimenti STATUS: error (mai consegnare output fuori schema a valle).
- Il wrapper persiste il deliverable (--out); il provider CLI resta read-only
  (config cross-family: codex gira --sandbox read-only — l'esterno non scrive
  MAI nel repo, scrive solo questo wrapper dove gli dici tu).

Config: riusa ~/.claude/fable-director/cross-family.json (cross-verify.py
--init per crearla). Il template codex nel config è tarato per verify
(effort high): per exec massivi valuta una voce dedicata nel config.

Uso:
  external-exec.py --spec-file F | --spec "..."
                   [--input FILE]... [--out FILE] [--schema-json]
                   [--provider gemini|gemini-stable|codex] [--type SLUG]
                   [--items N] [--timeout 120] [--allow-truncate]
  external-exec.py --doctor [--ping]   # setup guidato / diagnosi provider

--doctor: nessun budget richiesto (zero chiamate modello senza --ping).
Config assente → istruzioni onboarding: account Google → chiave Gemini free
tier (reset giornaliero); account ChatGPT → Codex CLI login (uso incluso nel
piano); oppure chiavi API a pagamento nelle stesse voci di config. Config
presente → checklist per provider (binario/chiave/auth_check) + uso odierno
vs limits.rpd dal config. --ping aggiunge una chiamata reale minima per
provider (consuma 1 richiesta di quota ciascuna: opt-in).

Pre-budget OBBLIGATORIO e verificato qui (il gate PreToolUse non vede le
chiamate Bash): senza budget open per il cwd lo script esce con errore.
Input oltre il cap → errore esplicito; il troncamento è solo opt-in
(--allow-truncate) e viene dichiarato al modello nel prompt.

Output (grep-abile):
  STATUS: ok|needs_context|unavailable|error
  PROVIDER: <provider> (<model>)
  CHECK: json-valid|raw|-
  OUTPUT: <path deliverable | ->
  DETAIL: <breve>
Exit: 0 ok · 1 unavailable/error · 2 needs_context. Zero dipendenze (stdlib).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Windows cp1252: output con caratteri non-Latin-1 crasherebbe (issue #1).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CONFIG_PATH = Path.home() / ".claude" / "fable-director" / "cross-family.json"
ACTIVE_PATH = Path.home() / ".claude" / "fable-director" / "xfam-active.json"
INPUT_CAP = 100_000  # char per file di input (stesso cap di cross-verify)

EXEC_SYSTEM = (
    "You are a batch executor. You receive a complete task spec (Objective / "
    "Files / Interfaces / Constraints / Verification). Execute it VERBATIM. "
    "Reply with ONLY the deliverable in the exact format the spec requires — "
    "no preamble, no commentary, no code fences unless the spec asks for them. "
    "If the spec cannot be executed without context you do not have, reply "
    "with exactly 'NEEDS_CONTEXT: <what is missing>' and nothing else. "
    "Never invent data to fill gaps: an honest NEEDS_CONTEXT beats a "
    "plausible-but-wrong deliverable."
)


def out(status, provider="-", model="-", check="-", output="-", detail="-"):
    print(f"STATUS: {status}")
    print(f"PROVIDER: {provider} ({model})")
    print(f"CHECK: {check}")
    print(f"OUTPUT: {output}")
    print(f"DETAIL: {detail}")


def unavailable(reason):
    out("unavailable", detail=(
        f"{reason} — esegui sulla rotta Claude normale (asse 4 standard). "
        f"MAI trattare unavailable come eseguito."))
    sys.exit(1)


def require_open_budget():
    """Il gate PreToolUse copre solo Agent/Task/Workflow: questo script gira
    via Bash e lo aggirerebbe (review cross-family 2026-07-10). Il check vive
    quindi QUI, deterministico: nessun budget open per il cwd → errore con il
    comando esatto. Enforcement end-to-end, non promesso."""
    cwd = os.getcwd()
    # Slug: identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    s = str(cwd).replace("\\", "/")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    try:
        budget = json.loads(bfile.read_text()) if bfile.is_file() else None
    except (json.JSONDecodeError, OSError):
        budget = None
    if not isinstance(budget, dict) or budget.get("status") != "open":
        out("error", detail=(
            "nessun pre-budget aperto per questo cwd: anche la rotta esterna "
            "è una delega. Apri il budget e riprova: fd-telemetry.py "
            "budget-open --task \"...\" --expected-output N --route script "
            "[--effort low] [--type slug]"))
        sys.exit(1)
    # data-class restricted: il confine privacy del README (la rotta esterna
    # è disclosure verso terzi) reso enforceable, non solo dichiarato.
    if budget.get("data_class") == "restricted":
        out("error", detail=(
            "rotta esterna BLOCCATA: il budget dichiara --data-class "
            "restricted (input che non possono uscire dalla macchina). "
            "Esegui sulla rotta Claude normale."))
        sys.exit(1)


def log_exec(payload):
    """Best-effort: telemetria oggettiva, mai bloccante."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event("external_exec", payload)
    except Exception:
        pass


def today_usage():
    """Chiamate esterne di oggi per provider, dalla telemetria (external_exec
    + verification cross-family). Best-effort: errore → dict vuoto."""
    counts = {}
    try:
        import sqlite3
        db = Path.home() / ".claude" / "fable-director" / "telemetry.db"
        con = sqlite3.connect(db, timeout=0.5)
        con.execute("PRAGMA busy_timeout=500")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for ev, pl in con.execute(
                "SELECT event, payload FROM events WHERE event IN "
                "('external_exec','verification') AND ts >= ?", (today,)):
            try:
                p = json.loads(pl or "{}")
            except json.JSONDecodeError:
                continue
            if ev == "verification" and p.get("kind") != "cross-family":
                continue
            if p.get("provider"):
                counts[p["provider"]] = counts.get(p["provider"], 0) + 1
        con.close()
    except Exception:
        pass
    return counts


def doctor(ping=False):
    """Setup guidato + diagnosi: mai chiamate modello senza --ping."""
    here = Path(__file__).parent
    print("FABLE-DIRECTOR — doctor executor esterni")
    if not CONFIG_PATH.is_file():
        print(f"""
Config assente: {CONFIG_PATH}

Gli executor esterni spostano batch NON quality-sensitive fuori dalla tua
quota Claude (Claude continua a pianificare e verificare). Due strade GRATUITE:

  1. Hai un account Google?  Chiave API Gemini gratuita:
     https://aistudio.google.com/apikey
     Free tier con limiti che si RESETTANO OGNI GIORNO: un giorno senza
     chiamate = capacita gratuita persa.
  2. Hai un account ChatGPT? Codex CLI, uso incluso nel piano:
     npm install -g @openai/codex   poi   codex login

Preferisci modelli a pagamento? Stesse voci di config con la tua chiave API
a pagamento — la telemetria confronta gli esiti allo stesso modo.

Setup:
  python3 "{here / 'cross-verify.py'}" --init
  (poi inserisci la chiave nel config o via env var indicata)
Ricontrolla:
  python3 "{here / 'external-exec.py'}" --doctor [--ping]""")
        sys.exit(1)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"config illeggibile: {e}")
        sys.exit(1)
    usage = today_usage()
    problems = 0
    for name, prov in (cfg.get("providers") or {}).items():
        checks = []
        ok = True
        if prov.get("type") == "cli":
            cmd = prov.get("command") or []
            binary = cmd[0] if cmd else None
            if binary and shutil.which(binary):
                checks.append(f"binario '{binary}' presente")
                auth = prov.get("auth_check")
                if auth:
                    try:
                        rc = subprocess.run(auth, capture_output=True,
                                            timeout=20).returncode
                        if rc == 0:
                            checks.append("login OK")
                        else:
                            ok = False
                            checks.append(f"login MANCANTE (exit {rc}) — "
                                          f"esegui: {' '.join(auth[:1])} login")
                    except Exception as e:
                        checks.append(f"auth_check non eseguibile ({e})")
                else:
                    checks.append("auth_check non configurato nel provider "
                                  "(opzionale: es. [\"codex\",\"login\","
                                  "\"status\"])")
            else:
                ok = False
                checks.append(f"binario '{binary}' NON installato "
                              f"({prov.get('note', '')})")
        else:
            key = prov.get("api_key") or os.environ.get(
                prov.get("api_key_env", ""), "")
            if key:
                checks.append("chiave API presente")
            else:
                ok = False
                checks.append(f"chiave API ASSENTE — export "
                              f"{prov.get('api_key_env')}=... o api_key nel config")
        if ping and ok:
            probe = "Reply with exactly: OK"
            try:
                if prov.get("type") == "cli":
                    fd, tmp = tempfile.mkstemp(prefix="xf-ping-", suffix=".txt")
                    os.close(fd)
                    cmd = [a.replace("{output_file}", tmp)
                           for a in prov["command"]]
                    p = subprocess.run(cmd, input=probe.encode(), timeout=90,
                                       capture_output=True)
                    resp = (Path(tmp).read_text(errors="replace").strip()
                            or p.stdout.decode(errors="replace").strip())
                    os.unlink(tmp)
                    if p.returncode != 0:
                        raise RuntimeError(
                            f"exit {p.returncode}: "
                            f"{p.stderr.decode(errors='replace')[:120]}")
                else:
                    body = json.dumps({"model": prov["model"], "messages": [
                        {"role": "user", "content": probe}]}).encode()
                    key = prov.get("api_key") or os.environ.get(
                        prov.get("api_key_env", ""), "")
                    req = urllib.request.Request(
                        prov["base_url"].rstrip("/") + "/chat/completions",
                        data=body, headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {key}"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        resp = json.loads(r.read().decode(errors="replace")
                                          )["choices"][0]["message"]["content"]
                checks.append(f"ping OK ({str(resp).strip()[:40]!r})")
            except Exception as e:
                ok = False
                checks.append(f"ping FALLITO: {str(e)[:160]}")
        rpd = (prov.get("limits") or {}).get("rpd")
        used = usage.get(name, 0)
        checks.append(f"oggi {used} chiamate"
                      + (f" / {rpd} rpd dichiarati" if rpd else ""))
        if used == 0 and ok:
            checks.append("free tier del giorno INUTILIZZATO (reset "
                          "giornaliero: capacita persa se non usata)")
        mark = "OK " if ok else "FAIL"
        problems += 0 if ok else 1
        print(f"[{mark}] {name} ({prov.get('model', '?')}): "
              + " · ".join(checks))
    print(f"\nesito: {'tutto configurato' if not problems else str(problems) + ' provider da sistemare'}"
          + ("" if ping else " (statico — aggiungi --ping per una verifica live, 1 richiesta per provider)"))
    sys.exit(0 if not problems else 1)


def parse_args(argv):
    opts = {"--spec": None, "--spec-file": None, "--out": None,
            "--provider": None, "--type": None, "--items": None,
            "--timeout": "120"}
    inputs = []
    flags = {"--schema-json": False, "--allow-truncate": False,
             "--doctor": False, "--ping": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--input" and i + 1 < len(argv):
            inputs.append(argv[i + 1])
            i += 2
        elif a in flags:
            flags[a] = True
            i += 1
        elif a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
            i += 2
        else:
            sys.exit(f"argomento non riconosciuto: {a}\n{__doc__}")
    return opts, inputs, flags


def call_http(prov, name, api_key, user_msg, timeout):
    body = json.dumps({
        "model": prov["model"],
        "messages": [{"role": "system", "content": EXEC_SYSTEM},
                     {"role": "user", "content": user_msg}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        prov["base_url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        unavailable(f"HTTP {e.code} da {name} (rate limit/endpoint cambiato?)")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unavailable(f"rete/timeout verso {name}: {e}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def call_cli(prov, name, user_msg, timeout):
    """Sottoprocesso (es. Codex CLI): preflight, spec via stdin, output su
    mktemp unico, timeout — stessa disciplina di cross-verify.py."""
    cmd_template = prov.get("command") or []
    if not cmd_template or not shutil.which(cmd_template[0]):
        unavailable(f"CLI '{cmd_template[0] if cmd_template else '?'}' non "
                    f"installata per '{name}' ({prov.get('note', '')})")
    fd, out_file = tempfile.mkstemp(prefix="external-exec-", suffix=".txt")
    os.close(fd)
    cmd = [a.replace("{output_file}", out_file) for a in cmd_template]
    spec = f"{EXEC_SYSTEM}\n\n{user_msg}"
    try:
        proc = subprocess.run(cmd, input=spec.encode(), timeout=timeout,
                              capture_output=True)
        if proc.returncode != 0:
            unavailable(f"CLI '{name}' exit {proc.returncode}: "
                        f"{proc.stderr.decode(errors='replace')[:200]}")
        content = Path(out_file).read_text(errors="replace")
        return content if content.strip() else proc.stdout.decode(errors="replace")
    except subprocess.TimeoutExpired:
        unavailable(f"CLI '{name}' timeout ({timeout}s)")
    finally:
        try:
            os.unlink(out_file)
        except OSError:
            pass


def main():
    opts, inputs, flags = parse_args(sys.argv[1:])
    if flags["--doctor"]:
        doctor(ping=flags["--ping"])
    spec_text = opts["--spec"]
    if opts["--spec-file"]:
        try:
            spec_text = Path(opts["--spec-file"]).read_text(errors="replace")
        except OSError as e:
            sys.exit(f"spec-file illeggibile: {e}")
    if not spec_text or not spec_text.strip():
        sys.exit(__doc__)

    require_open_budget()

    if not CONFIG_PATH.is_file():
        unavailable(f"config assente ({CONFIG_PATH}): cross-verify.py --init")
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        unavailable(f"config illeggibile: {e}")
    name = opts["--provider"] or cfg.get("default")
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        unavailable(f"provider '{name}' non definito nel config")
    is_cli = prov.get("type") == "cli"
    api_key = ""
    if not is_cli:
        api_key = prov.get("api_key") or os.environ.get(prov.get("api_key_env", ""), "")
        if not api_key:
            unavailable(f"chiave API assente per '{name}' "
                        f"(export {prov.get('api_key_env')}=... o api_key nel config)")

    user_msg = f"TASK SPEC:\n{spec_text}\n"
    for fpath in inputs:
        try:
            content = Path(fpath).read_text(errors="replace")
        except OSError as e:
            unavailable(f"input illeggibile ({fpath}): {e}")
        if len(content) > INPUT_CAP:
            # Fail-closed: un deliverable calcolato su input troncato in
            # silenzio è il peggior esito possibile (review cross-family
            # 2026-07-10). Troncare è una scelta del chiamante, mai dello script.
            if not flags["--allow-truncate"]:
                out("error", name, prov["model"], "input-oversize", "-",
                    f"{fpath}: {len(content)} char > cap {INPUT_CAP} — spezza "
                    f"l'input o passa --allow-truncate (esplicito)")
                sys.exit(1)
            content = content[:INPUT_CAP]
            user_msg += f"\n[NOTA: {fpath} TRONCATO a {INPUT_CAP} char su richiesta]\n"
        user_msg += f"\nINPUT FILE {fpath}:\n{content}\n"
    if flags["--schema-json"]:
        user_msg += ("\nOUTPUT FORMAT: strict JSON only — a single valid JSON "
                     "document, no code fences, no trailing text.\n")

    timeout = int(opts["--timeout"])
    try:
        ACTIVE_PATH.write_text(json.dumps(
            {"provider": name, "pid": os.getpid(),
             "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    except OSError:
        pass
    try:
        if is_cli:
            content = call_cli(prov, name, user_msg, timeout)
        else:
            content = call_http(prov, name, api_key, user_msg, timeout)
    finally:
        try:
            ACTIVE_PATH.unlink()
        except OSError:
            pass

    if content is None or not str(content).strip():
        out("error", name, prov["model"], detail="risposta vuota dal provider")
        log_exec({"provider": name, "model": prov["model"],
                  "type": opts.get("--type"), "ok": False, "check": "empty",
                  "chars_in": len(user_msg)})
        sys.exit(1)
    content = str(content).strip()

    if content.startswith("NEEDS_CONTEXT"):
        out("needs_context", name, prov["model"],
            detail=content[:300])
        log_exec({"provider": name, "model": prov["model"],
                  "type": opts.get("--type"), "ok": False,
                  "check": "needs_context", "chars_in": len(user_msg)})
        sys.exit(2)

    check = "raw"
    if flags["--schema-json"]:
        candidate = content.strip().strip("`").strip()
        if candidate[:4].lower() == "json":
            candidate = candidate[4:].strip()
        try:
            json.loads(candidate)
            content = candidate
            check = "json-valid"
        except json.JSONDecodeError as e:
            out("error", name, prov["model"], "json-invalid", "-",
                f"output fuori schema JSON ({e}) — NON consegnare a valle, "
                f"retry o rotta Claude")
            log_exec({"provider": name, "model": prov["model"],
                      "type": opts.get("--type"), "ok": False,
                      "check": "json-invalid", "chars_in": len(user_msg)})
            sys.exit(1)

    dest = "-"
    if opts["--out"]:
        try:
            Path(opts["--out"]).write_text(content)
            dest = opts["--out"]
        except OSError as e:
            out("error", name, prov["model"], check, "-",
                f"scrittura --out fallita: {e}")
            sys.exit(1)
    out("ok", name, prov["model"], check, dest,
        f"{len(content)} char (~{len(content) // 4} token)")
    if dest == "-":
        print("---")
        print(content)
    log_exec({"provider": name, "model": prov["model"],
              "type": opts.get("--type"),
              "items": int(opts["--items"]) if opts["--items"] else None,
              "ok": True, "check": check, "chars_out": len(content),
              "chars_in": len(user_msg)})
    sys.exit(0)


if __name__ == "__main__":
    main()

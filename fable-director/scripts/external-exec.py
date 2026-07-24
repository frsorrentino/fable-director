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
  Con --schema-file lo schema è enforced anche lato provider (schema_args nel
  config, es. --output-schema di Codex) + ricontrollo locale delle chiavi
  required top-level: CHECK schema-valid|schema-invalid.
- Il wrapper persiste il deliverable (--out); il provider CLI resta read-only
  (config cross-family: codex gira --sandbox read-only — l'esterno non scrive
  MAI nel repo, scrive solo questo wrapper dove gli dici tu).

Config: riusa ~/.claude/fable-director/cross-family.json (cross-verify.py
--init per crearla). Il template CLI usa placeholder {model}/{effort} con
default dai campi omonimi del provider: --model/--effort li sovrascrivono
a runtime (batch massivi → --effort low; il verify resta high) senza voci
duplicate. Un override senza placeholder nel template è errore rumoroso,
mai ignorato. Timeout: --timeout, poi campo "timeout" del provider, poi 120.

--resume-last (solo provider CLI con "resume_command" nel config): continua
l'ultimo thread Codex di QUESTA directory (--last filtra per cwd) inviando
solo l'istruzione delta — retry dopo needs_context/json-invalid a frazione
del costo della spec intera. SOLO retry sequenziale immediato: in batch
paralleli riprenderebbe il thread sbagliato. Pattern distillato da
openai/codex-plugin-cc (review 2026-07-13).

Uso:
  external-exec.py --spec-file F | --spec "..."
                   [--input FILE]... [--out FILE] [--schema-json]
                   [--schema-file SCHEMA.json] [--resume-last] [--paid-ok]
                   [--provider gemini|gemini-stable|codex] [--type SLUG]
                   [--model M] [--effort low|medium|high|...]
                   [--items N] [--timeout N] [--allow-truncate]
  external-exec.py --doctor [--ping]   # setup guidato / diagnosi provider

--doctor: nessun budget richiesto (zero chiamate modello senza --ping).
Config assente → istruzioni onboarding: account Google → chiave Gemini free
tier (reset giornaliero); account ChatGPT → Codex CLI login (uso incluso nel
piano); oppure chiavi API a pagamento nelle stesse voci di config. Config
presente → checklist per provider (binario/chiave/auth_check) + uso odierno
vs limits.rpd dal config. --ping aggiunge una chiamata reale minima per
provider (consuma 1 richiesta di quota ciascuna: opt-in).

--paid-ok: obbligatorio per provider con "billing" diverso da "free" nel
config (campo assente = paid, fail-closed). Va passato SOLO dopo consenso
esplicito dell'utente nella conversazione corrente — mai preventivamente,
mai "per efficienza". I provider free ignorano il flag.

Pre-budget OBBLIGATORIO e verificato qui (il gate PreToolUse non vede le
chiamate Bash): senza budget open per il cwd lo script esce con errore.
Input oltre il cap → errore esplicito; il troncamento è solo opt-in
(--allow-truncate) e viene dichiarato al modello nel prompt.

Provider "type": "image" (es. gemini-image): la spec È il prompt; endpoint
nativo generateContent, bytes su --out (OBBLIGATORIO — binario mai su
stdout, perimetro scrittura del budget rispettato). Incompatibili e
rumorosi: --schema-*, --effort, --resume-last, --allow-truncate, --input
(v1 text-to-image puro). 429 "limit: 0" = billing non abilitato sul
progetto Google, messaggio dedicato.

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

# Contratto a blocchi XML: sulla famiglia GPT/Codex i blocchi con tag stabili
# tengono meglio della prosa (pattern distillato da openai/codex-plugin-cc,
# skill gpt-5-4-prompting). Il contenuto è lo stesso contratto di prima.
EXEC_SYSTEM = (
    "<role>You are a batch executor. You receive a complete task spec "
    "(Objective / Files / Interfaces / Constraints / Verification) and you "
    "execute it VERBATIM.</role>\n"
    "<output_contract>Reply with ONLY the deliverable in the exact format "
    "the spec requires — no preamble, no commentary, no code fences unless "
    "the spec asks for them.</output_contract>\n"
    "<missing_context_gating>If the spec cannot be executed without context "
    "you do not have, reply with exactly 'NEEDS_CONTEXT: <what is missing>' "
    "and nothing else.</missing_context_gating>\n"
    "<grounding_rules>Never invent data to fill gaps: an honest "
    "NEEDS_CONTEXT beats a plausible-but-wrong deliverable."
    "</grounding_rules>"
)


def out(status, provider="-", model="-", check="-", output="-", detail="-"):
    print(f"STATUS: {status}")
    print(f"PROVIDER: {provider} ({model})")
    print(f"CHECK: {check}")
    print(f"OUTPUT: {output}")
    print(f"DETAIL: {detail}")


def unavailable(reason):
    out("unavailable", detail=(
        f"{reason} — run on the normal Claude route (standard axis 4). "
        f"NEVER treat unavailable as executed."))
    sys.exit(1)


def billing_of(prov):
    """Fail-closed: billing non dichiarato = paid — mai proposto né
    eseguito senza consenso esplicito. La policy vive nel campo del
    config, mai in euristiche sul nome del provider."""
    return "free" if prov.get("billing") == "free" else "paid"


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
            "no open pre-budget for this cwd: the external route is a "
            "delegation too. Open the budget and retry: fd-telemetry.py "
            "budget-open --task \"...\" --expected-output N --route external "
            "[--effort low] [--type slug]"))
        sys.exit(1)
    # Stesso orizzonte 24h del gate pre-delega: un budget aperto ieri non
    # autorizza disclosure esterna oggi (review esterna 2026-07-11).
    try:
        dt = datetime.fromisoformat(
            str(budget.get("declared_at")).replace("Z", "+00:00"))
        stale = (datetime.now(timezone.utc) - dt).total_seconds() > 86400
    except (ValueError, TypeError):
        stale = True
    if stale:
        out("error", detail=(
            "the open budget is older than 24h or has no valid declared_at "
            "(abandoned task). Close it (budget-close --outcome abandoned) "
            "and open a fresh pre-budget — same horizon as the delegation "
            "gate."))
        sys.exit(1)
    # data-class restricted: il confine privacy del README (la rotta esterna
    # è disclosure verso terzi) reso enforceable, non solo dichiarato.
    if budget.get("data_class") == "restricted":
        out("error", detail=(
            "external route BLOCKED: the budget declares --data-class "
            "restricted (inputs must not leave the machine). Run on the "
            "normal Claude route."))
        sys.exit(1)
    return budget


def check_out_perimeter(budget, out_path):
    """--out scrive dove il chiamante dice: deve rispettare lo STESSO
    perimetro delle Write del modello — never_write sempre, paths del
    budget dentro il progetto (review esterna 2026-07-11). Realpath contro
    i symlink, stessa semantica di perimeter-gate.py."""
    import fnmatch
    ap = os.path.realpath(out_path).replace("\\", "/")
    try:
        rel = os.path.relpath(
            ap, os.path.realpath(os.getcwd())).replace("\\", "/")
        inside = not rel.startswith("..")
    except ValueError:
        rel, inside = ap, False
    base = Path(rel).name

    def hits(pats):
        return any(
            fnmatch.fnmatch(rel, str(p).replace("\\", "/"))
            or fnmatch.fnmatch(ap, str(p).replace("\\", "/"))
            or fnmatch.fnmatch(base, str(p).replace("\\", "/"))
            for p in pats)

    nw = []
    for cf in (Path(os.getcwd()) / ".fd-perimeter.json",
               Path.home() / ".claude" / "fable-director" / "perimeter.json"):
        if cf.is_file():
            try:
                nw += list(json.loads(cf.read_text()).get("never_write") or [])
            except (json.JSONDecodeError, OSError):
                pass
    if nw and hits(nw):
        out("error", detail=(f"--out '{out_path}' matches a never_write "
                             f"pattern — refused, pick another destination"))
        sys.exit(1)
    paths = (budget or {}).get("paths") or []
    if inside and paths and not hits(paths):
        out("error", detail=(f"--out '{out_path}' is outside the budget's "
                             f"declared perimeter ({', '.join(map(str, paths))})"
                             f" — budget-amend first or write elsewhere"))
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


def doctor(ping=False, paid_ok=False):
    """Setup guidato + diagnosi: mai chiamate modello senza --ping."""
    here = Path(__file__).parent
    print("FABLE-DIRECTOR — external executors doctor")
    if not CONFIG_PATH.is_file():
        print(f"""
Config missing: {CONFIG_PATH}

External executors move NON-quality-sensitive batches off your Claude quota
(Claude keeps planning and verifying). Two FREE paths:

  1. Got a Google account?  Free Gemini API key:
     https://aistudio.google.com/apikey
     Free-tier limits RESET EVERY DAY: a day without calls is free
     capacity lost.
  2. Got a ChatGPT account? Codex CLI, usage included in your plan:
     npm install -g @openai/codex   then   codex login

Prefer paid models? Same config entries with your paid API key and
"billing": "paid" — consent-gated (--paid-ok), never auto-proposed.

Setup:
  python3 "{here / 'cross-verify.py'}" --init
  (then put the key in the config or the indicated env var)
Re-check:
  python3 "{here / 'external-exec.py'}" --doctor [--ping]""")
        sys.exit(1)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"config unreadable: {e}")
        sys.exit(1)
    usage = today_usage()
    problems = 0
    for name, prov in (cfg.get("providers") or {}).items():
        checks = []
        ok = True
        if "billing" not in prov:
            ok = False
            checks.append('billing UNDECLARED → treated as PAID '
                          '(fail-closed): add "billing": "free"|"paid" '
                          'to the config entry')
        elif billing_of(prov) == "free":
            checks.append("billing: free")
        else:
            checks.append("billing: PAID"
                          + (f" ({prov['cost_note']})"
                             if prov.get("cost_note") else ""))
        if prov.get("type") == "cli":
            cmd = prov.get("command") or []
            binary = cmd[0] if cmd else None
            if binary and shutil.which(binary):
                checks.append(f"binary '{binary}' present")
                auth = prov.get("auth_check")
                if auth:
                    try:
                        rc = subprocess.run(auth, capture_output=True,
                                            timeout=20).returncode
                        if rc == 0:
                            checks.append("login OK")
                        else:
                            ok = False
                            checks.append(f"login MISSING (exit {rc}) — "
                                          f"run: {' '.join(auth[:1])} login")
                    except Exception as e:
                        checks.append(f"auth_check not runnable ({e})")
                else:
                    checks.append("auth_check not configured for provider "
                                  "(optional, e.g. [\"codex\",\"login\","
                                  "\"status\"])")
            else:
                ok = False
                checks.append(f"binary '{binary}' NOT installed "
                              f"({prov.get('note', '')})")
        else:
            key = prov.get("api_key") or os.environ.get(
                prov.get("api_key_env", ""), "")
            if key:
                checks.append("API key present")
            else:
                ok = False
                checks.append(f"API key MISSING — export "
                              f"{prov.get('api_key_env')}=... or api_key in config")
        if ping and ok and billing_of(prov) != "free" and not paid_ok:
            checks.append("ping SKIPPED (billed provider — costs real "
                          "money; add --paid-ok to consent)")
        elif ping and ok and prov.get("type") == "image":
            try:
                key = prov.get("api_key") or os.environ.get(
                    prov.get("api_key_env", ""), "")
                req = urllib.request.Request(
                    prov["base_url"].rstrip("/") + "/models?pageSize=200",
                    headers={"x-goog-api-key": key})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode(errors="replace"))
                model = prov.get("model", "")
                found = any(
                    m.get("name") == model or m.get("name", "").endswith("/" + model)
                    for m in data.get("models", []))
                if found:
                    checks.append(f"models-list OK ('{prov['model']}' present)")
                else:
                    ok = False
                    checks.append(f"models-list FAILED: model '{model}' not in list")
            except Exception as e:
                ok = False
                checks.append(f"ping FAILED: {str(e)[:160]}")
        elif ping and ok:
            probe = "Reply with exactly: OK"
            try:
                if prov.get("type") == "cli":
                    fd, tmp = tempfile.mkstemp(prefix="xf-ping-", suffix=".txt")
                    os.close(fd)
                    cmd = [a.replace("{output_file}", tmp)
                            .replace("{model}", prov.get("model", ""))
                            .replace("{effort}", prov.get("effort", "high"))
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
                checks.append(f"ping FAILED: {str(e)[:160]}")
        rpd = (prov.get("limits") or {}).get("rpd")
        used = usage.get(name, 0)
        checks.append(f"today {used} calls"
                      + (f" / {rpd} declared rpd" if rpd else ""))
        if used == 0 and ok and billing_of(prov) == "free":
            checks.append("today's free tier UNUSED (daily reset: unused "
                          "capacity is lost)")
        mark = "OK " if ok else "FAIL"
        problems += 0 if ok else 1
        print(f"[{mark}] {name} ({prov.get('model', '?')}): "
              + " · ".join(checks))
    print(f"\nresult: {'all configured' if not problems else str(problems) + ' provider(s) to fix'}"
          + ("" if ping else " (static — add --ping for a live check, 1 request per provider)"))
    sys.exit(0 if not problems else 1)


def parse_args(argv):
    opts = {"--spec": None, "--spec-file": None, "--out": None,
            "--provider": None, "--type": None, "--items": None,
            "--timeout": None, "--schema-file": None, "--model": None,
            "--effort": None}
    inputs = []
    flags = {"--schema-json": False, "--allow-truncate": False,
             "--doctor": False, "--ping": False, "--resume-last": False,
             "--paid-ok": False}
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
            sys.exit(f"unrecognized argument: {a}\n{__doc__}")
    return opts, inputs, flags


BILLING_MARKERS = ("billing", "payment required", "credit", "insufficient_quota",
                   "insufficient quota", "no credits", "purchase", "subscription",
                   "upgrade your plan", "enable billing")


def http_failure(e, name, prov, detail=None):
    """Classifica un HTTPError PRIMA di chiamarlo 'rate limit'. Un free tier che
    CHIUDE — finestra promozionale scaduta (è il caso dichiarato di Grok),
    credito esaurito, chiave decaduta — risponde 401/402/403, o 429 con un corpo
    che parla di billing. Trattarlo come quota transitoria fa ritentare una porta
    che non riaprirà, e fa sembrare gratuito ciò che gratuito non è più. Qui il
    provider viene dichiarato non disponibile con la diagnosi giusta e l'evento
    finisce in telemetria: fail-closed, mai un fallback silenzioso. Non ritorna
    mai (unavailable esce 1)."""
    if detail is None:
        try:
            detail = e.read().decode(errors="replace")[:500]
        except Exception:
            detail = ""
    low = (detail or "").lower()
    if e.code in (401, 402, 403) or (
            e.code == 429 and any(m in low for m in BILLING_MARKERS)):
        log_exec({"provider": name, "model": prov.get("model"),
                  "billing": billing_of(prov), "ok": False,
                  "check": "billing-block", "http": e.code,
                  "detail": (detail or "")[:200]})
        unavailable(
            f"[{name}] HTTP {e.code}: access or billing refused, NOT a "
            f"transient quota error — the free window may have closed, or the "
            f"key lost entitlement. Check the provider's CURRENT free tier "
            f"before retrying: fix the key, or declare \"billing\": \"paid\" "
            f"in cross-family.json (consent-gated, needs --paid-ok). "
            f"Detail: {(detail or '')[:180]}")
    unavailable(f"HTTP {e.code} from {name} (rate limit / endpoint changed?)"
                + (f" — {detail[:180]}" if detail else ""))


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
        http_failure(e, name, prov)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unavailable(f"network/timeout towards {name}: {e}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def call_image(prov, name, api_key, prompt, timeout):
    """Provider "type": "image" (famiglia gemini-*-image): endpoint nativo
    generateContent, risposta inlineData base64. Chiave SOLO nell'header
    x-goog-api-key: in query string finirebbe nei log. Ritorna
    (bytes, mime, None) oppure (None, None, testo-del-provider)."""
    import base64
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = (prov["base_url"].rstrip("/")
           + f"/models/{prov['model']}:generateContent")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429 and "limit: 0" in detail:
            unavailable(
                f"image models need billing enabled on the Google project "
                f"(free tier limit is 0) — not a transient quota error "
                f"[{name}]")
        http_failure(e, name, prov, detail)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unavailable(f"network/timeout towards {name}: {e}")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return None, None, None
    for p in parts:
        blob = p.get("inlineData") or p.get("inline_data") or {}
        if blob.get("data"):
            mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
            return base64.b64decode(blob["data"]), mime, None
    text = " ".join(str(p.get("text", "")) for p in parts).strip()
    return None, None, (text[:300] or None)


def load_schema(path):
    """--schema-file: schema illeggibile o non-JSON è errore rumoroso — uno
    schema rotto non deve mai degradare a nessun-controllo."""
    try:
        text = Path(path).read_text(errors="replace")
        return json.loads(text), text
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"unreadable/invalid --schema-file {path}: {e}")


def schema_required_gaps(schema, parsed):
    """Ricontrollo locale minimo (stdlib, niente jsonschema): tipo top-level
    e chiavi 'required'. L'enforcement pieno è lato provider (schema_args,
    es. --output-schema di Codex); questo è la cintura che l'output non sia
    palesemente fuori struttura prima di andare a valle."""
    gaps = []
    t = schema.get("type")
    if t == "object" and not isinstance(parsed, dict):
        gaps.append(f"top-level is {type(parsed).__name__}, schema wants object")
    elif t == "array" and not isinstance(parsed, list):
        gaps.append(f"top-level is {type(parsed).__name__}, schema wants array")
    if isinstance(parsed, dict):
        for key in schema.get("required") or []:
            if key not in parsed:
                gaps.append(f"missing required key '{key}'")
    return gaps


def render_cli_command(prov, name, opts, out_file, schema_path):
    """Rende il template CLI del provider. {model}/{effort} hanno default dai
    campi omonimi del config; --model/--effort li sovrascrivono SOLO se il
    template ha il placeholder (override mai ignorato in silenzio).
    --resume-last usa il template dedicato 'resume_command' (`codex exec
    resume` non accetta le stesse flag di exec: niente --sandbox, read-only
    via -c sandbox_mode). Con --schema-file accoda 'schema_args' rese
    (es. ["--output-schema", "{schema_file}"])."""
    template = (prov.get("resume_command") if opts["--resume-last"]
                else prov.get("command")) or []
    if opts["--resume-last"] and not template:
        out("error", name, prov.get("model", "?"), detail=(
            "--resume-last: provider has no 'resume_command' template in the "
            "config — add it (see cross-verify.py DEFAULT_CONFIG) or run fresh"))
        sys.exit(1)
    if not template or not shutil.which(template[0]):
        unavailable(f"CLI '{template[0] if template else '?'}' not "
                    f"installed for '{name}' ({prov.get('note', '')})")
    joined = " ".join(template)
    model = opts["--model"] or prov.get("model", "")
    effort = opts["--effort"] or prov.get("effort", "")
    for flag, placeholder in (("--model", "{model}"), ("--effort", "{effort}")):
        if opts[flag] and placeholder not in joined:
            out("error", name, prov.get("model", "?"), detail=(
                f"{flag} requested but the template in use for '{name}' has "
                f"no {placeholder} placeholder — update the config entry, an "
                f"override is never silently ignored"))
            sys.exit(1)
    for placeholder, value, field in (("{model}", model, "model"),
                                      ("{effort}", effort, "effort")):
        if placeholder in joined and not value:
            out("error", name, prov.get("model", "?"), detail=(
                f"template uses {placeholder} but the provider has no "
                f"'{field}' field and no CLI override was given"))
            sys.exit(1)
    cmd = [a.replace("{output_file}", out_file)
            .replace("{model}", model)
            .replace("{effort}", effort) for a in template]
    if schema_path:
        schema_args = prov.get("schema_args")
        if not schema_args:
            out("error", name, prov.get("model", "?"), detail=(
                "--schema-file: provider has no 'schema_args' in the config "
                "(e.g. [\"--output-schema\", \"{schema_file}\"]) — add it or "
                "fall back to --schema-json (JSON validity only)"))
            sys.exit(1)
        cmd += [a.replace("{schema_file}", schema_path) for a in schema_args]
    return cmd


def call_cli(prov, name, user_msg, timeout, opts, schema_path):
    """Sottoprocesso (es. Codex CLI): preflight, spec via stdin, output su
    mktemp unico, timeout — stessa disciplina di cross-verify.py."""
    fd, out_file = tempfile.mkstemp(prefix="external-exec-", suffix=".txt")
    os.close(fd)
    spec = f"{EXEC_SYSTEM}\n\n{user_msg}"
    try:
        cmd = render_cli_command(prov, name, opts, out_file, schema_path)
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
    # render_cli_command legge tutto da opts: allinea il flag booleano.
    opts["--resume-last"] = flags["--resume-last"]
    if flags["--doctor"]:
        doctor(ping=flags["--ping"], paid_ok=flags["--paid-ok"])
    spec_text = opts["--spec"]
    if opts["--spec-file"]:
        try:
            spec_text = Path(opts["--spec-file"]).read_text(errors="replace")
        except OSError as e:
            sys.exit(f"unreadable spec-file: {e}")
    if not spec_text or not spec_text.strip():
        sys.exit(__doc__)

    budget = require_open_budget()
    if opts["--out"]:
        check_out_perimeter(budget, opts["--out"])

    if not CONFIG_PATH.is_file():
        unavailable(f"config missing ({CONFIG_PATH}): cross-verify.py --init")
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        unavailable(f"config unreadable: {e}")
    name = opts["--provider"] or cfg.get("default")
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        unavailable(f"provider '{name}' not defined in config")
    if billing_of(prov) != "free" and not flags["--paid-ok"]:
        log_exec({"provider": name, "model": prov.get("model", "?"),
                  "billing": "paid", "ok": False, "check": "paid-refused"})
        unavailable(
            f"provider '{name}' is billed"
            + (f" ({prov['cost_note']})" if prov.get("cost_note") else "")
            + " — requires explicit user consent in this conversation; "
              "re-run with --paid-ok ONLY after the user agreed")
    is_image = prov.get("type") == "image"
    if is_image:
        for bad, val in (("--schema-json", flags["--schema-json"]),
                         ("--schema-file", opts["--schema-file"]),
                         ("--effort", opts["--effort"]),
                         ("--resume-last", flags["--resume-last"]),
                         ("--allow-truncate", flags["--allow-truncate"]),
                         ("--input", inputs)):
            if val:
                out("error", name, prov.get("model", "?"), detail=(
                    f"{bad} is not supported for image providers "
                    f"(v1 is text-to-image only)"))
                sys.exit(1)
        if not opts["--out"]:
            out("error", name, prov.get("model", "?"), detail=(
                "--out FILE is required for image providers (binary "
                "output never goes to stdout)"))
            sys.exit(1)
    is_cli = prov.get("type") == "cli"
    # --model override: vale per entrambe le rotte (HTTP: modello nel body;
    # CLI: placeholder {model}) e la riga PROVIDER riporta il modello
    # EFFETTIVO, non il default del config — telemetria inclusa.
    if opts["--model"]:
        prov = {**prov, "model": opts["--model"]}
    api_key = ""
    if not is_cli:
        # --effort/--resume-last sono semantiche CLI-only → errore, mai
        # ignorati in silenzio.
        if not is_image and opts["--effort"]:
            out("error", name, prov.get("model", "?"), detail=(
                "--effort is only supported for CLI providers with an "
                "{effort} placeholder in the command template"))
            sys.exit(1)
        if not is_image and flags["--resume-last"]:
            out("error", name, prov.get("model", "?"), detail=(
                "--resume-last is only supported for CLI providers with a "
                "'resume_command' template in the config"))
            sys.exit(1)
        api_key = prov.get("api_key") or os.environ.get(prov.get("api_key_env", ""), "")
        if not api_key:
            unavailable(f"API key missing for '{name}' "
                        f"(export {prov.get('api_key_env')}=... or api_key in config)")

    schema_obj, schema_text, schema_path = None, None, None
    if opts["--schema-file"]:
        schema_path = str(Path(opts["--schema-file"]).resolve())
        schema_obj, schema_text = load_schema(schema_path)

    user_msg = f"TASK SPEC:\n{spec_text}\n"
    for fpath in inputs:
        try:
            content = Path(fpath).read_text(errors="replace")
        except OSError as e:
            unavailable(f"unreadable input ({fpath}): {e}")
        if len(content) > INPUT_CAP:
            # Fail-closed: un deliverable calcolato su input troncato in
            # silenzio è il peggior esito possibile (review cross-family
            # 2026-07-10). Troncare è una scelta del chiamante, mai dello script.
            if not flags["--allow-truncate"]:
                out("error", name, prov["model"], "input-oversize", "-",
                    f"{fpath}: {len(content)} chars > cap {INPUT_CAP} — split "
                    f"the input or pass --allow-truncate (explicit)")
                sys.exit(1)
            content = content[:INPUT_CAP]
            user_msg += f"\n[NOTE: {fpath} TRUNCATED to {INPUT_CAP} chars on request]\n"
        user_msg += f"\nINPUT FILE {fpath}:\n{content}\n"
    if schema_text:
        user_msg += ("\nOUTPUT FORMAT: strict JSON only — a single valid JSON "
                     "document matching this JSON Schema, no code fences, no "
                     f"trailing text:\n{schema_text}\n")
    elif flags["--schema-json"]:
        user_msg += ("\nOUTPUT FORMAT: strict JSON only — a single valid JSON "
                     "document, no code fences, no trailing text.\n")

    # Risoluzione timeout: flag esplicita > campo del provider > default 120.
    timeout = int(opts["--timeout"] or prov.get("timeout") or 120)
    try:
        ACTIVE_PATH.write_text(json.dumps(
            {"provider": name, "pid": os.getpid(),
             "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    except OSError:
        pass
    try:
        if is_image:
            img_bytes, img_mime, img_text = call_image(
                prov, name, api_key, spec_text, timeout)
        elif is_cli:
            content = call_cli(prov, name, user_msg, timeout, opts, schema_path)
        else:
            content = call_http(prov, name, api_key, user_msg, timeout)
    finally:
        try:
            ACTIVE_PATH.unlink()
        except OSError:
            pass

    if is_image:
        base_log = {"provider": name, "model": prov["model"],
                    "billing": billing_of(prov),
                    "type": opts.get("--type"), "chars_in": len(spec_text)}
        if img_bytes is None:
            out("error", name, prov["model"], detail=(
                "no image in provider response"
                + (f" — provider said: {img_text}" if img_text else "")))
            log_exec({**base_log, "ok": False, "check": "no-image"})
            sys.exit(1)
        try:
            Path(opts["--out"]).write_bytes(img_bytes)
        except OSError as e:
            out("error", name, prov["model"], "image", "-",
                f"--out write failed: {e}")
            sys.exit(1)
        out("ok", name, prov["model"], "image", opts["--out"],
            f"{img_mime}, {len(img_bytes)} bytes")
        log_exec({**base_log, "ok": True, "check": "image",
                  "bytes_out": len(img_bytes)})
        sys.exit(0)

    base_log = {"provider": name, "model": prov["model"],
                "billing": billing_of(prov),
                "type": opts.get("--type"), "resume": flags["--resume-last"],
                "chars_in": len(user_msg)}
    if content is None or not str(content).strip():
        out("error", name, prov["model"], detail="empty response from provider")
        log_exec({**base_log, "ok": False, "check": "empty"})
        sys.exit(1)
    content = str(content).strip()

    if content.startswith("NEEDS_CONTEXT"):
        out("needs_context", name, prov["model"],
            detail=content[:300])
        log_exec({**base_log, "ok": False, "check": "needs_context"})
        sys.exit(2)

    check = "raw"
    if schema_obj is not None or flags["--schema-json"]:
        candidate = content.strip().strip("`").strip()
        if candidate[:4].lower() == "json":
            candidate = candidate[4:].strip()
        try:
            parsed = json.loads(candidate)
            content = candidate
            check = "json-valid"
        except json.JSONDecodeError as e:
            out("error", name, prov["model"], "json-invalid", "-",
                f"output violates JSON schema ({e}) — do NOT hand downstream, "
                f"retry (--resume-last on CLI providers sends only the delta) "
                f"or Claude route")
            log_exec({**base_log, "ok": False, "check": "json-invalid"})
            sys.exit(1)
        if schema_obj is not None:
            gaps = schema_required_gaps(schema_obj, parsed)
            if gaps:
                out("error", name, prov["model"], "schema-invalid", "-",
                    f"output violates --schema-file ({'; '.join(gaps[:5])}) — "
                    f"do NOT hand downstream, retry (--resume-last sends only "
                    f"the delta) or Claude route")
                log_exec({**base_log, "ok": False, "check": "schema-invalid"})
                sys.exit(1)
            check = "schema-valid"

    dest = "-"
    if opts["--out"]:
        try:
            Path(opts["--out"]).write_text(content)
            dest = opts["--out"]
        except OSError as e:
            out("error", name, prov["model"], check, "-",
                f"--out write failed: {e}")
            sys.exit(1)
    out("ok", name, prov["model"], check, dest,
        f"{len(content)} char (~{len(content) // 4} token)")
    if dest == "-":
        print("---")
        print(content)
    log_exec({**base_log,
              "items": int(opts["--items"]) if opts["--items"] else None,
              "ok": True, "check": check, "chars_out": len(content)})
    sys.exit(0)


if __name__ == "__main__":
    main()

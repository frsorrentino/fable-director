#!/usr/bin/env python3
"""Cross-family verifier — controllo avversariale da una famiglia di modelli diversa.

Un ensemble tutto-Claude condivide errori CORRELATI by construction; un lineage
diverso (Gemini, DeepSeek) ha punti ciechi non sovrapposti. Questo script è il
gradino opzionale in cima alla verification ladder (rung 3+): claim ad alta
posta, raro, fuori dalla quota Claude.

Principi (da fable-advisor, mantenuti):
- NO SILENT FALLBACK: config assente, chiave assente, endpoint giù, rate limit
  → STATUS: unavailable con istruzione esplicita di degradare al verifier
  same-family a contesto fresco. MAI trattare unavailable come "verificato".
- Il verifier vede SOLO artefatto + rubrica, mai il reasoning del maker
  (maker ≠ grader è strutturale).
- Prompt avversariale: prova a CONFUTARE; in dubbio → refuted.

Config: ~/.claude/fable-director/cross-family.json (creala con --init).
URL e modelli vivono nel config, non nel codice: i free tier cambiano senza
preavviso — il punto di verità deve essere modificabile senza toccare lo script.
Chiavi API: via env var (campo api_key_env) o campo api_key nel config.

Provider: HTTP OpenAI-compatibile (gemini, deepseek) o "type": "cli"
(codex: sottoprocesso Codex CLI, login ChatGPT — spec via stdin, output su
file mktemp unico, preflight `command -v`).

Uso:
  cross-verify.py --init
  cross-verify.py --usage        # contatore locale vs limiti dichiarati (i
                                 # free tier non espongono la quota via API)
  cross-verify.py --claim "..." --rubric "..." [--context-file F]
                  [--provider gemini|deepseek|codex] [--timeout 60]

Output (grep-abile):
  STATUS: ok|unavailable|error
  PROVIDER: <provider> (<model>)
  VERDICT: refuted|supported|uncertain
  REASONING: <breve>
Exit code: 0 solo su STATUS ok. Zero dipendenze: solo stdlib.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "fable-director" / "cross-family.json"
DB_PATH = Path.home() / ".claude" / "fable-director" / "telemetry.db"
# Marker "chiamata cross-family in corso" per il segmento [XF] dello statusline
ACTIVE_PATH = Path.home() / ".claude" / "fable-director" / "xfam-active.json"

DEFAULT_CONFIG = {
    "default": "gemini",
    "providers": {
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "limits": {"rpd": 1500, "rpm": 10},
            "note": "free tier AI Studio — la subscription consumer AI Pro NON alimenta l'API"
        },
        "deepseek": {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-v4-flash:free",
            "api_key_env": "OPENROUTER_API_KEY",
            "limits": {"rpd": 100},
            "note": "OpenRouter free tier ~100 req/giorno per chiave"
        },
        "codex": {
            "type": "cli",
            "command": ["codex", "exec", "--model", "gpt-5.5",
                        "-c", "model_reasoning_effort=high",
                        "--sandbox", "read-only", "--skip-git-repo-check",
                        "--output-last-message", "{output_file}"],
            "model": "gpt-5.5",
            "note": "richiede Codex CLI installata + login ChatGPT (quota finestra 5h del piano; nessuna API di lettura quota)"
        }
    }
}

VERIFIER_SYSTEM = (
    "You are an independent adversarial verifier from a different model family. "
    "You see ONLY the artifact and the rubric below — you have no access to the "
    "maker's reasoning and no stake in its conclusions. Try to REFUTE the claim. "
    "If the evidence provided is insufficient to support it, verdict is 'refuted' "
    "or 'uncertain', never 'supported'. Reply with STRICT JSON only: "
    '{"verdict": "refuted|supported|uncertain", "reasoning": "<max 120 words>"}'
)


def out(status, provider="-", model="-", verdict="-", reasoning="-"):
    print(f"STATUS: {status}")
    print(f"PROVIDER: {provider} ({model})")
    print(f"VERDICT: {verdict}")
    print(f"REASONING: {reasoning}")


def unavailable(reason):
    out("unavailable", reasoning=(
        f"{reason} — degrade to the same-family fresh-context verifier "
        f"(verification ladder rung 3). NEVER treat 'unavailable' as verified."))
    sys.exit(1)


def log_verification(payload):
    """Best-effort: telemetria oggettiva, mai bloccante."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event("verification", payload)
    except Exception:
        pass


def cmd_usage():
    """Contatore LOCALE per provider vs limiti dichiarati nel config.
    I free tier non espongono la quota via API (né Gemini né Codex/ChatGPT):
    questo conta le chiamate loggate in telemetria da QUESTA macchina — se la
    chiave è usata anche altrove, sottostima. Il limite vero resta rumoroso
    comunque: HTTP 429 → STATUS unavailable."""
    if not CONFIG_PATH.is_file():
        sys.exit(f"config assente: cross-verify.py --init")
    cfg = json.loads(CONFIG_PATH.read_text())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = {}
    if DB_PATH.is_file():
        try:
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT payload FROM events WHERE event='verification' AND ts >= ?",
                (today,)).fetchall()
            con.close()
            for (payload,) in rows:
                try:
                    p = json.loads(payload or "{}")
                except json.JSONDecodeError:
                    continue
                if p.get("kind") == "cross-family" and p.get("provider"):
                    counts[p["provider"]] = counts.get(p["provider"], 0) + 1
        except sqlite3.Error:
            pass
    print(f"# cross-family usage — oggi {today} UTC (contatore LOCALE: "
          f"non vede uso della chiave fuori da questa macchina)")
    for name, prov in (cfg.get("providers") or {}).items():
        n = counts.get(name, 0)
        rpd = (prov.get("limits") or {}).get("rpd")
        lim = f"/{rpd} rpd dichiarato" if rpd else " (nessun limite dichiarato nel config)"
        print(f"  {name}: {n}{lim}")
        if rpd and n >= rpd * 0.8:
            print(f"    ⚠ ALLARME (non target): ≥80% del limite dichiarato")


def cmd_init():
    if CONFIG_PATH.is_file():
        print(f"config già presente: {CONFIG_PATH} (non sovrascrivo)")
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
    print(f"config creata: {CONFIG_PATH}")
    print("Prossimi passi —")
    for name, p in DEFAULT_CONFIG["providers"].items():
        if p.get("api_key_env"):
            print(f"  {name}: export {p['api_key_env']}=...  # {p.get('note', '')}")
        else:
            print(f"  {name}: {p.get('note', '')}")
    print("URL/modelli nel config sono la fotografia di luglio 2026: "
          "riverificali, i free tier cambiano senza preavviso.")


def parse_args(argv):
    opts = {"--claim": None, "--rubric": None, "--context-file": None,
            "--provider": None, "--timeout": "60"}
    i = 0
    while i < len(argv):
        if argv[i] == "--init":
            cmd_init()
            sys.exit(0)
        if argv[i] == "--usage":
            cmd_usage()
            sys.exit(0)
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            sys.exit(f"argomento non riconosciuto: {argv[i]}\n{__doc__}")
    return opts


def call_http(prov, name, api_key, user_msg, timeout):
    body = json.dumps({
        "model": prov["model"],
        "messages": [{"role": "system", "content": VERIFIER_SYSTEM},
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
    """Provider a sottoprocesso (es. Codex CLI). Disciplina Appendice C:
    preflight esplicito, spec via STDIN (niente quoting hazard), output su
    file mktemp unico (parallel-safe), timeout, mai fallback silenzioso."""
    cmd_template = prov.get("command") or []
    if not cmd_template or not shutil.which(cmd_template[0]):
        unavailable(f"CLI '{cmd_template[0] if cmd_template else '?'}' non "
                    f"installata per '{name}' ({prov.get('note', '')})")
    fd, out_file = tempfile.mkstemp(prefix="cross-verify-", suffix=".txt")
    os.close(fd)
    cmd = [a.replace("{output_file}", out_file) for a in cmd_template]
    spec = f"{VERIFIER_SYSTEM}\n\n{user_msg}"
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
    opts = parse_args(sys.argv[1:])
    if not opts["--claim"]:
        sys.exit(__doc__)

    # Preflight — ogni mancanza è rumorosa, mai fallback silenzioso.
    if not CONFIG_PATH.is_file():
        unavailable(f"config assente ({CONFIG_PATH}): esegui cross-verify.py --init")
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
                        f"(export {prov.get('api_key_env')}=... o campo api_key nel config)")

    context = ""
    if opts["--context-file"]:
        try:
            context = Path(opts["--context-file"]).read_text(errors="replace")
        except OSError as e:
            unavailable(f"context-file illeggibile: {e}")

    user_msg = f"CLAIM TO VERIFY:\n{opts['--claim']}\n"
    if opts["--rubric"]:
        user_msg += f"\nRUBRIC:\n{opts['--rubric']}\n"
    if context:
        user_msg += f"\nARTIFACT:\n{context[:100_000]}\n"

    timeout = int(opts["--timeout"])
    # Marker per lo statusline: [XF <provider>▶] finché la chiamata è viva.
    # finally garantisce la rimozione anche su unavailable (SystemExit).
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

    try:
        # tollera code fence attorno al JSON
        content = content.strip().strip("`").removeprefix("json").strip()
        verdict_obj = json.loads(content)
        verdict = verdict_obj.get("verdict", "uncertain")
        reasoning = verdict_obj.get("reasoning", "")
    except (AttributeError, TypeError, json.JSONDecodeError):
        # risposta fuori schema: NON è una verifica valida
        out("error", name, prov["model"], "uncertain",
            "risposta del provider fuori schema — trattala come NON verificata")
        sys.exit(1)

    if verdict not in ("refuted", "supported", "uncertain"):
        verdict = "uncertain"
    out("ok", name, prov["model"], verdict, reasoning)
    log_verification({"kind": "cross-family", "provider": name,
                      "model": prov["model"], "verdict": verdict,
                      "found": verdict == "refuted"})
    sys.exit(0)


if __name__ == "__main__":
    main()

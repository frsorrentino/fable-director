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

Uso:
  cross-verify.py --init
  cross-verify.py --claim "..." --rubric "..." [--context-file F]
                  [--provider gemini|deepseek] [--timeout 60]

Output (grep-abile):
  STATUS: ok|unavailable|error
  PROVIDER: <provider> (<model>)
  VERDICT: refuted|supported|uncertain
  REASONING: <breve>
Exit code: 0 solo su STATUS ok. Zero dipendenze: solo stdlib.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "fable-director" / "cross-family.json"

DEFAULT_CONFIG = {
    "default": "gemini",
    "providers": {
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3-flash",
            "api_key_env": "GEMINI_API_KEY",
            "note": "free tier AI Studio — la subscription consumer AI Pro NON alimenta l'API"
        },
        "deepseek": {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-v4-flash:free",
            "api_key_env": "OPENROUTER_API_KEY",
            "note": "OpenRouter free tier ~100 req/giorno per chiave"
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


def cmd_init():
    if CONFIG_PATH.is_file():
        print(f"config già presente: {CONFIG_PATH} (non sovrascrivo)")
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
    print(f"config creata: {CONFIG_PATH}")
    print("Prossimi passi: esporta le chiavi API nell'ambiente —")
    for name, p in DEFAULT_CONFIG["providers"].items():
        print(f"  {name}: export {p['api_key_env']}=...  # {p.get('note', '')}")
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
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            sys.exit(f"argomento non riconosciuto: {argv[i]}\n{__doc__}")
    return opts


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
        with urllib.request.urlopen(req, timeout=int(opts["--timeout"])) as resp:
            data = json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        unavailable(f"HTTP {e.code} da {name} (rate limit/endpoint cambiato?)")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unavailable(f"rete/timeout verso {name}: {e}")

    try:
        content = data["choices"][0]["message"]["content"]
        # tollera code fence attorno al JSON
        content = content.strip().strip("`").removeprefix("json").strip()
        verdict_obj = json.loads(content)
        verdict = verdict_obj.get("verdict", "uncertain")
        reasoning = verdict_obj.get("reasoning", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
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

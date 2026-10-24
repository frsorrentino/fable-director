#!/usr/bin/env python3
"""Verifica deterministica — controlli all'avvio sul modello dei subagent (1.60.0).

Claude Code sceglie il modello di un subagent in quest'ordine (docs subagents,
lette il 24/10/2026): `model` della chiamata, `model` del frontmatter
(`inherit` = modello della sessione), `CLAUDE_CODE_SUBAGENT_MODEL`, modello della
sessione. Dalla 2.1.251 la variabile da sola e' quindi un DEFAULT: i pin di
fd-executor (sonnet) e fd-verifier (inherit) la battono. Con
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (2.1.257) Claude Code ignora il `model`
delle definizioni e quello passato dalla chiamata, agent dei workflow inclusi:
i pin cadono. Con entrambe i subagent girano sulla variabile; con FORCE da
sola, sul modello della sessione (stessa pagina di docs). Il kernel
SessionStart lo dice con una riga sola nel momento in cui morde, e tace quando
le variabili non ci sono.

  S1 nessuna variabile → nessuna riga sui modelli dei subagent
  S2 CLAUDE_CODE_SUBAGENT_MODEL=haiku da sola → riga informativa, senza ⚠:
     e' un default, fd-executor resta su sonnet, fd-verifier sul modello di
     sessione, la variabile FORCE e' nominata come l'unica che scavalca
  S3 MODEL=haiku + FORCE=1 → avviso ⚠: pin ignorati, fd-executor e fd-verifier
     finiscono su haiku, anche il `model` delle chiamate e dei workflow cade
  S4 FORCE=1 da sola → avviso ⚠: fd-executor lascia sonnet per il modello di
     sessione, fd-verifier invariato
  S5 MODEL=inherit → come assente (docs: "the same as leaving it unset")
  S6 con l'avviso il kernel resta intero, sotto il cap e senza marker di taglio
  S7 skill delega-efficiente: nota su `opusplan` (opus in plan mode, sonnet in
     esecuzione) da misurare con la telemetria, senza cifre inventate
  S8 CHANGELOG nomina le variabili e opusplan

Usage: python3 tests/startup-checks-verify.py   (exit 0 = all green)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "fable-director"
SCRIPT = ROOT / "scripts" / "session-kernel.sh"
SKILL = ROOT / "skills" / "delega-efficiente" / "SKILL.md"
CHANGELOG = REPO / "CHANGELOG.md"
VARS = ("CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE")
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def fresh_home():
    home = Path(tempfile.mkdtemp(prefix="fd-startup-test")) / "home"
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True)
    # Blocchi one-shot gia' consumati: l'output resta il kernel piu' le righe
    # sotto esame, e il test non dipende da legenda e onboarding.
    (base / "help-hint-shown").touch()
    (base / "xf-onboarding-done").touch()
    return home


def run(env_extra=None):
    e = dict(os.environ, HOME=str(fresh_home()), CLAUDE_PLUGIN_ROOT=str(ROOT))
    e.pop("CLAUDE_CONFIG_DIR", None)
    for v in VARS:           # la macchina del test non deve contare
        e.pop(v, None)
    e.update(env_extra or {})
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1",
                          "cwd": "/tmp/fd-startup-cwd", "source": "startup"})
    p = subprocess.run(["bash", str(SCRIPT)], env=e, text=True, input=payload,
                       capture_output=True, timeout=120)
    return p.stdout


def line_with(out, needle):
    """La prima riga dell'output che contiene `needle`, o ''."""
    return next((ln for ln in out.splitlines() if needle in ln), "")


print("controlli all'avvio sul modello dei subagent:")

# S1: niente variabili → silenzio sul tema
out = run()
check("S1 nessuna variabile → nessuna riga su CLAUDE_CODE_SUBAGENT_MODEL",
      "CLAUDE_CODE_SUBAGENT_MODEL" not in out and "FABLE-DIRECTOR KERNEL" in out,
      line_with(out, "SUBAGENT_MODEL")[:200])

# S2: la variabile da sola e' un default — i pin reggono, lo si dice senza ⚠
out = run({"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
ln = line_with(out, "CLAUDE_CODE_SUBAGENT_MODEL=haiku")
check("S2 MODEL=haiku da sola → riga informativa con il valore",
      bool(ln) and ln.startswith("FD"), out[-400:])
check("S2b la riga dice che e' un default e che i pin reggono",
      "default" in ln and "fd-executor" in ln and "sonnet" in ln
      and "fd-verifier" in ln and "session model" in ln, ln)
check("S2c nomina CLAUDE_CODE_SUBAGENT_MODEL_FORCE come cio' che scavalca, senza ⚠",
      "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" in ln and "⚠" not in ln, ln)

# S3: FORCE + MODEL → i pin cadono, entrambi gli agenti finiscono sul modello forzato
out = run({"CLAUDE_CODE_SUBAGENT_MODEL": "haiku",
           "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})
ln = line_with(out, "CLAUDE_CODE_SUBAGENT_MODEL_FORCE")
check("S3 MODEL=haiku + FORCE=1 → avviso ⚠ con entrambe le variabili",
      bool(ln) and "⚠" in ln and "CLAUDE_CODE_SUBAGENT_MODEL=haiku" in ln, out[-400:])
check("S3b l'avviso dice che fd-executor e fd-verifier girano su haiku",
      "fd-executor" in ln and "fd-verifier" in ln and ln.count("haiku") >= 2
      and "ignores" in ln, ln)
# Il kernel chiede `model:` per stadio nei workflow: sotto FORCE anche quello cade.
check("S3c l'avviso dice che cade anche il model delle chiamate e dei workflow",
      "Agent/workflow calls" in ln, ln)

# S4: solo FORCE → fd-executor perde sonnet per il modello di sessione
out = run({"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})
ln = line_with(out, "CLAUDE_CODE_SUBAGENT_MODEL_FORCE")
check("S4 FORCE=1 da sola → avviso ⚠",
      bool(ln) and "⚠" in ln, out[-400:])
check("S4b fd-executor lascia sonnet per il modello di sessione, fd-verifier invariato",
      "fd-executor" in ln and "sonnet" in ln and "session model" in ln
      and "fd-verifier" in ln and "unchanged" in ln, ln)
check("S4c senza MODEL nessun valore inventato (niente 'MODEL=')",
      "CLAUDE_CODE_SUBAGENT_MODEL=" not in ln, ln)

# S5: inherit vale come assente
out = run({"CLAUDE_CODE_SUBAGENT_MODEL": "inherit"})
check("S5 MODEL=inherit → come assente, nessuna riga",
      "CLAUDE_CODE_SUBAGENT_MODEL" not in out, line_with(out, "SUBAGENT_MODEL")[:200])

# S6: l'avviso non sfonda il cap e il kernel resta intero
out = run({"CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-5-5",
           "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})
check("S6 kernel intero, sotto il cap, senza marker di taglio",
      "FABLE-DIRECTOR KERNEL" in out and "Never overrides axes 1-2" in out
      and len(out) <= 8000 and out.count("\n") <= 200
      and "output cut" not in out,
      f"len={len(out)} lines={out.count(chr(10))}")

# S7: la skill nomina opusplan come alternativa da misurare, senza cifre
skill = SKILL.read_text(encoding="utf-8")
op_lines = [ln for ln in skill.splitlines() if "opusplan" in ln]
check("S7 skill delega-efficiente nomina `opusplan`", bool(op_lines))
op = op_lines[0] if op_lines else ""
# La nota su opusplan: dal nome alla fine della riga che lo contiene. Si
# controlla solo quella coda, non il resto del punto (che ha cifre misurate sue).
sentence = op[op.index("`opusplan`"):] if "`opusplan`" in op else ""
check("S7b dice opus in plan mode e sonnet in esecuzione",
      bool(sentence) and re.search(r"plan mode", sentence, re.I)
      and re.search(r"\bopus\b", sentence, re.I)
      and re.search(r"\bsonnet\b", sentence, re.I), sentence[:300] or op[:300])
check("S7c e' un'alternativa da misurare con la telemetria, non una regola",
      bool(sentence) and re.search(r"measur|telemetry|report", sentence, re.I)
      and re.search(r"alternative|compare|against", sentence, re.I), sentence[:300])
check("S7d nessuna cifra inventata (percentuali, ×, k eq) nella frase",
      bool(sentence) and not re.search(r"\d+\s*%|\d+(?:[.,]\d+)?\s*×|\d+k\s*eq", sentence),
      sentence[:300])

# S8: CHANGELOG
cl = CHANGELOG.read_text(encoding="utf-8")
check("S8 CHANGELOG nomina CLAUDE_CODE_SUBAGENT_MODEL_FORCE e opusplan",
      "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" in cl and "opusplan" in cl)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: controlli all'avvio sul modello dei subagent")

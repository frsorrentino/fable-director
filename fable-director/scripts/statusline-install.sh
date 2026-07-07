#!/bin/bash
# fable-director — installer idempotente della statusline in settings.json.
#
# Perché serve: la statusLine NON è un componente che un plugin può registrare
# da solo (a differenza di hook/skill/command). Va scritta in settings.json.
# Questo script lo fa in modo deterministico, merge-safe e uguale su ogni macchina:
# si auto-localizza accanto a statusline-ctx.sh, quindi il path assoluto scritto in
# settings.json è sempre quello reale di QUESTA installazione (GitHub o directory locale).
#
# Uso:
#   bash statusline-install.sh          # installa / aggiorna il path
#   bash statusline-install.sh --remove # rimuove SOLO la nostra statusLine
#
# Dopo la scrittura: riavviare Claude Code (la statusLine è letta all'avvio).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/statusline-ctx.sh"
CFG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CFG_DIR/settings.json"

if [ ! -f "$TARGET" ]; then
  echo "ERRORE: statusline-ctx.sh non trovato accanto all'installer ($TARGET)." >&2
  exit 1
fi

MODE="install"
[ "${1:-}" = "--remove" ] && MODE="remove"

# Tutta la logica di merge in python: parsing/scrittura JSON deterministici,
# preserva le altre chiavi, non tocca una statusLine di terzi.
CLAUDE_SETTINGS="$SETTINGS" FD_TARGET="$TARGET" FD_MODE="$MODE" python3 - <<'PY'
import json, os, sys, shutil
from pathlib import Path

settings = Path(os.environ["CLAUDE_SETTINGS"])
target   = os.environ["FD_TARGET"]
mode     = os.environ["FD_MODE"]

command = f'bash "{target}"'
marker  = "statusline-ctx.sh"   # firma per riconoscere una NOSTRA statusLine

# Carica settings esistenti (o oggetto vuoto).
data = {}
if settings.is_file():
    txt = settings.read_text(encoding="utf-8").strip()
    if txt:
        try:
            data = json.loads(txt)
        except json.JSONDecodeError as e:
            print(f"ERRORE: {settings} non è JSON valido ({e}). Non tocco nulla.", file=sys.stderr)
            sys.exit(1)
    if not isinstance(data, dict):
        print(f"ERRORE: {settings} non è un oggetto JSON. Non tocco nulla.", file=sys.stderr)
        sys.exit(1)

existing = data.get("statusLine")
existing_cmd = existing.get("command", "") if isinstance(existing, dict) else ""
is_ours = marker in existing_cmd

def backup_and_write(new_data, msg):
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.is_file():
        shutil.copy2(settings, settings.with_suffix(settings.suffix + ".bak"))
    settings.write_text(json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(msg)

if mode == "remove":
    if is_ours:
        data.pop("statusLine", None)
        backup_and_write(data, f"RIMOSSA statusLine fable-director. Backup: {settings}.bak")
    elif existing is not None:
        print("Nessuna rimozione: la statusLine presente NON è di fable-director. Lasciata intatta.")
    else:
        print("Nessuna statusLine da rimuovere.")
    sys.exit(0)

# mode == install
if existing is not None and not is_ours:
    print("ATTENZIONE: esiste già una statusLine di terzi in settings.json:", file=sys.stderr)
    print(f"  {existing_cmd}", file=sys.stderr)
    print("Non la sovrascrivo. Per usare quella di fable-director rimuovila a mano, poi rilancia.", file=sys.stderr)
    sys.exit(2)

if is_ours and existing_cmd == command:
    print("statusLine fable-director già installata e aggiornata. Nulla da fare.")
    sys.exit(0)

data["statusLine"] = {"type": "command", "command": command}
verb = "AGGIORNATA" if is_ours else "INSTALLATA"
backup_and_write(data, f"statusLine fable-director {verb}. Backup: {settings}.bak\n  → {command}\nRiavvia Claude Code per vederla.")
PY

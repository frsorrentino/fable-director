"""fable-director — statusLine merge into settings.json, run by statusline-install.sh.

Input from the environment: CLAUDE_SETTINGS, FD_TARGET, FD_MODE (install|remove|auto),
FD_OPTOUT. Until 1.65.2 this code was a here-document read from stdin; same code.
"""
import json, os, sys, shutil
from pathlib import Path

settings = Path(os.environ["CLAUDE_SETTINGS"])
target   = os.environ["FD_TARGET"]
mode     = os.environ["FD_MODE"]
optout   = Path(os.environ["FD_OPTOUT"])
auto     = mode == "auto"
if auto and optout.is_file():
    sys.exit(0)   # rimossa apposta con --remove: non ricompare da sola

command = f'bash "{target}"'
marker  = "statusline-ctx.sh"   # firma per riconoscere una NOSTRA statusLine

# refreshInterval (secondi, minimo 1 — doc statusline): gli update sono
# event-driven e "possono zittirsi mentre il coordinatore aspetta subagent in
# background". È esattamente il momento in cui questa statusline serve: budget
# che sale, deleghe in volo, quota che scende. Senza timer l'HUD resta congelato
# per tutta la durata di un workflow. Override: FD_STATUSLINE_REFRESH (0 = mai).
# 30 e non 5 (1.63.1): su una macchina carica un giro durava piu di 5 s e i
# giri si accavallavano; a 30 s il timer tiene viva la riga senza rincorrersi.
try:
    refresh = int(os.environ.get("FD_STATUSLINE_REFRESH", "30"))
except ValueError:
    refresh = 30
refresh = 0 if refresh <= 0 else max(1, refresh)

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
        try:
            optout.parent.mkdir(parents=True, exist_ok=True)
            optout.touch()
        except OSError:
            pass
    elif existing is not None:
        print("Nessuna rimozione: la statusLine presente NON è di fable-director. Lasciata intatta.")
    else:
        print("Nessuna statusLine da rimuovere.")
    sys.exit(0)

# mode == install | auto
if existing is not None and not is_ours:
    if auto:
        sys.exit(0)   # statusLine di terzi: mai toccata, e in auto nemmeno un avviso
    print("ATTENZIONE: esiste già una statusLine di terzi in settings.json:", file=sys.stderr)
    print(f"  {existing_cmd}", file=sys.stderr)
    print("Non la sovrascrivo. Per usare quella di fable-director rimuovila a mano, poi rilancia.", file=sys.stderr)
    sys.exit(2)

desired = {"type": "command", "command": command}
if refresh:
    desired["refreshInterval"] = refresh

if is_ours and isinstance(existing, dict) and existing == desired:
    if not auto:
        print("statusLine fable-director già installata e aggiornata. Nulla da fare.")
    sys.exit(0)

data["statusLine"] = desired
verb = "AGGIORNATA" if is_ours else "INSTALLATA"
if auto:
    # corta e in inglese (va nel contesto del modello): la riga entra nel cap del SessionStart
    backup_and_write(data, f"{'updated' if is_ours else 'installed'} in settings.json")
    sys.exit(0)
try:
    optout.unlink()   # install esplicito: la rinuncia decade
except OSError:
    pass
tick = (f"\n  → refreshInterval: {refresh}s (aggiorna anche mentre aspetti "
        f"subagent in background; FD_STATUSLINE_REFRESH=0 per disattivarlo)"
        if refresh else "\n  → refreshInterval: disattivato (solo eventi)")
backup_and_write(data, f"statusLine fable-director {verb}. Backup: {settings}.bak\n  → {command}{tick}\nRiavvia Claude Code per vederla.")

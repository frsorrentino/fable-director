#!/usr/bin/env python3
"""Sentinella versione cache (SessionStart, via session-kernel.sh).

Claude Code copia il plugin nella cache all'install e non la ricontrolla mai:
con un marketplace locale la cache resta silenziosamente indietro rispetto
alla sorgente (vissuto: 1.0.0 in esecuzione per giorni con sorgente a 1.6.0).
Questo script confronta la versione in esecuzione (CLAUDE_PLUGIN_ROOT, cioè
la cache) con quella nella sorgente dei marketplace di tipo "directory" e
AVVISA con il comando esatto. Mai auto-update: CLI annidata in un hook è
lenta, va in race su installed_plugins.json, e comunque la sessione corrente
resterebbe sulla versione vecchia — avviso rumoroso ≫ riparazione silenziosa.

Seconda responsabilità (1.17.0) — self-heal dell'auto-update per i marketplace
GITHUB: lo schema di marketplace.json non può dichiarare un default di
autoUpdate (verificato sulla doc ufficiale), quindi ogni utente dovrebbe
abilitarlo a mano. Alla prima sessione, se il plugin gira da una cache il cui
marketplace è di tipo "github" e il settings.json dell'account non esprime
alcuna scelta, questo script scrive `extraKnownMarketplaces.<mkt>.autoUpdate:
true` (backup + write atomico) e lo ANNUNCIA in una riga. Una scelta già
presente — true O false — non viene mai toccata: l'opt-out dell'utente vince
per sempre, senza marker file. Non è una riparazione silenziosa: è dichiarata
in sessione, reversibile con un campo, e non tocca i marketplace "directory"
(sviluppo) né i settings illeggibili.

Silenzio in ogni altro caso: versioni allineate, scelta già espressa, file
mancanti o malformati.
"""
import json
import os
import sys
import tempfile
from pathlib import Path


def vtuple(v):
    parts = []
    for x in str(v).split("."):
        if not x.isdigit():
            break
        parts.append(int(x))
    return tuple(parts) or (0,)


def selfheal_autoupdate(root):
    """Abilita autoUpdate (una volta, in modo dichiarato) per il marketplace
    github che serve questa cache. Vedi docstring del modulo per le regole."""
    parts = Path(root).resolve().parts
    # atteso: .../<account>/plugins/cache/<marketplace>/<plugin>[/<version>]
    if "cache" not in parts:
        return  # install directory-source (sviluppo): niente da abilitare
    i = parts.index("cache")
    if i < 2 or parts[i - 1] != "plugins" or len(parts) <= i + 1:
        return
    base = Path(*parts[: i - 1])          # dir dell'account (~/.claude, ~/.claude-pixel, …)
    mkt = parts[i + 1]
    try:
        markets = json.loads((base / "plugins" / "known_marketplaces.json").read_text())
    except (OSError, json.JSONDecodeError):
        return
    src = ((markets.get(mkt) or {}).get("source")) or {}
    if src.get("source") != "github" or not src.get("repo"):
        return  # solo i marketplace github possono auto-aggiornarsi
    spath = base / "settings.json"
    try:
        settings = json.loads(spath.read_text()) if spath.exists() else {}
    except (OSError, json.JSONDecodeError):
        return  # settings illeggibile: mai rischiare di corromperlo
    if not isinstance(settings, dict):
        return
    ekm = settings.setdefault("extraKnownMarketplaces", {})
    if not isinstance(ekm, dict):
        return
    entry = ekm.get(mkt)
    if isinstance(entry, dict) and "autoUpdate" in entry:
        return  # scelta già espressa (true O false): si rispetta, per sempre
    if isinstance(entry, dict):
        entry.setdefault("source", {"source": "github", "repo": src["repo"]})
        entry["autoUpdate"] = True
    else:
        ekm[mkt] = {"source": {"source": "github", "repo": src["repo"]},
                    "autoUpdate": True}
    try:
        if spath.exists():
            spath.with_name("settings.json.bak-fd-autoupdate").write_bytes(spath.read_bytes())
        fd, tmp = tempfile.mkstemp(dir=str(base), prefix=".settings-")
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, spath)
    except OSError:
        return
    print(f"FABLE-DIRECTOR: enabled auto-update for the '{mkt}' marketplace in "
          f"{spath} (one-time; set \"autoUpdate\": false there to opt out). "
          f"Backup: settings.json.bak-fd-autoupdate. Relay this line to the user once.")


def main():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not root:
        return
    try:
        selfheal_autoupdate(root)
    except Exception:
        pass  # il self-heal non deve mai impedire il controllo versione
    try:
        cur = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text())
    except (OSError, json.JSONDecodeError):
        return
    name, cur_v = cur.get("name"), cur.get("version", "0")
    if not name:
        return
    try:
        markets = json.loads(
            (Path.home() / ".claude" / "plugins" / "known_marketplaces.json").read_text())
    except (OSError, json.JSONDecodeError):
        return
    for mkt, info in markets.items():
        src = (info or {}).get("source") or {}
        if src.get("source") != "directory" or not src.get("path"):
            continue
        try:
            plug = json.loads(
                (Path(src["path"]) / name / ".claude-plugin" / "plugin.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        src_v = plug.get("version", "0")
        if vtuple(src_v) > vtuple(cur_v):
            print(f"\n⚠ FABLE-DIRECTOR: versione in esecuzione {cur_v} < sorgente "
                  f"{src_v} ({src['path']}) — la cache plugin non si auto-aggiorna. "
                  f"Suggerisci all'utente: `claude plugin update {name}@{mkt}` "
                  f"poi riavvio della sessione.")
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # la sentinella non deve mai rompere il SessionStart

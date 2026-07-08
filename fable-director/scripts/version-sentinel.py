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

Silenzio in ogni altro caso: marketplace remoto (niente path da confrontare,
niente rete), versioni allineate, file mancanti o malformati.
"""
import json
import os
import sys
from pathlib import Path


def vtuple(v):
    parts = []
    for x in str(v).split("."):
        if not x.isdigit():
            break
        parts.append(int(x))
    return tuple(parts) or (0,)


def main():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not root:
        return
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

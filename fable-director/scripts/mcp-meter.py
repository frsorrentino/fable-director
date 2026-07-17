#!/usr/bin/env python3
"""Hook PostToolUse (mcp__* | ToolSearch): metering del peso MCP in contesto.

Due grandezze diverse, che vanno tenute separate:

- FLUSSO (`mcp_meter`): i risultati dei tool MCP entrano interi nel contesto —
  la banda 2-20k token passa senza troncamento e gonfia in silenzio. Costo
  pagato UNA volta, alla chiamata.
- GIACENZA (`mcp_schema_load`): gli schemi che ToolSearch carica restano nel
  prefisso e si ripagano a OGNI turno finché la sessione vive. È la "context
  dilution": non la vedi passare, la paghi per sempre. Un carico da 30 tool
  costa più di una risposta da 30k byte, perché la risposta la paghi una volta
  e lo schema n volte.

L'harness differisce già i tool (schema assente finché non fai ToolSearch),
quindi metà del problema qui non esiste: resta da misurare quanto costa il
carico quando lo fai davvero — es. chrome-bridge, dichiarato preferito in
soft-deps.json e quindi caricato spesso.

Zero token modello, zero giudizio: `fd-telemetry.py report` aggrega e mostra
dove va il peso — la decisione (filtrare, chiedere pattern, sostituire con
script, caricare meno tool per volta) resta al regista.

Fail-silent by design: il metering non deve mai disturbare la chiamata.
"""
import json
import sys
from pathlib import Path


def measure(resp):
    try:
        return len(json.dumps(resp, ensure_ascii=False)) if resp is not None else 0
    except (TypeError, ValueError):
        return len(str(resp))


def main():
    data = json.load(sys.stdin)
    tool = str(data.get("tool_name") or "")
    resp = data.get("tool_response")

    if tool == "ToolSearch":
        # I byte restituiti SONO gli schemi iniettati nel prefisso: misuro la
        # giacenza alla fonte. `query` distingue il select: mirato dalla
        # ricerca a strascico — è lì che si vede chi carica 30 tool per usarne 2.
        size = measure(resp)
        if not size:
            return
        query = str((data.get("tool_input") or {}).get("query") or "")[:120]
        write_event("mcp_schema_load",
                    {"query": query, "bytes": size,
                     "est_tokens": size // 4})
        return

    if not tool.startswith("mcp__"):
        return
    size = measure(resp)
    parts = tool.split("__")
    server = parts[1] if len(parts) > 1 else "?"
    write_event("mcp_meter", {"server": server, "tool": tool, "bytes": size})


def write_event(event, payload):
    import random
    import sqlite3
    import time
    from datetime import datetime, timezone
    base = Path.home() / ".claude" / "fable-director"
    base.mkdir(parents=True, exist_ok=True)  # install fresca: dir assente
    row = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           event, json.dumps(payload))
    # retry+backoff: le chiamate MCP arrivano a raffica (browser automation)
    # e un busy_timeout scaduto perderebbe l'evento in silenzio
    for attempt in range(4):
        try:
            con = sqlite3.connect(base / "telemetry.db", timeout=1.0)
            con.execute("PRAGMA busy_timeout=1000")
            con.execute("CREATE TABLE IF NOT EXISTS events("
                        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                        "session_id TEXT, cwd TEXT, event TEXT NOT NULL, "
                        "payload TEXT)")
            con.execute("INSERT INTO events(ts, event, payload) "
                        "VALUES(?,?,?)", row)
            con.commit()
            con.close()
            return
        except sqlite3.OperationalError:
            time.sleep(0.05 * (2 ** attempt) + random.random() * 0.05)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass

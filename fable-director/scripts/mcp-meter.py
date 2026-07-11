#!/usr/bin/env python3
"""Hook PostToolUse (mcp__*): metering del peso MCP in contesto.

I risultati dei tool MCP entrano interi nel contesto: la banda 2-20k token
passa senza troncamento e gonfia in silenzio (stessa diagnosi della regola
harness sulla lettura dei tool output). Nessuno la misura — questo hook sì:
per ogni chiamata MCP logga server, tool e byte della risposta. Zero token
modello, zero giudizio: `fd-telemetry.py report` aggrega per server e mostra
dove va il peso — la decisione (filtrare, chiedere pattern, sostituire con
script) resta al regista.

Fail-silent by design: il metering non deve mai disturbare la chiamata.
"""
import json
import sys
from pathlib import Path


def main():
    data = json.load(sys.stdin)
    tool = str(data.get("tool_name") or "")
    if not tool.startswith("mcp__"):
        return
    resp = data.get("tool_response")
    try:
        size = len(json.dumps(resp, ensure_ascii=False)) if resp is not None else 0
    except (TypeError, ValueError):
        size = len(str(resp))
    parts = tool.split("__")
    server = parts[1] if len(parts) > 1 else "?"
    import sqlite3
    from datetime import datetime, timezone
    db = Path.home() / ".claude" / "fable-director" / "telemetry.db"
    con = sqlite3.connect(db, timeout=0.5)
    con.execute("PRAGMA busy_timeout=500")
    con.execute(
        "INSERT INTO events(ts, event, payload) VALUES(?,?,?)",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "mcp_meter",
         json.dumps({"server": server, "tool": tool, "bytes": size})))
    con.commit()
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass

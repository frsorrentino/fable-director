#!/usr/bin/env python3
"""Hook PostToolUse (Read): dedup lossless-recuperabile delle riletture.

Il sink di token più grosso dei loop agentici è ri-mandare contenuto file
già visto (rilettura post-edit per verifica, re-read dello stesso file da
angolazioni diverse). Questo hook, alla RILETTURA di un file già letto nella
sessione, sostituisce l'output di Read con il solo diff (o un marker se
identico) via `updatedToolOutput` — token risparmiati sul contenuto stabile.

RISCHIO DI CORRETTEZZA (perché è opt-in e conservativo). Un diff è
ricostruibile in contenuto pieno SOLO se la lettura originale è ancora nel
contesto. Dopo un compact, l'originale può essere sparito: il diff diventa
fuorviante. Un MCP risolve dando un tool di retrieve (token-optimizer-mcp);
un hook no. Mitigazioni:
- ESCAPE ALTERNATO: dopo aver dato un diff per un file, la rilettura
  SUCCESSIVA fa passthrough pieno e ri-cachea. Il modello ha sempre un
  cammino a 1-read verso il contenuto pieno → ogni gap da diff è limitato a
  una rilettura. Bounded, non eliminato.
- Solo file GRANDI (>THRESHOLD char): sui piccoli il marker non ripaga.
- Salta i read parziali (offset/limit): lo slicing romperebbe il diff.
- Header esplicito nell'output: dice al modello che è un diff e come
  ri-espandere.
- OPT-IN: inerte se manca il toggle (env FD_READ_DEDUP=1 o file
  ~/.claude/fable-director/read-dedup.on). Non è nel hooks.json di default:
  chi non lo abilita non paga né subprocess né rischio.

Fail-open: qualunque errore → nessun output = l'output originale di Read
resta intatto. Un bug del dedup non deve mai corrompere una lettura.
"""
import difflib
import hashlib
import json
import os
import sys
from pathlib import Path

THRESHOLD = 2000          # char minimi del contenuto per valere il dedup
DIFF_MAX_RATIO = 0.6      # sostituisci solo se il diff è < 60% del nuovo contenuto
BASE = Path.home() / ".claude" / "fable-director"


def enabled():
    if os.environ.get("FD_READ_DEDUP") == "1":
        return True
    return (BASE / "read-dedup.on").is_file()


def extract_text(resp):
    """Estrae il testo dal tool_response di Read, difensivo sulle forme
    possibili (stringa; dict con 'content' stringa o lista di blocchi)."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        c = resp.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for b in c:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    parts.append(b["text"])
            if parts:
                return "\n".join(parts)
        for k in ("text", "file", "output"):
            v = resp.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict) and isinstance(v.get("content"), str):
                return v["content"]
    return None


def emit(text, note):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": text,
            "additionalContext": note,
        }
    }, ensure_ascii=False))


def main():
    if not enabled():
        return
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Read":
        return
    ti = data.get("tool_input") or {}
    path = ti.get("file_path")
    if not path:
        return
    # Read parziale: lo slicing (offset/limit) romperebbe la semantica del
    # diff a livello di file. Passthrough sempre.
    if ti.get("offset") or ti.get("limit"):
        return

    new_text = extract_text(data.get("tool_response"))
    if not isinstance(new_text, str) or len(new_text) < THRESHOLD:
        return

    sid = data.get("session_id") or "unknown"
    d = BASE / "read-cache" / str(sid)
    d.mkdir(parents=True, exist_ok=True)
    ph = hashlib.sha1(str(path).encode()).hexdigest()[:16]
    meta_f = d / f"{ph}.json"
    blob_f = d / f"{ph}.blob"

    meta = None
    old_text = None
    if meta_f.is_file() and blob_f.is_file():
        try:
            meta = json.loads(meta_f.read_text())
            old_text = blob_f.read_text()
        except (OSError, json.JSONDecodeError):
            meta = None

    new_sha = hashlib.sha256(new_text.encode()).hexdigest()

    def recache(last):
        blob_f.write_text(new_text)
        meta_f.write_text(json.dumps(
            {"path": path, "sha": new_sha, "seq": (meta or {}).get("seq", 0) + 1,
             "last": last}, ensure_ascii=False))

    # Primo read di questo file nella sessione: passthrough, cachea.
    if meta is None or old_text is None:
        recache("full")
        return

    seq = meta.get("seq", 1)
    # ESCAPE: la lettura dopo un dedup fa passthrough pieno e ri-cachea, così
    # il modello può sempre riottenere il contenuto pieno in 1 read.
    if meta.get("last") == "diff":
        recache("full")
        return

    # Rilettura identica: marker corto invece del contenuto pieno.
    if meta.get("sha") == new_sha:
        recache("diff")
        n_lines = new_text.count("\n") + 1
        emit(
            f"[FD read-dedup] '{path}' invariato dalla lettura #{seq} di questa "
            f"sessione ({n_lines} righe, sha {new_sha[:12]}). Contenuto già in "
            f"contesto sopra. Se è stato compattato via, rileggi ancora una "
            f"volta: la prossima Read ritorna il file pieno.",
            "Rilettura identica soppressa per risparmio token (dedup lossless "
            "opt-in, recuperabile in 1 read).")
        return

    # Rilettura con modifiche: diff unificato, solo se conviene.
    diff = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"{path} (lettura #{seq})", tofile=f"{path} (ora)"))
    if not diff or len(diff) >= len(new_text) * DIFF_MAX_RATIO:
        # Diff non conviene (cambiamento troppo grande): passthrough pieno.
        recache("full")
        return
    recache("diff")
    emit(
        f"[FD read-dedup] '{path}' già letto (#{seq}); mostro solo il diff da "
        f"allora. Il contenuto pieno è in quella lettura precedente; se è stato "
        f"compattato via, rileggi ancora una volta per riaverlo intero.\n\n{diff}",
        "Contenuto stabile soppresso, mostrato solo il diff (dedup lossless "
        "opt-in, recuperabile in 1 read).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: mai corrompere una lettura per un bug del dedup

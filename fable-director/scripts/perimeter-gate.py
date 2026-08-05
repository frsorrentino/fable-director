#!/usr/bin/env python3
"""Hook PreToolUse (Write|Edit|NotebookEdit + Bash): perimetro impatto.

Il pre-budget vincola la SPESA dichiarata; questo hook vincola l'IMPATTO
dichiarato — dove il task può scrivere. Tre livelli indipendenti:

1. never_write (utente, permanente): pattern in `.fd-perimeter.json` nel
   progetto e/o `~/.claude/fable-director/perimeter.json`
   (`{"never_write": ["migrations/*", ".env*"]}`). Scrittura lì → deny
   SEMPRE, budget o no: è il "production writes senza backup" del kernel
   trasformato da consiglio a muro. Lo toglie solo l'utente dal file.
2. --paths (modello, per-task): il budget aperto può dichiarare il
   perimetro (`budget-open --paths "src/parser/*,tests/*"`). Scritture nel
   progetto fuori dal perimetro → deny con il comando di emendamento
   esplicito (`budget-amend --add-paths ... --reason ...`). Nessun --paths
   dichiarato → nessun vincolo (opt-in, come --verify).
3. deny_git (utente, permanente, opt-in): il perimetro sui file non vede i
   comandi Bash — `git reset --hard` distrugge senza toccare Write/Edit.
   Stessi file di config, chiave `deny_git`: lista di frammenti di
   sottocomando git (`{"deny_git": ["reset --hard", "clean -f",
   "branch -D", "checkout .", "restore .", "push --force", "push -f"]}`
   è il set consigliato — `push` semplice NON incluso di proposito).
   Comando Bash che contiene `git` E un frammento (match su comando
   normalizzato a spazi singoli) → deny, budget o no. Il match è
   volutamente grezzo: meglio un falso positivo spiegato — il messaggio
   dice all'utente di eseguire lui il comando o togliere il pattern — che
   un reset passato in un compound command. Ispirato a
   git-guardrails-claude-code (mattpocock/skills, MIT), reso opt-in e
   config-driven invece di lista fissa.

File FUORI dal progetto (scratchpad, /tmp, stato in HOME) non sono mai
vincolati dal livello 2: gli script di appoggio restano liberi. Il livello
1 li copre se il pattern è assoluto.

Matching fnmatch su path relativo al cwd, path assoluto e basename
(`*` attraversa anche le directory: "content/*" copre i sottoalberi).
NB: fnmatch è case-sensitive su POSIX e case-insensitive su Windows —
scrivi i pattern nel case reale dei file.

Fail-open by design (identico al gate pre-delega): errore interno → allow.
Uscita rapida a costo ~zero quando nessun livello è configurato.
"""
import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def norm(p):
    return str(p).replace("\\", "/")


def matches(abs_path, rel_path, patterns):
    base = Path(rel_path).name
    for pat in patterns:
        pat = norm(str(pat))
        if (fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(abs_path, pat)
                or fnmatch.fnmatch(base, pat)):
            return True
    return False


def log_deny(kind, payload):
    """Best-effort: telemetria oggettiva, mai bloccante."""
    try:
        import sqlite3
        from datetime import datetime, timezone
        db = Path.home() / ".claude" / "fable-director" / "telemetry.db"
        con = sqlite3.connect(db, timeout=0.5)
        con.execute("PRAGMA busy_timeout=500")
        con.execute(
            "INSERT INTO events(ts, event, payload) VALUES(?,?,?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             kind, json.dumps(payload, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception:
        pass


def load_configs(cwd):
    """Config perimetro: progetto prima, globale poi (merge additivo)."""
    out = []
    for cf in (Path(cwd) / ".fd-perimeter.json",
               Path.home() / ".claude" / "fable-director" / "perimeter.json"):
        if cf.is_file():
            try:
                out.append(json.loads(cf.read_text()))
            except (json.JSONDecodeError, OSError):
                pass
    return out


def check_git_guard(data):
    """Livello 3: comandi git distruttivi (tool Bash)."""
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if "git" not in cmd:
        return
    cwd = data.get("cwd") or os.getcwd()
    frags = []
    for cfg in load_configs(cwd):
        frags += [str(f) for f in (cfg.get("deny_git") or [])]
    if not frags:
        return
    flat = " ".join(cmd.split())
    for frag in frags:
        if " ".join(frag.split()) in flat:
            log_deny("perimeter_deny", {"level": "deny_git", "fragment": frag})
            deny(f"✕ FABLE-DIRECTOR git command DENIED — matches deny_git "
                 f"fragment '{frag}' (permanent user protection in "
                 f".fd-perimeter.json).\n"
                 f"No AI task may run it: if it is truly needed, the USER "
                 f"runs the command themselves or removes the fragment from "
                 f"the config — do not work around this.")
            return


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") == "Bash":
        check_git_guard(data)
        return
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path")
    if not fp:
        return
    cwd = data.get("cwd") or os.getcwd()
    # realpath, non abspath: un symlink dentro il progetto che punta fuori
    # non deve scavalcare perimetro né never_write (review esterna 2026-07-11)
    abs_path = norm(os.path.realpath(os.path.join(cwd, str(fp))))
    try:
        rel_path = norm(os.path.relpath(abs_path, os.path.realpath(cwd)))
        inside_project = not rel_path.startswith("..")
    except ValueError:
        # Windows, drive diversi: relpath impossibile. Il livello never_write
        # resta applicabile (pattern assoluti/basename); il livello 2 no.
        rel_path = abs_path
        inside_project = False

    # Livello 1 — never_write: progetto prima, globale poi.
    nw = []
    for cfg in load_configs(cwd):
        nw += list(cfg.get("never_write") or [])
    if nw and matches(abs_path, rel_path, nw):
        log_deny("perimeter_deny", {"path": rel_path, "level": "never_write"})
        deny(f"✕ FABLE-DIRECTOR write DENIED — '{rel_path}' matches a "
             f"never_write pattern (permanent user protection).\n"
             f"No AI task may write it: if it is truly needed, the USER "
             f"removes the pattern from .fd-perimeter.json — do not work "
             f"around this.")
        return

    # Livello 2 — perimetro dichiarato dal budget aperto (solo nel progetto).
    if not inside_project:
        return
    s = norm(cwd)
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    if not bfile.is_file():
        return
    try:
        budget = json.loads(bfile.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if budget.get("status") != "open":
        return
    paths = budget.get("paths") or []
    if not paths:
        return
    if matches(abs_path, rel_path, paths):
        return
    log_deny("perimeter_deny", {"path": rel_path, "level": "budget",
                                "declared": paths})
    deny(f"✕ FABLE-DIRECTOR write DENIED — '{rel_path}' is outside this "
         f"task's declared perimeter ({', '.join(map(str, paths))}).\n"
         f"If the task truly needs it, amend EXPLICITLY and retry:\n"
         f"fd-telemetry.py budget-amend --add-paths \"{rel_path}\" "
         f"--reason \"why it is needed\"")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: un bug del perimetro non blocca mai una scrittura

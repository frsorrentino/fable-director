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
   Match A TOKEN, non substring (review 1.35.1: il substring era
   bypassato da riordino argomenti e quoting): il comando viene diviso
   in invocazioni git (token `git` ovunque nel comando: copre compound,
   xargs, $(...) appiattito da shlex), ogni frammento matcha se TUTTI i
   suoi token sono presenti nell'invocazione — subcommand e long-flag
   per uguaglianza, flag corta anche combinata POSIX (`-f` matcha
   `-fd`). Ordine e argomenti frapposti irrilevanti: `git push origin
   main --force` e `git reset -q "--hard"` sono presi. `gitbook` o un
   frammento citato in un grep NON scattano (nessun token `git`);
   `echo git reset --hard` è un falso positivo accettato e spiegato —
   meglio di un reset passato. Quoting rotto (shlex fallisce) →
   fallback substring prudente sull'intero comando. Ispirato a
   git-guardrails-claude-code (mattpocock/skills, MIT), reso opt-in e
   config-driven invece di lista fissa.

Una config presente ma rotta (JSON invalido, deny_git non-lista) NON è
più silenzio (review 1.35.1): il muro che si spegne lo dice — warning
via systemMessage, throttled per mtime, mai bloccante. I frammenti
vuoti vengono scartati (un frammento vuoto matchava tutto: fail-open
invertito in fail-closed da un errore di tipo).

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
    """Best-effort: telemetria oggettiva, mai bloccante. Insert-with-retry
    single-sourced in fd-telemetry.log_event (review 1.35.1)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event(kind, payload)
    except Exception:
        pass


def load_configs(cwd):
    """Config perimetro: progetto prima, globale poi (merge additivo).

    Ritorna (configs, broken): broken elenca i file presenti ma non
    parsabili — il chiamante DEVE avvisare, un muro spento in silenzio è
    il failure mode peggiore (review 1.35.1)."""
    out, broken = [], []
    for cf in (Path(cwd) / ".fd-perimeter.json",
               Path.home() / ".claude" / "fable-director" / "perimeter.json"):
        if cf.is_file():
            try:
                out.append(json.loads(cf.read_text()))
            except (json.JSONDecodeError, OSError):
                broken.append(cf)
    return out, broken


def warn_broken(broken):
    """Avviso non bloccante per config rotta, throttled per (path, mtime):
    stesso file rotto = un solo avviso finché non viene toccato."""
    if not broken:
        return None
    import hashlib as _h
    lines = []
    marker_dir = Path.home() / ".claude" / "fable-director" / "grinding"
    for cf in broken:
        try:
            mt = int(cf.stat().st_mtime)
        except OSError:
            mt = 0
        key = _h.sha256(f"{cf}:{mt}".encode()).hexdigest()[:16]
        marker = marker_dir / f"perimeter-warn-{key}"
        if marker.exists():
            continue
        try:
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            pass
        lines.append(str(cf))
    if not lines:
        return None
    return ("⚠ FABLE-DIRECTOR: perimeter config file NOT parseable — its "
            "never_write/deny_git protections are OFF until fixed: "
            + ", ".join(lines))


def norm_token(tok):
    """Token senza quote residue (shlex le toglie già) e spazi."""
    return tok.strip()


def frag_matches(frag_tokens, cmd_tokens):
    """Ogni token del frammento deve essere presente nell'invocazione:
    subcommand/long-flag per uguaglianza, flag corta anche combinata
    POSIX (-f matcha -fd ma non --force né -file… solo lettere)."""
    cset = list(cmd_tokens)
    for ft in frag_tokens:
        hit = False
        for ct in cset:
            if ct == ft:
                hit = True
                break
            if (len(ft) == 2 and ft.startswith("-") and not ft.startswith("--")
                    and ct.startswith("-") and not ct.startswith("--")
                    and ft[1] in ct[1:] and ct[1:].isalpha()):
                hit = True  # flag corta combinata: -f dentro -fd
                break
        if not hit:
            return False
    return True


def git_invocations(cmd):
    """Tutte le finestre di token che seguono un token `git` (comando,
    compound, xargs, subshell appiattita). shlex gestisce il quoting:
    'git reset "--hard"' produce il token --hard pulito. Quoting rotto →
    None (il chiamante fa fallback substring, prudente)."""
    import shlex
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    out = []
    for i, t in enumerate(tokens):
        base = t.rsplit("/", 1)[-1]
        if base == "git":
            out.append([norm_token(x) for x in tokens[i + 1:]])
    return out


def dash_c_targets(invocations):
    """Path passati a `git -C <dir>`: la protezione del progetto COLPITO
    vale anche se la sessione gira altrove (review 1.35.1). Il `cd`
    persistito nella shell resta un limite dichiarato: lo stato della
    shell non è visibile all'hook."""
    out = []
    for inv in invocations or []:
        for i, t in enumerate(inv):
            if t == "-C" and i + 1 < len(inv):
                out.append(inv[i + 1])
            elif t.startswith("-C") and len(t) > 2:
                out.append(t[2:])
    return out


def check_git_guard(data):
    """Livello 3: comandi git distruttivi (tool Bash)."""
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()
    configs, broken = load_configs(cwd)
    if "git" in cmd:
        for target in dash_c_targets(git_invocations(cmd)):
            tdir = Path(os.path.expanduser(target))
            if not tdir.is_absolute():
                tdir = Path(cwd) / tdir
            if tdir.is_dir() and os.path.realpath(tdir) != os.path.realpath(cwd):
                c2, b2 = load_configs(tdir)
                configs += c2
                broken += b2
    warn = warn_broken(broken)
    frags = []
    for cfg in configs:
        dg = cfg.get("deny_git")
        if dg and not isinstance(dg, list):
            # stringa al posto della lista: iterarla per caratteri negava
            # OGNI comando git (fail-closed da errore di tipo) — meglio
            # ignorare la chiave e dirlo.
            warn = ((warn + "\n") if warn else "") + (
                "⚠ FABLE-DIRECTOR: deny_git must be a LIST of fragments — "
                "key ignored until fixed.")
            continue
        for f in (dg or []):
            ftoks = [norm_token(x) for x in str(f).split() if norm_token(x)]
            if ftoks:
                frags.append((str(f), ftoks))
    if not frags or "git" not in cmd:
        if warn:
            print(json.dumps({"systemMessage": warn}, ensure_ascii=False))
        return

    invocations = git_invocations(cmd)
    hit_frag = None
    if invocations is None:
        # quoting rotto: fallback substring prudente sull'intero comando
        flat = " ".join(cmd.split())
        for raw, _ in frags:
            if " ".join(raw.split()) in flat:
                hit_frag = raw
                break
    else:
        for inv in invocations:
            for raw, ftoks in frags:
                if frag_matches(ftoks, inv):
                    hit_frag = raw
                    break
            if hit_frag:
                break
    if hit_frag:
        log_deny("perimeter_deny", {"level": "deny_git", "fragment": hit_frag})
        deny(f"✕ FABLE-DIRECTOR git command DENIED — matches deny_git "
             f"fragment '{hit_frag}' (permanent user protection in "
             f".fd-perimeter.json).\n"
             f"No AI task may run it: if it is truly needed, the USER "
             f"runs the command themselves or removes the fragment from "
             f"the config — do not work around this.")
    elif warn:
        print(json.dumps({"systemMessage": warn}, ensure_ascii=False))


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
    configs, broken = load_configs(cwd)
    warn = warn_broken(broken)
    if warn:
        print(json.dumps({"systemMessage": warn}, ensure_ascii=False))
    nw = []
    for cfg in configs:
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

#!/usr/bin/env python3
"""Istruzioni ripetute nei transcript — proposte di vincoli, zero token di modello.

Legge i turni umani veri degli ultimi N giorni (~/.claude*/projects/*/*.jsonl),
li spezza in frasi e raggruppa quelle simili (shingle di 3 parole, Jaccard ≥ 0,5).
Un gruppo è una ripetizione quando compare in almeno 3 sessioni diverse e
contiene un marcatore di regola. Se il testo è già in un CLAUDE.md, nella
memoria o nei vincoli di claude-master non è proposto: finisce nell'elenco
«già scritto, ignorato N volte».

Niente entra da solo: il plugin propone, l'utente dice sì.
  - con claude-master (sul PATH e con tasks.db):
    `claude-master task constraint add (--project P | --topic T) TESTO`
  - senza: una riga [candidata] nel playbook (~/.claude/delega-playbook.md).

Uso:
  repeat-finder.py [scan] [--days N] [--min-sessions K] [--json]
  repeat-finder.py accept ID [--topic T]
  repeat-finder.py dismiss ID
  repeat-finder.py pending          (numero di proposte nuove, per lo status)

Tutto resta sul disco: le frasi possono contenere nomi di clienti, come i
transcript da cui vengono. La telemetria registra solo conteggi.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
BASE = Path.home() / ".claude" / "fable-director"
OUT = BASE / "repeats.json"
STATE = BASE / "repeats-state.json"
PLAYBOOK = Path.home() / ".claude" / "delega-playbook.md"
CM_DB = Path.home() / ".config" / "claude-master" / "tasks.db"

MIN_WORDS = 6
JACCARD = 0.5
FORWARDED = "forwarded"  # tutti gli inoltri contano come una sessione sola

_spec = importlib.util.spec_from_file_location("fdt", str(HERE / "fd-telemetry.py"))
fdt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fdt)

PASTED_RE = re.compile(r"<pasted_content\b.*?</pasted_content>", re.S | re.I)
FORWARD_RE = re.compile(r"(?i)^\s*(da master\b|dall.utente via telefono|another claude session)")
SPLIT_RE = re.compile(r"(?<=[.;!?])\s+|\n+")
PATH_RE = re.compile(r"(?:~|\.{0,2})?/\S+|\S+\.(?:py|sh|md|json|js|ts|php|txt)\b")
NUM_RE = re.compile(r"\d+(?:[.,:]\d+)*")
RULE_RE = re.compile(
    r"(?i)(`[^`]+`|\b(sempre|mai|non|usa|usare|lancia|lanciare|prima di|ricorda|ricordati|"
    r"evita|devi|always|never|use|run|don'?t|do not|avoid|remember|must)\b)")


def normalize(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = PATH_RE.sub(" _path_ ", s)
    s = NUM_RE.sub(" _n_ ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return s.split()


def shingles(words):
    if len(words) < 3:
        return {tuple(words)}
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def sentences(text):
    text = PASTED_RE.sub(" ", text)
    for raw in SPLIT_RE.split(text):
        raw = raw.strip(" -*>\t")
        w = normalize(raw)
        if len(w) >= MIN_WORDS:
            yield raw, w


def config_dirs():
    dirs = set(Path.home().glob(".claude*"))
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        dirs.add(Path(os.environ["CLAUDE_CONFIG_DIR"]))
    return sorted(d for d in dirs if (d / "projects").is_dir())


def collect(days):
    """[(raw, words, session, cwd, date)] dai turni umani degli ultimi N giorni."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = []
    seen = set()  # una conversazione ripresa ricopia i turni nel file nuovo
    for cdir in config_dirs():
        for f in (cdir / "projects").glob("*/*.jsonl"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < cutoff:
                    continue
                fh = open(f, encoding="utf-8", errors="ignore")
            except OSError:
                continue
            turns = []
            with fh:
                for line in fh:
                    if '"user"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("isCompactSummary") or r.get("isVisibleInTranscriptOnly"):
                        continue
                    if r.get("uuid"):
                        if r["uuid"] in seen:
                            continue
                        seen.add(r["uuid"])
                    t = fdt.human_turn_text(r)
                    if not t or t.startswith("[Request interrupted"):
                        continue
                    ts = fdt.parse_ts(r.get("timestamp") or "")
                    if ts and ts < cutoff:
                        continue
                    fwd = ((r.get("origin") or {}).get("kind") == "peer"
                           or bool(FORWARD_RE.match(t)))
                    turns.append((FORWARD_RE.sub("", t, count=1) if fwd else t, fwd,
                                  r.get("cwd") or "", ts))
            # Un solo turno umano diretto = prompt da script (`claude -p`,
            # benchmark, generatori di recap): non è un'abitudine di chi scrive.
            if sum(1 for _t, fwd, _c, _ts in turns if not fwd) < 2:
                turns = [x for x in turns if x[1]]
            for t, fwd, cwd, ts in turns:
                sess = FORWARDED if fwd else f"{cdir.name}/{f.stem}"
                for raw, w in sentences(t):
                    items.append((raw, w, sess, cwd, (ts.date().isoformat() if ts else "")))
    return items


def cluster(items):
    """Raggruppamento goloso: ogni frase va nel primo gruppo il cui
    rappresentante ha Jaccard ≥ soglia; indice inverso per shingle."""
    groups, index = [], {}
    for raw, w, sess, cwd, day in items:
        sh = shingles(w)
        cand = set()
        for s in sh:
            cand.update(index.get(s, ()))
        best = None
        for gi in sorted(cand):
            if jaccard(sh, groups[gi]["sh"]) >= JACCARD:
                best = gi
                break
        if best is None:
            best = len(groups)
            groups.append({"sh": sh, "members": []})
            for s in sh:
                index.setdefault(s, []).append(best)
        groups[best]["members"].append((raw, sess, cwd, day))
    return groups


def known_sentences():
    """[(shingle, fonte)] dai testi già scritti: CLAUDE.md globali e di
    progetto, memorie, vincoli di claude-master."""
    out = []

    def add_text(text, source):
        for _raw, w in sentences(text):
            out.append((shingles(w), source))

    for cdir in config_dirs() + [Path.home() / ".claude"]:
        f = cdir / "CLAUDE.md"
        if f.is_file():
            add_text(f.read_text(errors="ignore"), str(f).replace(str(Path.home()), "~"))
        for m in cdir.glob("projects/*/memory/*.md"):
            add_text(m.read_text(errors="ignore"), "memoria " + m.name)
    try:
        con = sqlite3.connect(f"file:{CM_DB}?mode=ro", uri=True)
        for (text,) in con.execute("SELECT text FROM constraints"):
            add_text(text or "", "claude-master constraints")
        con.close()
    except Exception:
        pass
    return out


def project_md(cwds):
    out = []
    for c in cwds:
        for f in (Path(c) / "CLAUDE.md", Path(c) / ".claude" / "CLAUDE.md"):
            try:
                if f.is_file():
                    for _raw, w in sentences(f.read_text(errors="ignore")):
                        out.append((shingles(w), str(f).replace(str(Path.home()), "~")))
            except OSError:
                pass
    return out


def already_written(sh, known):
    """Fonte se la frase è contenuta (≥ soglia dei suoi shingle) in un testo noto."""
    for ksh, src in known:
        if sh and len(sh & ksh) / len(sh) >= JACCARD:
            return src
    return None


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"accepted": [], "dismissed": []}


def scan(days, min_sessions):
    groups = cluster(collect(days))
    known = known_sentences()
    proposals, ignored = [], []
    for g in groups:
        sess = {m[1] for m in g["members"]}
        if len(sess) < min_sessions:
            continue
        if not any(RULE_RE.search(m[0]) for m in g["members"]):
            continue
        direct = [m for m in g["members"] if m[1] != FORWARDED] or g["members"]
        text = max({m[0] for m in direct}, key=lambda r: sum(1 for m in direct if m[0] == r))
        cwds = sorted({m[2] for m in g["members"] if m[2]})
        gid = hashlib.sha1(" ".join(normalize(text)).encode()).hexdigest()[:8]
        entry = {"id": gid, "text": text, "sessions": len(sess),
                 "occurrences": len(g["members"]), "projects": cwds,
                 "dates": sorted({m[3] for m in g["members"] if m[3]})}
        src = already_written(g["sh"], known + project_md(cwds))
        if src:
            entry["source"] = src
            ignored.append(entry)
            continue
        entry["scope"] = {"project": cwds[0]} if len(cwds) == 1 else {"topic": "global"}
        proposals.append(entry)
    key = lambda e: (-e["sessions"], -e["occurrences"])
    data = {"generated_at": fdt.now_iso(), "days": days, "min_sessions": min_sessions,
            "proposals": sorted(proposals, key=key), "known": sorted(ignored, key=key)}
    BASE.mkdir(parents=True, exist_ok=True)
    fdt.write_json_atomic(OUT, data)
    try:
        fdt.log_event("repeat_proposed", {"proposals": len(proposals), "known": len(ignored),
                                          "days": days})
    except Exception:
        pass
    return data


def short(e):
    where = (f"progetto {e['scope']['project']}" if "project" in e.get("scope", {})
             else f"{len(e['projects'])} progetti")
    return f"{e['sessions']} sessioni, {where}, {e['dates'][0] if e['dates'] else '?'}→{e['dates'][-1] if e['dates'] else '?'}"


def print_scan(data):
    st = load_state()
    done = set(st["accepted"]) | set(st["dismissed"])
    props = [p for p in data["proposals"] if p["id"] not in done]
    print(f"Istruzioni ripetute, ultimi {data['days']} giorni "
          f"(≥{data['min_sessions']} sessioni): {len(props)} proposte, "
          f"{len(data['known'])} già scritte")
    for p in props:
        print(f"\n[{p['id']}] «{p['text']}»\n  {short(p)}")
        print(f"  sì → repeat-finder.py accept {p['id']}   ·   no → repeat-finder.py dismiss {p['id']}")
    if data["known"]:
        print("\nGià scritto, ignorato N volte (il problema non è un vincolo mancante):")
        for k in data["known"]:
            print(f"  [{k['id']}] «{k['text']}» — ripetuto in {k['sessions']} sessioni, "
                  f"già in {k['source']}")


def find(pid):
    try:
        data = json.loads(OUT.read_text())
    except Exception:
        sys.exit("nessuna scansione: lancia prima repeat-finder.py scan")
    for p in data.get("proposals", []):
        if p["id"] == pid:
            return p
    sys.exit(f"proposta {pid} non trovata nell'ultima scansione")


def save_state(st):
    BASE.mkdir(parents=True, exist_ok=True)
    fdt.write_json_atomic(STATE, st)


def accept(pid, topic=None):
    p = find(pid)
    scope = {"topic": topic} if topic else p["scope"]
    cm = shutil.which("claude-master")
    if cm and CM_DB.is_file():
        flag, val = (("--project", scope["project"]) if "project" in scope
                     else ("--topic", scope["topic"]))
        r = subprocess.run([cm, "task", "constraint", "add", flag, val, p["text"]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"claude-master ha rifiutato il vincolo: {(r.stderr or r.stdout).strip()}")
        where = f"claude-master ({flag} {val})"
    else:
        month = datetime.now().strftime("%Y-%m")
        line = (f"- [candidata {month}] istruzione ripetuta: {p['text']} "
                f"(ripetuta in {p['sessions']} sessioni, {short(p)}) (uses:0 ok:0 ko:0)\n")
        PLAYBOOK.parent.mkdir(parents=True, exist_ok=True)
        prev = PLAYBOOK.read_text() if PLAYBOOK.is_file() else ""
        PLAYBOOK.write_text(prev + ("" if not prev or prev.endswith("\n") else "\n") + line)
        where = f"playbook {PLAYBOOK}"
    st = load_state()
    st["accepted"] = sorted(set(st["accepted"]) | {pid})
    save_state(st)
    try:
        fdt.log_event("repeat_accepted", {"route": "claude-master" if cm and CM_DB.is_file()
                                          else "playbook", "sessions": p["sessions"]})
    except Exception:
        pass
    print(f"registrata in {where}")


def dismiss(pid):
    find(pid)
    st = load_state()
    st["dismissed"] = sorted(set(st["dismissed"]) | {pid})
    save_state(st)
    print(f"{pid} scartata: non sarà più proposta")


def pending():
    try:
        data = json.loads(OUT.read_text())
    except Exception:
        print(0)
        return
    st = load_state()
    done = set(st["accepted"]) | set(st["dismissed"])
    print(sum(1 for p in data.get("proposals", []) if p["id"] not in done))


def main(argv):
    cmd = argv[0] if argv and not argv[0].startswith("-") else "scan"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    opts = {"--days": "7", "--min-sessions": "3", "--topic": None}
    pos, flags, i = [], set(), 0
    while i < len(rest):
        a = rest[i]
        if a in opts and i + 1 < len(rest):
            opts[a] = rest[i + 1]
            i += 2
        elif a == "--json":
            flags.add(a)
            i += 1
        elif not a.startswith("-"):
            pos.append(a)
            i += 1
        else:
            sys.exit(f"argomento non riconosciuto: {a}\n{__doc__}")
    if cmd == "scan":
        data = scan(int(opts["--days"]), int(opts["--min-sessions"]))
        if "--json" in flags:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        else:
            print_scan(data)
    elif cmd in ("accept", "dismiss") and pos:
        accept(pos[0], opts["--topic"]) if cmd == "accept" else dismiss(pos[0])
    elif cmd == "pending":
        pending()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

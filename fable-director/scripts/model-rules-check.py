#!/usr/bin/env python3
"""Controllo delle istruzioni rispetto alle regole di un modello (solo lettura).

Legge CLAUDE.md, skill, agenti e settings dei due account (~/.claude,
~/.claude-pixel) e kernel/skill/agenti dei nostri plugin INSTALLATI, e li
confronta con model-rules/<modello>.json. Le regole sono dati: un modello
nuovo e' un file nuovo, lo script non cambia. Non modifica nessun file.

Tipi di regola:
  forbid   — ogni riga che combacia e' una segnalazione (file:riga + estratto)
  require  — per account, almeno un file dello scope deve combaciare
  setting  — la chiave deve comparire in settings.json dell'account

Usage:
  model-rules-check.py [--model ID] [--json]
    --model ID   regole del modello (prefisso piu' lungo sui nomi dei file,
                 es. claude-opus-5-5[1m] → claude-opus-5-5.json); senza,
                 tutti i file di regole
    --json       uscita strutturata
Exit: 0 nessuna segnalazione, 1 segnalazioni, 2 errore d'uso.
"""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RULES_DIR = Path(__file__).resolve().parent.parent / "model-rules"
MAX_EXCERPT = 100


def expand(p):
    s = str(p)
    return Path.home() / s[2:] if s.startswith("~/") else Path(s)


def tilde(p):
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h + "/") else s


def rule_files(model=None):
    files = sorted(f for f in RULES_DIR.glob("*.json") if not f.name.startswith("_"))
    if not model:
        return files
    m = str(model).split("[", 1)[0]
    best = max((f for f in files if m.startswith(f.stem)), key=lambda f: len(f.stem),
               default=None)
    return [best] if best else []


def own_plugin_roots(account, names):
    """installPath dei nostri plugin da installed_plugins.json dell'account."""
    roots = []
    try:
        data = json.loads((account / "plugins" / "installed_plugins.json").read_text())
    except Exception:
        return roots
    for key, entries in (data.get("plugins") or {}).items():
        if key.split("@", 1)[0] not in names:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            p = Path(str((e or {}).get("installPath") or ""))
            if str(p) and p.is_dir() and p not in roots:
                roots.append(p)
    return roots


def scope_files(scope, account, plugins):
    if scope == "claude_md":
        return [account / "CLAUDE.md"]
    if scope == "skills":
        return sorted((account / "skills").rglob("SKILL.md"))
    if scope == "agents":
        return sorted((account / "agents").glob("*.md"))
    if scope == "settings":
        return [account / "settings.json"]
    out = []
    for root in plugins:
        if scope == "plugin_kernel":
            out.append(root / "kernel.md")
        elif scope == "plugin_claude_md":
            out.append(root / "CLAUDE.md")
        elif scope == "plugin_skills":
            out += sorted((root / "skills").rglob("SKILL.md"))
        elif scope == "plugin_agents":
            out += sorted((root / "agents").glob("*.md"))
    return out


def read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def check_account(account, rules, plugins):
    findings = []
    for r in rules:
        files = []
        for sc in r.get("scope") or []:
            files += [f for f in scope_files(sc, account, plugins) if f.is_file()]
        kind = r.get("kind")
        if kind == "forbid":
            rx = re.compile(r["pattern"])
            for f in files:
                text = read(f)
                for n, line in enumerate((text or "").splitlines(), 1):
                    m = rx.search(line)
                    if m:
                        lo = max(0, m.start() - 30)
                        findings.append({"rule": r["id"], "kind": kind, "path": tilde(f),
                                         "line": n, "excerpt": line[lo:lo + MAX_EXCERPT].strip()})
        elif kind == "require":
            rx = re.compile(r["pattern"])
            if not any(rx.search(read(f) or "") for f in files):
                findings.append({"rule": r["id"], "kind": kind, "path": None, "line": None,
                                 "excerpt": f"no match in {', '.join(r.get('scope') or [])}"
                                            f" ({len(files)} file(s) read)"})
        elif kind == "setting":
            key = r["key"]
            present = None
            for f in files:
                try:
                    d = json.loads(read(f) or "{}")
                except Exception:
                    continue
                if key in d:
                    present = d[key]
            if present is None:
                findings.append({"rule": r["id"], "kind": kind, "path": tilde(account / "settings.json"),
                                 "line": None, "excerpt": f"{key} not set"})
    return findings


def main(argv):
    model, as_json = None, False
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--model" and args:
            model = args.pop(0)
        elif a == "--json":
            as_json = True
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    files = rule_files(model)
    if not files:
        print(f"no rule file for {model} in {tilde(RULES_DIR)}", file=sys.stderr)
        return 2
    try:
        targets = json.loads((RULES_DIR / "_targets.json").read_text())
    except Exception as e:
        print(f"_targets.json unreadable: {e}", file=sys.stderr)
        return 2
    report, total = [], 0
    for rf in files:
        spec = json.loads(rf.read_text())
        rules = spec.get("rules") or []
        for acc_s in targets.get("accounts") or []:
            acc = expand(acc_s)
            if not acc.is_dir():
                continue
            plugins = own_plugin_roots(acc, set(targets.get("own_plugins") or []))
            f = check_account(acc, rules, plugins)
            total += len(f)
            report.append({"model": spec.get("model", rf.stem), "account": tilde(acc),
                           "plugins": [tilde(p) for p in plugins], "rules": len(rules),
                           "findings": f})
    if as_json:
        print(json.dumps({"findings": total, "results": report}, ensure_ascii=False, indent=1))
        return 1 if total else 0
    why = {}
    for rf in files:
        for r in json.loads(rf.read_text()).get("rules") or []:
            why[r["id"]] = r.get("why", "")
    for block in report:
        print(f"== {block['model']} — {block['account']} "
              f"({block['rules']} rules, {len(block['plugins'])} own plugin(s))")
        if not block["findings"]:
            print("  clean")
        for f in block["findings"]:
            where = f"{f['path']}:{f['line']}" if f["line"] else (f["path"] or "")
            print(f"  {f['kind'].upper():8} {f['rule']}  {where}  {f['excerpt']}".rstrip())
    seen = sorted({f["rule"] for b in report for f in b["findings"]})
    if seen:
        print("\nWhy:")
        for rid in seen:
            print(f"  {rid}: {why.get(rid, '')}")
    print(f"\n{total} finding(s) — report only, no file changed.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

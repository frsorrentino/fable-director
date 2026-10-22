#!/usr/bin/env python3
"""Audit dell'usage segnaposto nei transcript dei subagenti, per versione di
Claude Code. Sola lettura, zero token modello.

Claude Code < 2.1.307 registrava le risposte in streaming di un subagente con
l'usage di message_start (output_tokens 1) e stop_reason vuoto (changelog
2.1.307: «Fixed subagent transcripts recording placeholder token usage and an
empty stop reason for streamed responses»). Le componenti di input (input,
cache_read, cache_creation) arrivano con message_start e restano vere; manca
l'output, la parte a 5× dell'eq. Ogni misura letta dai file agent-*.jsonl su
quelle versioni (eq per agente, taratura della finestra, output dei subagenti,
delegation_overhead, coordination_cost, rapporto turno main/subagente) legge
basso. Questo script dice QUANTE misure ne soffrono, divise per versione, cosi'
che i numeri del kernel e del playbook si rifacciano sulle versioni pulite.

Un turno e' segnaposto quando nessun suo record porta stop_reason e l'output
massimo visto resta <= 1 (PLACEHOLDER_OUT_MAX in fd-telemetry.py). Un agente e'
«all-placeholder» quando lo sono tutti i suoi turni: il suo eq e' monco.

Usage: usage-placeholder-audit.py [--root DIR]... [--main] [--json]
  --root  cartella projects di un account (default: $CLAUDE_CONFIG_DIR/projects
          o ~/.claude/projects); ripetibile per piu' account (~/.claude-pixel)
  --main  conta anche i transcript principali, senza i record isSidechain
          (controllo: il difetto riguarda i subagenti, il main deve stare a zero)
  --json  output macchina: by_version, before_2_1_307, from_2_1_307, unknown,
          main_by_version
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

FIX_VERSION = (2, 1, 307)
EMPTY = {"agents": 0, "agents_placeholder": 0, "turns": 0,
         "placeholder_turns": 0, "output_tokens": 0}


def load_fdt():
    spec = importlib.util.spec_from_file_location(
        "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_root():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(cfg) if cfg else Path.home() / ".claude") / "projects"


def add(bucket, s):
    bucket["agents"] += 1
    bucket["turns"] += s["turns"]
    bucket["placeholder_turns"] += s["placeholder_turns"]
    bucket["output_tokens"] += s["output_tokens"]
    if s["turns"] and s["placeholder_turns"] == s["turns"]:
        bucket["agents_placeholder"] += 1


def merge(dst, src):
    for k in EMPTY:
        dst[k] += src[k]


def split(by, version_key):
    before, after, unknown = dict(EMPTY), dict(EMPTY), dict(EMPTY)
    for v, b in by.items():
        if v == "unknown":
            merge(unknown, b)
        elif version_key(v) >= FIX_VERSION:
            merge(after, b)
        else:
            merge(before, b)
    return before, after, unknown


def share(b):
    return f"{100.0 * b['placeholder_turns'] / b['turns']:.0f}%" if b["turns"] else "-"


def print_table(title, by, version_key):
    print(title)
    print(f"  {'version':<12}{'agents':>8}{'all-ph':>8}{'turns':>9}{'ph turns':>10}"
          f"{'share':>7}{'output tok':>13}")
    for v in sorted(by, key=lambda x: (x == "unknown", version_key(x))):
        b = by[v]
        print(f"  {v:<12}{b['agents']:>8}{b['agents_placeholder']:>8}{b['turns']:>9}"
              f"{b['placeholder_turns']:>10}{share(b):>7}{b['output_tokens']:>13,}")


def print_split(label, b):
    print(f"  {label:<12} agents {b['agents']} (all-placeholder {b['agents_placeholder']}), "
          f"turns {b['turns']}, placeholder {b['placeholder_turns']} ({share(b)})")


def main(argv):
    roots, want_json, want_main = [], False, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root" and i + 1 < len(argv):
            roots.append(Path(argv[i + 1]).expanduser())
            i += 2
            continue
        if a == "--json":
            want_json = True
        elif a == "--main":
            want_main = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"argomento sconosciuto: {a}", file=sys.stderr)
            return 2
        i += 1
    if not roots:
        roots = [default_root()]
    fdt = load_fdt()
    by, by_main, n_files = {}, {}, 0
    for root in roots:
        if not root.is_dir():
            print(f"cartella assente: {root}", file=sys.stderr)
            continue
        for f in root.rglob("agent-*.jsonl"):
            if "subagents" not in f.parts:
                continue
            s = fdt.placeholder_scan(f)
            if not s["turns"]:
                continue
            n_files += 1
            add(by.setdefault(s["version"] or "unknown", dict(EMPTY)), s)
        if want_main:
            for f in root.glob("*/*.jsonl"):
                s = fdt.placeholder_scan(f, skip_sidechain=True)
                if s["turns"]:
                    add(by_main.setdefault(s["version"] or "unknown", dict(EMPTY)), s)
    before, after, unknown = split(by, fdt.version_key)
    if want_json:
        out = {"roots": [str(r) for r in roots], "files": n_files, "by_version": by,
               "before_2_1_307": before, "from_2_1_307": after, "unknown": unknown}
        if want_main:
            out["main_by_version"] = by_main
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"Subagent transcripts: {n_files} agent files with usage under "
          + ", ".join(str(r) for r in roots))
    print_table("Per Claude Code version (agents = agent-*.jsonl files; all-ph = every "
                "turn placeholder; ph turns = output_tokens <= 1 and no stop_reason):",
                by, fdt.version_key)
    print_split("< 2.1.307:", before)
    print_split(">= 2.1.307:", after)
    if unknown["agents"]:
        print_split("no version:", unknown)
    if want_main:
        print_table("Main transcripts, control (isSidechain records excluded; expected ~0):",
                    by_main, fdt.version_key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

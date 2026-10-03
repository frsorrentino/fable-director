#!/usr/bin/env python3
"""Lint del --verify: un controllo vale solo se PUO' fallire.

Il kernel chiede un done verificabile prima di delegare; `budget-open --verify`
lo rende un comando che lo Stop hook esegue. Ma un comando che esce sempre 0
(`true`, `echo ok`, `npm test | tail -3`, `pytest || true`, `grep -c X f`) non
verifica niente: il verde e' garantito prima ancora di lavorare. Questo modulo
classifica il verify e dice perche' e' finto e come renderlo vero.

classify(verify, cwd=None) -> (kind, reason)
  kind: "missing" | "prose" | "command" | "fake"
  reason: None per "command", altrimenti una frase con la correzione.

Misurato il 03/10/2026 su 30 giorni: 124 verify dichiarati su 161 budget, circa
meta' in prosa, e comandi veri (`test -s`, `grep -q`, `git diff --quiet`, script
per percorso) trattati come prosa dal vecchio riconoscimento a runner fissi:
mai eseguiti. Il riconoscimento qui sotto li include.

Fail-open sul parsing: una riga che shlex non sa spezzare (virgolette aperte)
e' un comando non analizzabile, non un finto — il gate nega solo quando sa dire
perche'.

CLI: verify-lint.py "<verify>" [cwd]  → stampa kind e motivo, exit 0 se command.
"""
import os
import re
import shlex
import sys
from pathlib import Path

# Primo comando riconosciuto come eseguibile. Include i comandi che non possono
# fallire (true, echo…): vanno riconosciuti come comandi per poterli dire finti.
RUNNERS = {
    "python3", "python", "pytest", "bash", "sh", "zsh", "npm", "npx", "node",
    "make", "cargo", "go", "php", "composer", "phpunit", "pnpm", "yarn", "uv",
    "uvx", "ruby", "perl", "deno", "bun", "tsc", "ruff", "mypy", "shellcheck",
    "test", "[", "[[", "git", "gh", "grep", "egrep", "fgrep", "rg", "jq", "diff",
    "cmp", "ls", "find", "cd", "true", "false", ":", "echo", "printf", "exit",
    "wc", "cat", "head", "tail", "stat", "file", "ffprobe", "curl", "timeout",
    "env", "claude", "set",
}
SCRIPT_SUFFIXES = (".sh", ".py", ".js", ".mjs", ".php", ".rb", ".pl")
# Escono 0 qualunque cosa succeda (a parita' di argomenti sensati).
ALWAYS_OK = {"true", ":", "echo", "printf", "wc", "cd", "set"}
# Ultimo stadio di una pipe: l'esito della pipe e' il loro, e leggono stdin.
PIPE_SINKS = ALWAYS_OK | {"tail", "head", "cat", "sort", "uniq", "tee", "tr",
                          "cut", "find", "less", "more", "column", "nl"}
OPS = {"&&", "||", ";", "|", "&"}
TRIVIAL_PY = re.compile(
    r"^\s*(pass|print\(.*\)|(sys\.)?exit\(\s*0?\s*\)|quit\(\s*0?\s*\)|import sys|"
    r"console\.log\(.*\)|process\.exit\(\s*0?\s*\))\s*$")

FIX_HINT = ("Make it a check that exits non-zero when the done does not hold: "
            "`python3 -m pytest -q`, `test -s out.md && grep -q '## Esito' out.md`, "
            "`test $(grep -c X f) -eq 0`, `git diff --quiet`, a script that asserts.")


def _strip_substitutions(s):
    """$(...) e `...` → segnaposto: dentro c'e' un comando il cui esito NON e'
    quello della riga (es. `test $(grep -c X f) -eq 0`)."""
    out, i, n = [], 0, len(s)
    while i < n:
        if s.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            out.append(" __SUB__ ")
            i = j
        elif s[i] == "`":
            j = s.find("`", i + 1)
            out.append(" __SUB__ ")
            i = n if j < 0 else j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


REDIR = re.compile(r"\d*>&\d*-?|&>>?")


def _tokens(s):
    s = REDIR.sub(" __REDIR__ ", _strip_substitutions(s))
    lex = shlex.shlex(s.replace("\n", " ; "),
                      posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    return list(lex)


def _split(tokens, ops):
    """Spezza su un insieme di operatori: [(op_precedente, [token…]), …]."""
    parts, cur, prev = [], [], None
    for t in tokens:
        if t in ops:
            parts.append((prev, cur))
            cur, prev = [], t
        else:
            cur.append(t)
    parts.append((prev, cur))
    return [(op, p) for op, p in parts if p]


def _core(argv):
    """Toglie `!`, assegnazioni X=y, `timeout N`, `env`: resta il comando."""
    neg = False
    a = list(argv)
    while a:
        if a[0] == "!":
            neg, a = not neg, a[1:]
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a[0]):
            a = a[1:]
        elif a[0] == "env":
            a = a[1:]
        elif a[0] == "timeout" and len(a) > 1:
            a = a[2:]
        else:
            break
    return neg, a


def _looks_runnable(tok, cwd):
    if tok in RUNNERS:
        return True
    if tok.startswith(("/", "./", "../", "~/")):
        return True
    if "/" in tok:
        if tok.endswith(SCRIPT_SUFFIXES):
            return True
        p = Path(cwd or ".") / tok
        return p.is_file() and os.access(p, os.X_OK)
    return False


def _cmd_never_fails(argv, pipe_last=False, pipefail=False):
    """Motivo se questo singolo comando esce 0 comunque, altrimenti None."""
    neg, a = _core(argv)
    if not a or neg:
        return None
    name = Path(a[0]).name if "/" in a[0] else a[0]
    if name == "exit" and (len(a) == 1 or a[1] == "0"):
        return "`exit 0` always succeeds"
    if name in ALWAYS_OK:
        return f"`{name}` always exits 0"
    if pipe_last and not pipefail and name in PIPE_SINKS:
        return (f"the exit status of a pipe is its last stage, `{name}`, which "
                f"never fails (use `set -o pipefail` or drop the pipe)")
    if "--exit-zero" in a:
        return "`--exit-zero` turns every finding into success"
    if name == "git" and len(a) > 1:
        sub = a[1]
        if sub in ("status", "log", "show", "branch", "remote"):
            return f"`git {sub}` reports, it does not check: it exits 0"
        if sub == "diff" and not {"--quiet", "--exit-code", "--check"} & set(a):
            return "`git diff` exits 0 with or without changes (add `--quiet`)"
    if name in ("grep", "egrep", "fgrep", "rg"):
        flags = [t for t in a[1:] if t.startswith("-") and not t.startswith("--")]
        if any("c" in f[1:] for f in flags) or "--count" in a:
            return ("`grep -c` prints a count and exits 0 on any match: it never "
                    "compares with the count you expect (`test $(grep -c X f) -eq N`)")
    if name in ("python", "python3", "node") and len(a) >= 3 and a[1] in ("-c", "-e"):
        stmts = [s for s in re.split(r"[;\n]", a[2]) if s.strip()]
        if stmts and all(TRIVIAL_PY.match(s) for s in stmts):
            return f"`{name} {a[1]}` only prints or exits 0: it asserts nothing"
    if name in ("bash", "sh", "zsh") and len(a) >= 3 and a[1] == "-c":
        try:
            return _fake_reason(_tokens(a[2]), "pipefail" in a[2])
        except ValueError:
            return None
    return None


# Dentro una catena && questi non rendono finta la catena intera (un `npm test
# && echo ok` e' un controllo vero), ma DICHIARANO un controllo che non c'e':
# `npm test | tail -3 && tsc` ingoia il fallimento dei test.
def _swallows(cmd, pipefail):
    stages = _split(cmd, {"|"})
    neg, a = _core(stages[-1][1])
    if not a or neg:
        return None
    if len(stages) > 1 and not pipefail and a[0] in PIPE_SINKS:
        return _cmd_never_fails(stages[-1][1], pipe_last=True, pipefail=False)
    r = _cmd_never_fails(stages[-1][1])
    if r and a[0] not in ALWAYS_OK and a[0] != "exit":
        return r
    return None


def _and_or_never_fails(tokens, pipefail):
    """Una lista && / || : motivo se il suo esito e' sempre 0."""
    items = _split(tokens, {"&&", "||"})
    if not items:
        return None
    reasons = []
    for _, cmd in items:
        stages = _split(cmd, {"|"})
        last = stages[-1][1] if stages else cmd
        r = _cmd_never_fails(last, pipe_last=len(stages) > 1, pipefail=pipefail)
        reasons.append(r)
    last_op = items[-1][0]
    if last_op != "||":
        for _, cmd in items:
            r = _swallows(cmd, pipefail)
            if r:
                return r
    if last_op == "||" and reasons[-1]:
        return (f"`|| {' '.join(items[-1][1])}` swallows the failure: "
                + reasons[-1])
    # Catena di &&: fallisce se UN comando fallisce. Finta solo se nessuno puo'.
    if all(reasons):
        return reasons[-1]
    return None


def _fake_reason(tokens, pipefail):
    seq = _split(tokens, {";", "&"})
    if not seq:
        return None
    # Sequenza con ; : l'esito e' quello dell'ULTIMO elemento.
    r = _and_or_never_fails(seq[-1][1], pipefail)
    if r and len(seq) > 1:
        r = f"after `;` only the last command decides the exit status, and {r}"
    return r


PROSE = "--verify is prose, not a command: no hook can run it. "


def classify(verify, cwd=None):
    v = str(verify or "").strip()
    if not v:
        return "missing", ("no --verify: a delegated task needs a check that can "
                           "fail. " + FIX_HINT)
    try:
        tokens = _tokens(v)
    except ValueError:
        first = v.split()[0]
        if _looks_runnable(first, cwd):
            return "command", None  # non analizzabile: fail-open
        return "prose", PROSE + FIX_HINT
    seq = _split(tokens, {";", "&"})
    if not seq:
        return "missing", "empty --verify. " + FIX_HINT
    # Ogni elemento della riga deve iniziare con un comando: `ls x | wc -l ≥ 5;
    # fermi in out/` e' prosa con un comando davanti, non un controllo.
    for _, part in seq:
        for _, item in _split(part, {"&&", "||"}):
            _, cmd = _core(_split(item, {"|"})[0][1])
            if not cmd or not _looks_runnable(cmd[0], cwd):
                return "prose", PROSE + FIX_HINT
    reason = _fake_reason(tokens, "pipefail" in v)
    if reason:
        return "fake", f"--verify is not a real check: {reason}. {FIX_HINT}"
    return "command", None


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    kind, reason = classify(argv[0], argv[1] if len(argv) > 1 else None)
    print(kind + (f": {reason}" if reason else ""))
    return 0 if kind == "command" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

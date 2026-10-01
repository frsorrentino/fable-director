---
name: verify
description: Run right before every commit in this repository (Claude Code 2.1.286+ calls it on its own; docs-only and tests-only commits are exempt). Runs the test suites and, since the repository is public, a privacy check on the staged diff. Do not commit when it fails.
---

# verify — fable-director marketplace

Two checks, in this order. Report each with its real output; on a failure, stop
and do not commit.

## 1. Tests

The house rule (also enforced by `release.sh`) is every suite green before the commit:

```bash
python3 tests/transcript-contract/run.py
for t in tests/*.py; do python3 "$t" >/dev/null 2>&1 && echo "OK $t" || echo "FAIL $t"; done
```

Rerun a failing suite alone (`python3 tests/<name>.py`) to read its FAIL lines.
A suite that only fails for an unrelated reason already on `main` is reported
as such, not fixed in the same commit.

## 2. Privacy (public repository)

Nothing personal leaves with the commit. On the staged diff:

```bash
d=$(git diff --cached -U0 | grep '^+' | grep -v '^+++')
printf '%s\n' "$d" | grep -nF "$HOME" && echo "PRIVACY: home path"
printf '%s\n' "$d" | grep -niF "$(git config user.email)" && echo "PRIVACY: email"
printf '%s\n' "$d" | grep -nE 'sk-ant-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY' && echo "PRIVACY: secret"
git diff --cached --name-only | grep -E '(^|/)(corpus-private|corpus/reale|anonymizer-corpus)/|\.private\.' && echo "PRIVACY: private corpus"
```

Any `PRIVACY:` line blocks the commit: replace the value (`~`, `<user>`,
`example.com`) or unstage the file. Client names and real documents never
enter this repository (see `.gitignore`).

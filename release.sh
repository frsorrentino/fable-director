#!/usr/bin/env bash
# fable-director — release in one command (author tooling, not shipped in the zip).
#
#   bash release.sh 1.17.0
#
# Does, in order (fails loudly at the first broken step):
#   1. preflight   — version consistent in plugin.json / CHANGELOG / README What's new
#   2. tests       — every suite must be green BEFORE the commit (house rule)
#   3. zip         — ../fable-director-plugin-<v>.zip (same layout as previous releases)
#   4. commit+push — "release: <v>" with everything staged
#   5. tag+release — v<v> on GitHub with notes extracted from CHANGELOG + zip asset
#   6. install     — copies the plugin into the cache of BOTH local accounts
#                    (~/.claude and ~/.claude-pixel) and updates installed_plugins.json
#
# Colleagues on the GitHub marketplace with autoUpdate get the new version
# automatically at their next session start (see ONBOARDING.md) — nothing to do here.
set -euo pipefail

VER="${1:?usage: bash release.sh <version>  (e.g. 1.17.0)}"
cd "$(dirname "$0")"
[ -f .claude-plugin/marketplace.json ] || { echo "FAIL: run from the marketplace repo root"; exit 1; }

echo "== 1/6 preflight =="
grep -q "\"version\": \"$VER\"" fable-director/.claude-plugin/plugin.json \
  || { echo "FAIL: plugin.json is not at $VER — bump it first"; exit 1; }
grep -q "\*\*$VER" CHANGELOG.md \
  || { echo "FAIL: CHANGELOG.md has no $VER entry"; exit 1; }
grep -q "\*\*$VER\*\*" README.md \
  || { echo "FAIL: README What's new has no $VER line (plain simple title!)"; exit 1; }
git rev-parse "v$VER" >/dev/null 2>&1 \
  && { echo "FAIL: tag v$VER already exists"; exit 1; }

echo "== 2/6 test suites (must be green BEFORE the commit) =="
python3 tests/transcript-contract/run.py
for t in tests/external-exec-verify.py tests/budget-reopen-verify.py \
         tests/concurrency-stress.py tests/windows-verify.py; do
  [ -f "$t" ] && python3 "$t"
done

echo "== 3/6 zip =="
ZIP="../fable-director-plugin-$VER.zip"
rm -f "$ZIP"
( cd .. && zip -r -q "fable-director-plugin-$VER.zip" \
    fable-director-marketplace/.claude-plugin \
    fable-director-marketplace/INSTALL.md \
    fable-director-marketplace/fable-director \
    -x '*/.git/*' '*/__pycache__/*' '*.bak*' '*.pyc' )
unzip -l "$ZIP" | tail -1

echo "== 4/6 commit + push =="
git add -A
if git diff --cached --quiet; then
  echo "(nothing new to commit — releasing HEAD as is)"
else
  git commit -m "release: $VER"
fi
git push origin main

echo "== 5/6 tag + GitHub release =="
TITLE=$(awk -v v="$VER" '$0 ~ "^- \\*\\*" v {sub(/^- \*\*/,""); sub(/\.\*\*.*/,""); sub(/^[0-9.]+ — /,""); print; exit}' CHANGELOG.md)
git tag -a "v$VER" -m "v$VER — ${TITLE:-release}"
git push origin "v$VER"
NOTES=$(mktemp)
awk -v v="$VER" '$0 ~ "^- \\*\\*" v {f=1} f {print} f && /^$/ {exit}' CHANGELOG.md > "$NOTES"
[ -s "$NOTES" ] || { echo "FAIL: could not extract $VER notes from CHANGELOG"; exit 1; }
gh release create "v$VER" --title "v$VER — ${TITLE:-release}" --notes-file "$NOTES" "$(readlink -f "$ZIP")"
rm -f "$NOTES"

echo "== 6/6 install into local accounts =="
SHA=$(git rev-parse HEAD)
python3 - "$VER" "$SHA" <<'PY'
import json, shutil, os, sys
from datetime import datetime, timezone
VER, SHA = sys.argv[1], sys.argv[2]
SRC = os.path.abspath("fable-director")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
      + f"{datetime.now(timezone.utc).microsecond//1000:03d}Z"
for base in [os.path.expanduser("~/.claude"), os.path.expanduser("~/.claude-pixel")]:
    ipj = f"{base}/plugins/installed_plugins.json"
    if not os.path.exists(ipj):
        print(f"[skip] {base}: no installed_plugins.json"); continue
    cache = f"{base}/plugins/cache/pixelfarm/fable-director/{VER}"
    if os.path.exists(cache): shutil.rmtree(cache)
    shutil.copytree(SRC, cache, ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.bak*'))
    shutil.copy2(ipj, ipj + f".bak-pre-{VER}")
    d = json.load(open(ipj))
    e = d["plugins"]["fable-director@pixelfarm"][0]
    e.update(installPath=cache, version=VER, gitCommitSha=SHA, lastUpdated=NOW)
    json.dump(d, open(ipj, "w"), indent=2)
    print(f"[installed] {base} -> {VER} ({SHA[:7]})")
PY

echo ""
echo "DONE v$VER — verify: https://github.com/frsorrentino/fable-director/releases/tag/v$VER"
echo "Local sessions pick up $VER on next restart. Colleagues: automatic via autoUpdate."

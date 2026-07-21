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
grep -q "version-$VER-blue" README.md \
  || { echo "FAIL: README version badge is not at $VER (line 5: shields.io version-<v>-blue)"; exit 1; }
# What's new: telegrafico, MAX 5 voci (regola autore 2026-07-20 — la storia
# completa vive nel CHANGELOG; il README degenera se si appende senza potare).
WN_COUNT=$(sed -n "/## 🆕 What's new/,/^Full history/p" README.md | grep -c '^- \*\*')
[ "$WN_COUNT" -le 5 ] \
  || { echo "FAIL: README What's new has $WN_COUNT entries (max 5) — prune the oldest"; exit 1; }
WN_LONG=$(sed -n "/## 🆕 What's new/,/^Full history/p" README.md | grep '^- \*\*' | awk 'length > 160' | wc -l)
[ "$WN_LONG" -eq 0 ] \
  || { echo "FAIL: README What's new has $WN_LONG entries over 160 chars — telegraphic, not narrative"; exit 1; }
git rev-parse "v$VER" >/dev/null 2>&1 \
  && { echo "FAIL: tag v$VER already exists"; exit 1; }

echo "== 2/6 test suites (must be green BEFORE the commit) =="
python3 tests/transcript-contract/run.py
for t in tests/*.py; do
  python3 "$t"
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
import json, re, shutil, os, sys
from datetime import datetime, timezone
VER, SHA = sys.argv[1], sys.argv[2]
SRC = os.path.abspath("fable-director")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
      + f"{datetime.now(timezone.utc).microsecond//1000:03d}Z"

# Quante versioni tenere in cache per account. NON 1: una sessione VIVA gira
# ancora dalla cartella della sua versione (il passaggio alla nuova avviene solo
# al riavvio) — potare troppo stretto le spaccherebbe gli hook a meta' volo.
# 5 = margine per le sessioni aperte, senza accumulare all'infinito (osservate
# 26 versioni su ~/.claude e 20 su ~/.claude-pixel prima di questa potatura).
KEEP = 5
VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+$")


def vkey(name):
    """Ordinamento NUMERICO: lessicograficamente 1.9.0 batterebbe 1.10.10."""
    return tuple(int(x) for x in name.split("."))


def prune(root, keep_ver):
    """Tiene le KEEP versioni piu' recenti + quella appena installata.
    Tocca solo nomi X.Y.Z sotto la cache del plugin: qualunque altro nome
    (backup, file, refusi) resta dov'e'. Ritorna (rimossi, falliti).

    Due lezioni pagate il 2026-07-17, entrambe da non ripetere:

    1. SYMLINK. La cache conteneva 21 "versioni" che erano symlink, tutti verso
       la stessa dir (un dedup del 07-13). `os.path.isdir()` dice True su un
       link->dir, quindi finivano tra i condannati, e `shutil.rmtree()` su un
       symlink SOLLEVA — errore che `ignore_errors=True` inghiottiva. Risultato:
       cancellato il bersaglio condiviso e lasciati 28 link penzolanti.
       Qui i link si trattano per quel che sono: alias, non versioni (un link
       chiamato 1.12.0 verso 1.15.4 non conserva la 1.12.0). Si rimuovono con
       unlink, e solo se penzolano o se il loro bersaglio sta per morire.

    2. NIENTE ignore_errors. Riportava le INTENZIONI come consuntivo: diceva
       "rimosse 21" avendone rimosse 4. Qui ogni rimozione e' verificata con
       os.path.lexists() dopo il fatto, e i fallimenti si dichiarano."""
    if not os.path.isdir(root):
        return [], []
    entries = [d for d in os.listdir(root) if VERSION_DIR.match(d)]
    links = [d for d in entries if os.path.islink(f"{root}/{d}")]
    dirs = sorted((d for d in entries
                   if not os.path.islink(f"{root}/{d}")
                   and os.path.isdir(f"{root}/{d}")), key=vkey)

    keep = set(dirs[-KEEP:]) | {keep_ver}
    doomed = [v for v in dirs if v not in keep]

    # Un link il cui bersaglio muore (o gia' morto) va via con lui: lasciarlo
    # significherebbe fabbricare esattamente i penzolanti dell'incidente.
    for l in links:
        p = f"{root}/{l}"
        tgt = os.path.basename(os.readlink(p).rstrip("/"))
        if not os.path.exists(p) or tgt in doomed:
            doomed.append(l)

    removed, failed = [], []
    for v in doomed:
        p = f"{root}/{v}"
        try:
            if os.path.islink(p):
                os.unlink(p)          # MAI rmtree su un link
            else:
                shutil.rmtree(p)
        except OSError as e:
            failed.append(f"{v} ({e.__class__.__name__})")
            continue
        # verifica: lexists vede anche i link rotti, exists no
        (removed if not os.path.lexists(p) else failed).append(v)
    return removed, failed


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
    dropped, failed = prune(f"{base}/plugins/cache/pixelfarm/fable-director", VER)
    if dropped:
        print(f"[pruned]    {base} -> rimosse {len(dropped)} voci vecchie, "
              f"tenute le ultime {KEEP} versioni")
    if failed:
        # Rumoroso di proposito: una potatura che fallisce in silenzio e' come
        # non averla (l'incidente del 07-17 e' nato esattamente cosi').
        print(f"[WARN]      {base} -> {len(failed)} voci NON rimosse: "
              f"{', '.join(failed)}")
PY

echo ""
echo "DONE v$VER — verify: https://github.com/frsorrentino/fable-director/releases/tag/v$VER"
echo "Local sessions pick up $VER on next restart. Colleagues: automatic via autoUpdate."

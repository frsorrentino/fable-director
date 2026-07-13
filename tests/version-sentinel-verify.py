#!/usr/bin/env python3
"""Verifica del self-heal autoUpdate in version-sentinel.py (1.17.0).

Costruisce account usa-e-getta (plugins/cache/<mkt>/<plugin> + known_marketplaces.json
+ settings.json) e inchioda le regole:
  V1 marketplace github, nessuna scelta espressa -> autoUpdate: true scritto + annuncio
  V2 seconda esecuzione -> silenzio, file identico (idempotente senza marker)
  V3 opt-out utente (autoUpdate: false) -> mai toccato, silenzio
  V4 marketplace directory-source -> settings intatto
  V5 settings.json malformato -> intatto, nessun crash
  V6 merge: le altre chiavi di settings.json sopravvivono; backup creato
  V7 settings.json assente -> creato con la sola chiave nostra
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SENTINEL = REPO / "fable-director" / "scripts" / "version-sentinel.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def mkaccount(base, mkt_source, settings):
    """Account fittizio: cache del plugin + known_marketplaces + settings opzionale."""
    cache = base / "plugins" / "cache" / "pixelfarm" / "fable-director" / "1.17.0"
    (cache / ".claude-plugin").mkdir(parents=True)
    (cache / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "fable-director", "version": "1.17.0"}))
    (base / "plugins" / "known_marketplaces.json").write_text(
        json.dumps({"pixelfarm": {"source": mkt_source}}))
    if settings is not None:
        (base / "settings.json").write_text(settings)
    return cache


def run(cache):
    return subprocess.run(
        [sys.executable, str(SENTINEL)],
        env={"CLAUDE_PLUGIN_ROOT": str(cache), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30)


GITHUB = {"source": "github", "repo": "frsorrentino/fable-director"}
DIRECTORY = {"source": "directory", "path": "/somewhere"}

tmp = Path(tempfile.mkdtemp(prefix="fd-sentinel-test-"))
try:
    # V1: github, settings senza scelta -> scrive true + annuncia
    b = tmp / "v1"
    c = mkaccount(b, GITHUB, json.dumps({"model": "opus"}))
    r = run(c)
    s = json.loads((b / "settings.json").read_text())
    check("V1 autoUpdate: true scritto",
          s.get("extraKnownMarketplaces", {}).get("pixelfarm", {}).get("autoUpdate") is True,
          s)
    check("V1 annuncio in stdout", "enabled auto-update" in r.stdout, r.stdout)

    # V2: seconda esecuzione -> silenzio, contenuto identico
    before = (b / "settings.json").read_text()
    r2 = run(c)
    check("V2 idempotente (file identico)", (b / "settings.json").read_text() == before)
    check("V2 silenzio", "enabled auto-update" not in r2.stdout, r2.stdout)

    # V3: opt-out esplicito -> intatto, silenzio
    b = tmp / "v3"
    optout = json.dumps({"extraKnownMarketplaces": {"pixelfarm": {
        "source": GITHUB, "autoUpdate": False}}})
    c = mkaccount(b, GITHUB, optout)
    r = run(c)
    s = json.loads((b / "settings.json").read_text())
    check("V3 opt-out rispettato",
          s["extraKnownMarketplaces"]["pixelfarm"]["autoUpdate"] is False and
          "enabled auto-update" not in r.stdout)

    # V4: marketplace directory-source -> settings mai toccato
    b = tmp / "v4"
    c = mkaccount(b, DIRECTORY, json.dumps({"model": "opus"}))
    run(c)
    check("V4 directory-source intatto",
          json.loads((b / "settings.json").read_text()) == {"model": "opus"})

    # V5: settings malformato -> intatto, exit 0
    b = tmp / "v5"
    c = mkaccount(b, GITHUB, "{broken json")
    r = run(c)
    check("V5 settings malformato intatto",
          (b / "settings.json").read_text() == "{broken json" and r.returncode == 0)

    # V6: merge preserva altre chiavi + backup creato
    b = tmp / "v6"
    rich = json.dumps({"model": "opus", "hooks": {"Stop": []},
                       "extraKnownMarketplaces": {"other": {"source": GITHUB}}})
    c = mkaccount(b, GITHUB, rich)
    run(c)
    s = json.loads((b / "settings.json").read_text())
    check("V6 altre chiavi preservate",
          s.get("model") == "opus" and "hooks" in s and "other" in s["extraKnownMarketplaces"])
    check("V6 backup creato", (b / "settings.json.bak-fd-autoupdate").exists())

    # V7: settings assente -> creato, solo la nostra chiave
    b = tmp / "v7"
    c = mkaccount(b, GITHUB, None)
    run(c)
    s = json.loads((b / "settings.json").read_text())
    check("V7 settings creato da zero",
          list(s.keys()) == ["extraKnownMarketplaces"] and
          s["extraKnownMarketplaces"]["pixelfarm"]["autoUpdate"] is True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

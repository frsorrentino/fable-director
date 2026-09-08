#!/usr/bin/env python3
"""soft-deps-sync.py — add the plugin's shipped soft-dependency entries to the
user's registry, once, never overwriting.

Called by session-kernel.sh at SessionStart. ~/.claude/fable-director/
soft-deps.json is the USER's file (route-hint.py reads it, the kernel points
the model at it): the plugin cannot ship it whole without clobbering local
entries. So this script:

  - creates the file from soft-deps-template.json when it is missing;
  - otherwise adds only the template entries whose key is absent AND has not
    been synced before (`_synced` list): an entry the user deleted stays
    deleted;
  - never touches an existing entry, never removes anything;
  - prints ONE short line only when it ADDS to an existing user file (the
    SessionStart output has a hard cap; creation on a fresh install is
    silent, the file's _schema says where it came from), nothing on error.

Usage: soft-deps-sync.py [--dry-run]   (exit 0 always)
"""
import json
import os
import sys
from pathlib import Path

LIVE = Path.home() / ".claude" / "fable-director" / "soft-deps.json"


def template_path():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    cands = [Path(root) / "soft-deps-template.json"] if root else []
    cands.append(Path(__file__).resolve().parent.parent / "soft-deps-template.json")
    return next((c for c in cands if c.is_file()), None)


def main():
    dry = "--dry-run" in sys.argv[1:]
    tp = template_path()
    if not tp:
        return
    try:
        template = json.loads(tp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    shipped = [k for k in template if not k.startswith("_")]
    if not LIVE.is_file():
        if not dry:
            try:
                LIVE.parent.mkdir(parents=True, exist_ok=True)
                template["_synced"] = shipped
                LIVE.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
            except OSError:
                return
        # Fresh install: nothing of the user's existed, so nothing to
        # announce — and this is the same first session that carries the
        # onboarding question, which the SessionStart cap drops first
        # (measured: one extra line of 85 chars was enough). The file's
        # own _schema says where it came from.
        return
    try:
        live = json.loads(LIVE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(live, dict):
        return
    synced = live.get("_synced")
    synced = list(synced) if isinstance(synced, list) else []
    added = [k for k in shipped if k not in live and k not in synced]
    if not added:
        return
    for k in added:
        live[k] = template[k]
    live["_synced"] = sorted(set(synced) | set(shipped))
    if not dry:
        try:
            LIVE.write_text(json.dumps(live, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except OSError:
            return
    print(f"[fd] soft-deps.json: +{', '.join(added)} from the plugin template (yours untouched)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass

#!/usr/bin/env python3
"""handoff.py — planned expiry made operational: propose, write, resume.

A long session pays its whole context again at every turn, and a cold resume
re-caches all of it (measured 2026-09-09: 296k tokens, ~$5.93 at list price,
to reopen one session). At a VERIFIED task boundary a ~2k-token distillate on
disk plus a fresh session costs a fraction (81 long sessions: 72.5% of their
cost fell after a recognizable boundary, net saving ~52% — to be confirmed by
the `report` section this module feeds). Mid-task the reasoning in context is
load-bearing: nothing here ever fires with an open budget.

Four entry points, all called by hooks or by the /fable-director:handoff
command — never by the user directly, except --prepare/--written via the
command:

  --boundary   (Stop hook, stdin = hook payload) this turn closed a task
               (budget-close ok / --verify passed / git commit landed), no
               budget is open, context >= threshold (handoff.json
               {"threshold_pct": 60}, bands 60 and 80, once per band per
               session) → marker handoff-due/<sid>.json + event
               handoff_proposed. Silent otherwise.
  --prompt     (UserPromptSubmit, stdin = hook payload) marker < 30 min for
               this session and a real prompt (not a slash command, not a
               one-word reply) → ONE additionalContext line telling the model
               to ask the user, marker consumed.
  --resume-line (SessionStart, stdin = hook payload) newest handoff for this
               cwd, < 14 days → one line <= 110 chars naming the file.
  --prepare [--here]  (command) refuses with an open budget; otherwise prints
               the destination path, the context %, the sections. The model
               then writes the file and calls --written PATH (event
               handoff_written with the size).

Handoffs live in ~/.claude/fable-director/handoffs/<cwd-slug>/<date>.md, or
in <cwd>/docs/handoff-<date>.md with --here (versioned with the project).
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path.home() / ".claude" / "fable-director"
DUE_DIR = BASE / "handoff-due"
SEEN_DIR = BASE / "handoff-seen"
HANDOFFS = BASE / "handoffs"
CONFIG = BASE / "handoff.json"
DEFAULT_THRESHOLD = 60
BANDS = (80, 60)
MARKER_TTL_S = 30 * 60
BOUNDARY_WINDOW_S = 15 * 60
RESUME_MAX_DAYS = 14
TAIL_BYTES = 400_000
COMMIT_RE = re.compile(r"^\[[^\]\n]+ [0-9a-f]{7,40}\]", re.MULTILINE)

SECTIONS = [
    "1. Task and outcome (one line each; version/commit when there is one)",
    "2. Decisions and why (only what the code does not show)",
    "3. Verified facts (numbers, commands run, decisive outputs)",
    "4. Open: what is missing, proposals not executed, questions for the user",
    "5. Paths touched (files, folders, budgets/receipts)",
    "6. Invoke next: skills, commands, briefs to execute",
    "7. Date and what invalidates this handoff",
]


def slug_of(cwd):
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def log_event(event, payload, session_id=None, cwd=None):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event(event, payload, session_id=session_id, cwd=cwd)
    except Exception:
        pass


def context_pct(sid):
    """From sessions/<sid>.json written by the statusline. Without a session
    id (the command runs in a Bash tool, CLAUDE_SESSION_ID may be unset) the
    snapshot updated in the last 5 minutes stands in. None if unknown."""
    snap = load_json(BASE / "sessions" / f"{sid}.json") if sid else None
    if not snap:
        try:
            cands = sorted((BASE / "sessions").glob("*.json"), key=lambda q: q.stat().st_mtime)
            if cands and time.time() - cands[-1].stat().st_mtime < 300:
                snap = load_json(cands[-1])
        except OSError:
            snap = None
    if not snap:
        return None
    try:
        ctx, size = int(snap.get("ctx_tokens") or 0), int(snap.get("ctx_size") or 0)
    except (TypeError, ValueError):
        return None
    if ctx <= 0 or size <= 0:
        return None
    return 100.0 * ctx / size


def threshold():
    cfg = load_json(CONFIG) or {}
    try:
        return int(cfg.get("threshold_pct", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def budget_state(cwd):
    """(budget or None, state or None) for this cwd."""
    bfile = BASE / "budgets" / f"{slug_of(cwd)}.json"
    return load_json(bfile), load_json(bfile.with_name(bfile.stem + ".state.json"))


def recent(ts_iso, window_s):
    dt = parse_iso(ts_iso)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() <= window_s


def commit_in_tail(transcript_path):
    """A Bash `git commit` whose result looks like `[branch abc1234] ...` in
    the transcript tail — the boundary a developer recognizes."""
    p = Path(transcript_path or "")
    if not p.is_file():
        return False
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    commit_ids, found = set(), False
    for line in raw.split("\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            # A new user turn resets: only THIS turn's commit counts.
            continue
        if d.get("type") == "user" and any(
                isinstance(b, dict) and b.get("type") == "text" for b in content):
            commit_ids, found = set(), False
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = str((b.get("input") or {}).get("command") or "")
                if "git commit" in cmd:
                    commit_ids.add(b.get("id"))
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in commit_ids:
                txt = b.get("content")
                txt = txt if isinstance(txt, str) else json.dumps(txt, ensure_ascii=False)
                if not b.get("is_error") and COMMIT_RE.search(txt) \
                        and "nothing to commit" not in txt:
                    found = True
    return found


def boundary_kind(cwd, transcript):
    """Which verified boundary closed in this turn, or None. Never with an
    open budget."""
    budget, state = budget_state(cwd)
    if budget and budget.get("status") == "open":
        return None
    if budget and budget.get("status") == "closed" and budget.get("outcome") == "ok" \
            and recent(budget.get("closed_at"), BOUNDARY_WINDOW_S):
        return "budget-close"
    if state and state.get("verify_rc") == 0 and recent(state.get("verify_at"), BOUNDARY_WINDOW_S):
        return "verify"
    if commit_in_tail(transcript):
        return "commit"
    return None


def cmd_boundary(data):
    sid = str(data.get("session_id") or "")
    cwd = data.get("cwd") or os.getcwd()
    if not sid:
        return
    pct = context_pct(sid)
    if pct is None or pct < threshold():
        return
    band = next((b for b in BANDS if pct >= b), None)
    if band is None:
        band = threshold()
    seen_f = SEEN_DIR / f"{sid}.json"
    seen = load_json(seen_f) or {"bands": []}
    if band in (seen.get("bands") or []):
        return
    kind = boundary_kind(cwd, data.get("transcript_path"))
    if not kind:
        return
    try:
        DUE_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_DIR.mkdir(parents=True, exist_ok=True)
        (DUE_DIR / f"{sid}.json").write_text(json.dumps(
            {"pct": round(pct, 1), "band": band, "boundary": kind, "cwd": str(cwd),
             "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
        seen["bands"] = sorted(set(seen.get("bands") or []) | {band})
        seen_f.write_text(json.dumps(seen))
    except OSError:
        return
    log_event("handoff_proposed", {"pct": round(pct, 1), "band": band,
                                   "boundary": kind, "auto": True}, sid, cwd)


def cmd_prompt(data):
    sid = str(data.get("session_id") or "")
    if not sid:
        return
    mf = DUE_DIR / f"{sid}.json"
    marker = load_json(mf)
    if not marker:
        return
    if not recent(marker.get("ts"), MARKER_TTL_S):
        try:
            mf.unlink()
        except OSError:
            pass
        return
    prompt = str(data.get("prompt") or "").strip()
    # A slash command or a one-word reply is not the next task: keep the
    # marker for the real prompt (within its TTL).
    if prompt.startswith("/") or len(prompt.split()) < 3:
        return
    try:
        mf.unlink()
    except OSError:
        pass
    print(f"[fd-handoff] context {marker.get('pct', '?')}%, last task closed at a "
          f"verified boundary ({marker.get('boundary', '?')}). Before starting this "
          f"prompt, ask the user in ONE line: \"Save a handoff and restart in a fresh "
          f"session (/fable-director:handoff — about half the cost of continuing), or "
          f"go on here?\" — then do what they say. If this prompt is clearly a "
          f"continuation of the closed task, skip the question.")


def newest_handoff(cwd):
    """(path, date) of the newest handoff for this cwd, in the store or in
    <cwd>/docs, within RESUME_MAX_DAYS; None otherwise."""
    cands = []
    for p in list((HANDOFFS / slug_of(cwd)).glob("*.md")) + \
            list((Path(cwd) / "docs").glob("handoff-*.md")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if m:
            cands.append((m.group(1), p))
    if not cands:
        return None
    date, path = max(cands)
    try:
        d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) - d > timedelta(days=RESUME_MAX_DAYS):
        return None
    return path, date


def cmd_resume_line(data):
    cwd = data.get("cwd") or os.getcwd()
    hit = newest_handoff(cwd)
    if not hit:
        return
    path, date = hit
    shown = str(path)
    home = str(Path.home())
    if shown.startswith(home):
        shown = "~" + shown[len(home):]
    try:
        rel = path.resolve().relative_to(Path(cwd).resolve())
        shown = str(rel)
    except (ValueError, OSError):
        pass
    print(f"Handoff {date}: {shown} — read it before starting.")


def cmd_prepare(here):
    cwd = os.getcwd()
    budget, _ = budget_state(cwd)
    if budget and budget.get("status") == "open":
        print("STATUS: refused")
        print(f"DETAIL: a budget is open for this cwd ({str(budget.get('task') or '')[:60]}) "
              "— a handoff is written only at a verified boundary. Finish or close the "
              "task (fd-telemetry.py budget-close) first; mid-task the reasoning in "
              "context is load-bearing.")
        sys.exit(1)
    date = time.strftime("%Y-%m-%d")
    dest = (Path(cwd) / "docs" / f"handoff-{date}.md") if here \
        else (HANDOFFS / slug_of(cwd) / f"{date}.md")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("STATUS: error")
        print(f"DETAIL: cannot create {dest.parent}: {e}")
        sys.exit(1)
    sid = os.environ.get("CLAUDE_SESSION_ID") or ""
    pct = context_pct(sid) if sid else None
    print("STATUS: ok")
    print(f"PATH: {dest}")
    print(f"CONTEXT: {pct:.0f}%" if pct is not None else "CONTEXT: unknown")
    print("SECTIONS:")
    for s in SECTIONS:
        print(f"  {s}")
    print("RULES: ~2k tokens max; facts with numbers, no narrative; only decisions the "
          "code does not show; if the file exists, overwrite it (same day = same handoff).")
    print(f"THEN: python3 \"{Path(__file__).resolve()}\" --written \"{dest}\"")


def cmd_written(path):
    p = Path(path)
    if not p.is_file():
        print("STATUS: error")
        print(f"DETAIL: {path} not found — the handoff was not written")
        sys.exit(1)
    size = p.stat().st_size
    sid = os.environ.get("CLAUDE_SESSION_ID") or None
    pct = context_pct(sid) if sid else None
    log_event("handoff_written", {"path": str(p), "bytes": size,
                                  "pct": (round(pct, 1) if pct is not None else None)},
              sid, os.getcwd())
    print("STATUS: ok")
    print(f"OUTPUT: {p}")
    print(f"DETAIL: {size} bytes (~{size // 4} tokens). Close this session and open "
          f"`claude` in the same folder: the handoff is announced at startup.")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] in ("--boundary", "--prompt", "--resume-line"):
        try:
            data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        {"--boundary": cmd_boundary, "--prompt": cmd_prompt,
         "--resume-line": cmd_resume_line}[args[0]](data)
    elif args[0] == "--prepare":
        cmd_prepare(here="--here" in args[1:])
    elif args[0] == "--written" and len(args) > 1:
        cmd_written(args[1])
    else:
        sys.exit(f"unrecognized arguments: {' '.join(args)}\n{__doc__}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Hook paths must never break a turn.
        if sys.argv[1:2] in (["--prepare"], ["--written"]):
            raise

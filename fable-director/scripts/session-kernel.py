"""fable-director — SessionStart hook: inject the policy kernel (~500 tokens) into the context.

The full body loads on demand through the skill fable-director:delega-efficiente.
Run as `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/session-kernel.py"`.

Until 1.65.3 this was session-kernel.sh. The plugin directory's check follows a
shell script that a hook runs and refuses one that computes a path (variables
other than CLAUDE_PLUGIN_ROOT, command substitutions, programs named by a
variable): the kernel needs all of them, so it moved to Python, which the
directory holds for a reviewer instead of refusing (1.65.4). Same output.

Without Python this file never runs: py.sh prints its "no Python 3 found"
notice on stdout, and SessionStart hands that line to the model.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)
SCRIPTS = ROOT + "/scripts"   # as the shell printed "${CLAUDE_PLUGIN_ROOT}/scripts/…"
HOME = os.path.expanduser("~")
FD_DIR = os.path.join(HOME, ".claude", "fable-director")

# Harness cap (Claude Code 2.1.260, read from the binary): SessionStart output
# counts as additionalContext, 8000 characters / 200 lines, beyond that CUT
# silently mid-word (measured by the community on claude-code#91870). The kernel
# alone is ~6200 characters: the optional blocks brought it to the limit.
# Everything goes through a buffer: kernel always whole, XF block only if it
# fits (without burning the attempt), a VISIBLE final cut if something still
# overflows.
CAP_CHARS = 7900
CAP_LINES = 195
CUT_MARK = "[fd: SessionStart output cut at the 8000-char/200-line hook cap]"


def is_windows():
    """Windows under Git Bash: Python reports win32 or cygwin; OSTYPE when a test sets it."""
    return (sys.platform.startswith(("win", "cygwin", "msys"))
            or os.environ.get("OSTYPE", "").startswith(("msys", "cygwin", "win")))


def run_py(script, *args, stdin_text=""):
    """A plugin script with this same interpreter; its stdout, stderr dropped, never an error."""
    try:
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                           input=stdin_text, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=50)
        return r.stdout
    except Exception:
        return ""


def read_input():
    """The hook payload, read ONCE and handed to the children that use it (session-hindsight
    takes the cwd from stdin). `source` tells five openings apart: startup | resume | clear |
    compact | fork. Up to 1.28.x the kernel read no stdin and treated them alike: every
    COMPACTION burned one of the 3 onboarding attempts and repeated the hindsight already seen
    in the same session. The kernel itself is always emitted again: after a compaction the
    injected text may be gone."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return "", ""
        raw = sys.stdin.read()
    except Exception:
        return "", ""
    try:
        d = json.loads(raw)
        src = (d or {}).get("source") or ""
    except Exception:
        src = ""
    return raw, src if isinstance(src, str) else ""


def core(fd_input, source):
    out = []
    out.append("FABLE-DIRECTOR KERNEL, delegation policy — full body in skill fable-director:delega-efficiente\n")
    try:
        with open(os.path.join(ROOT, "kernel.md"), encoding="utf-8") as f:
            out.append(f.read())
    except OSError:
        pass

    # Windows (Git Bash): `python3` and the bare script paths land on the Store
    # alias — the model has to run them through the launcher.
    if is_windows():
        win_py = f"{SCRIPTS}/py.sh".replace("\\", "/")
        win_name = f"{SCRIPTS}/NAME.py".replace("\\", "/")
        out.append(f'\nFD Windows: run every plugin script as bash "{win_py}" "{win_name}" ARGS — never python3 '
                   'or the bare path, since python3 here is the Microsoft Store alias.\n')

    # Version sentinel: warns when the running cache is older than the local
    # marketplace source (the cache never updates itself).
    out.append(run_py("version-sentinel.py"))

    # Forked session: budget and telemetry are per cwd, not per session. Parent and
    # fork share the same budget file — a limit stated in the README, here a
    # warning at the exact moment it bites.
    if source == "fork":
        out.append("\nFD ⑂ forked session: the open pre-budget is ONE FILE PER CWD, shared with the parent session — "
                   "parallel budgeted work needs separate worktrees, not a fork.\n")

    # Subagent model from the environment (sub-agents docs, read on 24/10/2026).
    # Claude Code order since 2.1.251: the call's `model`, the frontmatter's
    # `model` (`inherit` = session model), CLAUDE_CODE_SUBAGENT_MODEL, the session
    # model. The variable alone is therefore a DEFAULT: the pins of fd-executor
    # (sonnet) and fd-verifier (inherit) hold; it does not touch Explore and Plan
    # either. With CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 (2.1.257) Claude Code ignores
    # the definitions' `model` and the one passed by the call (workflow agents
    # too): everything runs on the variable or, without it, on the session model.
    # One line only and only when a variable is set: a warning where the pins
    # fall, a note where they hold. `inherit` counts as unset (docs). Pinned
    # efforts have no documented rule under FORCE: no line.
    sm = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", "")
    if sm == "inherit":
        sm = ""
    smf = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "")
    if smf.lower() in ("", "0", "false", "no", "off"):
        smf = ""
    if smf and sm:
        out.append(f"\nFD ⚠ CLAUDE_CODE_SUBAGENT_MODEL_FORCE with CLAUDE_CODE_SUBAGENT_MODEL={sm}: Claude Code ignores "
                   f"the model in agent files and in Agent/workflow calls — fd-executor (sonnet), fd-verifier (inherit) "
                   f"and every workflow stage run on {sm}. Unset it (settings.json env) or route knowing the cheap "
                   f"executor and the fresh verifier sit on {sm}.\n")
    elif smf:
        out.append("\nFD ⚠ CLAUDE_CODE_SUBAGENT_MODEL_FORCE is on without CLAUDE_CODE_SUBAGENT_MODEL: Claude Code ignores "
                   "the model in agent files and in Agent/workflow calls — fd-executor leaves sonnet for the session "
                   "model, measured at the same correctness and 5.5× the USD on Fable 5.1 — fd-verifier, set to "
                   "inherit, is unchanged.\n")
    elif sm:
        out.append(f"\nFD: CLAUDE_CODE_SUBAGENT_MODEL={sm} is a default since Claude Code 2.1.251: fd-executor stays on "
                   f"sonnet, fd-verifier on the session model; subagents with no model of their own run on {sm}, "
                   f"except Explore and Plan. Only CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 overrides the pins.\n")

    # Short lines, always wanted, BEFORE the long blocks: if the cap cuts, it cuts
    # at the end.
    pb = os.path.join(HOME, ".claude", "delega-playbook.md")
    pb_txt = pb.replace("\\", "/")   # printed with / as the shell did, also on Windows
    if os.path.isfile(pb):
        out.append(f"\nHeuristics playbook: {pb_txt} — consult it before orchestrating batch/workflow/multi-agent work.\n")
    else:
        out.append(f"\nPlaybook not initialized: copy {ROOT}/playbook-template.md to {pb_txt} "
                   "if missing, never overwrite it.\n")

    # Soft-deps shipped by the plugin (media-tools): added to the user's registry
    # once, never overwriting — one line only when something changes.
    out.append(run_py("soft-deps-sync.py"))

    # Handoff from a previous session in this cwd (< 14 days): one line <= 110
    # chars, the file does the rest.
    out.append(run_py("handoff.py", "--resume-line", stdin_text=fd_input))

    # One-shot legend hint: the statusline uses compact symbols — once only,
    # suggest that the model point at the command that explains them.
    help_mark = os.path.join(FD_DIR, "help-hint-shown")
    if not os.path.isfile(help_mark):
        out.append("\nSTATUSLINE LEGEND, mention to the user ONCE, never again: /fable-director:help explains every "
                   "statusline segment, symbol and color.\n")
        try:
            os.makedirs(FD_DIR, exist_ok=True)
            open(help_mark, "w").close()
        except OSError:
            pass

    # Hindsight: brings back the overruns already logged on THIS cwd. Silent where
    # there is no evidence (zero tokens), 5 lines at most where there is. Skipped
    # after a compaction: same session, warning already given.
    if source != "compact":
        out.append(run_py("session-hindsight.py", stdin_text=fd_input))

    # Same as the shell's $(fd_core): trailing newlines dropped.
    return "".join(out).rstrip("\n")


def main():
    try:
        sys.stdout.reconfigure(newline="\n")   # Windows: no \r\n eating the cap
    except Exception:
        pass
    fd_input, source = read_input()
    fd_out = core(fd_input, source)

    # External executors onboarding, as a multiple-choice QUESTION (1.17.1). The
    # old "relay this notice" burned the one-shot even when the model relayed
    # nothing; the hook now tells the model to ASK the question with
    # AskUserQuestion, the final marker is written only on an answer ("No"), and a
    # counter stops everything at the 3rd session without an answer. The legacy
    # marker (burned blindly by versions <= 1.17.0) is retired.
    xf_cfg = os.path.join(FD_DIR, "cross-family.json")
    xf_done = os.path.join(FD_DIR, "xf-onboarding-done")
    xf_count = os.path.join(FD_DIR, "xf-onboarding-count")
    xf_done_txt = xf_done.replace("\\", "/")
    try:
        os.remove(os.path.join(FD_DIR, "xf-onboarding-shown"))
    except OSError:
        pass
    xf_next = None
    # Only REAL openings use an attempt (startup/resume, or `source` missing on
    # older Claude Code): compact/fork/clear are the same session starting again.
    if source not in ("compact", "fork", "clear") and not os.path.isfile(xf_cfg) and not os.path.isfile(xf_done):
        try:
            os.makedirs(FD_DIR, exist_ok=True)
        except OSError:
            pass
        try:
            n = open(xf_count, encoding="utf-8").read().strip()
            n = int(n) if n.isdigit() else 0
        except OSError:
            n = 0
        if n < 3:
            py_sh = f"{SCRIPTS}/py.sh"
            ext = f"{SCRIPTS}/external-exec.py"
            xf = (f"\nXF ONBOARDING — ASK THE USER NOW, attempt {n + 1}/3: external free-tier executors are not "
                  "configured. Before anything else, ask ONE multiple-choice question with the AskUserQuestion tool, "
                  "or a plain question if unavailable: header \"Executors\"; question \"fable-director can route "
                  "non-quality-sensitive batch work and cross-family verification to free external models, at zero "
                  "Claude tokens. Connect one now?\"; options (1) \"Gemini — free API key\" (Google AI Studio, "
                  "daily-reset limits), (2) \"Codex CLI — ChatGPT plan\" (usage included in the plan), (3) \"Both\", "
                  f"(4) \"No — don't ask again\". On 1-3: run bash \"{py_sh}\" \"{ext}\" --doctor and follow its "
                  f"guidance. On 4: run touch \"{xf_done_txt}\" so this never reappears. If the user skips it, do "
                  "nothing — asked again next session, 3 times at most.")
            # The block goes in only if it stays under the cap: otherwise it would be
            # cut halfway and the question would never arrive — and the attempt is
            # NOT used.
            if len(fd_out) + len(xf) + 1 <= CAP_CHARS:
                fd_out = fd_out + "\n" + xf
                xf_next = n + 1   # written only after the text has really gone out (below)
        else:
            try:
                open(xf_done, "w").close()
            except OSError:
                pass

    # Script paths outside Windows (1.60.3): they are not on PATH, and without the
    # path the model looked for them in the cwd or with `command -v`. Like the
    # statusline, AFTER the onboarding and only if it fits the cap.
    if not is_windows():
        sp = f"\nFD scripts, not on PATH: python3 \"{SCRIPTS}/fd-telemetry.py\""
        if len(fd_out) + len(sp) + 1 <= CAP_CHARS:
            fd_out = fd_out + "\n" + sp

    # Statusline (1.50.1): installed on its own, without asking. --auto is silent
    # when it is already there, belongs to someone else or was removed with
    # --remove; it also updates the path when the plugin cache changes version.
    # The line to the model only when settings.json changed, AFTER the onboarding
    # block and only if it fits the cap.
    if source != "compact":
        try:
            # bash by PATH order, as the shell found it: on Windows a bare "bash" makes
            # CreateProcess look in System32 first, where WSL's bash.exe may sit.
            bash = shutil.which("bash") or "bash"
            r = subprocess.run([bash, os.path.join(SCRIPTS, "statusline-install.sh"), "--auto"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=50)
            sl_line = r.stdout.rstrip("\n")
        except Exception:
            sl_line = ""
        if sl_line:
            sl = (f"\nSTATUSLINE {sl_line} (auto) — visible from the next start of Claude Code; tell the user once. "
                  "Off: /fable-director:statusline --remove")
            if len(fd_out) + len(sl) + 1 <= CAP_CHARS:
                fd_out = fd_out + "\n" + sl

    # Final cut, marked: better a line that says "cut" than a word cut in half.
    t = (fd_out + "\n").replace("\r\n", "\n")
    cut = False
    lines = t.split("\n")
    if len(lines) > CAP_LINES:
        t = "\n".join(lines[:CAP_LINES])
        cut = True
    if len(t) > CAP_CHARS:
        t = t[:CAP_CHARS]
        cut = True
    if cut:
        t = t.rstrip() + "\n" + CUT_MARK
    sys.stdout.write(t.rstrip("\n") + "\n")
    sys.stdout.flush()
    if xf_next is not None:
        try:
            with open(xf_count, "w", encoding="utf-8") as f:
                f.write(f"{xf_next}\n")
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass   # a SessionStart hook never turns into an error

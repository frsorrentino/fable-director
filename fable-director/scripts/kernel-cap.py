"""fable-director — cut the SessionStart output at the hook cap, with a visible mark.

Read by session-kernel.sh: the text on stdin, the caps in FD_CAP_CHARS and
FD_CAP_LINES. Better a line that says "cut" than a word cut in half. Until
1.65.3 this was a program inline in the kernel (`python3 -c`), which the plugin
directory's check does not read; same code.
"""
import os
import sys

try:
    sys.stdout.reconfigure(newline="\n")   # Windows: niente \r\n che consuma il cap
except Exception:
    pass
t = sys.stdin.read().replace("\r\n", "\n")
C, L = int(os.environ["FD_CAP_CHARS"]), int(os.environ["FD_CAP_LINES"])
cut = False
lines = t.split("\n")
if len(lines) > L:
    t = "\n".join(lines[:L]); cut = True
if len(t) > C:
    t = t[:C]; cut = True
if cut:
    t = t.rstrip() + "\n[fd: SessionStart output cut at the 8000-char/200-line hook cap]"
sys.stdout.write(t if t.endswith("\n") else t + "\n")

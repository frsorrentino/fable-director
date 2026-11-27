"""fable-director — for session-kernel.sh: "py-ok", then the `source` of the SessionStart input.

The first line tells the kernel that Python runs (py.sh prints nothing on
stdout when there is none); the second is the source (startup, resume,
compact, clear, fork; empty when missing or not JSON). Until 1.65.3 this was a
program inline in the kernel (`python3 -c`), which the plugin directory's check
does not read.
"""
import json
import sys

print("py-ok")
try:
    d = json.load(sys.stdin)
    print((d or {}).get("source") or "")
except Exception:
    print("")

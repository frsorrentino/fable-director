"""fable-director — print the `source` field of the SessionStart hook input.

Read by session-kernel.sh (startup, resume, compact, clear, fork; empty when
missing or not JSON). Until 1.65.3 this was a program inline in the kernel
(`python3 -c`), which the plugin directory's check does not read.
"""
import json
import sys

try:
    d = json.load(sys.stdin)
    print((d or {}).get("source") or "")
except Exception:
    print("")

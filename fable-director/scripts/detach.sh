#!/usr/bin/env bash
# fable-director — run a py.sh command detached from the hook that started it.
#
#   bash detach.sh script.py args…    (stdin is handed to the script)
#
# Why: since 2.1.287 every session.end hook shares one 1.5 s wall-clock bound;
# under `claude -p` the SessionEnd session-summary got "cancelled" mid-work.
# Python alone takes ~0.7-1 s to start on slow machines, so the hook itself
# must not wait for it: stdin goes to a temp file, the work runs in a new
# session (setsid where it exists) and this wrapper returns in milliseconds.
# A process the hook let go of outlives the bound.

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp "${TMPDIR:-/tmp}/fd-detach.XXXXXX") || exit 0
cat > "$tmp"
run() { bash "$here/py.sh" "$@" < "$tmp" > /dev/null 2>&1; rm -f "$tmp"; }
if command -v setsid > /dev/null 2>&1; then
  export -f run 2> /dev/null
  export here tmp
  setsid bash -c 'run "$@"' _ "$@" < /dev/null > /dev/null 2>&1 &
else
  ( run "$@" & ) < /dev/null > /dev/null 2>&1
fi
exit 0

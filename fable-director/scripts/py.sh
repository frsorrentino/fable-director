#!/usr/bin/env bash
# fable-director — run Python 3 portably (Linux, macOS, Windows under Git Bash).
#
#   bash py.sh script.py args…       run a script
#   bash py.sh -c 'code' args…       run inline code
#   . py.sh                          (sourced) only resolve: sets and exports FD_PYTHON, returns 1 if none
#
# Why: on stock Windows `python3` is the Microsoft Store alias — it exists on PATH (so `command -v` succeeds)
# but only prints "Python non è stato trovato…" and exits 9009/49. The real interpreter is `python` or `py -3`.
# Order: $FD_PYTHON (override) → python3 not under WindowsApps → cached path (Windows) → probe python3, python, py -3.
# The probe costs ~0.1-0.3 s on Windows, so its result is cached in ~/.claude/fable-director/python-path.
# Also: Windows Python writes pipes and files in cp1252 → PYTHONUTF8/PYTHONIOENCODING force UTF-8.
# With no Python at all: one line on stderr, exit 0 — a hook must not turn into an error on every event.

_fd_py_resolve() {
  [ -n "${FD_PYTHON:-}" ] && return 0
  local p c exe cache="$HOME/.claude/fable-director/python-path"
  p=$(type -P python3 2>/dev/null)
  case "$p" in
    "" | *WindowsApps*) ;;
    *) FD_PYTHON=$p; return 0 ;;
  esac
  if [ -r "$cache" ]; then
    IFS= read -r p < "$cache" || true
    [ -n "$p" ] && [ -x "$p" ] && { FD_PYTHON=$p; return 0; }
  fi
  for c in python3 python "py -3"; do
    # $c unquoted on purpose: "py -3" is launcher + flag
    exe=$(PYTHONUTF8=1 $c -c 'import sys
if sys.version_info < (3, 8): sys.exit(1)
print(sys.executable)' 2>/dev/null) || continue
    exe=${exe%$'\r'}
    exe=${exe//\\//}   # C:\Program Files\… → C:/Program Files/… (Git Bash runs both, only this survives quoting)
    [ -n "$exe" ] || continue
    FD_PYTHON=$exe
    case "${OSTYPE:-}" in
      msys* | cygwin* | win*) mkdir -p "${cache%/*}" 2>/dev/null && printf '%s\n' "$exe" > "$cache" 2>/dev/null ;;
    esac
    return 0
  done
  return 1
}

if _fd_py_resolve; then
  export FD_PYTHON PYTHONUTF8=1 PYTHONIOENCODING=utf-8
else
  unset FD_PYTHON
fi

# sourced: stop here, the caller uses "$FD_PYTHON"
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  [ -n "${FD_PYTHON:-}" ]
  return
fi

[ -n "${FD_PYTHON:-}" ] || {
  echo "fable-director: no Python 3 found (tried python3, python, py -3). Install Python 3.8+ (python.org); gates, telemetry and statusline stay off until then." >&2
  exit 0
}
exec "$FD_PYTHON" "$@"

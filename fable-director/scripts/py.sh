#!/usr/bin/env bash
# fable-director — run Python 3 portably (Linux, macOS, Windows under Git Bash).
#
#   bash py.sh script.py args…       run a script
#   bash py.sh -c 'code' args…       run inline code
#   source py.sh                     (sourced) only resolve: sets FD_PY_NAME, defines _fd_py_run, returns 1 if none
#
# Why: on stock Windows `python3` is the Microsoft Store alias — it exists on PATH (so `command -v` succeeds)
# but only prints "Python non è stato trovato…" and exits 9009/49. The real interpreter is `python` or `py -3`.
# Every program is named literally, one case per interpreter (python3, python, py -3): no program is
# taken from a variable, so the plugin directory's validator can name each one (PORTAL PROBE dc-13).
# Order: python3 not under WindowsApps → cached name (Windows) → probe python3, python, py -3.
# The probe costs ~0.1-0.3 s on Windows, so the name that worked is cached in ~/.claude/fable-director/python-name.
# Also: Windows Python writes pipes and files in cp1252 → PYTHONUTF8/PYTHONIOENCODING force UTF-8.
# With no Python at all: one line on stderr, exit 0 — a hook must not turn into an error on every event.

_fd_py_ok='import sys; sys.exit(sys.version_info < (3, 8))'

_fd_py_resolve() {
  [ -n "${FD_PY_NAME:-}" ] && return 0
  local p cache="$HOME/.claude/fable-director/python-name"
  p=$(type -P python3 2>/dev/null)
  case "$p" in
    "" | *WindowsApps*) ;;
    *) FD_PY_NAME=python3; return 0 ;;
  esac
  if [ -r "$cache" ]; then
    IFS= read -r p < "$cache" || true
    case "${p%$'\r'}" in
      python3 | python | py) FD_PY_NAME=${p%$'\r'}; return 0 ;;
    esac
  fi
  if PYTHONUTF8=1 python3 -c "$_fd_py_ok" > /dev/null 2>&1; then
    FD_PY_NAME=python3
  elif PYTHONUTF8=1 python -c "$_fd_py_ok" > /dev/null 2>&1; then
    FD_PY_NAME=python
  elif PYTHONUTF8=1 py -3 -c "$_fd_py_ok" > /dev/null 2>&1; then
    FD_PY_NAME=py
  else
    return 1
  fi
  case "${OSTYPE:-}" in
    msys* | cygwin* | win*) mkdir -p "$HOME/.claude/fable-director" 2>/dev/null && printf '%s\n' "$FD_PY_NAME" > "$cache" 2>/dev/null ;;
  esac
  return 0
}

# The interpreter by its literal name, for a caller that sourced this file.
_fd_py_run() {
  case "${FD_PY_NAME:-}" in
    python3) python3 "$@" ;;
    python) python "$@" ;;
    py) py -3 "$@" ;;
    *) return 127 ;;
  esac
}

# Bytecode cache outside the plugin: the installed plugin must stay byte-for-byte
# the reviewed one, and the scripts that load fd-telemetry.py & co. would
# otherwise write __pycache__/ next to them. Same speed, the cache just lives elsewhere.
if _fd_py_resolve; then
  export FD_PY_NAME PYTHONUTF8=1 PYTHONIOENCODING=utf-8
  export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/fable-director/pycache}"
else
  unset FD_PY_NAME
fi

# sourced: stop here, the caller uses _fd_py_run
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  [ -n "${FD_PY_NAME:-}" ]
  return
fi

case "${FD_PY_NAME:-}" in
  python3) exec python3 "$@" ;;
  python) exec python "$@" ;;
  py) exec py -3 "$@" ;;
esac
echo "fable-director: no Python 3 found (tried python3, python, py -3). Install Python 3.8+ (python.org); gates, telemetry and statusline stay off until then." >&2
exit 0

#!/usr/bin/env bash

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
    exe=$(PYTHONUTF8=1 $c -c 'import sys
if sys.version_info < (3, 8): sys.exit(1)
print(sys.executable)' 2>/dev/null) || continue
    exe=${exe%$'\r'}
    exe=${exe//\\//}
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
  export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${XDG_CACHE_HOME:-$HOME/.cache}/fable-director/pycache}"
else
  unset FD_PYTHON
fi

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  [ -n "${FD_PYTHON:-}" ]
  return
fi

[ -n "${FD_PYTHON:-}" ] || {
  echo "fable-director: no Python 3 found (tried python3, python, py -3). Install Python 3.8+ (python.org); gates, telemetry and statusline stay off until then." >&2
  exit 0
}
exec "$FD_PYTHON" "$@"

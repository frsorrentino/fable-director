#!/usr/bin/env bash
FD_PYTHON=python3

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

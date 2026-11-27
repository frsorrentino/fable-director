#!/usr/bin/env bash
FD_PYTHON=python3

# sourced: stop here, the caller uses "$FD_PYTHON"
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  [ -n "${FD_PYTHON:-}" ]
  return
fi

[ -n "${FD_PYTHON:-}" ] || exit 0
exec "$FD_PYTHON" "$@"

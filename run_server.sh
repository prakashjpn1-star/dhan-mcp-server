#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install .
exec .venv/bin/python -m dhan_mcp_server "$@"

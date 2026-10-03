#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python3 -m venv .venv-server
.venv-server/bin/python -m pip install --upgrade pip
if [[ "${1:-}" != "--text-only" ]]; then
    .venv-server/bin/python -m pip install -r requirements-server.txt
fi
if [[ ! -f .env.server ]]; then
    cp config/server.env.example .env.server
    chmod 600 .env.server
    .venv-server/bin/python -c 'import pathlib,secrets; p=pathlib.Path(".env.server"); p.write_text(p.read_text().replace("MARSI_TOKEN=\n", "MARSI_TOKEN="+secrets.token_hex(32)+"\n"))'
fi
printf '%s\n' 'Server environment ready. Continue with docs/ubuntu-server.md.'

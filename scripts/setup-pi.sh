#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python3 -m venv .venv-pi
.venv-pi/bin/python -m pip install --upgrade pip
.venv-pi/bin/python -m pip install -r requirements-pi.txt
if [[ ! -f .env.pi ]]; then
    cp config/pi.env.example .env.pi
    chmod 600 .env.pi
fi
printf '%s\n' 'Pi environment ready. Set the server address and token in .env.pi.'

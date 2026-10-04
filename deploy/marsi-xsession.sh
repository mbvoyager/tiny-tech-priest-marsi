#!/usr/bin/env bash
set -euo pipefail
cd -- "$HOME/tiny-tech-priest-marsi"
openbox &
exec .venv-pi/bin/python -m marsi_local.pi

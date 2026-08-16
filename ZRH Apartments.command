#!/bin/bash
# Double-clickable launcher for the ZRH Apartments desktop UI.
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python scripts/serve_ui.py

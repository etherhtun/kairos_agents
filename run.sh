#!/bin/bash
# Launcher for the Kairos menubar app.
# Usage: ./run.sh
set -e
cd "$(dirname "$0")"
exec python3 app.py

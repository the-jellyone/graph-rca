#!/usr/bin/env bash
# Quick launcher for Graph-RCA Incident Command Control Plane
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "================================================================="
echo " Starting Graph-RCA Incident Command Console on http://localhost:8000"
echo "================================================================="

# Use local venv if present, otherwise system python
if [ -f "../venv/bin/python" ]; then
    PYTHON="../venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    PYTHON="python3"
fi

$PYTHON app.py

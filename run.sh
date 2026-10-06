#!/bin/bash
# TeraBox Unified Manager Launcher
# Automatically uses the project virtual environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found at $SCRIPT_DIR/.venv"
    echo "Please run: python3 -m venv $SCRIPT_DIR/.venv && $SCRIPT_DIR/.venv/bin/pip install -r $SCRIPT_DIR/requirements.txt"
    exit 1
fi

# Default to Web App mode; pass --gui for CustomTkinter or --cli for terminal
if [ "$1" = "--cli" ]; then
    shift
    exec "$VENV_PYTHON" "$SCRIPT_DIR/terabox_manager.py" "$@"
elif [ "$1" = "--gui" ]; then
    shift
    exec "$VENV_PYTHON" "$SCRIPT_DIR/terabox_gui.py" "$@"
else
    exec "$VENV_PYTHON" "$SCRIPT_DIR/terabox_web_app.py" "$@"
fi

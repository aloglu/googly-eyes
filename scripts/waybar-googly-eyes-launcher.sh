#!/bin/sh

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GOOGLY_EYES_PY=${WAYBAR_GOOGLE_EYES_PY:-"$SCRIPT_DIR/waybar-googly-eyes.py"}
WAYBAR_CONFIG=${WAYBAR_GOOGLE_EYES_WAYBAR_CONFIG:-"$HOME/.config/waybar/config.jsonc"}

while true; do
    if [ -r "$WAYBAR_CONFIG" ] && grep -q '"image#googly-eyes"' "$WAYBAR_CONFIG"; then
        "$GOOGLY_EYES_PY" --daemon
    fi

    sleep 2
done

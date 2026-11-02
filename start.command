#!/bin/bash
# Start the SRT Generator. Run Install.command once first.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export PATH="$DIR/bin:$PATH"
export HF_HOME="$DIR/models/hf"

if [ ! -x ".venv/bin/python" ]; then
  echo "The app isn't installed yet. Double-click Install.command first."
  read -r -p "Press Enter to close…"
  exit 1
fi

echo "Starting SRT Generator — your browser will open in a moment."
echo "Keep this window open while you use the app. Close it to stop."
exec .venv/bin/python app.py

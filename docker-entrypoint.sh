#!/bin/sh
# Fetch the catalog audio on first start, then run the server.
set -e

# The audio folder is a volume, so it is empty on the first run. A placeholder catalog
# generates test tones; a real catalog downloads the release zip (see catalog/download.py).
if [ -z "$(find catalog/audio -maxdepth 1 -name '*.mp3' -print -quit 2>/dev/null)" ]; then
    echo "No audio in catalog/audio, fetching the catalog."
    uv run python catalog/download.py
fi

exec uv run radio-llama serve --host 0.0.0.0 --port 8000

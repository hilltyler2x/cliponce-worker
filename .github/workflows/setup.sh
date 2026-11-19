#!/usr/bin/env bash
# Shared setup for every ClipOnce worker job.
set -e
sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg fonts-dejavu-core > /dev/null
pip install -q --upgrade yt-dlp requests $EXTRA_PIP
mkdir -p fonts && curl -sSL -o fonts/Anton-Regular.ttf https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf
if [ -n "$YT_COOKIES" ]; then printf '%s' "$YT_COOKIES" > "$RUNNER_TEMP/cookies.txt"; echo "YT_COOKIES_FILE=$RUNNER_TEMP/cookies.txt" >> "$GITHUB_ENV"; fi
git config user.name "cliponce-bot" && git config user.email "cliponce-bot@users.noreply.github.com"

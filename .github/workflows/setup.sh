#!/usr/bin/env bash
# Shared setup for every ClipOnce worker job.
set -e
sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg fonts-dejavu-core > /dev/null
pip install -q --upgrade yt-dlp requests pillow $EXTRA_PIP
mkdir -p fonts
for u in https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/anton/Anton-Regular.ttf; do
  curl -sSL --retry 3 -o fonts/Anton-Regular.ttf "$u" && head -c 4 fonts/Anton-Regular.ttf | od -An -tx1 | grep -q "00 01 00 00" && break
done
fc-cache -f fonts >/dev/null 2>&1 || true
if [ -n "$YT_COOKIES" ]; then printf '%s' "$YT_COOKIES" > "$RUNNER_TEMP/cookies.txt"; echo "YT_COOKIES_FILE=$RUNNER_TEMP/cookies.txt" >> "$GITHUB_ENV"; fi
git config user.name "cliponce-bot" && git config user.email "cliponce-bot@users.noreply.github.com"

"""Shared helpers for the ClipOnce cloud worker."""
import json, os, re, subprocess, sys, time, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = ROOT / "out"
FONTS = ROOT / "fonts"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

# Never touch these, whatever the title says (posting safety rules).
BLOCK = re.compile(r"\b(leak(s|ed)?|pirat\w*|crack(ed)?|torrent|free download|download free|mod menu|cheat engine|account for sale|giveaway scam|strip ?club|stripper|private dance|lap ?dance|nude|naked|nsfw|onlyfans)\b", re.I)
SKIP_CHANNELS = {c.lower() for c in CONFIG.get("skip_channels", [])}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, check=True, **kw):
    log("$", " ".join(str(c) for c in cmd)[:300])
    r = subprocess.run([str(c) for c in cmd], text=True, capture_output=True, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({r.returncode}): {r.stderr[-1500:]}")
    return r


def load(name, default):
    p = STATE / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(name, data):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def safe_name(s, n=60):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:n] or "clip"


def is_blocked(*texts):
    t = " ".join(x or "" for x in texts)
    return bool(BLOCK.search(t))


# Only clip ORIGINAL creator videos. Skip clip / compilation / reupload channels and videos
# that credit someone else, because re-clipping another clipper's posted clip is lazy stealing.
CLIPPER_CHANNEL = re.compile(r"(clips?\b|clipz|clipper|clipped|highlights?|compilations?|\bbest of\b|moments\b|tiktoks?\b|shorts\b|reels?\b|daily ?gta|gta ?daily|news ?daily)", re.I)
CLIPPER_TITLE = re.compile(r"(compilation|\bclips?\b|clipped|best moments|funniest moments of|reupload|re-upload|#shorts|tiktok|reacts? to|reaction)", re.I)
CREDITS_OTHER = re.compile(r"(credits? ?(to|:|-|goes)|all credit|original (video|clip|creator|stream)|clipped from|\bsource ?:|\bvia @|\bfrom @\w+'?s? (stream|video|channel)|follow (the )?(original )?creator|full (video|stream) (by|from|on) @?\w+|go (follow|subscribe to) @?\w+|\bstreamer ?:)", re.I)


def is_secondhand(title="", channel="", description=""):
    """True when a video looks like someone else's clip, compilation, reaction or reupload."""
    if CLIPPER_CHANNEL.search(channel or "") or CLIPPER_TITLE.search(title or ""):
        return True
    return bool(CREDITS_OTHER.search((description or "")[:1500]))


def ytdlp_base():
    base = ["yt-dlp", "--no-warnings", "--no-progress", "--retries", "3"]
    ck = os.environ.get("YT_COOKIES_FILE")
    if ck and Path(ck).exists() and Path(ck).stat().st_size > 0:
        base += ["--cookies", ck]
    return base

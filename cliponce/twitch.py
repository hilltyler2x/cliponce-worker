"""Twitch discovery with the official Helix API (free app keys in TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET).

Viewer-made Twitch clips mark the moments a live audience reacted to. We only use them as markers:
the actual cut comes from the streamer's own VOD (their original broadcast), with our own captions."""
import datetime as dt, os, requests
from .common import CONFIG, log

API = "https://api.twitch.tv/helix"
_tok = None


def has_keys():
    return bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET"))


def _headers():
    global _tok
    if not _tok:
        r = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": os.environ["TWITCH_CLIENT_ID"], "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
            "grant_type": "client_credentials"}, timeout=30)
        r.raise_for_status()
        _tok = r.json()["access_token"]
    return {"Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": "Bearer " + _tok}


def get(path, **params):
    r = requests.get(API + path, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def game_ids():
    names = CONFIG.get("twitch_games", ["Grand Theft Auto VI"])
    ids = []
    for n in names:
        d = get("/games", name=n)
        if d:
            ids.append(d[0]["id"])
        else:
            log("twitch: game not found:", n)
    return ids


def top_clips(hours=36, first=100):
    """Most-viewed recent clips across the GTA 6 category, newest window first."""
    since = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for gid in game_ids():
        out += get("/clips", game_id=gid, started_at=since, first=min(first, 100))
    out.sort(key=lambda c: -(c.get("view_count") or 0))
    return out


def live_streams(n=3):
    """Top live GTA 6 streams by viewers."""
    out = []
    for gid in game_ids():
        out += get("/streams", game_id=gid, first=20, type="live")
    out.sort(key=lambda s: -(s.get("viewer_count") or 0))
    return out[:n]

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


def game_ids(names=None):
    names = names or CONFIG.get("twitch_games", ["Grand Theft Auto VI"])
    ids = []
    for n in names:
        d = get("/games", name=n)
        if d:
            ids.append(d[0]["id"])
        else:
            log("twitch: game not found:", n)
    return ids


def top_clips(hours=36, first=100, games=None, creators=None):
    """Most-viewed recent clips across a game category (GTA 6 by default) and/or from named creators."""
    since = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    if games is not None or not creators:
        for gid in game_ids(games):
            out += get("/clips", game_id=gid, started_at=since, first=min(first, 100))
    if creators:
        users = get("/users", login=[c.lower() for c in creators][:100])
        for u in users:
            out += get("/clips", broadcaster_id=u["id"], started_at=since, first=50)
        seen = set()
        out = [c for c in out if not (c["id"] in seen or seen.add(c["id"]))]
    out.sort(key=lambda c: -(c.get("view_count") or 0))
    return out


def live_streams(n=3):
    """Top live GTA 6 streams by viewers."""
    out = []
    for gid in game_ids():
        out += get("/streams", game_id=gid, first=20, type="live")
    out.sort(key=lambda s: -(s.get("viewer_count") or 0))
    return out[:n]

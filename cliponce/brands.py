"""Brands: one ClipOnce login can run several sets of social accounts. The site (cliponce.vercel.app) owns the
list; this worker reads it with a GitHub OIDC token (no shared secret) and gets short-lived access tokens only.

The owner's first brand ("legacy") keeps posting with the GitHub secrets it always used, and keeps the old
ledger and count keys, so nothing already posted can go out again."""
import os, requests
from .common import CONFIG, log

SITE = os.environ.get("CLIPONCE_URL", "https://cliponce.vercel.app")
DEFAULT = {"id": "default", "name": "DailyGTA6Info", "legacy": True, "game": "", "creators": [],
           "caps": CONFIG.get("daily_caps", {}), "post_start": 10, "post_end": 22, "paused": False, "links": {}}


def _oidc():
    url, tok = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not tok:
        return None
    r = requests.get(url + "&audience=cliponce", headers={"Authorization": "bearer " + tok}, timeout=30)
    r.raise_for_status()
    return r.json()["value"]


def mask(v):
    if v and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{v}", flush=True)  # the repo is public: never let a token reach the logs


def load_brands():
    """Every brand to work on. Falls back to the legacy brand alone if the site can't be reached."""
    try:
        tok = _oidc()
        if not tok:
            log("brands: no OIDC token here (needs permissions id-token: write), using the main brand only")
            return [dict(DEFAULT)]
        mask(tok)
        r = requests.get(SITE + "/api/brands?action=worker", headers={"Authorization": "Bearer " + tok}, timeout=60)
        r.raise_for_status()
        brands = r.json().get("brands") or []
    except Exception as e:
        log("brands: could not read the brand list, using the main brand only:", str(e)[:200])
        return [dict(DEFAULT)]
    out, have_legacy = [], False
    for b in brands:
        for l in (b.get("links") or {}).values():
            mask(l.get("token"))
        if b.get("legacy"):
            if have_legacy:
                continue
            have_legacy = True
            b["site_id"], b["id"] = b["id"], "default"
        out.append(b)
    if not have_legacy:
        out.insert(0, dict(DEFAULT))
    log("brands:", ", ".join(f"{b['name']} ({'main' if b.get('legacy') else b['id'][:8]})" for b in out))
    return out


def brand_key(b):
    """Ledger and count suffix. Empty for the main brand so its old records still match."""
    return "" if b.get("legacy") else ":" + b["id"]


def creds_for(b, plat):
    """Credentials dict for one platform of one brand, or None when the brand has no sign-in there."""
    if b.get("legacy"):
        return os.environ
    l = (b.get("links") or {}).get(plat)
    if not l or not l.get("token"):
        if l and l.get("error"):
            log(f"{b['name']} {plat}: sign-in problem: {l['error']}")
        return None
    meta = l.get("meta") or {}
    if plat == "youtube":
        return {"YT_ACCESS_TOKEN": l["token"]}
    if plat == "tiktok":
        return {"TIKTOK_ACCESS_TOKEN": l["token"]}
    if plat == "instagram":
        return {"IG_TOKEN": l["token"], "IG_USER_ID": meta.get("ig_user_id", "")}
    if plat == "facebook":
        return {"FB_PAGE_TOKEN": l["token"], "FB_PAGE_ID": meta.get("page_id", "")}
    if plat == "threads":
        return {"THREADS_TOKEN": l["token"], "THREADS_USER_ID": meta.get("user_id", "")}
    return None


def in_window(b, hour_ny):
    s, e = int(b.get("post_start", 0)), int(b.get("post_end", 24))
    return s <= hour_ny < e

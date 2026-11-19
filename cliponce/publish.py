"""Host finished clips as GitHub Release assets and post them to YouTube Shorts, Instagram and Threads.

Credentials come from GitHub Actions secrets (never from the repo):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
  IG_TOKEN, optional IG_USER_ID (default me), optional IG_GRAPH_HOST (auto from the token)
  THREADS_TOKEN, optional THREADS_USER_ID (default me)
A platform whose secrets are missing is skipped, not failed.
"""
import json, os, time, tempfile
from pathlib import Path
import requests
from .common import run, log, ROOT

REPO = os.environ.get("GITHUB_REPOSITORY", "")


# ---------- hosting ----------
def ensure_release(tag):
    r = run(["gh", "release", "view", tag], check=False)
    if r.returncode != 0:
        run(["gh", "release", "create", tag, "--title", tag, "--notes", "ClipOnce autopilot clips (credited to their creators)."])


def host(file: Path, tag):
    ensure_release(tag)
    run(["gh", "release", "upload", tag, str(file), "--clobber"])
    return f"https://github.com/{REPO}/releases/download/{tag}/{file.name}"


def direct_url(url):
    """Resolve GitHub's redirect to the short-lived direct file URL that Meta's servers can fetch."""
    r = requests.head(url, allow_redirects=False, timeout=30)
    return r.headers.get("Location", url) if r.status_code in (301, 302, 303, 307, 308) else url


def fetch(url):
    fd, p = tempfile.mkstemp(suffix=".mp4")
    with requests.get(url, stream=True, timeout=120) as r, os.fdopen(fd, "wb") as f:
        r.raise_for_status()
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    return Path(p)


# ---------- platforms ----------
def has(*keys):
    return all(os.environ.get(k) for k in keys)


def post_youtube(item):
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30).json()
    if "access_token" not in tok:
        raise RuntimeError(f"YouTube token refresh failed: {tok.get('error')}")
    h = {"Authorization": "Bearer " + tok["access_token"]}
    title = (item["title"].split(" | ")[0] + " #Shorts")[:100]
    meta = {"snippet": {"title": title, "description": item["caption"] + f"\n\nOriginal video: {item['source']}",
                        "tags": ["GTA 6", "GTA VI", "GTA 6 secrets", "Shorts"], "categoryId": "20"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    f = fetch(item["url"])
    try:
        init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                             headers={**h, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(f.stat().st_size)}, data=json.dumps(meta), timeout=60)
        if init.status_code >= 300:
            err = init.text[:400]
            if "uploadLimitExceeded" in err or "quota" in err.lower():
                raise CapReached(err)
            raise RuntimeError(f"YouTube init {init.status_code}: {err}")
        with open(f, "rb") as fh:
            up = requests.put(init.headers["Location"], headers={**h, "Content-Type": "video/mp4"}, data=fh, timeout=900)
        if up.status_code >= 300:
            if "uploadLimitExceeded" in up.text:
                raise CapReached(up.text[:300])
            raise RuntimeError(f"YouTube upload {up.status_code}: {up.text[:400]}")
        vid = up.json()["id"]
        if item.get("thumb"):  # custom thumbnail needs a verified channel; best effort
            try:
                img = requests.get(item["thumb"], timeout=60).content
                requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}",
                              headers={**h, "Content-Type": "image/jpeg"}, data=img, timeout=120)
            except Exception as e:
                log("youtube thumbnail skipped:", e)
        return "https://youtube.com/shorts/" + vid
    finally:
        f.unlink(missing_ok=True)


def _meta_poll(host, cid, token, field="status_code"):
    for _ in range(60):
        s = requests.get(f"https://{host}/{cid}", params={"fields": field, "access_token": token}, timeout=30).json()
        st = s.get(field) or s.get("status")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{host} processing {st}: {s}")
        time.sleep(10)
    raise RuntimeError(f"{host} processing timed out")


def post_instagram(item):
    tok = os.environ["IG_TOKEN"]
    # Instagram Login tokens (start with IG) live on graph.instagram.com; Facebook Login ones on graph.facebook.com.
    host = (os.environ.get("IG_GRAPH_HOST") or ("graph.instagram.com" if tok.startswith("IG") else "graph.facebook.com")) + "/v21.0"
    uid = os.environ.get("IG_USER_ID") or "me"
    c = requests.post(f"https://{host}/{uid}/media", data={"media_type": "REELS", "video_url": direct_url(item["url"]),
                      "caption": item["caption"], "share_to_feed": "true", "access_token": tok,
                      **({"cover_url": direct_url(item["thumb"])} if item.get("thumb") else {})}, timeout=60).json()
    if "id" not in c:
        raise RuntimeError(f"Instagram container failed: {c}")
    _meta_poll(host, c["id"], tok)
    p = requests.post(f"https://{host}/{uid}/media_publish", data={"creation_id": c["id"], "access_token": tok}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"Instagram publish failed: {p}")
    return "instagram media " + p["id"]


def post_threads(item):
    host = "graph.threads.net/v1.0"
    uid, tok = os.environ.get("THREADS_USER_ID") or "me", os.environ["THREADS_TOKEN"]
    text = item["caption"][:490]
    c = requests.post(f"https://{host}/{uid}/threads", data={"media_type": "VIDEO", "video_url": direct_url(item["url"]),
                      "text": text, "access_token": tok}, timeout=60).json()
    if "id" not in c:
        raise RuntimeError(f"Threads container failed: {c}")
    _meta_poll(host, c["id"], tok, field="status")
    p = requests.post(f"https://{host}/{uid}/threads_publish", data={"creation_id": c["id"], "access_token": tok}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"Threads publish failed: {p}")
    return "threads post " + p["id"]


class CapReached(Exception):
    pass


def post_facebook(item):
    """Facebook Page Reel via the official Reels publishing API (page token with pages_manage_posts)."""
    host = "graph.facebook.com/v21.0"
    tok = os.environ["FB_PAGE_TOKEN"]
    pid = os.environ.get("FB_PAGE_ID") or "me"
    st = requests.post(f"https://{host}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60).json()
    if "video_id" not in st:
        raise RuntimeError(f"Facebook start failed: {st}")
    vid = st["video_id"]
    up = requests.post(f"https://rupload.facebook.com/video-upload/v21.0/{vid}",
                       headers={"Authorization": "OAuth " + tok, "file_url": direct_url(item["url"])}, timeout=300).json()
    if not up.get("success"):
        raise RuntimeError(f"Facebook upload failed: {up}")
    fin = requests.post(f"https://{host}/{pid}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": item["caption"], "access_token": tok}, timeout=60).json()
    if not fin.get("success"):
        err = str(fin)
        if "limit" in err.lower():
            raise CapReached(err[:300])
        raise RuntimeError(f"Facebook publish failed: {fin}")
    for _ in range(60):
        s = requests.get(f"https://{host}/{vid}", params={"fields": "status", "access_token": tok}, timeout=30).json().get("status", {})
        if s.get("video_status") in ("ready", "published") or (s.get("publishing_phase") or {}).get("status") == "complete":
            break
        if s.get("video_status") == "error":
            raise RuntimeError(f"Facebook processing error: {s}")
        time.sleep(10)
    return f"https://www.facebook.com/reel/{vid}"


PLATFORMS = {
    "youtube": (("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"), post_youtube),
    "instagram": (("IG_TOKEN",), post_instagram),
    "threads": (("THREADS_TOKEN",), post_threads),
    "facebook": (("FB_PAGE_TOKEN",), post_facebook),
}


def refresh_tokens():
    """Long-lived Instagram and Threads tokens last 60 days; refreshing them daily keeps them alive."""
    tok = os.environ.get("IG_TOKEN")
    if tok and tok.startswith("IG"):
        r = requests.get("https://graph.instagram.com/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=30)
        log("instagram token refresh:", r.status_code)
    tok = os.environ.get("THREADS_TOKEN")
    if tok:
        r = requests.get("https://graph.threads.net/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": tok}, timeout=30)
        log("threads token refresh:", r.status_code)

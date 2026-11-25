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
def has(*keys, cr=None):
    cr = os.environ if cr is None else cr
    return all(cr.get(k) for k in keys)


def post_youtube(item, cr=None):
    cr = os.environ if cr is None else cr
    if cr.get("YT_ACCESS_TOKEN"):
        tok = {"access_token": cr["YT_ACCESS_TOKEN"]}  # a brand signed in on the site; the site refreshed it
    else:
        tok = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": cr["YT_CLIENT_ID"], "client_secret": cr["YT_CLIENT_SECRET"],
            "refresh_token": cr["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30).json()
    if "access_token" not in tok:
        raise RuntimeError(f"YouTube token refresh failed: {tok.get('error')}")
    h = {"Authorization": "Bearer " + tok["access_token"]}
    title = (item["title"].split(" | ")[0] + " #Shorts")[:100]
    game = item.get("game") or "GTA 6"
    meta = {"snippet": {"title": title, "description": item["caption"] + f"\n\nOriginal video: {item['source']}",
                        "tags": [game, "Gaming", "Shorts"] if game != "GTA 6" else ["GTA 6", "GTA VI", "GTA 6 secrets", "Shorts"], "categoryId": "20"},
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


def post_instagram(item, cr=None):
    cr = os.environ if cr is None else cr
    tok = cr["IG_TOKEN"]
    # Instagram Login tokens (start with IG) live on graph.instagram.com; Facebook Login ones on graph.facebook.com.
    host = (cr.get("IG_GRAPH_HOST") or ("graph.instagram.com" if tok.startswith("IG") else "graph.facebook.com")) + "/v21.0"
    uid = cr.get("IG_USER_ID") or "me"
    if "graph.facebook.com" in host:
        # Facebook-style token: find the Instagram account linked to the Page(s) it can manage.
        pages = requests.get(f"https://{host}/me/accounts", params={"fields": "instagram_business_account", "access_token": tok}, timeout=30).json()
        igs = [p["instagram_business_account"]["id"] for p in pages.get("data", []) if p.get("instagram_business_account")]
        if not igs:
            me = requests.get(f"https://{host}/me", params={"fields": "instagram_business_account", "access_token": tok}, timeout=30).json()
            igs = [me["instagram_business_account"]["id"]] if me.get("instagram_business_account") else []
        if igs:
            uid = igs[0]  # the Facebook-side id of the linked Instagram account
        elif uid == "me":
            raise SignInProblem("No Instagram account is linked to the Facebook Page this token manages")
    data = {"media_type": "REELS", "video_url": direct_url(item["url"]), "caption": item["caption"], "share_to_feed": "true",
            "access_token": tok, **({"cover_url": direct_url(item["thumb"])} if item.get("thumb") else {})}
    c = requests.post(f"https://{host}/{uid}/media", data=data, timeout=60).json()
    if "id" not in c and not cr.get("IG_GRAPH_HOST"):
        # Unified-login tokens can belong to the other Graph host; try it once.
        alt = ("graph.instagram.com" if "facebook" in host else "graph.facebook.com") + "/v21.0"
        c2 = requests.post(f"https://{alt}/{uid}/media", data=data, timeout=60).json()
        log("instagram: retried on", alt, "->", "ok" if "id" in c2 else str(c2)[:200])
        if "id" in c2:
            c, host = c2, alt
    if "id" not in c:
        if (c.get("error") or {}).get("code") in (100, 190, 10, 200):
            fb = "graph.facebook.com/v21.0"
            for path, f in (("me", "id,name"), ("me/accounts", "name,instagram_business_account{id,username}"),
                            (uid, "id,username")):
                try:
                    r = requests.get(f"https://{fb}/{path}", params={"fields": f, "access_token": tok}, timeout=30).json()
                    log("instagram check", path.replace(uid, "IG_USER_ID"), "->", str(r)[:400])
                except Exception as e:
                    log("instagram check failed", e)
            raise SignInProblem(f"Instagram sign-in or permission problem: {c}")
        raise RuntimeError(f"Instagram container failed: {c}")
    _meta_poll(host, c["id"], tok)
    p = requests.post(f"https://{host}/{uid}/media_publish", data={"creation_id": c["id"], "access_token": tok}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"Instagram publish failed: {p}")
    return "instagram media " + p["id"]


def post_threads(item, cr=None):
    cr = os.environ if cr is None else cr
    host = "graph.threads.net/v1.0"
    uid, tok = cr.get("THREADS_USER_ID") or "me", cr["THREADS_TOKEN"]
    text = item["caption"][:490]
    c = requests.post(f"https://{host}/{uid}/threads", data={"media_type": "VIDEO", "video_url": direct_url(item["url"]),
                      "text": text, "access_token": tok}, timeout=60).json()
    if "id" not in c:
        e = c.get("error") or {}
        if e.get("code") in (190, 200, 10) or "blocked" in str(e.get("message", "")):
            raise SignInProblem(f"Threads sign-in problem (needs a new THREADS_TOKEN): {c}")
        raise RuntimeError(f"Threads container failed: {c}")
    _meta_poll(host, c["id"], tok, field="status")
    p = requests.post(f"https://{host}/{uid}/threads_publish", data={"creation_id": c["id"], "access_token": tok}, timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"Threads publish failed: {p}")
    return "threads post " + p["id"]


class SignInProblem(Exception):
    """The token or permissions are wrong; no clip is at fault, so don't count a failure."""


class CapReached(Exception):
    pass


def post_facebook(item, cr=None):
    cr = os.environ if cr is None else cr
    """Facebook Page Reel via the official Reels publishing API (page token with pages_manage_posts)."""
    host = "graph.facebook.com/v21.0"
    tok = cr.get("FB_PAGE_TOKEN")
    pid = cr.get("FB_PAGE_ID") or "me"
    if not tok:
        # One system-user token for everything: get the Page's own token from it.
        pages = requests.get(f"https://{host}/me/accounts", params={"fields": "id,name,access_token", "access_token": cr["IG_TOKEN"]}, timeout=30).json()
        page = next((p for p in pages.get("data", []) if not cr.get("FB_PAGE_ID") or p["id"] == cr["FB_PAGE_ID"]), None)
        if not page:
            raise SignInProblem(f"Facebook: the token cannot manage any Page yet: {str(pages)[:200]}")
        tok, pid = page["access_token"], page["id"]
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


def post_tiktok(item, cr=None):
    """TikTok Content Posting API, Direct Post with a file upload (official API, no browser)."""
    cr = os.environ if cr is None else cr
    h = {"Authorization": "Bearer " + cr["TIKTOK_ACCESS_TOKEN"], "Content-Type": "application/json; charset=UTF-8"}
    api = "https://open.tiktokapis.com/v2/post/publish"
    info = requests.post("https://open.tiktokapis.com/v2/post/publish/creator_info/query/", headers=h, timeout=30).json()
    if (info.get("error") or {}).get("code") not in (None, "ok"):
        raise SignInProblem(f"TikTok sign-in problem: {info.get('error')}")
    opts = (info.get("data") or {}).get("privacy_level_options") or []
    if "PUBLIC_TO_EVERYONE" not in opts:
        raise SignInProblem(f"TikTok will not allow public posts for this account yet (options: {opts})")
    f = fetch(item["url"])
    try:
        size = f.stat().st_size
        init = requests.post(api + "/video/init/", headers=h, json={
            "post_info": {"title": item["caption"][:2100], "privacy_level": "PUBLIC_TO_EVERYONE",
                          "disable_comment": False, "disable_duet": False, "disable_stitch": False, "is_aigc": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}, timeout=60).json()
        err = init.get("error") or {}
        if err.get("code") not in (None, "ok"):
            if "unaudited" in str(err) or err.get("code") in ("access_token_invalid", "scope_not_authorized", "spam_risk_too_many_posts"):
                if "too_many" in str(err):
                    raise CapReached(str(err)[:300])
                raise SignInProblem(f"TikTok: {err}")
            raise RuntimeError(f"TikTok init failed: {err}")
        pid, up_url = init["data"]["publish_id"], init["data"]["upload_url"]
        with open(f, "rb") as fh:
            up = requests.put(up_url, data=fh, headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"}, timeout=900)
        if up.status_code >= 300:
            raise RuntimeError(f"TikTok upload {up.status_code}: {up.text[:300]}")
        for _ in range(60):
            s = requests.post(api + "/status/fetch/", headers=h, json={"publish_id": pid}, timeout=30).json().get("data") or {}
            if s.get("status") == "PUBLISH_COMPLETE":
                ids = s.get("publicaly_available_post_id") or s.get("publicly_available_post_id") or []
                return f"tiktok post {ids[0]}" if ids else f"tiktok publish {pid}"
            if s.get("status") == "FAILED":
                raise RuntimeError(f"TikTok publish failed: {s.get('fail_reason')}")
            time.sleep(10)
        return f"tiktok publish {pid}"
    finally:
        f.unlink(missing_ok=True)


def post_x(item, cr=None):
    """X (Twitter) video post with the official API v2: chunked media upload, then the post."""
    cr = os.environ if cr is None else cr
    h = {"Authorization": "Bearer " + cr["X_ACCESS_TOKEN"]}
    api = "https://api.x.com/2"
    f = fetch(item["url"])
    try:
        size = f.stat().st_size
        init = requests.post(api + "/media/upload/initialize", headers=h, json={
            "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"}, timeout=60)
        if init.status_code in (401, 403):
            raise SignInProblem(f"X sign-in or plan problem: {init.status_code} {init.text[:300]}")
        if init.status_code == 429:
            raise CapReached(init.text[:300])
        init.raise_for_status()
        mid = init.json()["data"]["id"]
        with open(f, "rb") as fh:
            i = 0
            while True:
                chunk = fh.read(4 << 20)
                if not chunk:
                    break
                r = requests.post(f"{api}/media/upload/{mid}/append", headers=h, files={"media": ("clip.mp4", chunk, "application/octet-stream")},
                                  data={"segment_index": str(i)}, timeout=300)
                if r.status_code >= 300:
                    raise RuntimeError(f"X append {r.status_code}: {r.text[:300]}")
                i += 1
        fin = requests.post(f"{api}/media/upload/{mid}/finalize", headers=h, timeout=60)
        if fin.status_code >= 300:
            raise RuntimeError(f"X finalize {fin.status_code}: {fin.text[:300]}")
        info = (fin.json().get("data") or {}).get("processing_info")
        for _ in range(60):
            if not info or info.get("state") == "succeeded":
                break
            if info.get("state") == "failed":
                raise RuntimeError(f"X processing failed: {info}")
            time.sleep(max(2, int(info.get("check_after_secs", 5))))
            st = requests.get(api + "/media/upload", headers=h, params={"command": "STATUS", "media_id": mid}, timeout=30).json()
            info = (st.get("data") or {}).get("processing_info")
        lines = [l for l in item["caption"].split("\n") if l.strip()]
        text = lines[0] if lines else item.get("title", "")
        credit = next((l for l in lines if "Credit" in l), "")
        tags = next((l for l in reversed(lines) if l.startswith("#")), "")
        for extra in (credit, tags):
            if extra and len(text) + 1 + len(extra) <= 280:
                text += "\n" + extra
        t = requests.post(api + "/tweets", headers=h, json={"text": text[:280], "media": {"media_ids": [mid]}}, timeout=60)
        if t.status_code == 429:
            raise CapReached(t.text[:300])
        if t.status_code in (401, 403):
            raise SignInProblem(f"X post refused: {t.text[:300]}")
        if t.status_code >= 300:
            raise RuntimeError(f"X post {t.status_code}: {t.text[:300]}")
        return "https://x.com/i/status/" + t.json()["data"]["id"]
    finally:
        f.unlink(missing_ok=True)


PLATFORMS = {
    "youtube": (("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"), post_youtube),
    "tiktok": (("TIKTOK_ACCESS_TOKEN",), post_tiktok),
    "x": (("X_ACCESS_TOKEN",), post_x),
    "instagram": (("IG_TOKEN",), post_instagram),
    "threads": (("THREADS_TOKEN",), post_threads),
    "facebook": (("IG_TOKEN",) if not os.environ.get("FB_PAGE_TOKEN") else ("FB_PAGE_TOKEN",), post_facebook),
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

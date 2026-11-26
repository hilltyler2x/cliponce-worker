"""Daily audience snapshot for the DailyGTA6Info page: followers and views per platform.

Writes state/stats/latest.json and adds a dated row to state/stats/history.json.
Only audience numbers are saved here (this repo is public); money lives in Stripe and is
read by the ClipOnce site's private /api/earnings endpoint instead.
Every platform is best effort: a missing sign-in or a changed API leaves a note, never a failed run.
"""
import json, os, re, time
import requests
from cliponce.common import STATE, log, today

FB = "https://graph.facebook.com/v21.0"
TH = "https://graph.threads.net/v1.0"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}


def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"error": {"message": f"HTTP {r.status_code}"}}


def err(j):
    e = j.get("error") if isinstance(j, dict) else None
    return (e.get("message") if isinstance(e, dict) else str(e))[:160] if e else None


def day_total(j):
    """Sum a Graph insights response (either total_value or daily values)."""
    out = {}
    for m in j.get("data", []):
        if "total_value" in m:
            out[m["name"]] = m["total_value"].get("value", 0)
        else:
            out[m["name"]] = sum(v.get("value", 0) for v in m.get("values", []) if isinstance(v.get("value"), (int, float)))
    return out


def meta_pages(tok):
    return get(f"{FB}/me/accounts", fields="id,name,access_token,followers_count,fan_count,instagram_business_account", access_token=tok).get("data", [])


def instagram(tok):
    pages = meta_pages(tok)
    ig = next((p["instagram_business_account"]["id"] for p in pages if p.get("instagram_business_account")), None)
    if not ig:
        return {"note": "no Instagram account linked to the token"}
    p = get(f"{FB}/{ig}", fields="username,followers_count,media_count", access_token=tok)
    if err(p):
        return {"note": err(p)}
    out = {"handle": "@" + p.get("username", ""), "followers": p.get("followers_count"), "posts": p.get("media_count")}
    since, until = int(time.time()) - 86400, int(time.time())
    for metric in ("views", "reach"):
        j = get(f"{FB}/{ig}/insights", metric=metric, period="day", metric_type="total_value", since=since, until=until, access_token=tok)
        if not err(j):
            out[f"{metric}_24h"] = day_total(j).get(metric)
    return out


def facebook(tok):
    pages = meta_pages(tok)
    want = os.environ.get("FB_PAGE_ID")
    page = next((p for p in pages if not want or p["id"] == want), None)
    if not page:
        return {"note": "the token cannot see the Page"}
    out = {"handle": page.get("name"), "followers": page.get("followers_count"), "likes": page.get("fan_count")}
    ptok = os.environ.get("FB_PAGE_TOKEN") or page.get("access_token")
    for metric, key in (("page_video_views", "video_views_24h"), ("page_impressions_unique", "reach_24h")):
        j = get(f"{FB}/{page['id']}/insights", metric=metric, period="day", access_token=ptok)
        if not err(j):
            vals = [v.get("value", 0) for m in j.get("data", []) for v in m.get("values", [])]
            if vals:
                out[key] = vals[-1]
    return out


def threads(tok):
    uid = os.environ.get("THREADS_USER_ID") or "me"
    me = get(f"{TH}/{uid}", fields="username", access_token=tok)
    if err(me):
        return {"note": err(me)}
    out = {"handle": "@" + me.get("username", "")}
    j = get(f"{TH}/{uid}/threads_insights", metric="followers_count", access_token=tok)
    if not err(j):
        out["followers"] = day_total(j).get("followers_count")
    since, until = int(time.time()) - 86400, int(time.time())
    j = get(f"{TH}/{uid}/threads_insights", metric="views,likes", since=since, until=until, access_token=tok)
    if not err(j):
        t = day_total(j)
        out["views_24h"], out["likes_24h"] = t.get("views"), t.get("likes")
    return out


def youtube():
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30).json()
    if "access_token" not in tok:
        return {"note": "YouTube sign-in failed: " + str(tok.get("error"))}
    h = {"Authorization": "Bearer " + tok["access_token"]}
    for params in ({"part": "snippet,statistics", "mine": "true"}, {"part": "snippet,statistics", "forHandle": "@Thedailygta6info"}):
        j = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, headers=h, timeout=30).json()
        items = j.get("items") or []
        if items:
            s, sn = items[0].get("statistics", {}), items[0].get("snippet", {})
            return {"handle": sn.get("customUrl") or sn.get("title"), "followers": int(s.get("subscriberCount", 0)),
                    "views_total": int(s.get("viewCount", 0)), "posts": int(s.get("videoCount", 0))}
    return {"note": "the YouTube sign-in can upload but cannot read channel stats: " + (err(j) or "no channel")}


def tiktok(handle="dailygta6info6"):
    try:
        html = requests.get(f"https://www.tiktok.com/@{handle}", headers=UA, timeout=30).text
    except Exception as e:
        return {"note": f"TikTok page unreachable: {e}"[:160]}
    num = lambda k: (lambda m: int(m.group(1)) if m else None)(re.search(r'"%s":(\d+)' % k, html))
    f = num("followerCount")
    if f is None:
        return {"handle": "@" + handle, "note": "TikTok did not show public counts to the cloud today"}
    return {"handle": "@" + handle, "followers": f, "likes": num("heartCount"), "posts": num("videoCount")}


def main():
    snap = {"date": today(), "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "platforms": {}}
    jobs = {
        "instagram": (("IG_TOKEN",), lambda: instagram(os.environ["IG_TOKEN"])),
        "facebook": (("IG_TOKEN",), lambda: facebook(os.environ["IG_TOKEN"])),
        "threads": (("THREADS_TOKEN",), lambda: threads(os.environ["THREADS_TOKEN"])),
        "youtube": (("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"), youtube),
        "tiktok": ((), tiktok),
    }
    for name, (keys, fn) in jobs.items():
        if not all(os.environ.get(k) for k in keys):
            snap["platforms"][name] = {"note": "no sign-in saved in the cloud"}
            continue
        try:
            snap["platforms"][name] = fn()
        except Exception as e:
            snap["platforms"][name] = {"note": f"{type(e).__name__}: {e}"[:160]}
        log(name, json.dumps(snap["platforms"][name]))
    snap["platforms"]["x"] = {"handle": "@DailyGTA6Info", "note": "X has no free stats API; check analytics on x.com"}
    # Posts made by the cloud worker yesterday and today, from its own counter.
    try:
        counts = json.loads((STATE / "post_counts.json").read_text(encoding="utf-8"))
        snap["cloud_posts"] = {d: {p: n for p, n in v.items() if not p.endswith(":streamer")} for d, v in counts.items() if d != "_last" and isinstance(v, dict)}
    except Exception:
        pass
    d = STATE / "stats"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    hp = d / "history.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    hist[snap["date"]] = {k: {f: v for f, v in p.items() if isinstance(v, (int, float))} for k, p in snap["platforms"].items()}
    hp.write_text(json.dumps(dict(sorted(hist.items())[-400:]), indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

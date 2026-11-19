"""Find today's best new GTA 6 videos to clip (no downloads, metadata only)."""
import json, datetime as dt
from .common import run, ytdlp_base, log, is_blocked, SKIP_CHANNELS, CONFIG


def search(query, n=15):
    r = run(ytdlp_base() + ["--flat-playlist", "-J", f"ytsearchdate{n}:{query}"], check=False, timeout=180)
    if r.returncode != 0:
        log("search failed:", r.stderr[-300:])
        return []
    try:
        return json.loads(r.stdout).get("entries", []) or []
    except Exception:
        return []


def info(url):
    r = run(ytdlp_base() + ["-J", "--skip-download", url], check=False, timeout=180)
    if r.returncode != 0:
        log("info failed:", url, r.stderr[-300:])
        return None
    return json.loads(r.stdout)


def candidates(seen_ids):
    """Most-viewed recent videos across the configured searches, minus ones already used."""
    max_age = CONFIG.get("max_age_days", 3)
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=max_age)).strftime("%Y%m%d")
    pool = {}
    for q in CONFIG["searches"]:
        for e in search(q, CONFIG.get("per_search", 15)):
            vid = e.get("id")
            if not vid or vid in seen_ids or vid in pool:
                continue
            title, ch = e.get("title") or "", (e.get("channel") or e.get("uploader") or "")
            if is_blocked(title) or ch.lower() in SKIP_CHANNELS:
                continue
            dur = e.get("duration") or 0
            if dur and (dur < 60 or dur > CONFIG.get("max_video_minutes", 90) * 60):
                continue
            pool[vid] = e
    ranked = sorted(pool.values(), key=lambda e: -(e.get("view_count") or 0))
    out = []
    for e in ranked[: CONFIG.get("inspect_top", 12)]:
        full = info(f"https://www.youtube.com/watch?v={e['id']}")
        if not full:
            continue
        up = full.get("upload_date") or "99999999"
        if up < cutoff or full.get("is_live") or full.get("was_live") and (full.get("duration") or 0) > 4 * 3600:
            continue
        if is_blocked(full.get("title"), full.get("description", "")[:500]):
            continue
        out.append(full)
    out.sort(key=lambda f: -(f.get("view_count") or 0))
    log(f"{len(out)} candidate videos")
    return out


def subtitles(full, dest_dir):
    """Download English subtitles (manual first, then auto) as VTT; return text or ''."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(dest_dir / full["id"])
    run(ytdlp_base() + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
                        "--sub-format", "vtt", "-o", tmpl, full["webpage_url"]], check=False, timeout=180)
    files = sorted(dest_dir.glob(full["id"] + "*.vtt"), key=lambda p: ("orig" in p.name, len(p.name)))
    return files[0].read_text(encoding="utf-8", errors="ignore") if files else ""

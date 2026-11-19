"""Cloud autopilot for Twitch: find the GTA 6 moments live audiences reacted to most, cut them from
the streamer's own VOD, caption them and queue them. Originals only, every clip credits the streamer."""
import datetime as dt, json, os, sys, time
from cliponce.common import OUT, CONFIG, load, save, today, safe_name, log, is_blocked, is_secondhand, SKIP_CHANNELS
from cliponce import render, twitch, thumb
from cliponce.moments import kind_of, score_text, EMOJI
import re
RISKY = re.compile(r"\b(want the ban|get(ting)? banned|bann?able|tos|dancer|dancing for|twerk\w*|thong|bikini|boobs?|cleavage|sexy|hooker|escort|prostitut\w*)\b", re.I)
from live import transcribe

HYPE_KIND = [("😂", "Funny moment"), ("🤣", "Funny moment"), ("💀", "Funny moment"), ("lmao", "Funny moment"), ("yap", "Funny moment"), ("lol", "Funny moment"), ("funny", "Funny moment"), ("bro", "Funny moment"), ("dead", "Funny moment"),
             ("glitch", "Glitch"), ("bug", "Glitch"), ("secret", "Hidden secret"), ("easter", "Easter egg"),
             ("hidden", "Hidden detail"), ("trick", "Trick"), ("first", "First ever"), ("insane", "Insane moment"),
             ("crazy", "Insane moment"), ("wtf", "Insane moment"), ("no way", "Insane moment")]


def kind_for(title):
    t = (title or "").lower()
    for k, label in HYPE_KIND:
        if k in t:
            return label
    k = kind_of(t)
    return k if k != "Crazy detail" else "Insane moment"


def main():
    if not twitch.has_keys():
        log("twitch: no TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET saved yet, skipping")
        return
    seen, queue = load("seen.json", {}), load("queue.json", [])
    # Runs every 30 minutes. Goal: be FIRST. Only moments from the last few hours, ranked by how fast
    # viewers are piling onto them (views per hour), big creators first, a couple per run.
    todays = [q for q in queue if q.get("created") == today() and str(q.get("id", "")).find("_tw_") > 0]
    target = max(0, min(CONFIG.get("fresh_per_run", 2), CONFIG.get("clips_per_day", 10) - len(todays)))
    made = 0
    per_streamer, min_views = CONFIG.get("twitch_per_streamer", 2), CONFIG.get("twitch_min_views", 80)
    min_vph = CONFIG.get("twitch_min_views_per_hour", 60)
    used = {}
    for q in todays:
        used[q.get("creator", "")] = used.get(q.get("creator", ""), 0) + 1
    work = OUT / today()
    now = time.time()
    clips = twitch.top_clips(CONFIG.get("fresh_hours", 3))
    for c in clips:
        try:
            age_h = max(0.25, (now - dt.datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()) / 3600)
        except Exception:
            age_h = 3
        c["_age_h"], c["_vph"] = age_h, (c.get("view_count") or 0) / age_h
    prio = [x.lower() for x in CONFIG.get("twitch_priority", [])]
    clips.sort(key=lambda c: (0 if (c.get("broadcaster_name") or "").lower().replace(" ", "") in prio else 1, -c["_vph"]))
    log(f"twitch: {len(clips)} GTA 6 clips from the last {CONFIG.get('fresh_hours', 3)} h, making up to {target}")
    for c in clips:
        if made >= target:
            break
        cid, who, title = c["id"], c.get("broadcaster_name") or "", c.get("title") or ""
        key = "tw:" + cid
        vodkey = f"twvod:{c.get('video_id')}:{int((c.get('vod_offset') or 0) // 60)}"
        # Many viewers clip the same moment; while the VOD offset is not known yet, group by streamer and 3 minutes.
        momkey = f"twmom:{c.get('broadcaster_id')}:{(c.get('created_at') or '')[:15]}{int((c.get('created_at') or '0000000000000000')[15:16] or 0) // 3}"
        if key in seen or vodkey in seen or momkey in seen:
            continue
        if (c.get("view_count") or 0) < min_views or c["_vph"] < min_vph:
            continue
        langs = CONFIG.get("twitch_languages", ["en"])
        if langs and (c.get("language") or "en").split("-")[0] not in langs:
            continue
        if is_blocked(title) or RISKY.search(title) or who.lower() in SKIP_CHANNELS or is_secondhand(title, who):
            continue
        # Stay well away from a part of a stream we already held back (same VOD, within 30 minutes).
        if c.get("video_id") and any(h.get("video_id") == c["video_id"] and abs((h.get("start") or 0) - (c.get("vod_offset") or 0)) < 1800
                                     for h in queue if h.get("held")):
            continue
        if used.get(who, 0) >= per_streamer:
            continue
        seen[key] = seen[vodkey] = seen[momkey] = today()
        handle = who.replace(" ", "")
        dur = float(c.get("duration") or 30)
        name = f"{today()}_tw_{safe_name(handle, 24)}_{cid[:20]}"
        try:
            src = None
            if c.get("video_id") and c.get("vod_offset") is not None:
                start = max(0, float(c["vod_offset"]) - 6)
                end = start + min(dur + 12, 58)
                src_url = f"https://www.twitch.tv/videos/{c['video_id']}"
                try:
                    # A VOD of a stream that is still live can be very slow; don't let one clip stall the run.
                    src = render.download_section(src_url, start, end, work / "src" / name, timeout=240)
                except Exception as e:
                    log("VOD cut too slow or failed, using the viewer clip of this moment:", str(e)[-200:])
            if src is None:
                start, end, src_url = 0, min(dur, 58), c["url"]
                src = render.download_section(src_url, 0, end, work / "src" / name)
            length = end - start
            cues = transcribe(src)
            said = " ".join(x[2] for x in cues)
            if is_blocked(said) or RISKY.search(said):
                log("skipped, adult or unsafe talk in the clip:", title)
                continue
            kind = kind_for(title + " " + " ".join(x[2] for x in cues))
            ass = work / (name + ".ass")
            render.build_ass(cues, 0, length, f"{kind} in GTA 6", f"🎥 @{handle} on Twitch", ass)
            out = render.render(src, ass, work / (name + ".mp4"), length)
        except Exception as e:
            log("twitch render failed:", e)
            continue
        quote = max((x[2] for x in cues), key=len, default="")[:90]
        cap = "\n".join(x for x in [f"{kind} in GTA 6 {EMOJI.get(kind, '🎮')}", f"“{quote}”" if quote else f"“{title}”",
                                   f"🎥 Credit: @{handle} on Twitch", "#GTA6 #GTAVI #Twitch #Gaming"] if x)
        meta = {"id": name, "video_id": c.get("video_id"), "source": src_url, "creator": who, "handle": handle,
                "start": start, "end": end, "kind": kind, "score": c.get("view_count"),
                "why": f"{c.get('view_count')} views in {c['_age_h']:.1f} h on the Twitch clip of this moment: {title}"[:160],
                "caption": cap, "title": f"{kind} in GTA 6 | {title}"[:95], "created": today(), "made_at": int(time.time()),
                "fresh": True, "hook": thumb.hook_text(title, quote, kind).capitalize(), "posted": {}}
        cover = thumb.cover_for(src, work / (name + "_cover.jpg"), title, quote, kind, f"@{handle} on Twitch",
                                length, around=(6 + dur / 2) if c.get("video_id") else dur / 2)
        if os.environ.get("GITHUB_ACTIONS"):
            from cliponce.publish import host
            try:
                if cover:
                    meta["thumb"] = host(cover, "clips-" + today())
                meta["url"] = host(out, "clips-" + today())
            except Exception as e:
                log("could not host clip:", e)
        queue.append(meta)
        used[who] = used.get(who, 0) + 1
        made += 1
        log("clip ready:", name, kind, c.get("view_count"), "views |", title)
        save("seen.json", seen)
        save("queue.json", queue)
    save("seen.json", seen)
    save("queue.json", queue)
    log(f"done: {made} new Twitch clips")


if __name__ == "__main__":
    sys.exit(main())

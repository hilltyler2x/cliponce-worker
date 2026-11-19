"""Cloud autopilot for Twitch: find the GTA 6 moments live audiences reacted to most, cut them from
the streamer's own VOD, caption them and queue them. Originals only, every clip credits the streamer."""
import json, os, sys
from cliponce.common import OUT, CONFIG, load, save, today, safe_name, log, is_blocked, is_secondhand, SKIP_CHANNELS
from cliponce import render, twitch
from cliponce.moments import kind_of, score_text, EMOJI
from live import transcribe

HYPE_KIND = [("lol", "Funny moment"), ("funny", "Funny moment"), ("bro", "Funny moment"), ("dead", "Funny moment"),
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
    made, target = 0, CONFIG.get("clips_per_day", 6)
    per_streamer, min_views = CONFIG.get("twitch_per_streamer", 2), CONFIG.get("twitch_min_views", 150)
    used = {}
    work = OUT / today()
    clips = twitch.top_clips(CONFIG.get("twitch_hours", 36))
    log(f"twitch: {len(clips)} recent GTA 6 clips found")
    for c in clips:
        if made >= target:
            break
        cid, who, title = c["id"], c.get("broadcaster_name") or "", c.get("title") or ""
        key = "tw:" + cid
        vodkey = f"twvod:{c.get('video_id')}:{int((c.get('vod_offset') or 0) // 60)}"
        if key in seen or vodkey in seen or (c.get("view_count") or 0) < min_views:
            continue
        if is_blocked(title) or who.lower() in SKIP_CHANNELS or is_secondhand(title, who):
            continue
        if used.get(who, 0) >= per_streamer:
            continue
        seen[key] = seen[vodkey] = today()
        handle = who.replace(" ", "")
        dur = float(c.get("duration") or 30)
        name = f"{today()}_tw_{safe_name(handle, 24)}_{cid[:20]}"
        try:
            if c.get("video_id") and c.get("vod_offset") is not None:
                start = max(0, float(c["vod_offset"]) - 6)
                end = start + min(dur + 12, 58)
                src_url = f"https://www.twitch.tv/videos/{c['video_id']}"
                src = render.download_section(src_url, start, end, work / "src" / name)
            else:
                start, end, src_url = 0, min(dur, 58), c["url"]
                src = render.download_section(src_url, 0, end, work / "src" / name)
            length = end - start
            cues = transcribe(src)
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
                "why": f"{c.get('view_count')} views on the Twitch clip of this moment: {title}"[:160],
                "caption": cap, "title": f"{kind} in GTA 6 | {title}"[:95], "created": today(), "posted": {}}
        if os.environ.get("GITHUB_ACTIONS"):
            from cliponce.publish import host
            try:
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

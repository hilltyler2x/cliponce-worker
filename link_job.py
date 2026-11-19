"""Website link job: clip the newest broadcast or video behind any Twitch link.

Started by the ClipOnce website (workflow_dispatch with job_id, url, count). Writes its progress and
results to state/jobs/<job_id>.json, which the website reads back.
Twitch channel link -> newest broadcast (the current one when live). VOD link -> that VOD. Clip link -> that clip.
Moments come from viewer-made Twitch clips of that broadcast when Twitch keys are saved, otherwise from
the loudest crowd / streamer reactions in the audio. Captions by Whisper, credit to the streamer."""
import array, json, os, re, subprocess, sys
from cliponce.common import OUT, STATE, log, run, ytdlp_base, safe_name, today, is_blocked
from cliponce import render, twitch
from cliponce.moments import score_text, EMOJI
from live import transcribe
from twitch_autopilot import kind_for

JOB = re.sub(r"[^A-Za-z0-9-]", "", os.environ.get("JOB_ID", "test"))[:40] or "test"
URL = os.environ.get("JOB_URL", "").strip()
COUNT = max(1, min(int(os.environ.get("JOB_COUNT", "3") or 3), 6))
PRE, POST, MAXLEN = 18, 14, 45
JOBS = STATE / "jobs"


def write(status, **kw):
    JOBS.mkdir(parents=True, exist_ok=True)
    data = {"id": JOB, "url": URL, "status": status, **kw}
    (JOBS / f"{JOB}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(f"git add state/jobs && git commit -qm 'job {JOB} {status}' && "
                       "for i in 1 2 3 4; do git pull -q --rebase -X theirs && git push -q && break; sleep 3; done",
                       shell=True)


def meta(url):
    r = run(ytdlp_base() + ["-J", "--skip-download", "--playlist-items", "1", url], check=False, timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "")[-300:])
    d = json.loads(r.stdout)
    if d.get("_type") == "playlist":
        d = (d.get("entries") or [None])[0]
        if not d:
            raise RuntimeError("no broadcasts found on that channel")
        if not d.get("duration"):
            d = meta(d.get("url") or d.get("webpage_url"))
    return d


def resolve(url):
    """Return (source info, kind) for a Twitch link."""
    if not re.search(r"(^|\.)twitch\.tv", url):
        raise ValueError("Only Twitch links work in the cloud right now.")
    if "/clip/" in url or "clips.twitch.tv" in url:
        return meta(url), "clip"
    if re.search(r"/videos/\d+", url):
        return meta(url), "vod"
    ch = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", url).group(1)
    return meta(f"https://www.twitch.tv/{ch}/videos?filter=archives&sort=time"), "vod"


def clip_markers(info):
    """Viewer clips of this exact broadcast, as (offset seconds, title, views)."""
    if not twitch.has_keys():
        return []
    try:
        login = (info.get("uploader_id") or info.get("uploader") or "").lower()
        users = twitch.get("/users", login=login)
        if not users:
            return []
        vid = str(info.get("id", "")).lstrip("v")
        out = []
        for c in twitch.get("/clips", broadcaster_id=users[0]["id"], first=100):
            if str(c.get("video_id")) == vid and c.get("vod_offset") is not None and not is_blocked(c.get("title")):
                out.append((float(c["vod_offset"]) + float(c.get("duration") or 20) * 0.6, c.get("title") or "", c.get("view_count") or 0))
        return sorted(out, key=lambda x: -x[2])
    except Exception as e:
        log("twitch clip lookup failed:", e)
        return []


def loud_markers(info, work):
    """Loudest reaction spikes in the broadcast audio (last 4 hours at most)."""
    dur = float(info.get("duration") or 0)
    a = max(0.0, dur - 4 * 3600) if dur else 0.0
    aud = work / "audio"
    args = ["-f", "audio_only/ba/worst", "-o", str(aud) + ".%(ext)s"]
    if dur:
        args += ["--download-sections", f"*{a:.0f}-{dur:.0f}"]
    run(ytdlp_base() + args + [info["webpage_url"]], timeout=3000)
    f = sorted(work.glob("audio.*"))[0]
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(f), "-ac", "1", "-ar", "1000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    s = array.array("h", raw[: len(raw) // 2 * 2])
    sec = [(sum(x * x for x in s[i:i + 1000]) / 1000) ** 0.5 for i in range(0, len(s) - 999, 1000)]
    if len(sec) < 60:
        return []
    win = [sum(sec[i:i + 3]) / 3 for i in range(len(sec) - 2)]
    peaks = []
    for i in range(60, len(win) - 10):
        ctx = sorted(win[max(0, i - 120):i])
        base = ctx[len(ctx) // 2] or 1
        peaks.append((win[i] / base, i))
    peaks.sort(reverse=True)
    out = []
    for ratio, i in peaks:
        if ratio < 1.6 or len(out) >= COUNT * 4:
            break
        if all(abs(i - j) > 90 for _, j in out):
            out.append((ratio, i))
    return [(a + i, "", round(r, 2)) for r, i in out]


def main():
    write("running", step="Finding the newest broadcast")
    work = OUT / "jobs" / JOB
    work.mkdir(parents=True, exist_ok=True)
    try:
        info, kind = resolve(URL)
        who = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "streamer"
        handle = re.sub(r"\s+", "", who)
        title = info.get("title") or ""
        if kind == "clip":
            marks = [(min(float(info.get("duration") or 30), MAXLEN) / 2, title, 0)]
        else:
            write("running", step=f"Finding the best moments in “{title[:60]}”", source=info.get("webpage_url"), creator=who)
            marks = clip_markers(info)
            if len(marks) < COUNT:
                marks += [m for m in loud_markers(info, work) if all(abs(m[0] - x[0]) > 60 for x in marks)]
        if not marks:
            raise RuntimeError("No standout moments found in that broadcast.")
        clips, tried = [], 0
        for t, ctitle, strength in marks:
            if len(clips) >= COUNT or tried >= COUNT * 3:
                break
            tried += 1
            write("running", step=f"Cutting clip {len(clips) + 1} of {COUNT}", source=info.get("webpage_url"), creator=who, clips=clips)
            if kind == "clip":
                start, end = 0.0, min(float(info.get("duration") or 30), MAXLEN)
            else:
                start = max(0.0, t - PRE)
                end = min(start + PRE + POST, float(info.get("duration") or 1e9))
            name = f"{JOB[:8]}_{safe_name(handle, 20)}_{int(start)}"
            try:
                src = render.download_section(info["webpage_url"], start, end, work / "src" / name)
                cues = transcribe(src)
                words = " ".join(c[2] for c in cues)
                if kind != "clip" and not ctitle and strength and strength < 2.2 and score_text(words) < 2:
                    log("weak moment, skipping", start)
                    continue
                k = kind_for(ctitle + " " + words + " " + title)
                ass = work / (name + ".ass")
                render.build_ass(cues, 0, end - start, f"{k} in GTA 6" if "gta" in title.lower() else k, f"🎥 @{handle} on Twitch", ass)
                out = render.render(src, ass, work / (name + ".mp4"), end - start)
                link = out.name
                if os.environ.get("GITHUB_ACTIONS"):
                    from cliponce.publish import host
                    link = host(out, "jobs-" + today())
                quote = max((c[2] for c in cues), key=len, default="")[:90]
                cap = "\n".join(x for x in [f"{k} {EMOJI.get(k, '🎮')}", f"“{ctitle or quote}”" if (ctitle or quote) else "",
                                           f"🎥 Credit: @{handle} on Twitch", "#Twitch #Clips" + (" #GTA6" if "gta" in title.lower() else "")] if x)
                clips.append({"url": link, "kind": k, "start": round(start), "end": round(end), "caption": cap,
                              "why": (f"{strength} views on viewers' clip: {ctitle}" if ctitle else f"Reaction spike, {strength}x louder than usual")[:140]})
            except Exception as e:
                log("clip failed:", e)
        if not clips:
            raise RuntimeError("Could not cut any clips from that broadcast.")
        write("done", source=info.get("webpage_url"), creator=who, title=title, clips=clips)
    except Exception as e:
        log("job failed:", e)
        write("failed", error=str(e)[:300])


if __name__ == "__main__":
    sys.exit(main())

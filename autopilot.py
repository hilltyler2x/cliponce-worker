"""Daily autopilot: find the best new GTA 6 videos, clip only the genuinely interesting
moments, and add them to the posting queue. Runs on GitHub Actions; no PC needed."""
import json, sys
from cliponce.common import OUT, CONFIG, load, save, today, safe_name, log
import os
from cliponce import find, moments, render, thumb


def main():
    seen = load("seen.json", {})
    queue = load("queue.json", [])
    made, target = 0, CONFIG.get("clips_per_day", 6)
    work = OUT / today()
    for full in find.candidates(set(seen)):
        if made >= target:
            break
        vid, creator = full["id"], (full.get("channel") or full.get("uploader") or "").strip()
        handle = (full.get("uploader_id") or "").lstrip("@") or creator.replace(" ", "")
        seen[vid] = today()
        vtt = find.subtitles(full, work / "subs")
        cues = moments.parse_vtt(vtt) if vtt else []
        picks = moments.pick(cues, full.get("chapters"), full.get("duration") or 0,
                             n=min(CONFIG.get("clips_per_video", 2), target - made),
                             threshold=CONFIG.get("min_score", 9))
        if not picks:
            log("no strong moments, skipping:", full.get("title"))
            continue
        for i, m in enumerate(picks):
            name = f"{today()}_{safe_name(creator, 24)}_{vid}_{int(m['start'])}"
            try:
                src = render.download_section(full["webpage_url"], m["start"], m["end"], work / "src" / name)
                ass = work / (name + ".ass")
                render.build_ass(cues, m["start"], m["end"], thumb.name_hook(creator, m["kind"], name), thumb.pretty_name(creator), ass)
                out = render.render(src, ass, work / (name + ".mp4"), m["end"] - m["start"])
            except Exception as e:
                log("render failed:", e)
                continue
            cap = moments.caption_for(m, handle, full.get("title"))
            meta = {"id": name, "file": str(out.relative_to(OUT.parent)), "video_id": vid, "source": full["webpage_url"],
                    "creator": creator, "handle": handle, "start": m["start"], "end": m["end"], "kind": m["kind"],
                    "score": round(m["score"], 1), "why": moments.best_quote(m["text"], 160, lines=m.get("hot")), "caption": cap,
                    "title": thumb.name_hook(creator, m["kind"], name)[:95],
                    "created": today(), "posted": {}}
            if os.environ.get("GITHUB_ACTIONS"):
                from cliponce.publish import host
                try:
                    meta["url"] = host(out, "clips-" + today())
                except Exception as e:
                    log("could not host clip:", e)
            (work / (name + ".json")).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
            queue.append(meta)
            made += 1
            log("clip ready:", name, m["kind"], round(m["score"], 1))
    save("seen.json", seen)
    save("queue.json", queue)
    log(f"done: {made} new clips")
    (OUT / "made.txt").parent.mkdir(exist_ok=True)
    (OUT / "made.txt").write_text(str(made))


if __name__ == "__main__":
    sys.exit(main())

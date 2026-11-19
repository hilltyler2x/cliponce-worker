"""Give queued clips that have no cover one: download the hosted clip, pick the best frame, add the hook."""
import sys, tempfile
from pathlib import Path
from cliponce.common import load, save, log, run
from cliponce import thumb


def main(limit=8):
    queue = load("queue.json", [])
    done = 0
    for item in reversed(queue):
        if done >= limit:
            break
        if item.get("thumb") or not item.get("url") or "github.com" not in item["url"]:
            continue
        p = item.get("posted") or {}
        if all(p.get(k) == "skip" for k in ("youtube", "instagram", "threads")):
            continue
        with tempfile.TemporaryDirectory() as d:
            v = Path(d) / "clip.mp4"
            try:
                run(["curl", "-sSL", "-o", str(v), item["url"]])
                length = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)]).stdout.strip())
                quote = next((l.strip("\u201c\u201d") for l in (item.get("caption") or "").split("\n") if l.startswith("\u201c")), "")
                title = (item.get("why") or "").split(": ", 1)[-1] if ": " in (item.get("why") or "") else ""
                out = thumb.cover_for(v, Path(d) / (item["id"] + "_cover.jpg"), title, quote, item.get("kind", ""),
                                      f"@{item.get('handle') or item.get('creator')} on Twitch", length)
                if not out:
                    continue
                from cliponce.publish import host
                tag = item["url"].split("/releases/download/")[1].split("/")[0]
                item["thumb"] = host(out, tag)
                item.setdefault("hook", thumb.hook_text(title, quote, item.get("kind", "")).capitalize())
                done += 1
                log("cover added:", item["id"])
            except Exception as e:
                log("cover backfill failed:", item.get("id"), e)
    save("queue.json", queue)


if __name__ == "__main__":
    sys.exit(main())

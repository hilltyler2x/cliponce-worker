"""Take a clip made on the PC and queue it for the cloud's Instagram, Facebook and Threads posting.
Usage (GitHub Actions): python ingest.py <video_url> '<meta json>'
meta: {"id", "creator", "handle", "kind", "source", "caption"?, "quote"?, "made_at"?}
The PC posts TikTok, YouTube Shorts and X itself; the cloud is the only poster for Instagram, Facebook and Threads."""
import hashlib, json, sys, tempfile, time
from pathlib import Path
from cliponce.common import load, save, log, run, today, safe_name
from cliponce import thumb
from cliponce.publish import host
from cliponce.moments import EMOJI


def main(url, meta):
    m = json.loads(meta)
    queue, seen = load("queue.json", []), load("seen.json", {})
    with tempfile.TemporaryDirectory() as d:
        v = Path(d) / "in.mp4"
        run(["curl", "-sSL", "--fail", "-o", str(v), url], timeout=600)
        sha = hashlib.sha1(v.read_bytes()).hexdigest()
        if f"pc:{sha}" in seen or any(q.get("sha1") == sha for q in queue):
            log("already queued:", sha)
            return
        creator, kind = m.get("creator") or m.get("handle") or "", m.get("kind") or "Insane moment"
        cid = m.get("id") or f"{today()}_pc_{safe_name(creator, 24)}_{sha[:10]}"
        f = Path(d) / (safe_name(cid, 90) + ".mp4")
        v.rename(f)
        length = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).stdout.strip())
        hook = thumb.name_hook(creator, kind, cid)
        pname = thumb.pretty_name(creator)
        quote = (m.get("quote") or "").strip()
        cap = m.get("caption") or "\n".join(x for x in [f"{hook} {EMOJI.get(kind, '🎮')}", f"“{quote}”" if quote else "",
                                                        f"🎥 Credit: {pname}", "#GTA6 #GTAVI #Gaming"] if x)
        tag = "pc-" + today()
        item = {"id": cid, "creator": creator, "handle": m.get("handle") or creator, "kind": kind, "source": m.get("source", ""),
                "caption": cap, "title": hook[:95], "hook": hook, "namehook": True, "sha1": sha, "from_pc": True,
                "created": today(), "made_at": int(m.get("made_at") or time.time()), "why": m.get("why") or "Picked on the PC",
                "url": host(f, tag), "posted": {"youtube": "skip"}}
        cover = thumb.cover_for(f, Path(d) / (f.stem + "_cover.jpg"), "", quote, kind, f"{pname}", length, hook=hook)
        if cover:
            item["thumb"] = host(cover, tag)
    queue.append(item)
    seen[f"pc:{sha}"] = today()
    save("queue.json", queue)
    save("seen.json", seen)
    log("queued from PC:", cid, item["url"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "{}")

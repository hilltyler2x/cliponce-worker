"""Post the next queued clip to each platform, within each platform's daily cap.
Runs every couple of hours; a clip over the cap simply waits for the next day."""
import sys
from cliponce.common import CONFIG, load, save, today, log
from cliponce.publish import PLATFORMS, has, CapReached, refresh_tokens


def main():
    queue = load("queue.json", [])
    counts = load("post_counts.json", {})
    if not counts.get(today()):
        try:
            refresh_tokens()
        except Exception as e:
            log("token refresh skipped:", e)
    day = counts.setdefault(today(), {})
    for k in list(counts):
        if k < today() and len(counts) > 14:
            counts.pop(k)
    caps = CONFIG.get("daily_caps", {})
    per_run = CONFIG.get("posts_per_run", 1)
    for plat, (keys, fn) in PLATFORMS.items():
        if not has(*keys):
            log(f"{plat}: no sign-in saved yet, skipping")
            continue
        done = 0
        # Freshest first: a moment is worth most before anyone else posts it.
        for item in sorted(queue, key=lambda q: (q.get("made_at") or 0, q.get("created") or ""), reverse=True):
            if done >= per_run or day.get(plat, 0) >= caps.get(plat, 1):
                break
            st = item.setdefault("posted", {})
            held = item.get("held") or all(st.get(k) == "skip" for k in ("youtube", "instagram", "threads"))
            if held or plat in st or not item.get("url"):
                continue
            if st.get(plat + "_fails", 0) >= 3:
                continue
            try:
                link = fn(item)
                st[plat] = link
                day[plat] = day.get(plat, 0) + 1
                done += 1
                log(f"{plat}: posted {item['id']} -> {link}")
            except CapReached as e:
                log(f"{plat}: platform daily limit reached, waiting until tomorrow ({e})")
                day[plat] = caps.get(plat, 1)
                break
            except Exception as e:
                st[plat + "_fails"] = st.get(plat + "_fails", 0) + 1
                st[plat + "_error"] = str(e)[:300]
                log(f"{plat}: failed {item['id']}: {e}")
            save("queue.json", queue)
            save("post_counts.json", counts)
    save("queue.json", queue[-200:])
    save("post_counts.json", counts)


if __name__ == "__main__":
    sys.exit(main())

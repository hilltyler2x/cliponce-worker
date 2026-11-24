"""Post queued clips, safely.

- Scheduled mode (no arguments): the next clip per platform, within the daily cap and spacing.
- Single clip mode: python post_queue.py --id <clip id> [--edit '<json>']  (the account page's Post / Edit buttons).

Never posts the same video twice: every post is recorded in state/posted_ledger.json keyed by
sha1(mp4):platform, the ledger is re-read from the repo right before each upload and saved right after,
so the cloud, the PC (which reads the same ledger) and manual posts can never double up, even if a run dies
halfway, the PC is off, or no Claude session is around. Everything here runs on GitHub Actions on its own.
"""
import hashlib, json, os, subprocess, sys, time
import requests
from cliponce.common import CONFIG, STATE, load, save, today, log
from cliponce.publish import PLATFORMS, has, CapReached, SignInProblem, refresh_tokens
from cliponce.brands import load_brands, brand_key, creds_for, in_window
from zoneinfo import ZoneInfo
import datetime as dt

META = ("instagram", "facebook", "threads")
BLOCK_HOURS = 12  # when Meta blocks API access, stop knocking for this long instead of retrying every run


def remote_json(name, default):
    """Latest copy from the repo (other runs and the PC may have written since this run started)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return load(name, default)
    subprocess.run("git fetch -q origin main", shell=True)
    r = subprocess.run(f"git show origin/main:state/{name}", shell=True, text=True, capture_output=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else default
    except Exception:
        return default


def video_sha1(item):
    if item.get("sha1"):
        return item["sha1"]
    h = hashlib.sha1()
    with requests.get(item["url"], stream=True, timeout=120) as r:
        r.raise_for_status()
        for chunk in r.iter_content(1 << 20):
            h.update(chunk)
    item["sha1"] = h.hexdigest()
    return item["sha1"]


def persist(msg):
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run([sys.executable, "sync_state.py", msg])


def blocked(alerts, plat):
    b = alerts.get("meta_blocked") if plat in META else None
    return b and time.time() - b.get("since", 0) < BLOCK_HOURS * 3600


def note_block(alerts, err):
    alerts["meta_blocked"] = {"since": int(time.time()), "error": str(err)[:300],
                              "message": "Meta blocked API posting for this business. Request a review in Meta Business Support; "
                                         "ClipOnce pauses Instagram, Facebook and Threads posting and retries every 12 hours."}
    save("alerts.json", alerts)


def post_one(item, plat, fn, ledger, by, alerts, brand=None, cr=None):
    """Post one clip to one platform of one brand, unless the ledger says this exact video is already there."""
    st = item.setdefault("posted", {})
    key = f"{video_sha1(item)}:{plat}{brand_key(brand) if brand else ''}"
    ledger.update(remote_json("posted_ledger.json", {}))
    if key in ledger:
        st[plat] = ledger[key].get("ref") or "posted"
        log(f"{plat}: {item['id']} is already posted there ({ledger[key].get('by')}), skipping the duplicate")
        return None
    link = fn(item, cr)
    st[plat] = link
    ledger[key] = {"ref": link, "by": by, "id": item["id"], **({"brand": brand["id"]} if brand and not brand.get("legacy") else {}), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save("posted_ledger.json", ledger)
    if plat in META and alerts.pop("meta_blocked", None):
        save("alerts.json", alerts)  # Meta posting works again
    return link


def brand_platforms(brand):
    """(platform, post function, credentials) this brand can post to from the cloud right now."""
    out = []
    approved = CONFIG.get("public_upload_approved", {})
    for plat, (keys, fn) in PLATFORMS.items():
        if brand.get("legacy"):
            if not has(*keys):
                continue
            cr = os.environ
        else:
            cr = creds_for(brand, plat)
            if cr is None:
                continue
            if plat == "youtube" and not approved.get("youtube"):
                # Until Google audits the ClipOnce API project, its uploads are locked to private. Hold them.
                log(f"{brand['name']} youtube: waiting for Google's API audit before public uploads")
                continue
        out.append((plat, fn, cr))
    return out


def brand_items(queue, brand):
    bid = "default" if brand.get("legacy") else brand["id"]
    return [q for q in queue if (q.get("brand") or "default") == bid]


def scheduled(queue, counts, ledger, alerts, brands):
    day = counts.setdefault(today(), {})
    last = counts.setdefault("_last", {})
    per_run = CONFIG.get("posts_per_run", 1)
    hour_ny = dt.datetime.now(ZoneInfo("America/New_York")).hour
    for brand in brands:
        name, bk = brand["name"], brand_key(brand)
        if brand.get("paused"):
            log(f"{name}: paused by the user")
            continue
        if not in_window(brand, hour_ny):
            log(f"{name}: outside its posting hours ({brand.get('post_start')}:00 to {brand.get('post_end')}:00 New York)")
            continue
        caps = brand.get("caps") or {}
        items = brand_items(queue, brand)
        for plat, fn, cr in brand_platforms(brand):
            ck = plat + bk
            cap = int(caps.get(plat, CONFIG.get("daily_caps", {}).get(plat, 0)) or 0)
            if cap <= 0:
                continue
            if blocked(alerts, plat):
                log(f"{name} {plat}: Meta has API posting blocked, waiting (next try {BLOCK_HOURS} h after the last block)")
                continue
            start = CONFIG.get("platform_start", {}).get(plat)
            if start and time.strftime("%Y-%m-%dT%H:%M", time.gmtime()) < start:
                log(f"{plat}: paused until {start} UTC")
                continue
            g = CONFIG.get("min_minutes_between_posts", 40)
            gap = int((g.get(plat, 40) if isinstance(g, dict) else g) * 60)
            if time.time() - last.get(ck, 0) < gap:
                log(f"{name} {plat}: last post was under {gap // 60} minutes ago, spacing posts out")
                continue
            done = 0
            # Freshest first: a moment is worth most before anyone else posts it.
            for item in sorted(items, key=lambda q: (q.get("made_at") or 0, q.get("created") or ""), reverse=True):
                if done >= per_run or day.get(ck, 0) >= cap:
                    break
                st = item.setdefault("posted", {})
                held = item.get("held") or all(st.get(k) == "skip" for k in ("youtube", "instagram", "threads"))
                if held or plat in st or not item.get("url") or st.get(plat + "_fails", 0) >= 3:
                    continue
                try:
                    link = post_one(item, plat, fn, ledger, "cloud schedule", alerts, brand, cr)
                    if link:
                        day[ck] = day.get(ck, 0) + 1
                        last[ck] = int(time.time())
                        done += 1
                        log(f"{name} {plat}: posted {item['id']} -> {link}")
                except SignInProblem as e:
                    log(f"{name} {plat}: {e}")
                    if "API access blocked" in str(e) or "5258005" in str(e):
                        note_block(alerts, e)
                    break
                except CapReached as e:
                    log(f"{name} {plat}: platform daily limit reached, waiting until tomorrow ({e})")
                    day[ck] = cap
                    break
                except Exception as e:
                    st[plat + "_fails"] = st.get(plat + "_fails", 0) + 1
                    st[plat + "_error"] = str(e)[:300]
                    log(f"{name} {plat}: failed {item['id']}: {e}")
                save("queue.json", queue)
                save("post_counts.json", counts)
                persist(f"posted {plat} {today()}")


def manual(queue, counts, ledger, alerts, cid, edit, brands):
    """The account page's Post or Edit button for one clip. Caps are the user's call (they saw the warning);
    duplicates are still impossible."""
    item = next((q for q in queue if q.get("id") == cid), None)
    if not item:
        log("no such clip:", cid)
        return 1
    if edit:
        for k in ("caption", "title", "hook"):
            if isinstance(edit.get(k), str) and edit[k].strip():
                item[k] = edit[k].strip()[:2200 if k == "caption" else 120]
        item["edited_at"] = int(time.time())
        log("edited", cid)
        if not edit.get("post"):
            return 0
    item.pop("held", None)
    bid = item.get("brand") or "default"
    brand = next((b for b in brands if b["id"] == bid), None)
    if not brand:
        log("this clip's brand is gone or not posting:", bid)
        return 1
    day = counts.setdefault(today(), {})
    caps = brand.get("caps") or {}
    for plat, fn, cr in brand_platforms(brand):
        ck = plat + brand_key(brand)
        if int(caps.get(plat, CONFIG.get("daily_caps", {}).get(plat, 0)) or 0) <= 0:
            continue  # platforms this brand doesn't post to from the cloud
        st = item.setdefault("posted", {})
        if st.get(plat) and st[plat] != "skip":
            continue
        if blocked(alerts, plat):
            log(f"{plat}: Meta has API posting blocked, not posting")
            continue
        try:
            st.pop(plat, None)
            link = post_one(item, plat, fn, ledger, "manual post button", alerts, brand, cr)
            if link:
                day[ck] = day.get(ck, 0) + 1
                counts.setdefault("_last", {})[ck] = int(time.time())
                log(f"{plat}: posted {cid} -> {link}")
        except SignInProblem as e:
            log(f"{plat}: {e}")
            if "API access blocked" in str(e) or "5258005" in str(e):
                note_block(alerts, e)
        except Exception as e:
            st[plat + "_error"] = str(e)[:300]
            log(f"{plat}: failed {cid}: {e}")
        save("queue.json", queue)
        save("post_counts.json", counts)
        persist(f"manual post {cid[:40]}")
    return 0


def main(argv):
    queue = load("queue.json", [])
    counts = load("post_counts.json", {})
    ledger = remote_json("posted_ledger.json", load("posted_ledger.json", {}))
    alerts = load("alerts.json", {})
    if not counts.get(today()):
        try:
            refresh_tokens()
        except Exception as e:
            log("token refresh skipped:", e)
    for k in list(counts):
        if not k.startswith("_") and k < today() and len(counts) > 14:
            counts.pop(k)
    rc = 0
    brands = load_brands()
    if "--id" in argv:
        i = argv.index("--id")
        edit = json.loads(argv[argv.index("--edit") + 1]) if "--edit" in argv else None
        rc = manual(queue, counts, ledger, alerts, argv[i + 1], edit, brands)
    else:
        scheduled(queue, counts, ledger, alerts, brands)
    save("alerts.json", alerts)
    save("queue.json", queue[-200:])
    save("post_counts.json", counts)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

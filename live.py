"""Live auto-clip: watch live Twitch / YouTube streamers in the cloud and clip moments
when chat explodes (message spikes, laugh / hype / sad emotes, "clip it").

Runs inside a GitHub Actions job for up to ~5.5 hours, then the next scheduled run picks up.
Streamers come from config.json "streamers": [{"url": "https://twitch.tv/name", "post": true}].
"""
import json, os, re, socket, subprocess, sys, threading, time, collections
from pathlib import Path
from cliponce.common import CONFIG, OUT, load, save, today, safe_name, log, run, ytdlp_base
from cliponce import render

MAX_SECONDS = int(os.environ.get("LIVE_MAX_SECONDS", 5 * 3600 + 1800))
PRE, POST = 25, 10           # seconds kept before / after the spike
COOLDOWN = 120
LAUGH = re.compile(r"\b(lul|kekw|omegalul|lmao+|lmfao|haha+|💀|😂|🤣|icant|xd)\b", re.I)
HYPE = re.compile(r"\b(pog|pogchamp|poggers|letsgo|let'?s go+|w+|insane|holy|no way|omg)\b", re.I)
SAD = re.compile(r"\b(sadge|biblethump|f+|rip|nooo+|😭)\b", re.I)
CLIPIT = re.compile(r"clip (it|that)|clipped|\bclip\b", re.I)


def is_live(url):
    r = run(ytdlp_base() + ["--print", "%(is_live)s|%(channel)s|%(uploader_id)s|%(title)s", "--playlist-items", "1", url], check=False, timeout=90)
    line = (r.stdout or "").strip().splitlines()[-1:] or [""]
    parts = line[0].split("|", 3)
    return (parts[0] == "True", parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else "")


class Chat:
    """Collects (timestamp, text) chat messages from Twitch IRC or YouTube live chat."""
    def __init__(self, url):
        self.msgs = collections.deque(maxlen=20000)
        self.url = url
        self.alive = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", self.url)
            self._twitch(m.group(1).lower()) if m else self._youtube()
        except Exception as e:
            log("chat reader stopped:", e)
            self.alive = False

    def _twitch(self, ch):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=300)
        s.sendall(f"PASS SCHMOOPIIE\r\nNICK justinfan{int(time.time()) % 99999}\r\nJOIN #{ch}\r\n".encode())
        buf = b""
        while True:
            data = s.recv(65536)
            if not data:
                raise RuntimeError("twitch chat closed")
            buf += data
            *lines, buf = buf.split(b"\r\n")
            for ln in lines:
                t = ln.decode("utf-8", "ignore")
                if t.startswith("PING"):
                    s.sendall(b"PONG :tmi.twitch.tv\r\n")
                elif " PRIVMSG " in t:
                    self.msgs.append((time.time(), t.split(" :", 1)[-1]))

    def _youtube(self):
        from chat_downloader import ChatDownloader
        for m in ChatDownloader().get_chat(self.url):
            self.msgs.append((time.time(), m.get("message") or ""))

    def window(self, a, b):
        return [t for ts, t in self.msgs if a <= ts < b]


class Recorder:
    """Keeps a rolling buffer of 5-second stream segments on disk."""
    def __init__(self, url, d: Path):
        self.d = d
        d.mkdir(parents=True, exist_ok=True)
        cmd = " ".join(f"'{c}'" for c in ytdlp_base() + ["-f", "best[height<=1080]/best", "-o", "-", url])
        self.p = subprocess.Popen(f"{cmd} | ffmpeg -loglevel error -i - -c copy -f segment -segment_time 5 "
                                  f"-reset_timestamps 1 -strftime 1 '{d}/%s.ts'", shell=True)

    def segments(self, a, b):
        segs = []
        for f in sorted(self.d.glob("*.ts")):
            t = int(f.stem)
            if a - 5 <= t <= b:
                segs.append(f)
        return segs

    def prune(self, keep=240):
        now = time.time()
        for f in self.d.glob("*.ts"):
            if now - int(f.stem) > keep:
                f.unlink(missing_ok=True)

    def alive(self):
        return self.p.poll() is None

    def stop(self):
        self.p.terminate()


def score(msgs):
    laugh = sum(bool(LAUGH.search(m)) for m in msgs)
    hype = sum(bool(HYPE.search(m)) for m in msgs)
    sad = sum(bool(SAD.search(m)) for m in msgs)
    clip = sum(bool(CLIPIT.search(m)) for m in msgs)
    kinds = {"Funny moment": laugh, "Hype moment": hype, "Sad moment": sad}
    return len(msgs), clip, max(kinds, key=kinds.get), max(kinds.values())


def transcribe(path):
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return []
    m = WhisperModel("base.en", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(path), vad_filter=True)
    return [(s.start, s.end, s.text.strip()) for s in segs]


def cut(rec, t, name, header, credit, work):
    segs = rec.segments(t - PRE, t + POST)
    if not segs:
        return None
    lst = work / (name + ".txt")
    lst.write_text("".join(f"file '{s}'\n" for s in segs))
    raw = work / (name + "_raw.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw])
    length = min(PRE + POST, 60)
    cues = transcribe(raw)
    ass = work / (name + ".ass")
    render.build_ass(cues, 0, length, header, credit, ass)
    out = work / (name + ".mp4")
    render.render(raw, ass, out, length)
    raw.unlink(missing_ok=True)
    return out, cues


def watch(streamer, deadline):
    url = streamer["url"]
    live, channel, handle, title = is_live(url)
    if not live:
        log("not live:", url)
        return 0
    handle = (handle or channel or url.rstrip("/").split("/")[-1]).lstrip("@")
    log(f"LIVE: {channel} ({title})")
    work = OUT / "live" / safe_name(handle)
    rec, chat = Recorder(url, work / "buf"), Chat(url)
    queue, made, last_clip = load("queue.json", []), 0, 0
    base = collections.deque(maxlen=60)   # 10 s message counts over the last 10 minutes
    time.sleep(20)
    while time.time() < deadline and rec.alive():
        time.sleep(10)
        now = time.time()
        msgs = chat.window(now - 10, now)
        n, clip, kind, kn = score(msgs)
        med = sorted(base)[len(base) // 2] if len(base) >= 6 else None
        base.append(n)
        rec.prune()
        spike = med is not None and n >= max(15, 2.5 * max(med, 1))
        fire = (spike and kn >= max(4, n * 0.25)) or clip >= 5 or (spike and n >= 4 * max(med, 1))
        if not fire or now - last_clip < COOLDOWN:
            continue
        last_clip = now
        reason = "Chat called for a clip" if clip >= 5 else kind
        log(f"spike: {n} msgs/10s vs usual {med}, {reason}")
        time.sleep(POST + 2)
        name = f"{today()}_live_{safe_name(handle, 24)}_{int(now)}"
        try:
            res = cut(rec, now, name, reason, f"🔴 @{handle} live", work)
        except Exception as e:
            log("clip failed:", e)
            continue
        if not res:
            continue
        out, cues = res
        quote = max((c[2] for c in cues), key=len, default="")[:90]
        cap = "\n".join(x for x in [f"{reason} on @{handle}'s stream 🔴", f"“{quote}”" if quote else "",
                                   f"🎥 Credit: @{handle}", "#GTA6 #Live #Clips" if "gta" in title.lower() else "#Live #Clips"] if x)
        meta = {"id": name, "kind": reason, "creator": channel, "handle": handle, "source": url, "caption": cap,
                "title": f"{reason} on {channel}'s stream"[:95], "why": f"{n} chat messages in 10 s vs usual {med}",
                "created": today(), "posted": {} if streamer.get("post", True) else {"youtube": "skip", "instagram": "skip", "threads": "skip"}}
        if os.environ.get("GITHUB_ACTIONS"):
            from cliponce.publish import host
            try:
                meta["url"] = host(out, "live-" + today())
            except Exception as e:
                log("could not host clip:", e)
        queue = load("queue.json", [])
        queue.append(meta)
        save("queue.json", queue)
        made += 1
        commit_state(f"live clip {name}")
    rec.stop()
    return made


def commit_state(msg):
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    subprocess.run(f"git add state && git commit -qm '{msg}' && git pull -q --rebase -X theirs && git push -q", shell=True)


def main():
    streamers = CONFIG.get("streamers", [])
    if not streamers:
        log("no streamers configured")
        return
    deadline = time.time() + MAX_SECONDS
    live = [s for s in streamers if is_live(s["url"])[0]]
    if not live:
        log("nobody is live right now")
        return
    threads = [threading.Thread(target=watch, args=(s, deadline)) for s in live[: CONFIG.get("max_live_at_once", 3)]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    sys.exit(main())

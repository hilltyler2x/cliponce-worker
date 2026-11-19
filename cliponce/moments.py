"""Pick clip-worthy moments from a video's subtitles and chapters.

Only genuinely interesting moments are kept: easter eggs, secrets, glitches,
tricks, never-seen-before details and big funny reactions. Filler scores low and
is dropped, so a video with no real moments produces no clips.
"""
import re

KEYWORDS = {
    # what the moment is about
    r"easter egg": 6, r"secret": 5, r"hidden": 5, r"glitch": 6, r"bug": 3, r"trick": 4, r"hack": 4,
    r"never (seen|noticed)|nobody (knows|noticed)|didn'?t know|first time|for the first time": 5,
    r"detail": 3, r"reference": 3, r"rare": 4, r"unlock": 3, r"crazy|insane|unbelievable|no way": 3,
    r"wait,? what|what the|oh my god|omg|bro what|are you kidding": 4,
    r"funny|hilarious|\[laughter\]|haha|lmao|lol": 4, r"tip|how to": 2,
}
FILLER = re.compile(r"\b(subscribe|like (the|this) video|sponsor|patreon|link in (the )?description|merch|today'?s video is brought)\b", re.I)
KIND_WORDS = [
    ("easter egg", "Easter egg"), ("secret", "Hidden secret"), ("hidden", "Hidden detail"), ("glitch", "Glitch"),
    ("trick", "Trick"), ("hack", "Hack"), ("detail", "Crazy detail"), ("rare", "Rare find"),
    ("laugh", "Funny moment"), ("funny", "Funny moment"),
]
EMOJI = {"Easter egg": "🥚", "Hidden secret": "🤫", "Hidden detail": "👀", "Glitch": "🐛", "Trick": "🧠",
         "Hack": "🧠", "Crazy detail": "🤯", "Rare find": "💎", "Funny moment": "😂"}


def parse_vtt(text):
    """Return [(start, end, text)] from a WebVTT file, de-duplicating YouTube's rolling captions."""
    cues, seen = [], set()
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    ts = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d+)\s+-->\s+(\d+):(\d\d):(\d\d)[.,](\d+)")
    for b in blocks:
        m = ts.search(b)
        if not m:
            continue
        g = list(map(int, m.groups()))
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        lines = b[m.end():].strip().split("\n")
        for ln in lines:
            ln = re.sub(r"<[^>]+>", "", ln).strip()
            if ln and ln not in seen:
                seen.add(ln)
                cues.append((s, e, ln))
    return cues


def score_text(t):
    t = t.lower()
    sc, hits = 0.0, []
    for pat, w in KEYWORDS.items():
        n = len(re.findall(pat, t))
        if n:
            sc += w * min(n, 3)
            hits.append(pat)
    if FILLER.search(t):
        sc -= 8
    return sc, hits


def kind_of(t):
    t = t.lower()
    for k, label in KIND_WORDS:
        if k in t:
            return label
    return "Crazy detail"


def pick(cues, chapters, duration, n=3, min_len=15, max_len=55, threshold=9):
    """Return the best non-overlapping moments as dicts with start, end, score, text, kind."""
    cands = []
    # 1) Chapters that name a secret / easter egg are usually perfect clips.
    for ch in chapters or []:
        s, e, title = ch.get("start_time", 0), ch.get("end_time", 0), ch.get("title", "")
        if e - s < 8:
            continue
        body = " ".join(c[2] for c in cues if s <= c[0] < e)
        sc, _ = score_text(title)
        sc2, _ = score_text(body)
        e2 = min(e, s + max_len)
        if e2 - s < min_len:
            e2 = min(duration or e, s + min_len)
        cands.append({"start": s, "end": e2, "score": sc * 1.5 + sc2 * 0.5 + 2, "text": body, "title": title})
    # 2) Sliding windows over the transcript.
    if cues:
        step = 5
        t = cues[0][0]
        last = cues[-1][1]
        while t < last:
            for L in (25, 35, 45):
                if L > max_len:
                    continue
                body = " ".join(c[2] for c in cues if t <= c[0] < t + L)
                if len(body) < 40:
                    continue
                sc, hits = score_text(body)
                # small bonus for density of excitement
                cands.append({"start": t, "end": t + L, "score": sc + len(hits) * 0.5 - L / 40, "text": body, "title": ""})
            t += step
    cands = [c for c in cands if c["score"] >= threshold]
    cands.sort(key=lambda c: -c["score"])
    out = []
    for c in cands:
        if any(not (c["end"] <= o["start"] or c["start"] >= o["end"]) for o in out):
            continue
        # start just before the first interesting line, not on intro filler
        hot = [q for q in cues if c["start"] <= q[0] < c["end"] and score_text(q[2])[0] > 0]
        if hot and not c["title"]:
            c["start"] = max(c["start"], hot[0][0] - 1.5)
        else:
            c["start"] = max(0.0, c["start"] - 1.0)
        if hot and not c["title"]:
            last_end = max(q[1] for q in cues if q[2] == hot[-1][2])
            c["end"] = min(c["end"], last_end + 2.0)
        if c["end"] - c["start"] < min_len:
            c["end"] = c["start"] + min_len
        c["hot"] = [q[2] for q in hot]
        c["kind"] = kind_of(c["title"] + " " + c["text"])
        out.append(c)
        if len(out) >= n:
            break
    return sorted(out, key=lambda c: c["start"])


def best_quote(text, maxlen=90, lines=None):
    sents = lines or re.split(r"(?<=[.!?])\s+", text)
    scored = sorted(sents, key=lambda s: -score_text(s)[0])
    q = (scored[0] if scored else text).strip()
    return q if len(q) <= maxlen else q[: maxlen - 1].rsplit(" ", 1)[0] + "…"


def caption_for(m, creator, video_title):
    kind = m["kind"]
    emoji = EMOJI.get(kind, "🎮")
    hook = m.get("title") or best_quote(m["text"], lines=m.get("hot"))
    hook = re.sub(r"^\d+[.)]\s*", "", hook).strip()
    lines = [f"{kind} in GTA 6 {emoji}", f"“{hook}”" if hook else "", f"🎥 Spotted by @{creator}" if creator else "",
             "#GTA6 #GTAVI #GTA6Secrets #Gaming"]
    return "\n".join(l for l in lines if l)

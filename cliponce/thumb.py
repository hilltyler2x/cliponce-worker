"""Click-worthy covers: pick the sharpest, most colorful frame around the peak moment and put a
bold 2 to 5 word hook on it, with the streamer credit. Used as the Reel cover and video thumbnail."""
import re, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat
from .common import FONTS, log

W, H = 1080, 1920
YELLOW, WHITE = (255, 210, 61), (255, 255, 255)
STOP = {"the", "a", "an", "and", "to", "of", "in", "on", "is", "it", "this", "that", "i", "my", "for", "with"}
PUNCH = re.compile(r"\b(insane|crazy|wtf|no way|secret|hidden|glitch|first|never|broke|caught|exposed|died|dead|how|why|lucia|jason|police|cops|chase|crash|fail|win|rage|scared|funny|lol)\b", re.I)


def _font(size):
    for p in [FONTS / "Anton-Regular.ttf", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def hook_text(title="", quote="", kind=""):
    """2 to 5 punchy words, no emoji."""
    clean = lambda t: re.sub(r"[^\w' ?!.,-]", " ", t or "").strip()
    for cand in (clean(title), clean(quote)):
        words = cand.split()
        if 2 <= len(words) <= 5:
            return " ".join(words).upper().rstrip(".,")
        if len(words) > 5:
            # keep the punchiest window of 4 words
            best = max(range(len(words) - 3), key=lambda i: len(PUNCH.findall(" ".join(words[i:i + 4]))))
            return " ".join(words[best:best + 4]).upper().rstrip(".,") + "..."
    return {"Funny moment": "I CAN'T BREATHE", "Glitch": "THIS SHOULDN'T WORK", "Easter egg": "NOBODY NOTICED THIS",
            "Hidden secret": "YOU MISSED THIS", "Insane moment": "NO WAY THIS HAPPENED"}.get(kind, "WAIT FOR IT")


def _score(img):
    g = img.convert("L")
    sharp = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    bright = ImageStat.Stat(g).mean[0]
    sat = ImageStat.Stat(img.convert("HSV").split()[1]).mean[0]
    return sharp * 1.5 + sat * 0.4 - abs(bright - 125) * 0.3


def _grab(video, t, path, width=None):
    vf = ["-vf", f"scale={width}:-2"] if width else []
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", *vf, str(path)])
    return Image.open(path).convert("RGB") if Path(path).exists() else None


def _center(img):
    """Vertical renders: score only the real picture band in the middle, not the blurred fill."""
    w, h = img.size
    if w / h < 0.7:
        bh = int(w * 9 / 16)
        img = img.crop((0, (h - bh) // 2, w, (h - bh) // 2 + bh))
    return img


def best_frame(video, length, around=None):
    """Pick the frame where something is happening: motion vs neighbours, sharpness, colour,
    sensible brightness, a bias toward the peak moment, and never a static wall."""
    a, b = (max(0.5, around - 5), min(length - 0.3, around + 3)) if around else (length * 0.2, length * 0.85)
    n = 18
    with tempfile.TemporaryDirectory() as d:
        ts = [a + (b - a) * i / (n - 1) for i in range(n)]
        small = [_grab(video, t, Path(d) / f"s{i}.png", 320) for i, t in enumerate(ts)]
        ok = [(t, _center(im)) for t, im in zip(ts, small) if im is not None]
        if not ok:
            return None
        gray = [im.convert("L").resize((96, 54)) for _, im in ok]
        scores = []
        for i, (t, im) in enumerate(ok):
            diffs = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(gray):
                    diffs.append(ImageStat.Stat(ImageChops.difference(gray[i], gray[j])).mean[0])
            motion = sum(diffs) / len(diffs) if diffs else 0
            detail = ImageStat.Stat(gray[i]).stddev[0]       # flat walls and skies score low
            mid = around if around else (a + b) / 2
            bias = -abs(t - mid) / max(1, (b - a)) * 12
            s = _score(im) + min(motion, 40) * 1.2 + detail * 0.5 + bias
            lum = ImageStat.Stat(gray[i]).mean[0]
            if detail < 18 or lum < 45:
                s -= 60
            elif lum < 70:
                s -= (70 - lum)
            scores.append(s)
        t = ok[max(range(len(ok)), key=scores.__getitem__)][0]
        return _grab(video, t, Path(d) / "best.png")


def _fit_text(draw, text, max_w, start=170, min_size=90):
    size = start
    while size >= min_size:
        f = _font(size)
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if draw.textlength(t, font=f) <= max_w:
                cur = t
            else:
                lines.append(cur); cur = w
        lines.append(cur)
        if len(lines) <= 3 and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, [l for l in lines if l]
        size -= 10
    return _font(min_size), [text]


def make_cover(video, out, hook, credit, length, around=None, source_vertical=False):
    frame = best_frame(video, length, around)
    if frame is None:
        return None
    # The rendered clip is already 1080x1920 with a blurred fill; zoom into the real picture so it fills the cover.
    fw, fh = frame.size
    if fw / fh < 0.7:  # vertical render: crop the sharp middle band and zoom it up
        band_h = int(fw * 9 / 16)
        top = (fh - band_h) // 2
        frame = frame.crop((0, top, fw, top + band_h))
        fw, fh = frame.size
    scale = max(W / fw, H / fh) * 1.02
    frame = frame.resize((int(fw * scale), int(fh * scale)), Image.LANCZOS)
    x0 = (frame.width - W) // 2
    y0 = (frame.height - H) // 2
    img = frame.crop((x0, y0, x0 + W, y0 + H))
    lum = ImageStat.Stat(img.convert("L")).mean[0]
    if lum < 95:  # lift night / indoor scenes so the cover pops in the feed
        img = ImageEnhance.Brightness(img).enhance(min(1.9, 105 / max(lum, 20)))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.25)).enhance(1.12)
    # Readability gradients top and bottom
    shade = Image.new("L", (1, H))
    for y in range(H):
        t = 0.62 * max(0, 1 - y / 760) ** 1.4 + 0.55 * max(0, (y - 1380) / 540) ** 1.6
        shade.putpixel((0, y), int(255 * min(t, 0.85)))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade.resize((W, H)))
    d = ImageDraw.Draw(img)
    f, lines = _fit_text(d, hook, W - 120)
    y = 210
    words_hot = set(w for w in hook.split() if PUNCH.search(w)) or {hook.split()[-1]} if hook.split() else set()
    for line in lines:
        x = (W - d.textlength(line, font=f)) / 2
        for word in line.split(" "):
            col = YELLOW if word in words_hot else WHITE
            d.text((x, y), word, font=f, fill=col, stroke_width=max(8, f.size // 14), stroke_fill=(0, 0, 0))
            x += d.textlength(word + " ", font=f)
        y += int(f.size * 1.08)
    if credit:
        cf = _font(52)
        credit = re.sub(r"[^\x00-￿]", "", credit).strip()
        tw = d.textlength(credit, font=cf)
        px, py = (W - tw) / 2, H - 230
        d.rounded_rectangle((px - 34, py - 18, px + tw + 34, py + 70), radius=44, fill=(255, 61, 139))
        d.text((px, py - 4), credit, font=cf, fill=WHITE)
    out = Path(out)
    img.save(out, "JPEG", quality=92, optimize=True)
    return out


def cover_for(video, out, title, quote, kind, credit, length, around=None):
    try:
        return make_cover(video, out, hook_text(title, quote, kind), credit, length, around)
    except Exception as e:
        log("cover failed:", e)
        return None

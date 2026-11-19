"""Download only the needed part of a video and render a 1080x1920 clip with captions and credit."""
import re
from pathlib import Path
from .common import run, ytdlp_base, FONTS, log

W, H = 1080, 1920


def ass_time(t):
    t = max(0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


TOP, GAME_H = 330, 1260          # hook band on top, gameplay fills the middle, credit band below
YELLOW, PINK = "&H003DD2FF&", "&H008B3DFF&"
PUNCH = re.compile(r"^(insane|crazy|wtf|no|never|first|secret|hidden|glitch|broke|caught|died|dead|how|why|what|police|cops|chase|crash|rage|scared|bro|lol|funny|wait|fastest|best|worst)\W*$", re.I)


def _noemoji(t):
    return re.sub(r"[^\u0000-\uFFFF]|[\u2600-\u27BF\uFE0F]", "", t or "").strip()


def build_ass(cues, start, end, header, credit, path):
    """Pro layout text: a big 2-line hook on top, bold 3-word captions with the spoken word lit up,
    and a pink credit pill at the bottom."""
    header, credit = _noemoji(header), _noemoji(credit)
    ev, L = [], end - start
    for cue in cues:
        s, e, text = cue[0], cue[1], _noemoji(cue[2])
        if e <= start or s >= end or not text:
            continue
        words = [(a, b, _noemoji(w)) for a, b, w in (cue[3] if len(cue) > 3 else []) if _noemoji(w)]
        if not words:  # no word times: spread the words evenly
            ws = text.split()
            step = (e - s) / max(1, len(ws))
            words = [(s + i * step, s + (i + 1) * step, w) for i, w in enumerate(ws)]
        words = [(max(a, start) - start, min(b, end) - start, w) for a, b, w in words if b > start and a < end]
        for i in range(0, len(words), 3):
            chunk = words[i:i + 3]
            for j, (a, b, _) in enumerate(chunk):
                nxt = chunk[j + 1][0] if j + 1 < len(chunk) else max(b, a + 0.25)
                txt = " ".join((("{\\c" + YELLOW + "\\fscx108\\fscy108}" + esc(w).upper() + "{\\r}") if k == j else esc(w).upper())
                               for k, (_, _, w) in enumerate(chunk))
                ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(nxt)},Cap,,0,0,0,,{txt}")
    words = header.upper().split()
    if len(header) > 16 and len(words) > 2:  # balanced two lines
        tot, acc, cut = len(header), 0, 1
        for i, w in enumerate(words):
            acc += len(w) + 1
            if acc >= tot / 2:
                cut = i + 1
                break
        lines = [words[:cut], words[cut:]]
    else:
        lines = [words]
    hook = "\\N".join(" ".join(("{\\c" + YELLOW + "}" + esc(w) + "{\\c&H00FFFFFF&}") if PUNCH.match(w) else esc(w) for w in ln)
                       for ln in lines if ln)
    longest = max(len(" ".join(ln)) for ln in lines if ln) if words else 1
    fs = max(70, min(124, int(2250 / max(1, longest))))  # as big as fits the width
    ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(L)},Hook,,0,0,0,,{{\\fs{fs}}}{hook}")
    if credit:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(L)},Credit,,0,0,0,,  {esc(credit)}  ")
    path.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,94,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,8,3,2,70,70,{H - TOP - GAME_H + 150},1
Style: Hook,Anton,98,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,6,0,5,60,60,0,1
Style: Credit,DejaVu Sans,42,&H00FFFFFF,&H00FFFFFF,{PINK},{PINK},1,0,0,0,100,100,0,0,3,14,0,2,60,60,{(H - TOP - GAME_H) // 2 - 30},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev).replace("Hook,,0,0,0,,", "Hook,,0,0,0,,{\\pos(" + str(W // 2) + "," + str(TOP // 2 + 10) + ")}") + "\n", encoding="utf-8")


def download_section(url, start, end, dest: Path, timeout=900):
    """Fetch just [start, end] of the video at up to 1080p."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmpl = str(dest.with_suffix("")) + ".%(ext)s"
    run(ytdlp_base() + ["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                        "--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", tmpl, url], timeout=timeout)
    got = sorted(dest.parent.glob(dest.stem + ".*"))
    if not got:
        raise RuntimeError("download produced no file")
    return got[0]


def render(src: Path, ass: Path, out: Path, length: float):
    """Gameplay zoomed to fill the middle (no blurred bands), dark hook band on top, credit band below,
    a thin progress bar, captions burned in."""
    fontsdir = str(FONTS) if FONTS.exists() else "/usr/share/fonts"
    sub = "subtitles='" + str(ass).replace("'", r"\'").replace(":", r"\:") + "':fontsdir='" + fontsdir + "'"
    L = f"{length:.2f}"
    fc = (f"color=c=0x0B0B10:s={W}x{H}:r=30:d={L}[bg];"
          f"[0:v]scale={W}:{GAME_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{GAME_H},"
          f"eq=contrast=1.06:saturation=1.15,setsar=1,fps=30[g];"
          f"color=c=0xFFD23D:s={W}x8:r=30:d={L}[pb];"
          f"[bg][g]overlay=0:{TOP}:shortest=1[c];"
          f"[c][pb]overlay=x='-w+w*t/{L}':y={TOP + GAME_H}:shortest=1,{sub}[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", L, "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", L, out], timeout=1200)
    return out

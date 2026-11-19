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


def build_ass(cues, start, end, header, credit, path):
    """Word-chunk captions (3 to 4 words), a top hook and a small credit line."""
    ev = []
    for s, e, text in cues:
        if e <= start or s >= end:
            continue
        words = text.split()
        if not words:
            continue
        s0, e0 = max(s, start) - start, min(e, end) - start
        chunks = [words[i:i + 3] for i in range(0, len(words), 3)]
        dur = max(0.3, (e0 - s0) / len(chunks))
        for i, ch in enumerate(chunks):
            a = s0 + i * dur
            ev.append(f"Dialogue: 0,{ass_time(a)},{ass_time(a + dur)},Cap,,0,0,0,,{esc(' '.join(ch)).upper()}")
    L = end - start
    ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(L)},Hook,,0,0,0,,{esc(header).upper()}")
    if credit:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(L)},Credit,,0,0,0,,{esc(credit)}")
    path.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,96,&H00FFFFFF,&H0000D2FF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,7,2,2,80,80,520,1
Style: Hook,Anton,78,&H003DD2FF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,6,2,8,70,70,190,1
Style: Credit,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,3,1,2,60,60,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n", encoding="utf-8")


def download_section(url, start, end, dest: Path):
    """Fetch just [start, end] of the video at up to 1080p."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmpl = str(dest.with_suffix("")) + ".%(ext)s"
    run(ytdlp_base() + ["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                        "--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts",
                        "--merge-output-format", "mp4", "-o", tmpl, url], timeout=900)
    got = sorted(dest.parent.glob(dest.stem + ".*"))
    if not got:
        raise RuntimeError("download produced no file")
    return got[0]


def render(src: Path, ass: Path, out: Path, length: float):
    fontsdir = str(FONTS) if FONTS.exists() else "/usr/share/fonts"
    sub = "subtitles='" + str(ass).replace("'", r"\'").replace(":", r"\:") + "':fontsdir='" + fontsdir + "'"
    fc = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,eq=brightness=-0.12[bg];"
          f"[b]scale={W}:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-40,setsar=1,fps=30,{sub}[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", f"{length:.2f}", "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out], timeout=1200)
    return out

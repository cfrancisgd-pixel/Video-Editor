"""Enhance picture quality and optionally add branding (logo watermark, name lower third, end card).

The input file is never modified. Typical use, after audiofix.py --wav-only:

  python videofix.py IN.mp4 --audio IN-clean.wav \
      --logo logo_white.png --name "Jane Doe" --title "Head of Sales · Acme" --color "#1F2A5C" \
      --endcard endcard.png

Options (all optional):
  --audio FILE        audio to use (WAV or any media with audio); default keeps the original audio
  --size WxH|auto|keep  output size; auto = upscale so the short side is at least 1080 (default)
  --denoise off|light|medium   compression/grain cleanup (default light)
  --sharpen 0.6       contrast-adaptive sharpening 0-1 (0 = off)
  --contrast 1.07 --saturation 1.1 --gamma 0.98   gentle grade (1.0 = unchanged)
  --logo PNG          transparent logo, burned in as a watermark for the whole main video
  --logo-pos bottom|top|top-left|top-right|bottom-left|bottom-right   (default bottom)
  --logo-width 0.18   logo width as a fraction of the frame width
  --logo-opacity 0.9  --logo-color white|original
  --name / --title    lower third text (shown from --lt-start for --lt-dur seconds)
  --color #RRGGBB     lower third panel colour (brand colour)
  --font PATH         TTF/OTF for the lower third (default: a system sans)
  --lt-y 0.52         vertical position of the lower third (fraction of height)
  --endcard FILE      image or video appended at the end with a crossfade
  --endcard-start S   start time inside an end card video (default 0)
  --endcard-dur 2.5   end card length    --endcard-bg #FFFFFF  padding colour if aspect differs
  --fade 0.6          crossfade length into the end card
  --crf 18            x264 quality (lower = better/larger)
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

from common import default_out, ffmpeg, probe, refuse_overwrite, run

FONT_CANDIDATES = {
    "bold": ["C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"],
    "regular": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans.ttf"],
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def font(kind, size, override=None):
    from PIL import ImageFont
    for p in ([override] if override else []) + FONT_CANDIDATES[kind]:
        if p and os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_lower_third(path, name, title, color, W, font_path=None):
    from PIL import Image, ImageDraw, ImageFilter
    s = W / 1080  # design at 1080 wide, scale to the output
    fb, fr = font("bold", int(62 * s), font_path), font("regular", int(38 * s), font_path)
    d0 = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw = max(d0.textlength(name, font=fb), d0.textlength(title or "", font=fr) if title else 0)
    w, h = int(tw + 92 * s), int((180 if title else 112) * s)
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    r, g, b = hex_rgb(color)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(24 * s), fill=(r, g, b, 235))
    d.rounded_rectangle([0, 0, int(12 * s), h - 1], radius=int(6 * s), fill=(255, 255, 255, 255))
    d.text((int(44 * s), int(18 * s)), name, font=fb, fill=(255, 255, 255, 255))
    if title:
        d.text((int(46 * s), int(104 * s)), title, font=fr, fill=(225, 230, 245, 255))
    pad = int(20 * s)
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle([pad, pad + 4, w + pad - 1, h + pad + 3], radius=int(24 * s),
                                          fill=(0, 0, 0, 110))
    out = out.filter(ImageFilter.GaussianBlur(10 * s))
    out.alpha_composite(panel, (pad, pad))
    out.save(path)


def make_watermark(path, logo, W, width_frac, color):
    from PIL import Image, ImageFilter
    lg = Image.open(logo).convert("RGBA")
    lw = max(16, int(W * width_frac))
    lg = lg.resize((lw, max(1, int(lg.height * lw / lg.width))), Image.LANCZOS)
    if color == "white":
        alpha = lg.split()[3]
        lg = Image.new("RGBA", lg.size, (255, 255, 255, 255))
        lg.putalpha(alpha)
    pad = 16
    wm = Image.new("RGBA", (lg.width + 2 * pad, lg.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    shadow.putalpha(lg.split()[3].point(lambda v: int(v * 0.55)))
    wm.alpha_composite(shadow, (pad + 2, pad + 3))
    wm = wm.filter(ImageFilter.GaussianBlur(4))
    wm.alpha_composite(lg, (pad, pad))
    wm.save(path)


def target_size(w, h, size):
    if size == "keep":
        return w, h
    if size != "auto":
        tw, th = map(int, size.lower().split("x"))
        return tw, th
    short = min(w, h)
    k = max(1.0, 1080 / short)
    even = lambda v: int(round(v * k / 2) * 2)
    return even(w), even(h)


def logo_xy(pos, margin_frac=0.06):
    m = f"H*{margin_frac}"
    mx = f"W*{margin_frac}"
    return {
        "bottom": ("(W-w)/2", f"H-h-{m}"), "top": ("(W-w)/2", m),
        "top-left": (mx, m), "top-right": (f"W-w-{mx}", m),
        "bottom-left": (mx, f"H-h-{m}"), "bottom-right": (f"W-w-{mx}", f"H-h-{m}"),
    }[pos]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("-o", "--output")
    ap.add_argument("--audio"); ap.add_argument("--size", default="auto")
    ap.add_argument("--denoise", default="light", choices=["off", "light", "medium"])
    ap.add_argument("--sharpen", type=float, default=0.6)
    ap.add_argument("--contrast", type=float, default=1.07)
    ap.add_argument("--saturation", type=float, default=1.1)
    ap.add_argument("--gamma", type=float, default=0.98)
    ap.add_argument("--logo"); ap.add_argument("--logo-pos", default="bottom")
    ap.add_argument("--logo-width", type=float, default=0.18)
    ap.add_argument("--logo-opacity", type=float, default=0.9)
    ap.add_argument("--logo-color", default="original", choices=["original", "white"])
    ap.add_argument("--name"); ap.add_argument("--title")
    ap.add_argument("--color", default="#1F2A5C"); ap.add_argument("--font")
    ap.add_argument("--lt-start", type=float, default=0.6)
    ap.add_argument("--lt-dur", type=float, default=5.0)
    ap.add_argument("--lt-y", type=float, default=0.52)
    ap.add_argument("--endcard"); ap.add_argument("--endcard-start", type=float, default=0)
    ap.add_argument("--endcard-dur", type=float, default=2.5)
    ap.add_argument("--endcard-bg", default="#FFFFFF")
    ap.add_argument("--fade", type=float, default=0.6)
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()

    branded = a.logo or a.name or a.endcard
    out = a.output or default_out(a.input, "enhanced + branded" if branded else "enhanced")
    refuse_overwrite(a.input, out)

    info = probe(a.input)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    W0, H0 = int(v["width"]), int(v["height"])
    num, den = map(int, v.get("r_frame_rate", "30/1").split("/"))
    fps = round(num / den, 3) if den else 30
    D = float(info["format"]["duration"])
    W, H = target_size(W0, H0, a.size)

    tmp = tempfile.mkdtemp()
    inputs = [["-i", a.input]]
    dn = {"off": "", "light": "hqdn3d=2:1.5:3:3,", "medium": "hqdn3d=4:3:6:4.5,"}[a.denoise]
    sharp = f",cas={a.sharpen}" if a.sharpen > 0 else ""
    fc = [f"[0:v]{dn}scale={W}:{H}:flags=lanczos{sharp},"
          f"eq=contrast={a.contrast}:saturation={a.saturation}:gamma={a.gamma},"
          f"fps={fps},format=yuv420p,setsar=1[v0]"]
    cur = "v0"

    if a.name:
        lt = Path(tmp) / "lowerthird.png"
        make_lower_third(lt, a.name, a.title, a.color, W, a.font)
        idx = len(inputs); inputs.append(["-loop", "1", "-t", f"{a.lt_start + a.lt_dur + 0.5}", "-i", lt])
        s, e = a.lt_start, a.lt_start + a.lt_dur - 0.4
        fc.append(f"[{idx}:v]format=rgba,fade=t=in:st={s}:d=0.4:alpha=1,"
                  f"fade=t=out:st={e}:d=0.4:alpha=1[lt]")
        x0 = int(W * 0.02)
        fc.append(f"[{cur}][lt]overlay=x='{x0}-{int(W*0.055)}*max(0,1-(t-{s})/0.4)*gte(t,{s})':"
                  f"y=H*{a.lt_y}:eval=frame:eof_action=pass[v1]")
        cur = "v1"

    if a.logo:
        wm = Path(tmp) / "watermark.png"
        make_watermark(wm, a.logo, W, a.logo_width, a.logo_color)
        idx = len(inputs); inputs.append(["-loop", "1", "-t", f"{D}", "-i", wm])
        x, y = logo_xy(a.logo_pos)
        fc.append(f"[{idx}:v]format=rgba,colorchannelmixer=aa={a.logo_opacity}[wm]")
        fc.append(f"[{cur}][wm]overlay=x={x}:y={y}:eof_action=pass[v2]")
        cur = "v2"

    total = D
    if a.endcard:
        ec = Path(a.endcard)
        idx = len(inputs)
        if ec.suffix.lower() in IMAGE_EXT:
            inputs.append(["-loop", "1", "-t", f"{a.endcard_dur}", "-i", ec])
        else:
            inputs.append(["-ss", f"{a.endcard_start}", "-t", f"{a.endcard_dur}", "-i", ec])
        fc.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={a.endcard_bg},fps={fps},"
                  f"format=yuv420p,setsar=1,setpts=PTS-STARTPTS[ec]")
        off = max(0.0, D - a.fade)
        fc.append(f"[{cur}][ec]xfade=transition=fade:duration={a.fade}:offset={off}[v3]")
        cur = "v3"
        total = off + a.endcard_dur

    amap = []
    if a.audio or has_audio:
        if a.audio:
            aidx = len(inputs); inputs.append(["-i", a.audio])
        else:
            aidx = 0
        af = f"[{aidx}:a]atrim=0:{D},asetpts=PTS-STARTPTS"
        if a.endcard:
            af += f",afade=t=out:st={max(0.0, D - a.fade - 0.2)}:d={a.fade + 0.2},apad=whole_dur={total}"
        fc.append(af + "[aout]")
        amap = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]

    cmd = [ffmpeg(), "-v", "error", "-y"]
    for i in inputs:
        cmd += i
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{cur}]", *amap,
            "-c:v", "libx264", "-preset", "slow", "-crf", str(a.crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{total}", out]
    run(cmd)
    print(f"wrote: {out}  ({W}x{H}, {total:.1f}s)")


if __name__ == "__main__":
    main()

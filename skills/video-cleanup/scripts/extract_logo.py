"""Cut a logo out of a flat-background image or video frame into transparent PNGs.

Useful when the user has no logo file but an existing video ends on a logo card.

Usage:
  python extract_logo.py IMAGE [--crop x,y,w,h] [--out-dir DIR]
  python extract_logo.py --from-video VIDEO [--time -0.1] [--crop x,y,w,h]
    (--time negative = seconds before the end, e.g. -0.1 for the last frame)

Writes logo_original.png (logo in its own colours) and logo_white.png (for dark footage),
trimmed to the logo, plus the source frame as frame.png so you can pick a crop.
Works best for a solid-colour logo on a plain light or dark background.
"""
import argparse
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from common import ffmpeg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--from-video"); ap.add_argument("--time", type=float, default=-0.1)
    ap.add_argument("--crop"); ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    od = Path(a.out_dir); od.mkdir(parents=True, exist_ok=True)

    if a.from_video:
        frame = od / "frame.png"
        seek = ["-sseof", str(a.time)] if a.time < 0 else ["-ss", str(a.time)]
        subprocess.run([ffmpeg(), "-v", "error", "-y", *seek, "-i", a.from_video, "-frames:v", "1", frame],
                       check=True)
        src = frame
    else:
        src = a.image
    im = np.asarray(Image.open(src).convert("RGB")).astype(float)
    if a.crop:
        x, y, w, h = map(int, a.crop.split(","))
        im = im[y:y + h, x:x + w]

    # Background colour = median of the border pixels; alpha = distance from it.
    border = np.concatenate([im[0], im[-1], im[:, 0], im[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((im - bg) ** 2).sum(-1))
    ink = np.percentile(dist[dist > 20], 90) if (dist > 20).any() else 255
    alpha = np.clip((dist - 12) / max(ink - 12, 1), 0, 1)
    ys, xs = np.where(alpha > 0.1)
    if len(ys) == 0:
        raise SystemExit("No logo found: the area looks empty. Try --crop around the logo.")
    m = 8
    y0, y1 = max(ys.min() - m, 0), min(ys.max() + m + 1, im.shape[0])
    x0, x1 = max(xs.min() - m, 0), min(xs.max() + m + 1, im.shape[1])
    im, alpha = im[y0:y1, x0:x1], alpha[y0:y1, x0:x1]

    solid = alpha > 0.9
    colour = np.median(im[solid], axis=0) if solid.any() else np.array([0, 0, 0])
    a8 = (alpha * 255).astype(np.uint8)
    for name, rgb in [("logo_original.png", colour), ("logo_white.png", (255, 255, 255))]:
        out = np.zeros((*a8.shape, 4), np.uint8)
        out[..., :3] = rgb
        out[..., 3] = a8
        Image.fromarray(out, "RGBA").save(od / name)
    print(f"logo colour #{int(colour[0]):02X}{int(colour[1]):02X}{int(colour[2]):02X}, "
          f"size {x1 - x0}x{y1 - y0}; wrote {od / 'logo_original.png'} and {od / 'logo_white.png'}")


if __name__ == "__main__":
    main()

"""Shared helpers: locate tools, run ffmpeg, decode audio to numpy, measure things."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SKILL_DIR = Path(__file__).resolve().parent.parent
DF_VERSION = "0.5.6"
DF_ASSETS = {
    ("Windows", "AMD64"): f"deep-filter-{DF_VERSION}-x86_64-pc-windows-msvc.exe",
    ("Linux", "x86_64"): f"deep-filter-{DF_VERSION}-x86_64-unknown-linux-musl",
    ("Linux", "aarch64"): f"deep-filter-{DF_VERSION}-aarch64-unknown-linux-gnu",
    ("Darwin", "arm64"): f"deep-filter-{DF_VERSION}-aarch64-apple-darwin",
    ("Darwin", "x86_64"): f"deep-filter-{DF_VERSION}-x86_64-apple-darwin",
}


def _which(name, extra=()):
    found = shutil.which(name)
    if found:
        return found
    for p in extra:
        p = Path(os.path.expandvars(os.path.expanduser(str(p))))
        if p.is_file():
            return str(p)
    return None


def ffmpeg(tool="ffmpeg"):
    exe = tool + (".exe" if os.name == "nt" else "")
    path = _which(tool, [
        f"$LOCALAPPDATA/Microsoft/WinGet/Links/{exe}",
        f"/opt/homebrew/bin/{tool}", f"/usr/local/bin/{tool}",
    ])
    if not path:
        sys.exit(f"ERROR: {tool} not found. Install it first:\n"
                 "  Windows: winget install Gyan.FFmpeg\n"
                 "  macOS:   brew install ffmpeg\n"
                 "  Linux:   sudo apt install ffmpeg")
    return path


def deep_filter():
    """Find the DeepFilterNet CLI. Exit code 3 with download instructions if missing."""
    names = ["deep-filter.exe", "deep-filter"]
    candidates = [os.environ.get("DEEP_FILTER", "")]
    for n in names:
        candidates += [Path.home() / ".video-cleanup" / "bin" / n, SKILL_DIR / "bin" / n,
                       Path.home() / "Tools" / "video-tools" / "bin" / n, Path.home() / ".local" / "bin" / n]
    for n in names:
        if shutil.which(n):
            return shutil.which(n)
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    asset = DF_ASSETS.get((platform.system(), platform.machine()), "(see release page)")
    print("DEEP_FILTER_MISSING", file=sys.stderr)
    print(f"DeepFilterNet CLI not found. With the user's OK (~30 MB download), run:\n"
          f"  python {Path(__file__).parent / 'setup_tools.py'}\n"
          f"or download https://github.com/Rikorose/DeepFilterNet/releases/download/v{DF_VERSION}/{asset}\n"
          f"and set DEEP_FILTER=/path/to/binary (chmod +x on macOS/Linux).", file=sys.stderr)
    sys.exit(3)


def run(cmd, capture=False):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"Command failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{r.stderr[-2000:]}")
    return r.stderr + r.stdout if capture else None


def probe(path):
    out = subprocess.run([ffmpeg("ffprobe"), "-v", "error", "-show_entries",
                          "format=duration:stream=index,codec_type,codec_name,width,height,"
                          "r_frame_rate,channels,sample_rate,bit_rate",
                          "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def decode_audio(path, sr=16000, channels=1):
    """Decode the first audio stream to float32 numpy (frames x channels if channels>1)."""
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:a:0",
                        "-f", "f32le", "-ac", str(channels), "-ar", str(sr), "-"],
                       capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Could not decode audio from {path}: {r.stderr.decode(errors='replace')[-500:]}")
    x = np.frombuffer(r.stdout, dtype=np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def loudness(path):
    """Integrated loudness (LUFS) and true peak (dBFS) via ebur128."""
    out = run([ffmpeg(), "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
               "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], capture=True)
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    return (float(i[-1]) if i else None), (float(p[-1]) if p else None)


def frame_db(x, sr=16000, frame_s=0.05):
    n = int(sr * frame_s)
    x = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(1)) + 1e-9)


def speech_noise_gap(x, sr=16000):
    """p90 frame level minus p10 frame level: a rough voice-above-background figure in dB."""
    r = frame_db(x, sr)
    r = r[r > -120]  # ignore digital silence
    if len(r) == 0:
        return None, None, None
    lo, hi = np.percentile(r, 10), np.percentile(r, 90)
    return float(lo), float(hi), float(hi - lo)


def sync_lag_ms(a, b, sr=16000, max_ms=120):
    """Lag of b relative to a in ms (positive = b is late), from 1 ms loudness envelopes."""
    hop = sr // 1000
    n = min(len(a), len(b))
    env = lambda y: np.sqrt((y[: n // hop * hop].reshape(-1, hop) ** 2).mean(1))
    ea, eb = env(a[:n]), env(b[:n])
    m = max_ms
    best, best_k = -2, 0
    for k in range(-m, m + 1):
        c = np.corrcoef(ea[m:-m], eb[m + k: len(eb) - m + k])[0, 1]
        if c > best:
            best, best_k = c, k
    return best_k, float(best)


def default_out(inp, suffix):
    p = Path(inp)
    return str(p.with_name(f"{p.stem} - {suffix}{p.suffix or '.mp4'}"))


def refuse_overwrite(inp, out):
    if Path(inp).resolve() == Path(out).resolve():
        sys.exit("Refusing to overwrite the input file. Choose a different output name.")

"""One-time setup: checks/installs everything the video-cleanup scripts need.

  python setup_tools.py              # check, install Python libs, download DeepFilterNet
  python setup_tools.py --check      # only report what is missing
  python setup_tools.py --install-ffmpeg   # also install ffmpeg via winget / brew / apt

- Python libraries: numpy, Pillow (pip --user)
- DeepFilterNet 0.5.6 CLI (MIT/Apache-2.0) from the official GitHub release, verified by SHA-256,
  saved to ~/.video-cleanup/bin/
- ffmpeg is not downloaded by this script (GPL builds); it is installed through the system
  package manager only when --install-ffmpeg is given.
"""
import argparse
import hashlib
import importlib.util
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

DF_VERSION = "0.5.6"
BASE = f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{DF_VERSION}/"
# SHA-256 of the official release assets (computed from the v0.5.6 release files).
DF_ASSETS = {
    ("Windows", "AMD64"): ("deep-filter-0.5.6-x86_64-pc-windows-msvc.exe", "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915"),
    ("Linux", "x86_64"): ("deep-filter-0.5.6-x86_64-unknown-linux-musl", "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da"),
    ("Linux", "aarch64"): ("deep-filter-0.5.6-aarch64-unknown-linux-gnu", "14e02a1c0028f3ca0bdf83b62b3336e56ba0556894ef295a95e8573f06557166"),
    ("Darwin", "arm64"): ("deep-filter-0.5.6-aarch64-apple-darwin", "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"),
    ("Darwin", "x86_64"): ("deep-filter-0.5.6-x86_64-apple-darwin", "d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f"),
}
DEST = Path.home() / ".video-cleanup" / "bin" / ("deep-filter.exe" if os.name == "nt" else "deep-filter")
FFMPEG_CMDS = {
    "Windows": ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                "--accept-source-agreements", "--accept-package-agreements"],
    "Darwin": ["brew", "install", "ffmpeg"],
    "Linux": ["sudo", "apt-get", "install", "-y", "ffmpeg"],
}


def have_ffmpeg():
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from common import _which
    except Exception:
        return shutil.which("ffmpeg")
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return _which("ffmpeg", [f"$LOCALAPPDATA/Microsoft/WinGet/Links/{exe}",
                             "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"])


def missing_libs():
    return [m for m, mod in (("numpy", "numpy"), ("Pillow", "PIL"))
            if importlib.util.find_spec(mod) is None]


def download_df():
    key = (platform.system(), platform.machine())
    if key not in DF_ASSETS:
        sys.exit(f"No prebuilt DeepFilterNet for {key}. See https://github.com/Rikorose/DeepFilterNet/releases")
    name, sha = DF_ASSETS[key]
    DEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".part")
    print(f"downloading {name} ...")
    urllib.request.urlretrieve(BASE + name, tmp)
    got = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if got != sha:
        tmp.unlink(missing_ok=True)
        sys.exit(f"Checksum mismatch for {name}: expected {sha}, got {got}. Not installed.")
    tmp.replace(DEST)
    if os.name != "nt":
        DEST.chmod(DEST.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed DeepFilterNet -> {DEST}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--install-ffmpeg", action="store_true")
    a = ap.parse_args()

    ff, libs, df = have_ffmpeg(), missing_libs(), DEST.is_file()
    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")
    print(f"ffmpeg:        {ff or 'MISSING'}")
    print(f"python libs:   {'ok' if not libs else 'MISSING ' + ', '.join(libs)}")
    print(f"DeepFilterNet: {DEST if df else 'MISSING'}")
    if a.check:
        sys.exit(0 if ff and not libs and df else 1)

    if libs:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", *libs], check=True)
    if not df:
        download_df()
    if not ff:
        cmd = FFMPEG_CMDS.get(platform.system())
        if a.install_ffmpeg and cmd:
            subprocess.run(cmd, check=True)
            print("ffmpeg installed; open a new terminal if it is not found yet.")
        else:
            print("ffmpeg still missing. Install it with:\n  " + " ".join(cmd or ["(see ffmpeg.org)"])
                  + "\n  or rerun: python setup_tools.py --install-ffmpeg")
    print("setup done.")


if __name__ == "__main__":
    main()

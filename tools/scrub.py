#!/usr/bin/env python3
"""Dialogue scrub (Cut 1): find the spoken parts of an interview and write a cut list.

Runs FFmpeg silencedetect, keeps speech with a little padding, closes short gaps
(so natural pauses survive) and caps long thinking pauses. The result is a loose
first pass for cutlist.py, not a finished edit: false starts and fillers still
need a human (or transcript) pass.

Usage: python3 scrub.py interview.mp4 -o cut1.json [--noise -32] [--min-silence 0.6]
       [--pad 0.15] [--keep-gap 0.35] [--min-speech 0.4]
"""
import argparse, json, os, re, subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("-o", "--out", default="cut1.json")
    ap.add_argument("--noise", type=float, default=-32, help="silence threshold in dB")
    ap.add_argument("--min-silence", type=float, default=0.6, help="pauses shorter than this are kept")
    ap.add_argument("--pad", type=float, default=0.15, help="handle kept before and after speech")
    ap.add_argument("--keep-gap", type=float, default=0.35, help="merge segments closer than this")
    ap.add_argument("--min-speech", type=float, default=0.4, help="drop blips shorter than this")
    a = ap.parse_args()

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", a.src],
                               capture_output=True, text=True, check=True).stdout)
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", a.src, "-vn", "-af",
                          f"silencedetect=noise={a.noise}dB:d={a.min_silence}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    silences = list(zip(starts, ends + [dur] * (len(starts) - len(ends))))

    speech, cur = [], 0.0
    for s, e in silences:
        if s > cur:
            speech.append([cur, s])
        cur = e
    if cur < dur:
        speech.append([cur, dur])
    speech = [[max(0, s - a.pad), min(dur, e + a.pad)] for s, e in speech if e - s >= a.min_speech]
    merged = []
    for s, e in speech:
        if merged and s - merged[-1][1] <= a.keep_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    rel = os.path.relpath(os.path.abspath(a.src), os.path.dirname(os.path.abspath(a.out)))
    clips = [{"src": rel, "in": round(s, 3), "out": round(e, 3)} for s, e in merged]
    kept = sum(e - s for s, e in merged)
    json.dump({"width": 1920, "height": 1080, "fps": 30, "loudness": -14, "clips": clips},
              open(a.out, "w"), indent=2)
    print(f"{len(clips)} speech segments, {kept:.1f}s kept of {dur:.1f}s ({100 * kept / dur:.0f}%) -> {a.out}")


if __name__ == "__main__":
    main()

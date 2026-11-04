"""Clean speech audio in a video: AI noise removal (DeepFilterNet) + gentle EQ/compression + loudness.

The video stream is copied untouched; the input file is never modified.

Usage:
  python audiofix.py IN.mp4 [-o OUT.mp4] [--channel auto|mix|left|right]
                     [--strength 35] [--target -14] [--presence 2.5] [--wav-only]

--strength  max dB of noise reduction (DeepFilterNet attenuation limit). 35 sounds natural;
            25 is gentler for very noisy sources that start sounding watery; 100 = no limit.
--presence  dB boost around 3.2 kHz; raise to 4 for muffled recordings.
--wav-only  write the cleaned audio as a WAV (for use with videofix.py --audio) instead of a video.
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from analyze import channel_advice
from common import (decode_audio, deep_filter, default_out, ffmpeg, loudness, refuse_overwrite,
                    run, speech_noise_gap, sync_lag_ms)

PANS = {"mix": "pan=mono|c0=0.5*c0+0.5*c1", "left": "pan=mono|c0=c0", "right": "pan=mono|c0=c1"}


def post_chain(presence):
    # Mud cut, presence lift, air shelf, gentle compression. Kept mild on purpose: heavier
    # processing (noise gates, aggressive FFT denoise) made voices sound harsh and "corrupted".
    return ("equalizer=f=220:t=q:w=1.2:g=-2,"
            f"equalizer=f=3200:t=q:w=1.0:g={presence},"
            "equalizer=f=7000:t=h:w=0.7:g=1.5,"
            "acompressor=threshold=-24dB:ratio=3:attack=8:release=150:makeup=2")


def two_pass_loudnorm(src, chain, target):
    out = run([ffmpeg(), "-hide_banner", "-nostats", "-i", src, "-af",
               f"{chain},loudnorm=I={target}:TP=-1.5:LRA=9:print_format=json", "-f", "null", "-"],
              capture=True)
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S).group(0))
    return (f"loudnorm=I={target}:TP=-1.5:LRA=9:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
            f"offset={m['target_offset']}:linear=true")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--channel", default="auto", choices=["auto", "mix", "left", "right"])
    ap.add_argument("--strength", type=float, default=35)
    ap.add_argument("--target", type=float, default=-14)
    ap.add_argument("--presence", type=float, default=2.5)
    ap.add_argument("--wav-only", action="store_true")
    a = ap.parse_args()

    out = a.output or default_out(a.input, "audiofix (AI DeepFilterNet)")
    if a.wav_only and not a.output:
        out = str(Path(out).with_suffix(".wav"))
    refuse_overwrite(a.input, out)
    df = deep_filter()

    ch = a.channel
    if ch == "auto":
        ch, why = channel_advice(decode_audio(a.input, 16000, channels=2))
        print(f"channel: {ch} ({why})")

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "speech.wav"
        run([ffmpeg(), "-v", "error", "-y", "-i", a.input, "-map", "0:a:0", "-af",
             f"{PANS[ch]},highpass=f=80:poles=2", "-ar", "48000", "-c:a", "pcm_s16le", wav])
        # -D compensates DeepFilterNet's processing delay so lip sync is preserved.
        run([df, "-D", "-a", a.strength, "-o", Path(tmp) / "df", wav])
        clean = Path(tmp) / "df" / "speech.wav"
        chain = post_chain(a.presence)
        ln = two_pass_loudnorm(clean, chain, a.target)
        if a.wav_only:
            run([ffmpeg(), "-v", "error", "-y", "-i", clean, "-af", f"{chain},{ln},aresample=48000",
                 "-c:a", "pcm_s16le", out])
        else:
            run([ffmpeg(), "-v", "error", "-y", "-i", a.input, "-i", clean, "-map", "0:v?", "-map", "1:a",
                 "-c:v", "copy", "-af", f"{chain},{ln},aresample=48000", "-c:a", "aac", "-b:a", "192k",
                 "-ac", "1", "-shortest", "-movflags", "+faststart", out])

    # Verify against the original
    before = decode_audio(a.input)
    after = decode_audio(out)
    lag, corr = sync_lag_ms(before, after)
    I, peak = loudness(out)
    g0, g1 = speech_noise_gap(before)[2], speech_noise_gap(after)[2]
    print(f"wrote: {out}")
    print(f"loudness {I} LUFS, peak {peak} dBFS | voice above background {g0:.1f} -> {g1:.1f} dB | "
          f"sync offset {lag} ms (envelope match {corr:.2f})")
    if abs(lag) > 20:
        print("WARNING: audio is out of sync by more than 20 ms; investigate before delivering.", file=sys.stderr)


if __name__ == "__main__":
    main()

"""Diagnose a video's audio: loudness, background noise, rumble, muffling, stereo problems, silences.

Usage: python analyze.py VIDEO [--window 10]
Prints a per-window table plus a summary with a recommended --channel mode for audiofix.py.
"""
import argparse
import re

import numpy as np

from common import decode_audio, ffmpeg, loudness, probe, run, speech_noise_gap

SR = 16000
CLEAN_GAP_DB = 28  # median voice above background at or above this: "fairly clean"
NOISY_GAP_DB = 15  # below this: "very noisy"


def band_pct(v, lo, hi):
    F = np.abs(np.fft.rfft(v)) ** 2
    f = np.fft.rfftfreq(len(v), 1 / SR)
    return 100 * F[(f >= lo) & (f < hi)].sum() / (F.sum() + 1e-12)


def median_window_gap(mono, sr=SR, window_s=10):
    """Median voice-above-background gap (dB) over fixed windows; None when no window has sound."""
    w = int(window_s * sr)
    gaps = [g for g in (speech_noise_gap(mono[i:i + w], sr)[2] for i in range(0, max(len(mono) - w + 1, 1), w))
            if g is not None]
    return float(np.median(gaps)) if gaps else None


def gap_verdict(gap):
    return "very noisy" if gap < NOISY_GAP_DB else "noticeable background" if gap < CLEAN_GAP_DB else "fairly clean"


def audio_channels(path):
    """Channel count of the first audio stream (decoding with -ac 2 makes mono look like stereo)."""
    audio = [s for s in probe(path).get("streams", []) if s.get("codec_type") == "audio"]
    return int(audio[0].get("channels") or 1) if audio else None


def channel_advice(x, source_channels=None):
    """Decide how to fold stereo to mono. Out-of-phase or unrelated channels thin the voice when mixed.
    Pass the source's channel count so a mono source is not treated as two identical channels."""
    if x.shape[1] < 2 or source_channels == 1:
        return "mix", "mono source"
    L, R = x[:, 0], x[:, 1]
    if L.std() == 0 or R.std() == 0:
        return ("right" if L.std() == 0 else "left"), "one channel is silent"
    corr = float(np.corrcoef(L, R)[0, 1])
    loss = 20 * np.log10(np.std(L + R) / (np.std(L) + np.std(R)) + 1e-12)
    if corr < 0.3 or loss < -2.5:
        gl = speech_noise_gap(L)[2] or 0
        gr = speech_noise_gap(R)[2] or 0
        pick = "left" if gl >= gr else "right"
        return pick, f"channels poorly related (corr {corr:.2f}, mono-sum loss {loss:.1f} dB): use the cleaner {pick} channel"
    return "mix", f"channels compatible (corr {corr:.2f}, mono-sum loss {loss:.1f} dB)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--window", type=float, default=10)
    a = ap.parse_args()

    info = probe(a.video)
    for s in info.get("streams", []):
        print("stream:", {k: v for k, v in s.items() if k != "index"})
    print("duration: %.1f s" % float(info.get("format", {}).get("duration", 0)))

    x = decode_audio(a.video, SR, channels=2)
    I, peak = loudness(a.video)
    print(f"integrated loudness: {I} LUFS (social target about -14), true peak {peak} dBFS")

    w = int(a.window * SR)
    print("\n  t(s)  L dB   R dB  L-R | noise  speech  gap | <150Hz%  >4kHz%")
    lows, highs, imb = [], [], []
    for i in range(0, max(len(x) - w + 1, 1), w):
        seg = x[i:i + w]
        m = seg.mean(1)
        db = lambda v: 20 * np.log10(np.sqrt(np.mean(v ** 2)) + 1e-12)
        lo, hi, gap = speech_noise_gap(m)
        if gap is None:
            continue
        lp, hp = band_pct(m, 0, 150), band_pct(m, 4000, 8000)
        d = db(seg[:, 0]) - db(seg[:, 1])
        lows.append(lp); highs.append(hp); imb.append(d)
        print(f"{i / SR:6.0f} {db(seg[:, 0]):6.1f} {db(seg[:, 1]):6.1f} {d:4.1f} | {lo:6.1f} {hi:6.1f} {gap:5.1f} | {lp:6.0f} {hp:8.2f}")

    sil = run([ffmpeg(), "-hide_banner", "-nostats", "-i", a.video, "-map", "0:a:0", "-af",
               "silencedetect=n=-45dB:d=0.4", "-f", "null", "-"], capture=True)
    starts = re.findall(r"silence_start: (-?[\d.]+)", sil)
    ends = re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", sil)

    print("\nSUMMARY")
    mode, why = channel_advice(x, audio_channels(a.video))
    print(f"- recommended --channel {mode}: {why}")
    if imb and (max(imb) - min(imb)) > 4:
        print(f"- left/right balance drifts {min(imb):.1f} to {max(imb):.1f} dB: voice wanders on headphones -> fold to mono")
    g = median_window_gap(x.mean(1), SR, a.window)
    if g is not None:
        print(f"- voice above background: median {g:.1f} dB ({gap_verdict(g)})")
    if lows and max(lows) > 12:
        print(f"- low rumble (wind/traffic/AC/handling): up to {max(lows):.0f}% of energy below 150 Hz")
    if highs and np.median(highs) < 0.5:
        print("- muffled: very little energy above 4 kHz -> presence boost helps")
    if I is not None and I < -18:
        print(f"- too quiet: {I} LUFS, needs about {(-14 - I):.0f} dB more")
    for s, (e, d) in zip(starts, ends):
        print(f"- silence {float(s):.1f}s to {float(e):.1f}s ({float(d):.1f}s)")


if __name__ == "__main__":
    main()

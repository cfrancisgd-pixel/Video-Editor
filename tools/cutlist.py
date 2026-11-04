#!/usr/bin/env python3
"""Assemble an edit from a JSON cut list with FFmpeg.

Handles what a documentary cut needs: frame-accurate trims, hard cuts or
xfade transitions (dissolve, fadeblack, wipes...), J-cuts and L-cuts (audio
leading or trailing the picture), per-clip speed and volume, a music bed with
ducking under dialogue, and loudness normalisation.

Usage: python3 cutlist.py cuts.json -o out.mp4 [--dry-run]

cuts.json:
{
  "width": 1920, "height": 1080, "fps": 30,
  "loudness": -14,                       # integrated LUFS target, null to skip
  "clips": [
    {"src": "interview.mp4", "in": 12.4, "out": 18.9},
    {"src": "broll.mp4", "in": 3, "out": 7, "volume": 0.3,
     "transition": {"type": "dissolve", "dur": 0.5}},
    {"src": "interview.mp4", "in": 40.2, "out": 47, "j": 0.8},   # audio starts 0.8 s early (J-cut)
    {"src": "speech.mp4", "in": 2, "out": 6, "l": 1.2},          # audio runs 1.2 s past the cut (L-cut)
    {"src": "action.mp4", "in": 0, "out": 4, "speed": 1.15},     # slightly sped up for hype finales
    {"src": "still.mp4", "in": 0, "out": 2, "mute": true}
  ],
  "music": {"src": "bed.mp3", "start": 0, "volume": 0.25, "fade_in": 0.4,
            "fade_out": 1.5, "duck": true}       # duck: sidechain under clip audio
}
Times are seconds in the source file. A J lead or L tail needs that much
extra source audio before "in" or after "out".
"""
import argparse, json, os, subprocess, sys

XFADE_ALIASES = {"dissolve": "fade", "dip": "fadeblack", "fadeblack": "fadeblack",
                 "fadewhite": "fadewhite", "cut": None, None: None}


def probe(src):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                          "-of", "json", src], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return float(d["format"]["duration"]), has_audio


def atempo_chain(speed):
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def build(cfg):
    W, H, FPS = cfg.get("width", 1920), cfg.get("height", 1080), cfg.get("fps", 30)
    clips = cfg["clips"]
    inputs, idx = [], {}
    for c in clips:
        if c["src"] not in idx:
            idx[c["src"]] = len(inputs); inputs.append(c["src"])
    music = cfg.get("music")
    if music:
        idx["__music__"] = len(inputs); inputs.append(music["src"])
    info = {s: probe(s) for s in inputs}

    f, timeline, vlabels, alabels = [], [], [], []
    t = 0.0
    for i, c in enumerate(clips):
        n = idx[c["src"]]
        sp = float(c.get("speed", 1.0))
        cin, cout = float(c["in"]), float(c["out"])
        if cout <= cin:
            sys.exit(f"clip {i}: out must be after in")
        dur = (cout - cin) / sp
        tr = c.get("transition") or {}
        xtype = XFADE_ALIASES.get(tr.get("type"), tr.get("type"))
        xd = float(tr.get("dur", 0.5)) if (xtype and i > 0) else 0.0
        start = t - xd
        timeline.append((start, dur, xtype, xd))
        t = start + dur
        # Video: trim, retime, conform to the delivery raster.
        f.append(f"[{n}:v]trim=start={cin}:end={cout},setpts=(PTS-STARTPTS)/{sp},"
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                 f"setsar=1,fps={FPS},format=yuv420p,settb=AVTB[v{i}]")
        vlabels.append(f"[v{i}]")
        # Audio with J lead / L tail, placed on the timeline.
        if c.get("mute") or not info[c["src"]][1]:
            continue
        j, l = float(c.get("j", 0)), float(c.get("l", 0))
        a_in, a_out = max(0.0, cin - j * sp), min(info[c["src"]][0], cout + l * sp)
        a_dur = (a_out - a_in) / sp
        place = start - (cin - a_in) / sp
        fi = max(0.03, min(j, 0.25)) if j else 0.02
        fo = max(0.03, min(l, 0.4)) if l else 0.02
        vol = float(c.get("volume", 1.0))
        delay = max(0, int(round(place * 1000)))
        f.append(f"[{n}:a]atrim=start={a_in}:end={a_out},asetpts=PTS-STARTPTS,{atempo_chain(sp)},"
                 f"aformat=sample_rates=48000:channel_layouts=stereo,volume={vol},"
                 f"afade=t=in:d={fi},afade=t=out:st={max(0, a_dur - fo):.3f}:d={fo},"
                 f"adelay={delay}|{delay}[a{i}]")
        alabels.append(f"[a{i}]")
    total = t

    # Picture: hard cuts concat; transitions chain through xfade.
    if all(x[2] is None for x in timeline[1:]):
        f.append("".join(vlabels) + f"concat=n={len(vlabels)}:v=1:a=0[vout]")
    else:
        cur, acc = vlabels[0], timeline[0][1]
        for i in range(1, len(vlabels)):
            start, dur, xtype, xd = timeline[i]
            lab = f"[vx{i}]" if i < len(vlabels) - 1 else "[vout]"
            if xtype:
                f.append(f"{cur}{vlabels[i]}xfade=transition={xtype}:duration={xd}:offset={acc - xd:.4f}{lab}")
                acc = acc - xd + dur
            else:
                f.append(f"{cur}{vlabels[i]}concat=n=2:v=1:a=0,fps={FPS},settb=AVTB{lab}")
                acc += dur
            cur = lab

    # Sound: dialogue/clip bus, optional ducked music bed, loudness.
    if alabels:
        # A silent base from 0 s keeps the audio stream aligned with the picture
        # even when the first clip is muted or starts with a J lead.
        f.append(f"anullsrc=r=48000:cl=stereo,atrim=end={total:.4f}[sil]")
        f.append("[sil]" + "".join(alabels) + f"amix=inputs={len(alabels) + 1}:normalize=0:dropout_transition=0,"
                 f"apad,atrim=end={total:.4f}[dia]")
    else:
        f.append(f"anullsrc=r=48000:cl=stereo,atrim=end={total:.4f}[dia]")
    bus = "[dia]"
    if music:
        n = idx["__music__"]
        ms = float(music.get("start", 0))
        mfi, mfo = float(music.get("fade_in", 0.4)), float(music.get("fade_out", 1.5))
        f.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"aloop=loop=-1:size=2147483647,atrim=start={ms}:end={ms + total:.4f},asetpts=PTS-STARTPTS,"
                 f"volume={music.get('volume', 0.25)},afade=t=in:d={mfi},afade=t=out:st={max(0, total - mfo):.3f}:d={mfo}[mus]")
        if music.get("duck", True) and alabels:
            f.append("[dia]asplit=2[dia1][sc]")
            f.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[musd]")
            f.append("[dia1][musd]amix=inputs=2:normalize=0[mix]")
        else:
            f.append("[dia][mus]amix=inputs=2:normalize=0[mix]")
        bus = "[mix]"
    lufs = cfg.get("loudness", -14)
    if lufs is not None:
        f.append(f"{bus}loudnorm=I={lufs}:TP=-1.0:LRA=11,aresample=48000[aout]")
    else:
        f.append(f"{bus}alimiter=limit=0.89[aout]")

    cmd = ["ffmpeg", "-y", "-v", "error", "-stats"]
    for s in inputs:
        cmd += ["-i", s]
    cmd += ["-filter_complex", ";\n".join(f), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.4f}"]
    return cmd, total, timeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cutlist"); ap.add_argument("-o", "--out", default="cut.mp4")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    base = os.path.dirname(os.path.abspath(a.cutlist))
    cfg = json.load(open(a.cutlist))
    for c in cfg["clips"]:
        c["src"] = os.path.join(base, c["src"]) if not os.path.isabs(c["src"]) else c["src"]
    if cfg.get("music") and not os.path.isabs(cfg["music"]["src"]):
        cfg["music"]["src"] = os.path.join(base, cfg["music"]["src"])
    cmd, total, tl = build(cfg)
    for i, (s, d, x, xd) in enumerate(tl):
        print(f"clip {i}: {s:7.3f}s -> {s + d:7.3f}s  {('(' + x + ' ' + str(xd) + 's)') if x else ''}")
    print(f"total {total:.3f}s")
    if a.dry_run:
        print(" ".join(cmd)); return
    subprocess.run(cmd + [a.out], check=True)
    print("wrote", a.out)


if __name__ == "__main__":
    main()

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

How it renders: one encoded file per clip (same size, frame rate and encoder
settings), each crossfade inside the file of the clip it fades into, joined
with the concat demuxer without re-encoding. The scene files are kept in
<out>.scenes/ next to the output (cut.mp4 -> cut.scenes/) and named by a hash
of everything that shapes them, so a re-run re-encodes only the clips that
changed (and the clip after one, if it fades from it). The sound (J and L
cuts, music, ducking, loudness) is built once for the whole edit and laid on
with -c:v copy. Delete the .scenes folder to force a full re-render.

Loudness: gain plus a peak limiter, measured on the encoded AAC and corrected
until it is within 0.5 LU of the target with peaks at most -1 dBTP (a warning
says so if it is not). The picture of a clip starts on the first source frame
at or after "in", so an in point between two frames moves the picture up to
one frame later than its sound.
"""
import argparse, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile
from fractions import Fraction

XFADE_ALIASES = {"dissolve": "fade", "dip": "fadeblack", "fadeblack": "fadeblack",
                 "fadewhite": "fadewhite", "cut": None, None: None}
VIDEO_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
NTSC_RATES = {23.976: Fraction(24000, 1001), 29.97: Fraction(30000, 1001), 47.952: Fraction(48000, 1001),
              59.94: Fraction(60000, 1001), 119.88: Fraction(120000, 1001)}
SR = 48000
LUFS_TOLERANCE = 0.5     # the playbook allows 1 LU either side; aim inside half of that
TRUE_PEAK_MAX = -1.0     # dBTP, measured on the encoded AAC
LIMIT_START_DBFS = -1.5  # first limiter ceiling (sample peak); lowered if the AAC still peaks too high
LIMIT_NO_LOUDNESS = 20 * math.log10(0.89)  # "loudness": null keeps the level and only catches peaks
LEVEL_TRIES = 5
SILENCE_LUFS = -70.0     # ebur128's absolute gate: a mix this quiet (or silent) is left as it is
MAX_GAIN_STEP = 6.0      # dB per try, so a heavily limited mix is not pushed far past the target
SCENE_RECIPE = 1        # bump when the scene filter or encoder changes, so old scene files are not reused
SCENE_FILE = re.compile(r"^[0-9a-f]{16}(\.part)?\.mp4$")
EXTRA_FRAMES = 2        # read a little past each piece; tpad + trim then make the frame count exact
PREROLL = 1.0           # seconds decoded before each cut point, so the decoder has settled
INLINE_GRAPH_MAX = 8000  # longer filter graphs go in a file (keeps the Windows command line short)


def probe(src):
    """Duration, whether it has sound, and its first timestamp (cut list times are timestamps)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type:format=duration,start_time", "-of", "json", src],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    fmt = d["format"]
    return {"dur": float(fmt["duration"]), "audio": has_audio, "start": float(fmt.get("start_time") or 0)}


def seek_args(src_info, at, dur):
    """Read [at, at + dur) of a source cheaply: seek to PREROLL before it, keep the source's own
    timestamps (-copyts), and let trim/atrim cut at `at` exactly, as one long graph would."""
    seek = max(0.0, at - src_info["start"] - PREROLL)
    return ["-ss", f"{seek:.6f}", "-t", f"{at - src_info['start'] - seek + dur:.6f}"]


def frame_rate(fps):
    """Exact frame rate: 29.97 means 30000/1001, as cameras record it."""
    fps = float(fps)
    near = min(NTSC_RATES, key=lambda r: abs(r - fps))
    return NTSC_RATES[near] if abs(near - fps) < 0.005 else Fraction(fps).limit_denominator(1001)


def atempo_chain(speed):
    """Speed change for sound, ending in a comma; empty at speed 1 (atempo=1 still drops and shifts
    samples on some sources, up to about 9 ms on a 44.1 kHz phone clip)."""
    if speed == 1.0:
        return ""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts) + ","


def plan(cfg):
    """Frame-exact timeline: each clip's length L and crossfade X in frames, and its start frame S."""
    rate = frame_rate(cfg.get("fps", 30))
    timeline, end = [], 0
    for i, c in enumerate(cfg["clips"]):
        sp = float(c.get("speed", 1.0))
        cin, cout = float(c["in"]), float(c["out"])
        if cout <= cin or sp <= 0:
            sys.exit(f"clip {i}: out must be after in, and speed above 0")
        frames = round((cout - cin) / sp * rate)
        if frames < 1:
            sys.exit(f"clip {i}: shorter than one frame")
        tr = c.get("transition") or {}
        xtype = XFADE_ALIASES.get(tr.get("type"), tr.get("type"))
        x = round(float(tr.get("dur", 0.5)) * rate) if (xtype and i > 0) else 0
        if x > min(frames, timeline[-1]["L"] if timeline else 0):
            sys.exit(f"clip {i}: the {tr.get('type')} is longer than this clip or the one before")
        timeline.append({"c": c, "sp": sp, "in": cin, "out": cout, "L": frames,
                         "X": x, "xtype": xtype if x else None, "S": end - x})
        end = end - x + frames
    for i, t in enumerate(timeline):
        nxt = timeline[i + 1]["X"] if i + 1 < len(timeline) else 0
        t["N"] = t["L"] - nxt  # frames in this clip's scene file (the next crossfade lives in the next file)
        if t["N"] < max(1, t["X"]):
            sys.exit(f"clip {i}: too short for the transitions on both its sides")
    return timeline, end, rate


def picture(label, start, sp, frames, cfg, rate, out):
    """Trim one input at `start`, retime and conform it to the delivery raster, exactly `frames` frames long."""
    W, H = cfg.get("width", 1920), cfg.get("height", 1080)
    return (f"[{label}:v]trim=start={start:.6f},setpts=(PTS-STARTPTS)/{sp},"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={rate},format=yuv420p,tpad=stop=-1:stop_mode=clone,"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS[{out}]")


def source_id(src):
    """Path, size and change time: a re-exported source gets new scene files."""
    st = os.stat(src)
    return [os.path.abspath(src), st.st_size, st.st_mtime_ns]


def scene(timeline, i, cfg, rate, info):
    """File name (a content hash) and FFmpeg command of clip i's scene file."""
    t = timeline[i]
    inputs = [(t["c"]["src"], t["in"], (t["N"] + EXTRA_FRAMES) * t["sp"] / rate)]
    graph = [picture(0, t["in"], t["sp"], t["N"], cfg, rate, "cur")]
    if t["X"]:
        p = timeline[i - 1]
        tail = p["in"] + (p["L"] - t["X"]) * p["sp"] / rate
        inputs.append((p["c"]["src"], tail, (t["X"] + EXTRA_FRAMES) * p["sp"] / rate))
        graph.append(picture(1, tail, p["sp"], t["X"] + EXTRA_FRAMES, cfg, rate, "prev"))
        graph.append(f"[prev][cur]xfade=transition={t['xtype']}:duration={float(t['X'] / rate):.6f}:offset=0,"
                     f"tpad=stop=-1:stop_mode=clone,trim=end_frame={t['N']}[v]")
    else:
        graph[0] = graph[0].replace("[cur]", "[v]")
    key = {"recipe": SCENE_RECIPE, "codec": VIDEO_CODEC, "graph": graph,
           "inputs": [source_id(s) + [round(a, 6), round(d, 6)] for s, a, d in inputs]}
    name = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16] + ".mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-copyts"]
    for s, at, dur in inputs:
        cmd += [*seek_args(info[s], at, dur), "-i", s]
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-an", *VIDEO_CODEC]
    return name, cmd


def audio_pieces(timeline, info, rate, work):
    """Cut each clip's sound (J lead, L tail, speed, volume, short fades) into its own float WAV.
    Returns (cmd, file, delay in samples) per piece; the pieces are mixed in one pass afterwards."""
    pieces = []
    for i, t in enumerate(timeline):
        c, sp = t["c"], t["sp"]
        src = info[c["src"]]
        if c.get("mute") or not src["audio"]:
            continue
        j, l = float(c.get("j", 0)), float(c.get("l", 0))
        a_in, a_out = max(src["start"], t["in"] - j * sp), min(src["start"] + src["dur"], t["out"] + l * sp)
        place = float(t["S"] / rate) - (t["in"] - a_in) / sp
        if place < 0:  # a J lead on the first clip: drop the part before 0 s instead of shifting the sound
            a_in, place = a_in - place * sp, 0.0
        if a_out <= a_in:
            continue
        a_dur = (a_out - a_in) / sp
        fi = max(0.03, min(j, 0.25)) if j else 0.02
        fo = max(0.03, min(l, 0.4)) if l else 0.02
        name = os.path.join(work, f"a{i:04d}.wav")
        chain = (f"atrim=start={a_in:.6f}:end={a_out:.6f},asetpts=PTS-STARTPTS,{atempo_chain(sp)}"
                 f"aformat=sample_fmts=flt:sample_rates={SR}:channel_layouts=stereo,"
                 f"volume={float(c.get('volume', 1.0))},afade=t=in:d={fi},"
                 f"afade=t=out:st={max(0, a_dur - fo):.6f}:d={fo}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-copyts", *seek_args(src, a_in, a_out - a_in),
               "-i", c["src"], "-vn", "-af", chain, "-c:a", "pcm_f32le"]
        pieces.append((cmd, name, round(place * SR)))
    return pieces


def mix_graph(pieces, cfg, total_s):
    """Dialogue bus from the placed pieces plus the music bed (ducked under it), as [aout].
    Inputs: the pieces in order, then the music. The level step comes after (encode_sound)."""
    n_samples = round(total_s * SR)
    f = [f"anullsrc=r={SR}:cl=stereo,atrim=end_sample={n_samples}[sil]"]
    for k, (_, _, delay) in enumerate(pieces):
        f.append(f"[{k}:a]adelay=delays={delay}S:all=1[a{k}]")
    labels = "".join(f"[a{k}]" for k in range(len(pieces)))
    # The silent base from 0 s keeps the sound aligned with the picture even when the first clip is muted.
    f.append(f"[sil]{labels}amix=inputs={len(pieces) + 1}:normalize=0:dropout_transition=0,"
             f"apad,atrim=end_sample={n_samples}[dia]")
    bus, music = "[dia]", cfg.get("music")
    if music:
        ms = float(music.get("start", 0))
        mfi, mfo = float(music.get("fade_in", 0.4)), float(music.get("fade_out", 1.5))
        f.append(f"[{len(pieces)}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
                 f"aloop=loop=-1:size=2147483647,atrim=start={ms}:end={ms + total_s:.6f},asetpts=PTS-STARTPTS,"
                 f"volume={music.get('volume', 0.25)},afade=t=in:d={mfi},"
                 f"afade=t=out:st={max(0, total_s - mfo):.6f}:d={mfo}[mus]")
        if music.get("duck", True) and pieces:
            f.append("[dia]asplit=2[dia1][sc]")
            f.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[musd]")
            f.append("[dia1][musd]amix=inputs=2:normalize=0[mix]")
        else:
            f.append("[dia][mus]amix=inputs=2:normalize=0[mix]")
        bus = "[mix]"
    f.append(f"{bus}atrim=end_sample={n_samples}[aout]")
    return ";\n".join(f)


def limiter(n_samples, gain_db=0.0, ceiling=LIMIT_NO_LOUDNESS):
    """Gain, then a peak limiter: level=0 keeps the gain as set, latency=1 keeps the sound on time.
    A second of padding goes in so the limiter's look-ahead keeps the last samples; atrim takes it out."""
    return (f"apad=pad_dur=1,volume={gain_db:.3f}dB,alimiter=limit={10 ** (ceiling / 20):.5f}:level=0:latency=1,"
            f"atrim=end_sample={n_samples}")


def measure(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's sound, from FFmpeg's ebur128."""
    err = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-nostdin", "-i", path, "-map", "0:a:0",
                          "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True,
                         check=True).stderr
    tail = err[err.rfind("Summary:"):]
    loud = re.search(r"I:\s+(-?[\d.]+|-inf)", tail).group(1)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf)", tail).group(1)
    return float(loud), float(peak)


def encode_sound(mix, lufs, n_samples, work):
    """Encode the mix to AAC, levelled to `lufs` with peaks at most -1 dBTP, both measured on the AAC
    itself (the encoder adds peak): gain plus a limiter, corrected and encoded again until both hold.
    One-pass loudnorm missed this (-14.2 LUFS at -0.7 dBTP on a test cut). Returns the .m4a path."""
    out = os.path.join(work, "sound.m4a")
    enc = ["-c:a", "aac", "-b:a", "192k"]
    if lufs is None:
        render(["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", mix, "-af", limiter(n_samples), *enc], out)
        return out
    lufs, (start, _) = float(lufs), measure(mix)
    if start <= SILENCE_LUFS:  # silence: nothing to level
        render(["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", mix, *enc], out)
        return out
    gain, ceiling, last = lufs - start, LIMIT_START_DBFS, None
    for attempt in range(1, LEVEL_TRIES + 1):
        render(["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", mix,
                "-af", limiter(n_samples, gain, ceiling), *enc], out)
        loud, peak = measure(out)
        print(f"loudness try {attempt}: gain {gain:+.2f} dB, limit {ceiling:.2f} dBFS -> "
              f"{loud:.1f} LUFS, {peak:.1f} dBTP", flush=True)
        if abs(loud - lufs) <= LUFS_TOLERANCE and peak <= TRUE_PEAK_MAX:
            return out
        # How much louder one more dB of gain made it last time (the limiter takes some back); 1 at first.
        slope = 1.0
        if last and gain != last[0] and ceiling == last[2]:
            slope = min(1.0, max(0.3, (loud - last[1]) / (gain - last[0])))
        last = (gain, loud, ceiling)
        if peak > TRUE_PEAK_MAX:
            ceiling -= peak - TRUE_PEAK_MAX + 0.1
        gain += max(-MAX_GAIN_STEP, min(MAX_GAIN_STEP, (lufs - loud) / slope))
    print(f"warning: loudness stopped at {loud:.1f} LUFS, {peak:.1f} dBTP after {LEVEL_TRIES} tries "
          f"(target {lufs} LUFS, at most {TRUE_PEAK_MAX} dBTP)", file=sys.stderr)
    return out


def graph_args(graph, work):
    """-filter_complex inline when short; from a file when long (FFmpeg 7 or later reads -/option files)."""
    if len(graph) <= INLINE_GRAPH_MAX:
        return ["-filter_complex", graph]
    path = os.path.join(work, "mix-graph.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(graph)
    return ["-/filter_complex", path]


def part_path(out):
    """Temporary name next to the output that keeps its extension, so FFmpeg picks the same format."""
    root, ext = os.path.splitext(out)
    return f"{root}.part{ext or '.mp4'}"


def render(cmd, out, cwd=None):
    """Write to a temporary name and rename only when FFmpeg exits 0, so a stopped render
    never leaves a short file under the real name."""
    part = part_path(out)
    try:
        subprocess.run(cmd + [part], check=True, cwd=cwd)
    except (subprocess.CalledProcessError, KeyboardInterrupt) as e:
        if os.path.exists(part):
            os.remove(part)
        sys.exit(f"render stopped ({e.__class__.__name__}); {out} was not written")
    os.replace(part, out)


def render_sound(pieces, cfg, total_s, work):
    """Cut the pieces, mix and duck the whole edit once into work/mix.wav, then level and encode it."""
    for cmd, name, _ in pieces:
        render(cmd, name)
    mix = os.path.join(work, "mix.wav")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    for _, name, _ in pieces:
        cmd += ["-i", os.path.basename(name)]
    if cfg.get("music"):
        cmd += ["-i", cfg["music"]["src"]]
    cmd += graph_args(mix_graph(pieces, cfg, total_s), work)
    cmd += ["-map", "[aout]", "-c:a", "pcm_f32le"]  # no -t: the graph's atrim ends the sound on the sample
    render(cmd, mix, cwd=work)  # cwd = work keeps every piece's -i short
    return encode_sound(mix, cfg.get("loudness", -14), round(total_s * SR), work)


def check_sources(cfg, info):
    """Stop early when a clip starts at or after the end of its source (it would have no picture)."""
    for i, c in enumerate(cfg["clips"]):
        src = info[c["src"]]
        if float(c["in"]) >= src["start"] + src["dur"]:
            sys.exit(f"clip {i}: in {c['in']} s is past the end of {c['src']} ({src['dur']:.3f} s)")


def frame_count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    return int(out.strip() or 0)


def render_scene(cmd, path, frames):
    """Encode one scene file and check it has the planned number of frames (the join depends on it)."""
    render(cmd, path)
    got = frame_count(path)
    if got != frames:
        os.remove(path)
        sys.exit(f"scene {os.path.basename(path)} came out {got} frames long, not {frames}; nothing was joined")


def remove_unused(scenes_dir, keep):
    """Delete scene files this cut list no longer uses (only names this script writes)."""
    for name in os.listdir(scenes_dir):
        if SCENE_FILE.match(name) and name not in keep:
            os.remove(os.path.join(scenes_dir, name))


def load(path):
    """The cut list, with every source path made absolute (relative paths start at the cut list)."""
    base = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for c in cfg["clips"]:
        c["src"] = os.path.join(base, c["src"]) if not os.path.isabs(c["src"]) else c["src"]
    if cfg.get("music") and not os.path.isabs(cfg["music"]["src"]):
        cfg["music"]["src"] = os.path.join(base, cfg["music"]["src"])
    return cfg


def show_plan(timeline, scenes, total, rate, scenes_dir):
    """Print each clip's place on the timeline and whether its scene file can be reused."""
    for i, (t, (name, _)) in enumerate(zip(timeline, scenes)):
        s, e = float(t["S"] / rate), float((t["S"] + t["L"]) / rate)
        x = f"({t['xtype']} {float(t['X'] / rate):.3f}s)" if t["xtype"] else ""
        cached = "cached" if os.path.exists(os.path.join(scenes_dir, name)) else "to render"
        print(f"clip {i}: {s:7.3f}s -> {e:7.3f}s  {x:22} scene {name} {cached}")
    print(f"total {float(total / rate):.3f}s ({total} frames at {rate} fps)")


def join(timeline, scenes, sound, total_s, rate, out, work):
    """Join the scene files without re-encoding (concat demuxer) and lay the sound on."""
    concat = os.path.join(work, "scenes.txt")
    with open(concat, "w", encoding="utf-8") as fh:
        for t, (name, _) in zip(timeline, scenes):
            fh.write(f"file '../{name}'\nduration {float(t['N'] / rate):.6f}\n")
    render(["ffmpeg", "-y", "-v", "error", "-stats", "-nostdin", "-f", "concat", "-safe", "0", "-i", concat,
            "-i", sound, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy",
            "-movflags", "+faststart", "-t", f"{total_s:.6f}"], out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cutlist"); ap.add_argument("-o", "--out", default="cut.mp4")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load(a.cutlist)
    info = {s: probe(s) for s in {c["src"] for c in cfg["clips"]}}
    check_sources(cfg, info)
    timeline, total, rate = plan(cfg)
    total_s = float(total / rate)
    scenes_dir = os.path.splitext(os.path.abspath(a.out))[0] + ".scenes"
    scenes = [scene(timeline, i, cfg, rate, info) for i in range(len(timeline))]
    show_plan(timeline, scenes, total, rate, scenes_dir)
    if a.dry_run:
        return
    os.makedirs(scenes_dir, exist_ok=True)
    frames = {name: t["N"] for t, (name, _) in zip(timeline, scenes)}
    todo = [(n, c) for n, c in dict(scenes).items() if not os.path.exists(os.path.join(scenes_dir, n))]
    for k, (name, cmd) in enumerate(todo, 1):
        print(f"scene {k}/{len(todo)}: {name}", flush=True)
        render_scene(cmd, os.path.join(scenes_dir, name), frames[name])
    work = tempfile.mkdtemp(prefix="work-", dir=scenes_dir)
    try:
        sound = render_sound(audio_pieces(timeline, info, rate, work), cfg, total_s, work)
        join(timeline, scenes, sound, total_s, rate, a.out, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    remove_unused(scenes_dir, {n for n, _ in scenes})
    print(f"wrote {a.out} ({len(todo)} of {len(scenes)} scenes rendered, the rest reused from {scenes_dir})")


if __name__ == "__main__":
    main()

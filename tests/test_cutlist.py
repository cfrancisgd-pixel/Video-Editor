"""Checks for tools/cutlist.py: J-cuts, L-cuts, dissolves, music ducking, scene reuse and loudness, on tiny clips.

Run from the repo root (needs ffmpeg and ffprobe, nothing else):
    python3 -m unittest discover -s tests -v        (Windows: py -m unittest discover -s tests -v)

Clip "a" is red picture with a 440 Hz tone, clip "b" blue with a 1000 Hz tone, and the music bed a
300 Hz tone, so the picture colour and each tone's level at a given time show what the edit did.
"""
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CUTLIST = Path(__file__).resolve().parent.parent / "tools" / "cutlist.py"
SR = 48000
W, H, FPS = 320, 240, 30
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def make_clip(path, color, tone_hz, seconds=6):
    ff("-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}:d={seconds}",
       "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate={SR}:duration={seconds}",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", path)


def decode_mono(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return struct.unpack(f"<{len(raw) // 2}h", raw)


def tone_db(samples, hz, t0, t1):
    """Level of one frequency between t0 and t1 seconds, in dB full scale (Goertzel filter)."""
    seg = samples[int(t0 * SR):int(t1 * SR)]
    coeff = 2 * math.cos(2 * math.pi * hz / SR)
    s1 = s2 = 0.0
    for x in seg:
        s1, s2 = x / 32768 + coeff * s1 - s2, s1
    power = max(s1 * s1 + s2 * s2 - coeff * s1 * s2, 1e-20)
    return 20 * math.log10(2 * math.sqrt(power) / len(seg))


def color_at(path, t):
    """Average (r, g, b) of the frame shown at t seconds."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out)


PRESENT_DB = -35  # a tone that is playing measures about -20 dBFS here
ABSENT_DB = -50


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg and ffprobe on PATH")
class CutlistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cutlist-test-"))
        make_clip(cls.tmp / "a.mkv", "red", 440)
        make_clip(cls.tmp / "b.mkv", "blue", 1000)
        ff("-f", "lavfi", "-i", f"sine=frequency=300:sample_rate={SR}:duration=10", cls.tmp / "music.wav")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, name, clips, **extra):
        cfg = {"width": W, "height": H, "fps": FPS, "loudness": None, "clips": clips, **extra}
        (self.tmp / f"{name}.json").write_text(json.dumps(cfg))
        out = self.tmp / f"{name}.mp4"
        subprocess.run([sys.executable, str(CUTLIST), str(self.tmp / f"{name}.json"), "-o", str(out)],
                       check=True, capture_output=True)
        self.assertFalse(any(self.tmp.glob(f"{name}.part*")), "temporary file left behind")
        return out

    def assertRed(self, rgb):
        self.assertTrue(rgb[0] > 180 and rgb[2] < 70, f"expected red, got {rgb}")

    def assertBlue(self, rgb):
        self.assertTrue(rgb[2] > 180 and rgb[0] < 70, f"expected blue, got {rgb}")

    def test_hard_cut_timing(self):
        out = self.render("hard", [{"src": "a.mkv", "in": 1, "out": 2.5}, {"src": "b.mkv", "in": 1, "out": 2.5}])
        self.assertAlmostEqual(duration(out), 3.0, delta=1.5 / FPS)
        self.assertRed(color_at(out, 1.4))
        self.assertBlue(color_at(out, 1.6))
        audio = decode_mono(out)
        self.assertGreater(tone_db(audio, 440, 0.5, 1.4), PRESENT_DB)
        self.assertLess(tone_db(audio, 440, 1.7, 2.8), ABSENT_DB)
        self.assertGreater(tone_db(audio, 1000, 1.7, 2.8), PRESENT_DB)

    def test_j_cut_audio_leads_picture(self):
        out = self.render("j", [{"src": "a.mkv", "in": 1, "out": 3}, {"src": "b.mkv", "in": 1, "out": 3, "j": 1.0}])
        audio = decode_mono(out)
        self.assertLess(tone_db(audio, 1000, 0.3, 0.8), ABSENT_DB)       # b's sound not yet in
        self.assertGreater(tone_db(audio, 1000, 1.4, 1.85), PRESENT_DB)  # b's sound under a's picture
        self.assertRed(color_at(out, 1.6))
        self.assertBlue(color_at(out, 2.2))
        self.assertAlmostEqual(duration(out), 4.0, delta=1.5 / FPS)

    def test_l_cut_audio_trails_picture(self):
        out = self.render("l", [{"src": "a.mkv", "in": 1, "out": 3, "l": 1.0}, {"src": "b.mkv", "in": 1, "out": 3}])
        audio = decode_mono(out)
        self.assertBlue(color_at(out, 2.3))
        self.assertGreater(tone_db(audio, 440, 2.15, 2.5), PRESENT_DB)   # a's sound over b's picture
        self.assertLess(tone_db(audio, 440, 3.15, 3.8), ABSENT_DB)       # and gone after its tail

    def test_dissolve(self):
        out = self.render("dissolve", [{"src": "a.mkv", "in": 0, "out": 2},
                                       {"src": "b.mkv", "in": 0, "out": 2, "transition": {"type": "dissolve", "dur": 1.0}}])
        self.assertAlmostEqual(duration(out), 3.0, delta=1.5 / FPS)  # 2 + 2 - 1 s overlap
        self.assertRed(color_at(out, 0.5))
        mid = color_at(out, 1.5)
        self.assertTrue(60 < mid[0] < 200 and 60 < mid[2] < 200, f"expected a red-blue mix, got {mid}")
        self.assertBlue(color_at(out, 2.5))

    def test_dip_to_black(self):
        out = self.render("dip", [{"src": "a.mkv", "in": 0, "out": 2},
                                  {"src": "b.mkv", "in": 0, "out": 2, "transition": {"type": "dip", "dur": 1.0}}])
        # FFmpeg's fadeblack reaches black early in the dip (about 1.2 s here), then fades up.
        self.assertLess(min(max(color_at(out, t)) for t in (1.2, 1.3, 1.4)), 40)
        self.assertRed(color_at(out, 0.5))
        self.assertBlue(color_at(out, 2.6))

    def ducking_gap(self, duck):
        clips = [{"src": "a.mkv", "in": 0, "out": 2}, {"src": "a.mkv", "in": 2, "out": 4, "mute": True}]
        music = {"src": "music.wav", "volume": 0.5, "fade_in": 0.05, "fade_out": 0.05, "duck": duck}
        audio = decode_mono(self.render(f"duck-{duck}", clips, music=music))
        under_dialogue, alone = tone_db(audio, 300, 0.6, 1.6), tone_db(audio, 300, 3.0, 3.8)
        self.assertGreater(alone, PRESENT_DB)
        return alone - under_dialogue

    def test_music_ducks_under_dialogue(self):
        self.assertGreater(self.ducking_gap(True), 6)

    def test_music_not_ducked_when_off(self):
        self.assertLess(abs(self.ducking_gap(False)), 1.5)

    def test_source_whose_timestamps_start_late(self):
        # Camcorder .MTS files start at a time above 0; cut list times still count from the file's start.
        ts = self.tmp / "late.ts"
        ff("-i", self.tmp / "a.mkv", "-i", self.tmp / "b.mkv", "-filter_complex",
           "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-output_ts_offset", "1.4", ts)
        out = self.render("late", [{"src": "late.ts", "in": 5.5, "out": 6.5}])
        self.assertRed(color_at(out, 0.2))
        self.assertBlue(color_at(out, 0.8))
        audio = decode_mono(out)
        self.assertGreater(tone_db(audio, 440, 0.1, 0.4), PRESENT_DB)
        self.assertGreater(tone_db(audio, 1000, 0.6, 0.9), PRESENT_DB)

    def test_rerun_reencodes_only_changed_scenes(self):
        clips = [{"src": "a.mkv", "in": 0, "out": 1}, {"src": "b.mkv", "in": 0, "out": 1}]
        self.render("rerun", clips)
        scenes = self.tmp / "rerun.scenes"
        first = {p.name: p.stat().st_mtime_ns for p in scenes.glob("*.mp4")}
        self.assertEqual(len(first), 2)
        clips[1]["out"] = 1.5
        out = self.render("rerun", clips)
        second = {p.name: p.stat().st_mtime_ns for p in scenes.glob("*.mp4")}
        self.assertEqual(len(second), 2, "the old scene file of clip 2 should be deleted")
        self.assertEqual(len(set(first.items()) & set(second.items())), 1, "clip 1's scene file should be reused")
        self.assertAlmostEqual(duration(out), 2.5, delta=1.5 / FPS)

    def test_loudness_target_on_the_aac(self):
        out = self.render("loud", [{"src": "a.mkv", "in": 0, "out": 3}], loudness=-14)
        err = subprocess.run(["ffmpeg", "-nostats", "-i", str(out), "-map", "0:a", "-af", "ebur128=peak=true",
                              "-f", "null", "-"], capture_output=True, text=True, check=True).stderr
        summary = err[err.rfind("Summary:"):]
        lufs = float(summary.split("I:")[1].split()[0])
        peak = float(summary.split("Peak:")[1].split()[0])
        self.assertAlmostEqual(lufs, -14, delta=1.0)
        self.assertLessEqual(peak, -1.0)


if __name__ == "__main__":
    unittest.main()

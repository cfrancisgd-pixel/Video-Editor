# HyperFrames and FFmpeg notes

Read before motion graphics or footage cuts. Moved from the old `editing-style-playbook.md` (Nov 2026); the editing rules themselves live in `SKILL.md`.

## Motion graphics: HyperFrames is the default

For every video edit request, motion graphics (titles, kinetic type, maps, charts, lower-thirds, end cards, explainer graphics) are built in **HyperFrames** (HeyGen, open source: HTML + CSS + GSAP rendered to MP4). Remotion only if Francis asks for it or the project is already in Remotion (offer /remotion-to-hyperframes to port it).

- Load the official skills first: hyperframes, hyperframes-cli, hyperframes-registry (search ready blocks before hand-building), media-use. Install with `npx skills add heygen-com/hyperframes --full-depth` if missing.
- Pin one version so renders match from week to week: call the CLI as `npx hyperframes@0.9.19` (not plain `npx hyperframes`; 0.9.19 was the latest on 2026-11-07), and update the skills and the CLI together.
- Set `HYPERFRAMES_NO_TELEMETRY=1` before every HyperFrames command (it stops usage reports).
- Loop: init → write scenes → lint → check --json → snapshot and look → preview → render (draft, then delivery) → verify duration and frames.
- Cloud workspace: set `HYPERFRAMES_BROWSER_PATH=/usr/bin/google-chrome`; CDNs are blocked, so bundle GSAP locally in `assets/` via npm.
- All the structure, pacing and sound rules in `SKILL.md` still apply inside HyperFrames compositions.

## Footage cuts: FFmpeg

Real footage editing (trims, assembly, J/L-cuts, transitions, music bed, loudness) is done with **FFmpeg**; HyperFrames stays on motion graphics. The final film = FFmpeg cut with HyperFrames graphics rendered and cut in as clips.

Tools (`tools/`; on Windows use `py` where these say `python3`):
- **scrub.py** — Cut 1 dialogue scrub. Runs silencedetect on an interview, keeps speech with 0.15 s handles, keeps natural pauses under 0.35 s, drops long silences, writes a cut list with the source's size and frame rate. `python3 scrub.py interview.mp4 -o cut1.json`
- **cutlist.py** — assembles any cut list. Frame-accurate trims, hard cuts or xfade transitions (dissolve, dip to black, wipes), J-cuts (`"j": seconds`), L-cuts (`"l": seconds`), per-clip speed and volume, mute, a looping music bed that ducks ~10 dB under dialogue, conform to the cut list's size and frame rate (1920×1080/30, 9:16, 1:1...), loudness to −14 LUFS / −1 dBTP measured on the final AAC. `python3 cutlist.py cuts.json -o cut.mp4` (`--dry-run` prints the timeline and which scenes are cached). It encodes one file per clip into `cut.scenes/` next to the output and joins them without re-encoding, so a re-run only re-encodes the clips that changed; the sound is rebuilt each run. Delete `cut.scenes/` to force a full re-render.

Workflow: scrub → Cut 1 JSON → edit the JSON by hand for Cut 2 / Skeleton (reorder, trim, pick endings) → add music + J/L-cuts for Build 1 → add B-roll and HyperFrames graphics clips for Picture. Save each stage's JSON (cut1.json, cut2.json, skeleton.json, build1.json) — the duplicate-every-stage habit.
Verified Nov 2026 on test footage: J-cut audio leads picture, L-cut audio trails into the next shot, dissolve/dip offsets correct, ducking works.
Automated checks of the same (J and L cuts, dissolve, dip, ducking, scene reuse, loudness; about 10 s, needs only FFmpeg): `python3 -m unittest discover -s tests -v` from the repo root. Run them after changing `cutlist.py`.

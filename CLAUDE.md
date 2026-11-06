# Video Editor Expert — Francis Cabrera

Francis is a multimedia designer and video editor (6+ years). This folder holds his video editing skills, style playbook and FFmpeg tools. Follow them for every video edit request.

## Folder layout
- `skills/documentary-editing-style/` — main editing style skill (build order, pacing, story, music, sound design, picture, review checklist)
- `skills/storytelling-scripts/` — hook / story lens / but-therefore formula for scripts, ideas and captions
- `skills/video-making/` — guidance before making a new video from scratch
- `skills/video-cleanup/` — scripts to analyze and fix supplied footage (audio, video, logo)
- `skills/documentary-editing-style/hyperframes-ffmpeg-notes.md` — HyperFrames and FFmpeg notes (read before motion graphics or footage cuts)
- `tools/scrub.py`, `tools/cutlist.py` — FFmpeg dialogue scrub and cut-list assembler

## Rules
- Read `skills/documentary-editing-style/SKILL.md` before any edit or review.
- Build order: dialogue scrub → Cut 2 → Skeleton (setup → conflict → resolution) → Build 1 (music/SFX, no B-roll) → B-roll and polish. Save each stage (cut1.json, cut2.json, skeleton.json, build1.json).
- Footage cuts with FFmpeg via `tools/`. Motion graphics in HyperFrames (Remotion only if asked or the project is already Remotion).
- CapCut edits: edit the CapCut project file directly, don't drive the CapCut app. Close CapCut first, back up the draft, write to a temporary file and swap it in, then ask Francis to open the draft.
- Short-form 9:16 can stay tight but still needs a deliberate pace change at the key beat.

## Quick commands
```
python3 tools/scrub.py interview.mp4 -o cut1.json
python3 tools/cutlist.py cut1.json -o cut1.mp4 --dry-run
python3 tools/cutlist.py cut1.json -o cut1.mp4
```
Needs `ffmpeg` and `ffprobe` (`brew install ffmpeg`; Windows: `winget install Gyan.FFmpeg`, and `py` instead of `python3`).

## Using the skills in Claude Code
To make these load as skills, copy the folders inside `skills/` into `~/.claude/skills/` (personal) or `.claude/skills/` in a project.

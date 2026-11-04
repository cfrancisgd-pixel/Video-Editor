# Documentary Editing Style Playbook

Distilled from three reference tutorials Francis shared (Nov 2026). Use this as the default approach when building or reviewing documentary / story-led edits (CapCut project files, Premiere, Resolve, Remotion).

Sources:
- Max McCoy — "Edit Like a Filmmaker, Not a Videographer" (structure & workflow)
- Inside The Edit (Paddy Bird) — "How Pacing Controls Emotion in Editing" (pacing)
- luvkilomedia — "Documentary Style Sound Design Like Netflix" (sound design)

---

## 1. Build order: story first, visuals last (Max McCoy)

Core belief: what separates a filmmaker from a videographer is structure and feeling, not gear, pretty shots or transitions.

1. **Sort** footage into buckets (short project: Dialogue + B-roll).
2. **Dialogue scrub (Cut 1)** — lay out every interview / vérité bite, remove obvious fat. Loose, not ruthless. (Example: 20 min → ~3 min.)
3. **Refine (Cut 2)** — duplicate the sequence, tighten hard toward target length (~3 min → 1:45). Start judging where lines *could* go. Add **text labels over each chunk** naming its theme.
4. **Skeleton** — reorder bites into a narrative spine. Act like a writer, not an editor. Simple 3-act frame: **setup → conflict → resolution**. Conflict can be an aspiration, a struggle, or an internal tension. Resolution can be emotional/musical rather than literal — "how do I want to leave the viewer?" Treat this stage as play; take breaks and return with fresh eyes.
5. **Audio-driven assembly (Build 1)** — add music, score, field recordings, SFX, silences. Edit it like a song with rise and fall. **Still no B-roll on the timeline.**
6. **Visuals + polish** — only now lay in B-roll, seek extra footage for variety/novelty.

Habit: duplicate the project/sequence at every stage (Cut 1, Cut 2, Skeleton, Build 1…) so nothing is lost and progress is visible.

## 2. Pacing: know when NOT to cut fast (Inside The Edit)

- Constant tight pacing (short-form reflex) removes weight. If everything moves at one speed, nothing matters.
- Long form should feel like a roller coaster: accelerate, then breathe; reveal, then withhold; tension, then release. Think **gears in a car** — motorway speed vs. town speed. Audiences crave **variation**.
- **Slow down at two places:**
  - **Complexity** — when a speaker packs dense ideas into one answer, don't simplify or leave it intact. **Split it into parts and insert space** (B-roll, music, a beat) so one idea lands at a time.
  - **Emotion** — non-actors don't know when they've said something big; the editor must. **Hold the shot longer before and after they speak, let reactions play, allow silence** rather than filling it.
- Editor = first audience. If it's hard to follow after 100 viewings, it's harder for a first-time viewer.
- Often the difference is just a few extra seconds in a few key places.

## 3. Sound design: Netflix-doc layering (luvkilomedia)

Sound is ~half the experience; strip it and the same intro goes flat.

**Layering rule:** build every big sound from **low + mid + high** (e.g. a swish = low whoosh + mid lighter/fire flicker + high whoosh).

| Element | Use | Level | Notes |
|---|---|---|---|
| Music | Fit the vibe; sync a musical hit to the title card | −5 to −25 dB | Fade in. Signature move: **reverb tail ending** (below) |
| Camera clicks / shutters | Image sequences, punchy hard cuts | −25 to −40 dB (often −30/−35) | Alternate 2+ click sounds; bigger camera-shot sound on the last image. Convert mono SFX to stereo so they're in both ears |
| Risers | Build tension into image bursts / big visual moments | ~−25 dB | End the riser on the impact |
| Whooshes / swishes | Any on-screen move (scale, rotation, position) | ~−25 dB | One go-to whoosh: slow to ~60% + pitch down ~5 semitones for the low layer; copy at ~120% + pitch up ~4–5 for the high layer. Peak on the movement. Light-leak/lighter sound on film-burn transitions (~−22 dB) |
| Bass hits | Release of tension, title cards | −25 / −30 / −35 dB layered | Start a few frames *before* the cut so it builds; tiny fade-in, earlier fade-out |
| Archival / news voice bed | Context (news, subject's real voice) under the sequence | ~−15 to −20 dB | Isolate voice with a vocal remover; split into segments; EQ to muffle slightly so it sits inside the mix; can act as a sound bridge off a title card |

**Reverb tail ending (music):** cut where the track should end → step back ~10 frames, split → duplicate that tail into a nested/compound clip → inside, extend a muted copy a few seconds so there's room → apply reverb (smallish room, ~4000 ms reverb time, 50% wet) → drop the nested clip ~−5 dB to match level.

Process truth: it's trial and error — audition, swap sounds that don't fit, adjust levels a few dB at a time.

Free sources mentioned: YouTube Audio Library, Freesound, Musicbed, a vocal remover tool.

---

## How I'll apply this to Francis's edits
- Rough cuts: dialogue-only spine first, labelled chunks, 3-act shape, then music, then B-roll.
- Reviews: flag spots where dense explanations need breathing room and where emotional beats get cut off too early; check pace varies.
- Sound pass: layered low/mid/high transitions, clicks on image sequences, riser → bass on title cards, reverb-tail music endings, levels in the ranges above.
- Short-form (9:16 ads) can stay tighter, but still use a deliberate pace change at the key beat.

---

## Motion graphics: HyperFrames is the default (added Nov 2026)

For every video edit request, motion graphics (titles, kinetic type, maps, charts, lower-thirds, end cards, explainer graphics) are built in **HyperFrames** (HeyGen, open source: HTML + CSS + GSAP rendered to MP4). Remotion only if Francis asks for it or the project is already in Remotion (offer /remotion-to-hyperframes to port it).

- Load the official skills first: hyperframes, hyperframes-cli, hyperframes-registry (search ready blocks before hand-building), media-use. Install with `npx skills add heygen-com/hyperframes --full-depth` if missing.
- Loop: init → write scenes → lint → check --json → snapshot and look → preview → render (draft, then delivery) → verify duration and frames.
- Cloud workspace: set `HYPERFRAMES_BROWSER_PATH=/usr/bin/google-chrome`; CDNs are blocked, so bundle GSAP locally in `assets/` via npm.
- All the structure, pacing and sound rules above still apply inside HyperFrames compositions.

---

## Footage cuts: FFmpeg (added Nov 2026)

Real footage editing (trims, assembly, J/L-cuts, transitions, music bed, loudness) is done with **FFmpeg**; HyperFrames stays on motion graphics. The final film = FFmpeg cut with HyperFrames graphics rendered and cut in as clips.

Tools (`tools/`):
- **scrub.py** — Cut 1 dialogue scrub. Runs silencedetect on an interview, keeps speech with 0.15 s handles, keeps natural pauses under 0.35 s, drops long silences, writes a cut list. `python3 scrub.py interview.mp4 -o cut1.json`
- **cutlist.py** — assembles any cut list. Frame-accurate trims, hard cuts or xfade transitions (dissolve, dip to black, wipes), J-cuts (`"j": seconds`), L-cuts (`"l": seconds`), per-clip speed and volume, mute, a looping music bed that ducks ~10 dB under dialogue, conform to 1920×1080/30 (or 9:16, 1:1), loudness to −14 LUFS / −1 dBTP. `python3 cutlist.py cuts.json -o cut.mp4` (`--dry-run` prints the timeline and command).

Workflow: scrub → Cut 1 JSON → edit the JSON by hand for Cut 2 / Skeleton (reorder, trim, pick endings) → add music + J/L-cuts for Build 1 → add B-roll and HyperFrames graphics clips for Picture. Save each stage's JSON (cut1.json, cut2.json, skeleton.json, build1.json) — the duplicate-every-stage habit.
Verified Nov 2026 on test footage: J-cut audio leads picture, L-cut audio trails into the next shot, dissolve/dip offsets correct, ducking works.

---
name: video-cleanup
description: Fix the audio and picture of talking-head / interview / product-demo videos and add simple branding, using ffmpeg plus an AI speech denoiser. Use this whenever someone wants a video's voice cleaned up (background noise, wind, hum, echo, too quiet, uneven or one-sided stereo, birds/traffic outdoors), a low-quality or re-compressed video (LinkedIn, WhatsApp, Instagram download) made to look better, or a logo watermark, name/title lower third or end card added — even if they just say "the audio is bad", "make this sound/look more professional", "it's too quiet on phones" or "add our branding". Also covers diagnosing what is wrong with a video's sound before fixing it.
compatibility: Needs ffmpeg/ffprobe, Python 3.9+ with numpy and Pillow, and the DeepFilterNet CLI (single ~30 MB binary; the scripts print the download link for the OS if missing). Works on Windows, macOS and Linux.
---

# Video cleanup

Clean up speech audio and picture quality in short videos and add light branding. Everything is
done by the scripts in `scripts/` (run them with `python`, from inside `scripts/` or by full path).
They never modify the input; they write new files next to it.

## Ground rules

- **Never overwrite or edit the user's original files.** Write new files with a descriptive suffix,
  e.g. `talk - audiofix (AI DeepFilterNet).mp4`, `talk - enhanced + branded v1.mp4`. If you make
  several attempts, keep each one with its own name so the user can compare them by ear/eye.
- **You can't hear or watch the video.** You measure audio (levels, noise, frequency balance) and
  look at extracted frames and spectrograms. Say so, and ask the user to judge with headphones,
  pointing them at specific timestamps.
- **Ask before downloading** anything (DeepFilterNet binary, a video from a URL, models), stating
  file name, source and size. Downloading a user's own video from LinkedIn/YouTube works with
  `yt-dlp` (`pip install yt-dlp`), but warn that platform copies are heavily re-compressed and
  the original export or raw phone files are always a better starting point — ask for those.

## Workflow

### 0. First-time setup

Run `python scripts/setup_tools.py --check`. If anything is missing, tell the user what will be
installed and ask before running `python scripts/setup_tools.py` (installs numpy/Pillow with pip
and downloads the DeepFilterNet binary from its official release, checksum-verified, to
`~/.video-cleanup/bin/`). Add `--install-ffmpeg` to install ffmpeg via winget/brew/apt.

### 1. Diagnose

```bash
python scripts/analyze.py "input.mp4"
```

It prints per-10-second levels, voice-above-background gap, low-rumble and high-frequency
share, left/right balance, silences, integrated loudness, and a recommended `--channel` mode.
Translate this into plain language for the user (e.g. "the voice drifts from left to right at
2:00", "it's 13 dB too quiet for phones", "wind rumble in the last minute"). For a visual check
you can render a spectrogram:
`ffmpeg -i in.mp4 -lavfi showspectrumpic=s=1600x600:legend=1:scale=log:fscale=log spec.png`
and extract a few frames (`ffmpeg -ss T -i in.mp4 -frames:v 1 f.png`) to see how it was filmed
(a speaker 2-4 m from the phone with no clip-on mic explains thin, echoey, noisy sound).

Rough reading of the voice-above-background gap: under 15 dB very noisy, 15-28 dB noticeable
background, above 28 dB fairly clean.

### 2. Fix the audio

```bash
python scripts/audiofix.py "input.mp4"                 # -> "input - audiofix (AI DeepFilterNet).mp4"
python scripts/audiofix.py "input.mp4" --wav-only -o clean.wav   # audio only, for step 3
```

What it does: folds stereo to mono (or picks one channel), 80 Hz high-pass for rumble,
DeepFilterNet AI noise removal with built-in delay compensation, a gentle EQ (mud cut, presence,
air), light compression, then two-pass loudness normalisation to -14 LUFS (social media level).
It then verifies against the original and prints loudness, the noise improvement, and the sync
offset. Anything over ~20 ms out of sync must be fixed before delivery.

Tuning:
- `--channel auto` (default) uses the analysis: when left and right are out of phase or unrelated,
  mixing them thins the voice, so it picks the cleaner side instead.
- `--strength 35` (default) is the noise-reduction ceiling in dB. Very noisy sources (gap < 12 dB)
  can turn watery; try `--strength 25`. Use 100 only if the user wants maximum removal.
- `--presence 4` for muffled recordings; `--target -16` if they want it a bit less loud.

Why this recipe: in testing, a pure-ffmpeg chain (afftdn + noise gate + heavier compression)
measured cleaner but sounded harsh and "corrupted" to the listener — gates clip word endings
and compression pumps the background. The AI denoiser with mild processing was preferred.
Don't swap back to gating to chase numbers.

Known limits to tell the user up front:
- Distant/echoey sound (no lavalier mic) improves only partly; a cheap wireless clip-on mic
  fixes it at the source for future shoots.
- **Birdsong and other whistle-like sounds survive.** Speech denoisers treat tonal chirps as
  voice-like. A second speech model (e.g. MossFormer2 via ClearerVoice) didn't help either, and a
  hand-written "chirp filter" ended up hitting the speaker's own voice. Real options: a
  text-queried separation model such as AudioSep ("birds chirping", ~3.6 GB of models — ask
  first), or the user gives timestamps and you attenuate 2.5-10 kHz only in those spots.
- Trimming leading/trailing silence also cuts the video; ask before doing it.

### 3. Improve the picture and add branding (optional)

```bash
python scripts/videofix.py "input.mp4" --audio clean.wav
python scripts/videofix.py "input.mp4" --audio clean.wav \
    --logo logo.png --logo-color white \
    --name "Jane Doe" --title "Head of Sales · Acme" --color "#1F2A5C" \
    --endcard endcard.png
```

Picture: light denoise/deblock (`hqdn3d`), Lanczos upscale so the short side is at least 1080
(platforms give higher-resolution uploads a better quality tier), contrast-adaptive sharpening,
and a gentle contrast/saturation lift for washed-out footage. Encoded with x264 CRF 18.

Do **not** use generative AI upscalers (Real-ESRGAN and similar) on real footage of people or
screens: in testing they invented fake text on a dashboard and added artifacts on a face, and
the lighter models made skin look plastic. Explain to the user that compression damage can be
softened but not undone; only the original file gets genuinely sharper results.

Branding — collect from the user before rendering:
- **Logo**: ask for a transparent PNG (or SVG to convert). If they have none but an existing video
  or image shows the logo on a plain background, cut it out:
  `python scripts/extract_logo.py --from-video other.mp4 --time -0.1 --crop x,y,w,h --out-dir brand`
  (check `brand/frame.png` first to choose the crop). Use `--logo-color white` over busy or dark
  footage. Match placement to the brand's existing videos if they have any (look at a few frames).
- **Lower third**: name and title — get the spelling and title from the user, or from the video's
  own captions/speech if clearly stated. Colour = brand colour (sample it from the logo if needed;
  `extract_logo.py` prints it). Check with a frame grab that it doesn't cover burned-in captions or
  the face; move it with `--lt-y` (fraction of height).
- **End card**: an image, or a segment of an existing brand video (`--endcard other.mp4
  --endcard-start 30.7 --endcard-dur 2.5`). Mention if the end card's language differs from the
  video's.

### 4. Verify and report

Before handing over, extract 4-6 frames across the output (including the lower third and the end
card) and look at them; re-check loudness with `ffmpeg -i out.mp4 -af ebur128 -f null -`.
Then tell the user:
- the output file names and what changed (before → after numbers: loudness, voice-above-background)
- what you could not fix and why (birds, burned-in captions can't be restyled, distance/echo,
  platform compression)
- what to listen/look for, with timestamps, and offer gentler/stronger variants.

## Sharing the results

Platforms and chat apps recompress video. To share without quality loss inside Microsoft 365 or
Google Workspace, upload to OneDrive/SharePoint/Drive and share a link with downloads allowed —
previews stream at reduced quality; downloading gets the original. Email attachments over
~20-25 MB fail anyway.

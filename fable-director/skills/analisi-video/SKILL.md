---
name: analisi-video
description: Use when a task involves video or audio files - analysing, comparing or summarising footage, storyboard vs shot material, an editing quote, a shot list, a transcription, or checking a voice-over script against its declared timing. Fixed procedure where every step costs zero model tokens until the top model reads the results; an external media model only for long footage or many files, with an open budget.
---

# Analisi video

Operating rule: **facts from ffprobe, reading from the top model, semantics from an external model only when the footage is too long to read.** The one fact everything hangs on — does this file have an audio track? — is never asked to a model: Gemini "transcribed" burnt-in subtitles on a file with no audio track (measured 2026-09-08).

Tools (all in `scripts/`, all print `STATUS:` / `OUTPUT:` / `DETAIL:`, exit non-zero on error, never a silent fallback):

| Step | Script | Cost | Measured |
|---|---|---|---|
| 1 | `video-sheet.sh <file> [--fps 1] [--cols 8] [--width 400] [--out DIR]` | zero | ffprobe card + contact sheet with burnt-in timestamps; 68 s → one 8×9 sheet the top model reads in one `Read`. Above 90 frames it splits into numbered sheets. 320 px or 1 frame / 2 s loses small on-screen text. |
| 2 | `transcribe.py <file> [--model small] [--lang it] [--out F.srt] [--json]` | zero (local CPU) | faster-whisper `small` int8: 68 s of Italian speech in 76 s (1.1× realtime), first model download 63 s. Refuses files without an audio track BEFORE loading the model. `medium` (~1.5 GB) announces its download. |
| 3 | `tts-timing.py --file SCRIPT [--voice it-IT-DiegoNeural] [--out DIR]` | zero (network) | edge-tts read of each block + ffprobe duration → table `label | slot | spoken | delta`. A 4 s slot needed 5.9 s of speech. Timing and animatics only: the production voice stays human. |
| 5 | `media-analyze.py --input FILE --spec "…" --schema-json --type video-shotlist [--out F]` | external model, **open budget required** | Gemini native `generateContent`: 10 MB mp4, 55 s → 75 s, 3.9k tokens in / 0.7k out, 9 shots with timestamps and on-screen text. Above `inline_max_mb` (19) the file goes through the Files API. `--model gemini-2.5-flash` is the explicit fallback on a 503. |

## Procedure

1. **`video-sheet.sh` on every file.** Read the `audio:` line first; it decides step 2. Keep the sheets: they are the evidence for every timestamp you quote.
2. **`transcribe.py` only on files whose `audio:` line is not `none`.** SRT by default, `--json` when you will compute on segments. Music-only tracks come back with 0 segments and say so.
3. **`tts-timing.py` if there is a voice-over script with declared slots** (client email, storyboard). Paste the script as blank-line-separated blocks with `# label (a-b s)` headers. Positive deltas are the first thing to tell the client.
4. **The top model reads sheets and transcripts and writes the scene table**: `timestamp | shot | on-screen text | reusable for`. Under ~10 minutes of total footage nothing else is needed — this is the whole job, and it is a reading job, not a delegation.
5. **`media-analyze.py` only when semantics are needed on long footage or many files** (axis 4: many similar items). Open the budget first: `fd-telemetry.py budget-open --task "…" --expected-output N --route external --type video-shotlist`. Always cross the result with the `AUDIO:` lines it prints (ffprobe), and treat any speech/music claim in the output as unverified. `--data-class restricted` blocks the route: client footage under NDA stays on steps 1-4.
6. **Deliverable**: scene table + work table with one letter per item — **G** shot material (exists, reusable), **M** motion graphics to make, **V** voice-over, **S** stock — plus the timing table from step 3 when a script exists. Reference layout: `~/Desktop/workspaces/pixelfarm/clienti/med-systems.it/docs/analisi-video-script-ilife-2026-09-08.md` §3 and §6.

## Boundaries

- Reading a contact sheet is top-model inline work (axis 1: visual judgment). Do not delegate it to a subagent that will re-pay the image tokens with a colder eye.
- Client-facing numbers (durations, hours, prices) are axis 2: they come from ffprobe, `tts-timing.py` and the top model's own table, never from the external model's prose.
- Setup: venv at `~/.claude/fable-director/tools/venv` (`python3 -m venv … && pip install faster-whisper edge-tts`), system `ffmpeg`/`ffprobe`, the `gemini-media` provider in `cross-family.json` (`external-exec.py --doctor` adds it and says so). Any missing piece is reported as `STATUS: unavailable` — say it to the user and stop, do not read the video "by eye" without saying so.

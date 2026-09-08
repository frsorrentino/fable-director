#!/usr/bin/env python3
"""transcribe.py — local speech-to-text with faster-whisper (zero model tokens).

  transcribe.py <file> [--model small|medium|large-v3] [--lang it] [--out F.srt]
                       [--json] [--words] [--no-cache] [--venv PATH]

Order of checks, cheapest first — none of them touches the model:
  1. ffprobe: no audio track → STATUS: error and exit 1. The model is NEVER
     asked whether there is speech (Gemini "transcribed" burnt-in subtitles
     on a file with no audio track, measured 2026-09-08).
  2. faster_whisper importable? If not, re-exec this same file with the
     venv python (~/.claude/fable-director/tools/venv). Venv missing →
     STATUS: unavailable with the install command, never a silent fallback.
  3. Model weights not in the local cache → a visible NOTE line before the
     download (small ~470 MB, medium ~1.5 GB, large-v3 ~3 GB); never silent.

Defaults: model small (int8, CPU; measured 1.25× realtime on Italian speech),
language auto-detected (--lang forces it), vad_filter=True, output
<basename>.srt in the current dir. --json writes/prints
{"segments":[{"start","end","text"}],"duration","language","model","elapsed"}.

--words adds word-level timestamps to the JSON segments
({"words":[{"start","end","word"}]}) — for syncing graphics to words, not
to sentences. SRT output is unchanged.

Cache: the transcript is stored under ~/.claude/fable-director/media-cache/
<sha1 of the file>/ keyed by model, language and --words; the same file in a
later session costs zero seconds of whisper and needs no venv at all
(CACHE: hit|miss|off line; --no-cache to bypass; FD_MEDIA_CACHE overrides
the directory).

Output (grep-able):
  CACHE: hit|miss|off
  STATUS: ok|unavailable|error
  OUTPUT: <srt or json path | ->
  DETAIL: <language, segments, elapsed, realtime factor>
Exit 0 only on STATUS ok.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VENV_DIR = Path.home() / ".claude" / "fable-director" / "tools" / "venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
HF_CACHE = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
MEDIA_CACHE = Path(os.environ.get("FD_MEDIA_CACHE")
                   or Path.home() / ".claude" / "fable-director" / "media-cache")
MODEL_SIZE_MB = {"tiny": 75, "base": 145, "small": 470, "medium": 1500,
                 "large-v2": 3000, "large-v3": 3000, "turbo": 1600, "large-v3-turbo": 1600}


def out(status, output="-", detail="-"):
    print(f"STATUS: {status}")
    print(f"OUTPUT: {output}")
    print(f"DETAIL: {detail}")


def die(detail, status="error", code=1):
    out(status, "-", detail)
    sys.exit(code)


def parse_args(argv):
    opts = {"--model": "small", "--lang": None, "--out": None, "--venv": None}
    flags = {"--json": False, "--words": False, "--no-cache": False}
    file = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        if a in flags:
            flags[a] = True
            i += 1
        elif a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
            i += 2
        elif a.startswith("-"):
            die(f"unrecognized argument: {a}")
        elif file is None:
            file = a
            i += 1
        else:
            die("only one input file")
    if not file:
        die("usage: transcribe.py <file> [--model small|medium] [--lang it] "
            "[--out F.srt] [--json] [--words] [--no-cache]")
    return file, opts, flags


def ffprobe_audio(path):
    """(has_audio, description). ffprobe missing → error, not a guess."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name,channels,sample_rate",
             "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        die("ffprobe not installed (apt install ffmpeg)")
    except subprocess.TimeoutExpired:
        die("ffprobe timeout")
    if r.returncode != 0:
        die(f"ffprobe cannot read {path}: {r.stderr.strip()[:200]}")
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        die("ffprobe returned non-JSON output")
    streams = data.get("streams") or []
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not streams:
        return False, "none", duration
    desc = " + ".join(f"{s.get('codec_name', '?')} {s.get('channels', '?')}ch "
                      f"{s.get('sample_rate', '?')} Hz" for s in streams)
    return True, desc, duration


def ensure_faster_whisper(opts):
    """Import, or re-exec with the venv python. Explicit, never silent."""
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    venv_py = (Path(opts["--venv"]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
               if opts["--venv"] else VENV_PY)
    if os.environ.get("FD_TRANSCRIBE_REEXEC") == "1":
        die(f"faster_whisper not importable even from {venv_py} — reinstall: "
            f"python3 \"{Path(__file__).with_name('media-doctor.py')}\" --setup",
            status="unavailable")
    if not venv_py.is_file():
        die("faster-whisper not installed and no venv at "
            f"{venv_py.parent.parent} — set it up (explicit, ~370 MB): python3 "
            f"\"{Path(__file__).with_name('media-doctor.py')}\" --setup "
            f"[--prefetch small]", status="unavailable")
    env = dict(os.environ, FD_TRANSCRIBE_REEXEC="1")
    os.execve(str(venv_py), [str(venv_py), str(Path(__file__).resolve())]
              + sys.argv[1:], env)


def cached(model):
    return (HF_CACHE / f"models--Systran--faster-whisper-{model}").is_dir()


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cache_file(path, opts, flags):
    """<media-cache>/<sha1>/transcript-<model>-<lang>[-words].json or None."""
    if flags["--no-cache"]:
        return None
    try:
        key = (f"transcript-{opts['--model']}-{opts['--lang'] or 'auto'}"
               + ("-words" if flags["--words"] else "") + ".json")
        return MEDIA_CACHE / sha1_of(path) / key
    except OSError:
        return None


def fmt_srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    file, opts, flags = parse_args(sys.argv[1:])
    path = Path(file)
    if not path.is_file():
        die(f"file not found: {file}")
    has_audio, audio_desc, duration = ffprobe_audio(str(path))
    print(f"file: {file}")
    print(f"audio: {audio_desc}")
    if not has_audio:
        die(f"no audio track in {path.name} (ffprobe) — nothing to transcribe, "
            f"model not loaded. Burnt-in text is read from the contact sheet "
            f"(video-sheet.sh), never inferred as speech.")

    model_name = opts["--model"]
    cf = cache_file(path, opts, flags)
    hit = None
    if cf is None:
        print("CACHE: off")
    elif cf.is_file():
        try:
            hit = json.loads(cf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hit = None
        print(f"CACHE: {'hit ' + str(cf.parent) if hit else 'miss'}")
    else:
        print("CACHE: miss")
    if hit:
        # Same file, same parameters: no whisper, no venv — a copy.
        emit(path, opts, flags, hit["segments"], hit.get("language", "?"),
             hit.get("language_probability"), hit.get("duration") or duration,
             model_name, 0.0, 0.0, from_cache=True)

    ensure_faster_whisper(opts)
    from faster_whisper import WhisperModel

    if not cached(model_name):
        size = MODEL_SIZE_MB.get(model_name)
        print(f"NOTE: model '{model_name}' not in {HF_CACHE} — downloading now"
              + (f" (~{size} MB, once)" if size else " (once)"), flush=True)
    t0 = time.time()
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    except Exception as e:  # download failure, unknown model, ...
        die(f"cannot load model '{model_name}': {str(e)[:200]}", status="unavailable")
    load_s = time.time() - t0

    t1 = time.time()
    try:
        segments_iter, info = model.transcribe(
            str(path), language=opts["--lang"], vad_filter=True,
            beam_size=5, condition_on_previous_text=False,
            word_timestamps=flags["--words"])
        segments = []
        for sg in segments_iter:
            item = {"start": round(sg.start, 3), "end": round(sg.end, 3),
                    "text": sg.text.strip()}
            if flags["--words"]:
                item["words"] = [{"start": round(w.start, 3), "end": round(w.end, 3),
                                  "word": w.word.strip()} for w in (sg.words or [])]
            segments.append(item)
    except Exception as e:
        die(f"transcription failed: {str(e)[:200]}")
    elapsed = time.time() - t1
    lang = getattr(info, "language", opts["--lang"] or "?")
    lang_p = getattr(info, "language_probability", None)
    audio_dur = getattr(info, "duration", duration) or duration
    if cf is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(
                {"file": str(path), "segments": segments, "language": lang,
                 "language_probability": (round(lang_p, 3) if lang_p else None),
                 "duration": round(float(audio_dur), 3), "model": model_name,
                 "elapsed": round(elapsed, 1)}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    emit(path, opts, flags, segments, lang, lang_p, audio_dur, model_name,
         elapsed, load_s)


def emit(path, opts, flags, segments, lang, lang_p, audio_dur, model_name,
         elapsed, load_s, from_cache=False):
    """Write SRT/JSON, print the receipt, exit 0."""
    base = path.stem
    if flags["--json"]:
        payload = {"file": str(path), "segments": segments,
                   "duration": round(float(audio_dur), 3), "language": lang,
                   "language_probability": (round(lang_p, 3) if lang_p else None),
                   "model": model_name, "elapsed": round(elapsed, 1),
                   "model_load_s": round(load_s, 1), "from_cache": from_cache}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        dest = opts["--out"]
    else:
        lines = []
        for i, s in enumerate(segments, 1):
            lines.append(f"{i}\n{fmt_srt_time(s['start'])} --> "
                         f"{fmt_srt_time(s['end'])}\n{s['text']}\n")
        text = "\n".join(lines) + ("\n" if lines else "")
        dest = opts["--out"] or f"{base}.srt"
    if dest:
        try:
            Path(dest).write_text(text, encoding="utf-8")
        except OSError as e:
            die(f"cannot write {dest}: {e}")
    rtf = (elapsed / audio_dur) if audio_dur else 0
    out("ok", dest or "-",
        f"language {lang}" + (f" (p={lang_p:.2f})" if lang_p else "")
        + f", {len(segments)} segments, {audio_dur:.0f} s audio, model "
        f"{model_name} int8, "
        + ("from cache (0 s)" if from_cache else f"{elapsed:.0f} s ({rtf:.2f}x realtime)")
        + (f", model load {load_s:.0f} s" if load_s > 5 else "")
        + (", word timestamps" if flags["--words"] else "")
        + ("" if segments else " — NO speech detected by VAD (music/silence?)"))
    if not dest:
        print("---")
        print(text)
    sys.exit(0)


if __name__ == "__main__":
    main()

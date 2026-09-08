#!/usr/bin/env python3
"""media-doctor.py — check and install the local pieces the media tools need.

  media-doctor.py                      # checklist, zero side effects
  media-doctor.py --setup              # create the venv + pip install (explicit, ~370 MB)
  media-doctor.py --setup --prefetch small|medium   # also download the whisper model now
  media-doctor.py --venv PATH          # non-default venv location

The plugin update ships the SCRIPTS (video-sheet.sh, transcribe.py,
tts-timing.py, media-analyze.py); it cannot ship these local dependencies:

  required  ffmpeg + ffprobe on PATH (all four scripts), with drawtext
            (fontconfig) for the timestamps on the contact sheet
  required  venv ~/.claude/fable-director/tools/venv with faster-whisper and
            edge-tts (transcribe.py, tts-timing.py) — --setup creates it
  optional  whisper model in the HF cache (first run downloads it:
            small ~470 MB in ~1 min, medium ~1.5 GB) — --prefetch does it now
  required  provider "type": "media" in cross-family.json with a key
            (media-analyze.py) — added by external-exec.py --doctor, which
            says so; this doctor only reports it
  optional  tesseract (offline OCR of a single frame; Gemini reads burnt-in
            text better, tesseract is the no-network fallback)

Nothing is installed without --setup, and --setup prints every command it
runs. apt/brew packages (ffmpeg) are never installed by this script: it
prints the command for your platform.

Exit 0 when every REQUIRED piece is present, 1 otherwise.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TOOLS_DIR = Path.home() / ".claude" / "fable-director" / "tools"
DEFAULT_VENV = TOOLS_DIR / "venv"
CONFIG_PATH = Path.home() / ".claude" / "fable-director" / "cross-family.json"
HF_CACHE = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
PACKAGES = ["faster-whisper", "edge-tts"]
MODEL_MB = {"tiny": 75, "base": 145, "small": 470, "medium": 1500, "large-v3": 3000}
HERE = Path(__file__).resolve().parent


def venv_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ffmpeg_install_hint():
    s = platform.system()
    if s == "Darwin":
        return "brew install ffmpeg"
    if s == "Windows":
        return "winget install Gyan.FFmpeg   (then reopen the terminal)"
    if shutil.which("apt"):
        return "sudo apt install ffmpeg"
    if shutil.which("dnf"):
        return "sudo dnf install ffmpeg"
    return "install ffmpeg with your package manager"


def run_quiet(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def parse_args(argv):
    opts = {"--venv": None, "--prefetch": None}
    flags = {"--setup": False}
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
        else:
            sys.exit(f"unrecognized argument: {a}\n{__doc__}")
    if opts["--prefetch"] and opts["--prefetch"] not in MODEL_MB:
        sys.exit(f"--prefetch: unknown model '{opts['--prefetch']}' "
                 f"(one of {', '.join(MODEL_MB)})")
    return opts, flags


def setup(venv, prefetch):
    """Create the venv and install the packages, every step printed."""
    print(f"== setup: venv {venv}")
    py = venv_python(venv)
    if not py.is_file():
        venv.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "venv", str(venv)]
        print("$ " + " ".join(cmd), flush=True)
        if subprocess.call(cmd) != 0:
            print("FAIL: venv creation failed (Debian/Ubuntu: sudo apt install python3-venv)")
            return False
    else:
        print(f"venv already present: {py}")
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q"] + PACKAGES
    print(f"$ {' '.join(cmd)}   (CPU wheels, ~370 MB, needs network)", flush=True)
    if subprocess.call(cmd) != 0:
        print("FAIL: pip install failed — see the output above")
        return False
    if prefetch:
        print(f"== prefetch whisper model '{prefetch}' (~{MODEL_MB[prefetch]} MB, "
              f"into {HF_CACHE})", flush=True)
        code = ("from faster_whisper import WhisperModel; "
                f"WhisperModel({prefetch!r}, device='cpu', compute_type='int8'); "
                "print('model ready')")
        if subprocess.call([str(py), "-c", code]) != 0:
            print("FAIL: model download failed — transcribe.py will retry on first use")
            return False
    print("== setup done — re-running the checklist\n")
    return True


def doctor(venv):
    print("FABLE-DIRECTOR — media tools doctor")
    problems = 0

    def line(ok, name, msg, required=True):
        nonlocal problems
        if not ok and required:
            problems += 1
        mark = "OK " if ok else ("FAIL" if required else "opt ")
        print(f"[{mark}] {name}: {msg}")

    # ffmpeg / ffprobe / drawtext
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        rc, out = run_quiet([ff, "-version"])
        ver = out.splitlines()[0].split(" ")[2] if rc == 0 and out else "?"
        rc, out = run_quiet([ff, "-v", "error", "-f", "lavfi", "-i",
                             "color=c=black:s=64x64:d=0.1", "-vf", "drawtext=text=x",
                             "-frames:v", "1", "-f", "null", "-"])
        if rc == 0:
            line(True, "ffmpeg", f"{ver}, ffprobe present, drawtext OK")
        else:
            line(False, "ffmpeg", f"{ver} present but drawtext FAILS (no fontconfig/"
                 f"libfreetype build?) — video-sheet.sh cannot burn timestamps: "
                 f"{out.strip()[:120]}")
    else:
        line(False, "ffmpeg", f"{'ffmpeg' if not ff else 'ffprobe'} NOT on PATH — "
             f"{ffmpeg_install_hint()}")

    # venv + packages
    py = venv_python(venv)
    setup_cmd = f"python3 \"{HERE / 'media-doctor.py'}\" --setup"
    if py.is_file():
        rc, out = run_quiet([str(py), "-c",
                             "import faster_whisper, edge_tts; "
                             "print(faster_whisper.__version__, edge_tts.__version__)"])
        if rc == 0:
            fw, et = (out.split() + ["?", "?"])[:2]
            line(True, "venv", f"{venv} — faster-whisper {fw}, edge-tts {et}")
        else:
            line(False, "venv", f"{venv} exists but packages missing/broken "
                 f"({out.strip().splitlines()[-1][:100] if out.strip() else 'import failed'}) — {setup_cmd}")
    else:
        line(False, "venv", f"missing at {venv} — {setup_cmd}  (~370 MB)")

    # whisper models in cache
    cached = sorted(p.name.replace("models--Systran--faster-whisper-", "")
                    for p in HF_CACHE.glob("models--Systran--faster-whisper-*") if p.is_dir())
    if cached:
        line(True, "whisper models", f"cached: {', '.join(cached)} ({HF_CACHE})", required=False)
    else:
        line(False, "whisper models", f"none cached — first transcribe.py run downloads "
             f"'small' (~470 MB, ~1 min); now: {setup_cmd} --prefetch small", required=False)

    # media provider
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError) as e:
            cfg = {}
            line(False, "gemini-media", f"config unreadable: {e}")
        media = {n: p for n, p in (cfg.get("providers") or {}).items()
                 if isinstance(p, dict) and p.get("type") == "media"}
        if media:
            for n, p in media.items():
                key = p.get("api_key") or os.environ.get(p.get("api_key_env", ""), "")
                line(bool(key), n, f"{p.get('model', '?')}, billing {p.get('billing', 'UNDECLARED=paid')}, "
                     + ("API key present" if key else
                        f"API key MISSING — export {p.get('api_key_env')}=... or api_key in config"))
        else:
            line(False, "gemini-media", "no \"type\": \"media\" provider in "
                 f"{CONFIG_PATH.name} — python3 \"{HERE / 'external-exec.py'}\" --doctor "
                 "adds it (and says so)")
    else:
        line(False, "gemini-media", f"no {CONFIG_PATH} — python3 \"{HERE / 'cross-verify.py'}\" "
             "--init (template includes gemini-media), then put the Gemini key")

    # tesseract (optional)
    tess = shutil.which("tesseract")
    if tess:
        rc, out = run_quiet([tess, "--list-langs"])
        langs = [l.strip() for l in out.splitlines()[1:] if l.strip()] if rc == 0 else []
        line(True, "tesseract", f"present, langs: {' '.join(langs) or '?'} (offline frame OCR)",
             required=False)
    else:
        line(False, "tesseract", "absent — optional, offline OCR of single frames only "
             "(apt install tesseract-ocr tesseract-ocr-ita)", required=False)

    print(f"\nresult: {'media tools ready' if not problems else str(problems) + ' required piece(s) missing'}"
          + ("" if problems else " — skill analisi-video, scripts video-sheet.sh / "
             "transcribe.py / tts-timing.py / media-analyze.py"))
    return problems


def main():
    opts, flags = parse_args(sys.argv[1:])
    venv = Path(opts["--venv"]).expanduser() if opts["--venv"] else DEFAULT_VENV
    if flags["--setup"]:
        if not setup(venv, opts["--prefetch"]):
            sys.exit(1)
    elif opts["--prefetch"]:
        sys.exit("--prefetch only makes sense with --setup")
    sys.exit(1 if doctor(venv) else 0)


if __name__ == "__main__":
    main()

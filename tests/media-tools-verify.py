#!/usr/bin/env python3
"""Media tools 1.41.x — deterministic checks on the four scripts + doctor + config.

Runs the REAL scripts on synthetic media (ffmpeg testsrc + sine, 5 s) against
a throwaway HOME. No model is ever called: the external route is exercised
only up to its guards (budget gate, data-class, key, provider type).

  M1  video-sheet.sh: ffprobe card + sheet jpg with the expected geometry
  M2  video-sheet.sh on a file without audio → "audio: none"
  M3  video-sheet.sh --max 2 → numbered sheets
  M4  transcribe.py on a file without audio → STATUS error BEFORE any model
  M5  transcribe.py with audio but no venv → STATUS unavailable (order proof)
  M6  transcribe.py on the real venv (skipped if absent) → STATUS ok
  M7  tts-timing.py block/slot parsing (offline, deterministic)
  M8  tts-timing.py --text short → ok with mp3, or clean unavailable offline
  M9  media-analyze.py without budget → gate error, no config read
  M10 media-analyze.py with --data-class restricted → blocked
  M11 media-analyze.py with media provider but no key → unavailable
  M12 media-analyze.py on a non-media provider → error pointing to external-exec
  M13 external-exec.py --provider <media> → error pointing to media-analyze
  M14 external-exec.py --doctor adds gemini-media once, explicitly, with backup
  M15 cross-verify.py --init template ships gemini-media (type media)
  M16 media-doctor.py on an empty HOME → FAIL lines with the --setup command, exit 1
  M17 media-doctor.py on the real HOME → exit 0 when the venv exists (else skipped)
  M18 soft-deps-sync.py: creates from template / adds missing once / never restores a deletion
  M19 video-sheet.sh --scenes: one frame per cut + the cut times
  M20 video-sheet.sh cache: second run is a CACHE: hit with the jpg copied
  M21 transcribe.py cache: seeded transcript → STATUS ok with NO venv, srt written
  M22 fd-telemetry.py report: media route line from a logged external_exec event

Usage: python3 tests/media-tools-verify.py   (exit 0 = all green)
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
REAL_VENV = Path.home() / ".claude" / "fable-director" / "tools" / "venv"

passed, failed, skipped = [], [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence[:600]}"))


def skip(name, why):
    skipped.append(name)
    print(f"SKIP  {name} — {why}")


def slug(cwd):
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def run(args, home, cwd, timeout=180, env_extra=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("GEMINI_API_KEY", None)
    env.update(env_extra or {})
    return subprocess.run(args, capture_output=True, env=env, cwd=cwd,
                          timeout=timeout, encoding="utf-8", errors="replace")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dims(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return r.stdout.strip()


def make_media(d):
    """5 s testsrc 320x240 with a 440 Hz sine (aac) and the same without audio."""
    with_a = d / "with-audio.mp4"
    no_a = d / "no-audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440", "-t", "5", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(with_a)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=25", "-t", "5", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-an", str(no_a)], check=True)
    return with_a, no_a


def open_budget(home, proj, extra=None):
    bdir = home / ".claude" / "fable-director" / "budgets"
    bdir.mkdir(parents=True, exist_ok=True)
    b = {"status": "open", "declared_at": datetime.now(timezone.utc).isoformat(),
         "route": "external"}
    b.update(extra or {})
    (bdir / f"{slug(proj)}.json").write_text(json.dumps(b))


def write_config(home, providers, default=None):
    cdir = home / ".claude" / "fable-director"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "cross-family.json").write_text(json.dumps(
        {"default": default or next(iter(providers)), "providers": providers}))


def main():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("FAIL: ffmpeg/ffprobe not installed — the media suite needs them")
        sys.exit(1)
    work = Path(tempfile.mkdtemp(prefix="fd-media-"))
    home = work / "home"
    proj = work / "proj"
    home.mkdir()
    proj.mkdir()
    with_a, no_a = make_media(work)
    sheet_sh = str(SCRIPTS / "video-sheet.sh")
    transcribe = str(SCRIPTS / "transcribe.py")
    tts = str(SCRIPTS / "tts-timing.py")
    media = str(SCRIPTS / "media-analyze.py")
    xexec = str(SCRIPTS / "external-exec.py")
    xverify = str(SCRIPTS / "cross-verify.py")

    # M1 — sheet geometry: 5 frames @1 fps, 8 cols → 5x1 tiles of 400x300.
    r = run(["bash", sheet_sh, str(with_a), "--out", str(proj / "s1")], home, proj)
    jpg = proj / "s1" / "with-audio-sheet.jpg"
    check("M1 video-sheet: card + jpg 2000x300, audio aac 1ch",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok"
          and jpg.is_file() and dims(jpg) == "2000,300"
          and "audio: aac 1ch 44100 Hz" in r.stdout
          and "duration: 5.0 s" in r.stdout and "video: h264 320x240" in r.stdout,
          r.stdout + r.stderr + f" dims={dims(jpg) if jpg.is_file() else '-'}")

    # M2 — no audio track → the fact is on the audio line and in DETAIL.
    r = run(["bash", sheet_sh, str(no_a), "--out", str(proj / "s2")], home, proj)
    check("M2 video-sheet: audio: none on a silent file",
          r.returncode == 0 and "\naudio: none\n" in r.stdout
          and field(r.stdout, "DETAIL").startswith("audio: none"),
          r.stdout + r.stderr)

    # M3 — split: 5 frames, --max 2 → 3 sheets of 2 columns (800 px wide).
    r = run(["bash", sheet_sh, str(with_a), "--max", "2", "--out", str(proj / "s3")], home, proj)
    sheets = sorted((proj / "s3").glob("with-audio-sheet-*.jpg"))
    check("M3 video-sheet: --max 2 → 3 numbered sheets, 800 px wide",
          r.returncode == 0 and len(sheets) == 3
          and all(dims(s).startswith("800,") for s in sheets)
          and field(r.stdout, "OUTPUT").count(".jpg") == 3,
          r.stdout + r.stderr + f" sheets={[s.name for s in sheets]}")

    # M4 — no audio: error from ffprobe, model never loaded (empty HOME = no venv).
    t0 = time.time()
    r = run([sys.executable, transcribe, str(no_a)], home, proj)
    el = time.time() - t0
    check("M4 transcribe: no audio track → STATUS error, no model, fast",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "no audio track" in r.stdout and "NOTE:" not in r.stdout and el < 20,
          r.stdout + r.stderr + f" elapsed={el:.1f}")

    # M5 — with audio but no venv in this HOME → unavailable naming the venv.
    r = run([sys.executable, transcribe, str(with_a)], home, proj,
            env_extra={"FD_TRANSCRIBE_REEXEC": ""})
    check("M5 transcribe: audio ok, venv missing → STATUS unavailable (audio checked first)",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "venv" in r.stdout and "audio: aac 1ch" in r.stdout,
          r.stdout + r.stderr)

    # M6 — real venv: transcribe a sine → ok (0 segments is the honest answer).
    if (REAL_VENV / "bin" / "python").is_file():
        r = run([sys.executable, transcribe, str(with_a), "--json", "--out",
                 str(proj / "t.json")], Path.home(), proj, timeout=600)
        ok = r.returncode == 0 and field(r.stdout, "STATUS") == "ok"
        if ok:
            try:
                d = json.loads((proj / "t.json").read_text())
                ok = "segments" in d and d.get("model") == "small" and "elapsed" in d
            except (OSError, json.JSONDecodeError):
                ok = False
        check("M6 transcribe: real venv, sine → STATUS ok + json with segments/model/elapsed",
              ok, r.stdout + r.stderr)
    else:
        skip("M6 transcribe on real venv", f"no venv at {REAL_VENV}")

    # M7 — parsing (deterministic, offline).
    mod = load_mod("tts_timing", tts)
    blocks = mod.parse_blocks(
        "# 1 Attacco (0-4s)\nriga uno\nriga due\n\n# 2 Scarsità (4–9 s)\ntesto\n\n"
        "# 3 Fisso (5s)\ntesto\n\nsenza etichetta\n\n\n\n")
    check("M7 tts-timing: blocks, labels and slots parsed",
          [b["label"] for b in blocks] == ["1 Attacco (0-4s)", "2 Scarsità (4–9 s)",
                                           "3 Fisso (5s)", "block 4"]
          and [b["slot"] for b in blocks] == [4.0, 5.0, 5.0, None]
          and blocks[0]["text"] == "riga uno riga due",
          json.dumps(blocks, ensure_ascii=False))

    # M8 — real synthesis, or a clean unavailable when offline / no venv.
    r = run([sys.executable, tts, "--text", "Prova breve.", "--out", str(proj / "tts")],
            Path.home(), proj, timeout=120)
    st = field(r.stdout, "STATUS")
    if st == "unavailable":
        skip("M8 tts-timing real synthesis", field(r.stdout, "DETAIL")[:120])
    else:
        mp3 = proj / "tts" / "01.mp3"
        tj = proj / "tts" / "timing.json"
        ok = (r.returncode == 0 and st == "ok" and mp3.is_file() and mp3.stat().st_size > 0
              and tj.is_file() and len(json.loads(tj.read_text())["blocks"]) == 1
              and "| text" in r.stdout and "TOTAL" in r.stdout)
        check("M8 tts-timing: --text → mp3 + timing.json + table", ok, r.stdout + r.stderr)

    # M9 — gate first: no budget → error naming budget-open, config never needed.
    r = run([sys.executable, media, "--input", str(no_a), "--spec", "x"], home, proj)
    check("M9 media-analyze: no budget → gate error, exit 1",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "no open pre-budget" in r.stdout, r.stdout + r.stderr)

    # M10 — restricted data class blocks the external route.
    open_budget(home, proj, {"data_class": "restricted"})
    r = run([sys.executable, media, "--input", str(no_a), "--spec", "x"], home, proj)
    check("M10 media-analyze: --data-class restricted → BLOCKED",
          r.returncode == 1 and "BLOCKED" in r.stdout, r.stdout + r.stderr)

    # M11 — budget ok, media provider without key → unavailable, AUDIO not yet printed.
    open_budget(home, proj)
    write_config(home, {
        "gemini-media": {"type": "media", "base_url": "https://127.0.0.1:9/v1beta",
                         "model": "m", "api_key_env": "FD_TEST_NO_SUCH_KEY",
                         "billing": "free", "inline_max_mb": 19},
        "gemini": {"base_url": "https://127.0.0.1:9/v1beta/openai", "model": "m",
                   "api_key_env": "FD_TEST_NO_SUCH_KEY", "billing": "free"},
    }, default="gemini")
    r = run([sys.executable, media, "--input", str(no_a), "--spec", "x"], home, proj)
    check("M11 media-analyze: media provider auto-picked, key missing → unavailable",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "API key missing for 'gemini-media'" in r.stdout
          and "AUDIO:" not in r.stdout, r.stdout + r.stderr)

    # M12 — a text provider is refused by media-analyze.
    r = run([sys.executable, media, "--input", str(no_a), "--spec", "x",
             "--provider", "gemini"], home, proj)
    check("M12 media-analyze: non-media provider → error pointing to external-exec",
          r.returncode == 1 and "not 'media'" in r.stdout and "external-exec.py" in r.stdout,
          r.stdout + r.stderr)

    # M13 — and the reverse: external-exec refuses the media provider.
    r = run([sys.executable, xexec, "--spec", "x", "--provider", "gemini-media"], home, proj)
    check("M13 external-exec: media provider → error pointing to media-analyze",
          r.returncode == 1 and "media-analyze.py" in r.stdout, r.stdout + r.stderr)

    # M14 — doctor adds gemini-media once, says so, keeps a backup; idempotent.
    home2 = work / "home2"
    write_config(home2, {"gemini": {"base_url": "x", "model": "m", "api_key": "k-test",
                                    "api_key_env": "GEMINI_API_KEY", "billing": "free"}})
    r1 = run([sys.executable, xexec, "--doctor"], home2, proj)
    cfg = json.loads((home2 / ".claude" / "fable-director" / "cross-family.json").read_text())
    r2 = run([sys.executable, xexec, "--doctor"], home2, proj)
    gm = cfg["providers"].get("gemini-media") or {}
    check("M14 doctor: [added] gemini-media once, key copied, backup written, idempotent",
          "[added] gemini-media" in r1.stdout and gm.get("type") == "media"
          and gm.get("api_key") == "k-test" and "api_key copied from 'gemini'" in r1.stdout
          and (home2 / ".claude" / "fable-director" / "cross-family.json.bak-pre-media").is_file()
          and "[added]" not in r2.stdout and "[OK ] gemini-media" in r2.stdout,
          r1.stdout + r2.stdout + r1.stderr)

    # M15 — fresh --init template carries the media provider.
    home3 = work / "home3"
    home3.mkdir()
    r = run([sys.executable, xverify, "--init"], home3, proj)
    cfg = json.loads((home3 / ".claude" / "fable-director" / "cross-family.json").read_text())
    gm = cfg["providers"].get("gemini-media") or {}
    check("M15 cross-verify --init: gemini-media type media, inline_max_mb 19, billing free",
          r.returncode == 0 and gm.get("type") == "media" and gm.get("inline_max_mb") == 19
          and gm.get("billing") == "free" and gm.get("api_key_env") == "GEMINI_API_KEY",
          r.stdout + r.stderr)

    # M16 — doctor reports, never installs: empty HOME → venv FAIL + setup command.
    doctor = str(SCRIPTS / "media-doctor.py")
    home4 = work / "home4"
    home4.mkdir()
    r = run([sys.executable, doctor], home4, proj)
    check("M16 media-doctor: empty HOME → [FAIL] venv with --setup command, exit 1, nothing created",
          r.returncode == 1 and "[FAIL] venv: missing" in r.stdout and "--setup" in r.stdout
          and "[FAIL] gemini-media" in r.stdout and "required piece(s) missing" in r.stdout
          and not (home4 / ".claude" / "fable-director" / "tools").exists(),
          r.stdout + r.stderr)

    # M17 — real HOME: ready when the venv exists.
    if (REAL_VENV / "bin" / "python").is_file():
        r = run([sys.executable, doctor], Path.home(), proj)
        check("M17 media-doctor: real HOME with venv → [OK ] venv, ffmpeg drawtext OK",
              "[OK ] venv" in r.stdout and "[OK ] ffmpeg" in r.stdout and "drawtext OK" in r.stdout,
              r.stdout + r.stderr)
    else:
        skip("M17 media-doctor on real HOME", f"no venv at {REAL_VENV}")

    # M18 — soft-deps sync: create, add-once, respect deletions.
    sync = str(SCRIPTS / "soft-deps-sync.py")
    home5 = work / "home5"
    home5.mkdir()
    r1 = run([sys.executable, sync], home5, proj)
    live_p = home5 / ".claude" / "fable-director" / "soft-deps.json"
    created = json.loads(live_p.read_text()) if live_p.is_file() else {}
    r2 = run([sys.executable, sync], home5, proj)
    # user file with own entries and no media-tools → added, own entry untouched
    live_p.write_text(json.dumps({"_schema": "mine", "my-tool": {"kind": "cli", "classes": ["x"]}}))
    r3 = run([sys.executable, sync], home5, proj)
    merged = json.loads(live_p.read_text())
    # user deletes media-tools → must not come back
    deleted = {k: v for k, v in merged.items() if k != "media-tools"}
    live_p.write_text(json.dumps(deleted))
    r4 = run([sys.executable, sync], home5, proj)
    after = json.loads(live_p.read_text())
    check("M18 soft-deps-sync: created silently / added once with a line / deletion respected",
          r1.stdout.strip() == "" and "media-tools" in created
          and created.get("_synced") == ["media-tools"] and r2.stdout.strip() == ""
          and "+media-tools from the plugin template" in r3.stdout and merged.get("_schema") == "mine"
          and merged.get("my-tool") == {"kind": "cli", "classes": ["x"]}
          and "media-tools" in merged and r4.stdout.strip() == "" and "media-tools" not in after,
          r1.stdout + r2.stdout + r3.stdout + r4.stdout)

    # M19 — scene mode on a 3-colour concat (cuts at 2 s and 4 s).
    cuts = work / "cuts.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=red:s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "color=blue:s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "color=green:s=320x240:d=1:r=25",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p[v]",
                    "-map", "[v]", "-c:v", "libx264", str(cuts)], check=True)
    r = run(["bash", sheet_sh, str(cuts), "--scenes", "0.3", "--no-cache", "--out", str(proj / "s19")], home, proj)
    sj = proj / "s19" / "cuts-scenes.jpg"
    check("M19 video-sheet --scenes: 2 cuts at 2.0 and 4.0 s → 3x1 sheet, CACHE: off",
          r.returncode == 0 and "scenes: 2 cuts (threshold 0.3) at 0.0, 2.0, 4.0 s" in r.stdout
          and sj.is_file() and dims(sj) == "1200,300" and "CACHE: off" in r.stdout,
          r.stdout + r.stderr)

    # M20 — sheet cache: miss, then hit with the file copied to the new --out.
    cache_dir = work / "mcache"
    env_c = {"FD_MEDIA_CACHE": str(cache_dir)}
    r1 = run(["bash", sheet_sh, str(with_a), "--out", str(proj / "c1")], home, proj, env_extra=env_c)
    r2 = run(["bash", sheet_sh, str(with_a), "--out", str(proj / "c2")], home, proj, env_extra=env_c)
    check("M20 video-sheet cache: miss then hit, jpg copied, same OUTPUT name",
          "CACHE: miss" in r1.stdout and "CACHE: hit" in r2.stdout
          and (proj / "c2" / "with-audio-sheet.jpg").is_file()
          and field(r2.stdout, "OUTPUT").endswith("c2/with-audio-sheet.jpg")
          and "from cache" in field(r2.stdout, "DETAIL"),
          r1.stdout + r2.stdout + r1.stderr + r2.stderr)

    # M21 — transcript cache seeded by hand: ok with no venv at all.
    import hashlib as _h
    sha = _h.sha1(with_a.read_bytes()).hexdigest()
    seed = cache_dir / sha / "transcript-small-auto.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps({"segments": [{"start": 0.5, "end": 1.2, "text": "ciao"}],
                                "language": "it", "language_probability": 0.9,
                                "duration": 5.0, "model": "small"}))
    r = run([sys.executable, transcribe, str(with_a), "--out", str(proj / "cached.srt")],
            home, proj, env_extra=env_c)
    srt = proj / "cached.srt"
    check("M21 transcribe cache: seeded hit → STATUS ok without venv, srt from cache",
          r.returncode == 0 and "CACHE: hit" in r.stdout and field(r.stdout, "STATUS") == "ok"
          and "from cache (0 s)" in r.stdout and srt.is_file()
          and "00:00:00,500 --> 00:00:01,200" in srt.read_text() and "ciao" in srt.read_text(),
          r.stdout + r.stderr)

    # M22 — report: a media external_exec event yields the media route line.
    tele = str(SCRIPTS / "fd-telemetry.py")
    home6 = work / "home6"
    (home6 / ".claude" / "fable-director").mkdir(parents=True)
    code = ("import importlib.util,sys; sp=importlib.util.spec_from_file_location('t', sys.argv[1]);"
            "m=importlib.util.module_from_spec(sp); sp.loader.exec_module(m);"
            "m.log_event('external_exec', {'provider':'gemini-media','model':'m','kind':'media',"
            "'billing':'free','type':'video-shotlist','inputs':1,'bytes_in':10485760,"
            "'transport':'inline','tokens_in':4000,'tokens_out':700,'elapsed':60,'ok':True,"
            "'check':'json-valid'})")
    r0 = run([sys.executable, "-c", code, tele], home6, proj)
    r = run([sys.executable, tele, "report", "--days", "1"], home6, proj)
    check("M22 report: media route line with runs, MB, ok-rate, avg tokens",
          r0.returncode == 0 and "media route" in r.stdout
          and re.search(r"video-shotlist: 1 run, 1 file\(s\), 10 MB, ok-rate 1\.00, avg 4[.,]000 in / 700 out, 60 s, inline", r.stdout) is not None,
          r0.stdout + r0.stderr + r.stdout[-1500:] + r.stderr[-500:])

    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()

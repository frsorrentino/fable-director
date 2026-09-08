#!/usr/bin/env bash
# video-sheet.sh — ffprobe card + contact sheet(s) with burnt-in timestamps.
# Zero model tokens: the fact that starts every media task ("does this file
# have an audio track?") comes from ffprobe here, never from a model (Gemini
# declared speech on a file with no audio track, measured 2026-09-08).
#
#   video-sheet.sh <file> [--fps N | --scenes T] [--cols N] [--width PX]
#                  [--out DIR] [--max N] [--no-cache]
#
# Defaults: fps 1, 8 columns, 400 px per frame, output in the current dir,
# at most 90 frames per sheet (above that the video is split into numbered
# sheets <basename>-sheet-1.jpg, -2.jpg, ...). Measured: 400 px / 1 fps is
# readable by the top model in ONE Read; 320 px or 1 frame every 2 s loses
# small on-screen text.
#
# --scenes T  one frame per CUT instead of a fixed rate: ffmpeg's scene score
#             (0..1; 0.3 = clear cuts, 0.15 = also soft transitions) selects
#             the first frame of every shot, plus frame 0. The sheet is then a
#             shot list at zero cost, and the "scenes:" line lists the cut
#             times for the scene table. Costs two decode passes.
#
# Cache: sheets are stored under ~/.claude/fable-director/media-cache/<sha1
# of the file>/ keyed by the parameters; the same file in a later session
# costs a copy, not a render (CACHE: hit|miss line; --no-cache to bypass;
# FD_MEDIA_CACHE overrides the directory).
#
# Output (grep-able, same shape as external-exec.py):
#   file / duration / video / audio: none|<codec> <ch>ch <rate> Hz / frames
#   [scenes: N cuts at t1, t2, ...]   (--scenes only)
#   CACHE: hit|miss|off
#   STATUS: ok|error
#   OUTPUT: <sheet.jpg>[, <sheet-2.jpg> ...]
#   DETAIL: audio: none|<codec> <ch>ch — <n> sheet(s) <cols>x<rows> @ <px>px
# Exit 0 only on STATUS ok. Requires ffmpeg + ffprobe (+ fontconfig for drawtext).
set -uo pipefail

FPS=1; COLS=8; WIDTH=400; OUTDIR="."; MAXF=90; FILE=""; SCENES=""; USE_CACHE=1

fail() { echo "STATUS: error"; echo "OUTPUT: -"; echo "DETAIL: $*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --fps)    FPS="${2:?--fps needs a value}"; shift 2 ;;
    --scenes) SCENES="${2:?--scenes needs a threshold (e.g. 0.3)}"; shift 2 ;;
    --cols)   COLS="${2:?--cols needs a value}"; shift 2 ;;
    --width)  WIDTH="${2:?--width needs a value}"; shift 2 ;;
    --out)    OUTDIR="${2:?--out needs a value}"; shift 2 ;;
    --max)    MAXF="${2:?--max needs a value}"; shift 2 ;;
    --no-cache) USE_CACHE=0; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    -*)       fail "unrecognized argument: $1" ;;
    *)        [ -z "$FILE" ] && FILE="$1" || fail "only one input file"; shift ;;
  esac
done

[ -n "$FILE" ] || fail "usage: video-sheet.sh <file> [--fps N | --scenes T] [--cols N] [--width PX] [--out DIR]"
[ -f "$FILE" ] || fail "file not found: $FILE"
command -v ffprobe >/dev/null || fail "ffprobe not installed (apt install ffmpeg)"
command -v ffmpeg  >/dev/null || fail "ffmpeg not installed (apt install ffmpeg)"
mkdir -p "$OUTDIR" || fail "cannot create --out dir: $OUTDIR"

# ---- ffprobe card -----------------------------------------------------------
DURATION=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$FILE" 2>/dev/null | head -1)
[ -n "$DURATION" ] && [ "$DURATION" != "N/A" ] || fail "ffprobe cannot read a duration from $FILE (not a media file?)"
BITRATE=$(ffprobe -v error -show_entries format=bit_rate -of default=nw=1:nk=1 "$FILE" 2>/dev/null | head -1)
# key=value output: the csv writer follows the stream's own field order
# (sample_rate before channels), which misread "aac 44100ch 1 Hz" once.
VIDEO=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,avg_frame_rate -of default=nw=1 "$FILE" 2>/dev/null)
[ -n "$VIDEO" ] || fail "no video stream in $FILE (audio-only? use transcribe.py)"
kv() { echo "$1" | awk -F= -v k="$2" '$1==k {print $2; exit}'; }
VCODEC=$(kv "$VIDEO" codec_name); VW=$(kv "$VIDEO" width); VH=$(kv "$VIDEO" height); VRATE=$(kv "$VIDEO" avg_frame_rate)
VFPS=$(awk -v r="$VRATE" 'BEGIN{split(r,a,"/"); if (a[2]+0>0) printf "%.2f", a[1]/a[2]; else print r}')
# Every audio stream: [STREAM] blocks, one per track.
AUDIO_ROWS=$(ffprobe -v error -select_streams a -show_entries stream=codec_name,channels,sample_rate -of default "$FILE" 2>/dev/null)
if [ -z "$AUDIO_ROWS" ]; then
  AUDIO_LINE="none"
else
  AUDIO_LINE=$(echo "$AUDIO_ROWS" | awk -F= '
    /^\[STREAM\]/ {c=""; ch=""; sr=""}
    $1=="codec_name" {c=$2} $1=="channels" {ch=$2} $1=="sample_rate" {sr=$2}
    /^\[\/STREAM\]/ {printf "%s%s %sch %s Hz", (n++?" + ":""), c, ch, sr}')
fi
MBIT=$(awk -v b="${BITRATE:-0}" 'BEGIN{ if (b+0>0) printf "%.2f Mbit/s", b/1000000; else print "n/a"}')

echo "file: $FILE"
printf "duration: %.1f s\n" "$DURATION"
echo "video: $VCODEC ${VW}x${VH} ${VFPS} fps, $MBIT"
echo "audio: $AUDIO_LINE"

BASE=$(basename "$FILE"); BASE="${BASE%.*}"
DRAW_STYLE="x=8:y=8:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=4"

# ---- cache lookup -----------------------------------------------------------
CACHE_DIR=""
if [ "$USE_CACHE" = 1 ]; then
  SHA=$(python3 -c 'import hashlib,sys; h=hashlib.sha1()
with open(sys.argv[1],"rb") as f:
    for b in iter(lambda: f.read(1<<20), b""): h.update(b)
print(h.hexdigest())' "$FILE" 2>/dev/null)
  if [ -n "$SHA" ]; then
    if [ -n "$SCENES" ]; then KEY="scenes${SCENES}-c${COLS}-w${WIDTH}-m${MAXF}"; else KEY="fps${FPS}-c${COLS}-w${WIDTH}-m${MAXF}"; fi
    CACHE_DIR="${FD_MEDIA_CACHE:-$HOME/.claude/fable-director/media-cache}/$SHA/sheet-$KEY"
    if [ -f "$CACHE_DIR/done" ] && ls "$CACHE_DIR"/*.jpg >/dev/null 2>&1; then
      OUTS=()
      for j in "$CACHE_DIR"/*.jpg; do
        cp "$j" "$OUTDIR/" || fail "cannot copy cached sheet to $OUTDIR"
        OUTS+=("$OUTDIR/$(basename "$j")")
      done
      [ -f "$CACHE_DIR/scenes.txt" ] && cat "$CACHE_DIR/scenes.txt"
      [ -f "$CACHE_DIR/frames.txt" ] && cat "$CACHE_DIR/frames.txt"
      echo "CACHE: hit $CACHE_DIR"
      echo "STATUS: ok"
      echo "OUTPUT: $(printf '%s, ' "${OUTS[@]}" | sed 's/, $//')"
      echo "DETAIL: audio: $AUDIO_LINE — ${#OUTS[@]} sheet(s) from cache$( [ -f "$CACHE_DIR/detail.txt" ] && printf ', %s' "$(cat "$CACHE_DIR/detail.txt")")"
      exit 0
    fi
    echo "CACHE: miss"
  fi
else
  echo "CACHE: off"
fi

OUTS=()
if [ -n "$SCENES" ]; then
  # ---- scene mode: pass 1 finds the cuts, pass 2 renders at the cuts --------
  CUTS=$(ffmpeg -v info -nostats -i "$FILE" -an -vf "select='gt(scene,${SCENES})',showinfo" -f null - 2>&1 \
         | grep -o 'pts_time:[0-9.]*' | cut -d: -f2)
  NCUTS=$(printf '%s\n' $CUTS | grep -c . || true)
  TOTAL=$((NCUTS + 1))   # frame 0 + one per cut
  LIST=$(printf '%s\n' $CUTS | awk 'NF {printf "%s%.1f", (n++?", ":""), $1}')
  SCENE_LINE="scenes: $NCUTS cuts (threshold $SCENES) at 0.0${LIST:+, $LIST} s"
  echo "$SCENE_LINE"
  read -r NSHEETS PER ROWS TCOLS <<<"$(awk -v total="$TOTAL" -v c="$COLS" -v m="$MAXF" 'BEGIN{
    n = int((total + m - 1) / m); if (n < 1) n = 1;
    per = int((total + n - 1) / n);
    cols = (per < c) ? per : c;
    rows = int((per + cols - 1) / cols);
    printf "%d %d %d %d", n, per, rows, cols }')"
  FRAMES_LINE="frames: $TOTAL (frame 0 + cuts) -> $NSHEETS sheet(s) ${TCOLS}x${ROWS} @ ${WIDTH}px"
  echo "$FRAMES_LINE"
  VF="select='eq(n\\,0)+gt(scene\\,${SCENES})',scale=${WIDTH}:-2,drawtext=text='%{pts\\:hms}':${DRAW_STYLE},tile=${TCOLS}x${ROWS}"
  if [ "$NSHEETS" -eq 1 ]; then PATTERN="$OUTDIR/${BASE}-scenes.jpg"; else PATTERN="$OUTDIR/${BASE}-scenes-%d.jpg"; fi
  ERR=$(ffmpeg -v error -y -i "$FILE" -an -vf "$VF" -vsync vfr -frames:v "$NSHEETS" -q:v 3 "$PATTERN" 2>&1)
  [ $? -eq 0 ] || fail "ffmpeg failed in scene mode: ${ERR:0:300}"
  if [ "$NSHEETS" -eq 1 ]; then
    [ -s "$PATTERN" ] || fail "ffmpeg produced no sheet in scene mode: ${ERR:0:300}"
    OUTS=("$PATTERN")
  else
    k=1
    while [ "$k" -le "$NSHEETS" ]; do
      [ -s "$OUTDIR/${BASE}-scenes-$k.jpg" ] || fail "scene sheet $k missing: ${ERR:0:300}"
      OUTS+=("$OUTDIR/${BASE}-scenes-$k.jpg"); k=$((k+1))
    done
  fi
  DETAIL_TAIL="${TOTAL} frames at cuts"
else
  # ---- fixed-rate mode --------------------------------------------------------
  read -r TOTAL NSHEETS PER ROWS TCOLS SPAN <<<"$(awk -v d="$DURATION" -v f="$FPS" -v c="$COLS" -v m="$MAXF" 'BEGIN{
    total = int(d*f + 0.999999); if (total < 1) total = 1;
    n = int((total + m - 1) / m);  if (n < 1) n = 1;
    per = int((total + n - 1) / n);
    cols = (per < c) ? per : c;
    rows = int((per + cols - 1) / cols);
    span = per / f;                       # seconds covered by one sheet
    printf "%d %d %d %d %d %.6f", total, n, per, rows, cols, span }')"
  FRAMES_LINE="frames: $TOTAL @ ${FPS} fps -> $NSHEETS sheet(s) ${TCOLS}x${ROWS} @ ${WIDTH}px"
  echo "$FRAMES_LINE"
  k=0
  while [ "$k" -lt "$NSHEETS" ]; do
    START=$(awk -v k="$k" -v s="$SPAN" 'BEGIN{printf "%.3f", k*s}')
    if [ "$NSHEETS" -eq 1 ]; then OUT="$OUTDIR/${BASE}-sheet.jpg"; else OUT="$OUTDIR/${BASE}-sheet-$((k+1)).jpg"; fi
    # Input-side -ss (fast keyframe seek) resets pts to 0: the drawtext offset
    # puts the ABSOLUTE time back on every frame (%{pts:hms:<offset>}).
    OFF=$(awk -v s="$START" 'BEGIN{printf "%d", s}')
    VF="fps=${FPS},scale=${WIDTH}:-2,drawtext=text='%{pts\\:hms\\:${OFF}}':${DRAW_STYLE},tile=${TCOLS}x${ROWS}"
    ERR=$(ffmpeg -v error -y -ss "$START" -t "$SPAN" -i "$FILE" -vf "$VF" -frames:v 1 -q:v 3 "$OUT" 2>&1)
    if [ $? -ne 0 ] || [ ! -s "$OUT" ]; then
      fail "ffmpeg failed on sheet $((k+1)): ${ERR:0:300}"
    fi
    OUTS+=("$OUT")
    k=$((k+1))
  done
  DETAIL_TAIL="${TOTAL} frames"
fi

# ---- cache store ------------------------------------------------------------
if [ -n "$CACHE_DIR" ]; then
  if mkdir -p "$CACHE_DIR" 2>/dev/null; then
    for o in "${OUTS[@]}"; do cp "$o" "$CACHE_DIR/" 2>/dev/null || true; done
    echo "$FRAMES_LINE" > "$CACHE_DIR/frames.txt"
    [ -n "$SCENES" ] && echo "$SCENE_LINE" > "$CACHE_DIR/scenes.txt"
    echo "${TCOLS}x${ROWS} @ ${WIDTH}px, ${DETAIL_TAIL}" > "$CACHE_DIR/detail.txt"
    : > "$CACHE_DIR/done"
  fi
fi

echo "STATUS: ok"
echo "OUTPUT: $(printf '%s, ' "${OUTS[@]}" | sed 's/, $//')"
echo "DETAIL: audio: $AUDIO_LINE — $NSHEETS sheet(s) ${TCOLS}x${ROWS} @ ${WIDTH}px, ${DETAIL_TAIL}"
exit 0

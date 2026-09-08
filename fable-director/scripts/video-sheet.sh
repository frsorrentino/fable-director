#!/usr/bin/env bash
# video-sheet.sh — ffprobe card + contact sheet(s) with burnt-in timestamps.
# Zero model tokens: the fact that starts every media task ("does this file
# have an audio track?") comes from ffprobe here, never from a model (Gemini
# declared speech on a file with no audio track, measured 2026-09-08).
#
#   video-sheet.sh <file> [--fps N] [--cols N] [--width PX] [--out DIR] [--max N]
#
# Defaults: fps 1, 8 columns, 400 px per frame, output in the current dir,
# at most 90 frames per sheet (above that the video is split into numbered
# sheets <basename>-sheet-1.jpg, -2.jpg, ...). Measured: 400 px / 1 fps is
# readable by the top model in ONE Read; 320 px or 1 frame every 2 s loses
# small on-screen text.
#
# Output (grep-able, same shape as external-exec.py):
#   file / duration / video / audio: none|<codec> <ch>ch <rate> Hz / frames
#   STATUS: ok|error
#   OUTPUT: <sheet.jpg>[, <sheet-2.jpg> ...]
#   DETAIL: audio: none|<codec> <ch>ch — <n> sheet(s) <cols>x<rows> @ <px>px
# Exit 0 only on STATUS ok. Requires ffmpeg + ffprobe (+ fontconfig for drawtext).
set -uo pipefail

FPS=1; COLS=8; WIDTH=400; OUTDIR="."; MAXF=90; FILE=""

fail() { echo "STATUS: error"; echo "OUTPUT: -"; echo "DETAIL: $*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --fps)   FPS="${2:?--fps needs a value}"; shift 2 ;;
    --cols)  COLS="${2:?--cols needs a value}"; shift 2 ;;
    --width) WIDTH="${2:?--width needs a value}"; shift 2 ;;
    --out)   OUTDIR="${2:?--out needs a value}"; shift 2 ;;
    --max)   MAXF="${2:?--max needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*)      fail "unrecognized argument: $1" ;;
    *)       [ -z "$FILE" ] && FILE="$1" || fail "only one input file"; shift ;;
  esac
done

[ -n "$FILE" ] || fail "usage: video-sheet.sh <file> [--fps N] [--cols N] [--width PX] [--out DIR]"
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

# ---- sheet geometry ---------------------------------------------------------
read -r TOTAL NSHEETS PER ROWS TCOLS SPAN <<<"$(awk -v d="$DURATION" -v f="$FPS" -v c="$COLS" -v m="$MAXF" 'BEGIN{
  total = int(d*f + 0.999999); if (total < 1) total = 1;
  n = int((total + m - 1) / m);  if (n < 1) n = 1;
  per = int((total + n - 1) / n);
  cols = (per < c) ? per : c;
  rows = int((per + cols - 1) / cols);
  span = per / f;                       # seconds covered by one sheet
  printf "%d %d %d %d %d %.6f", total, n, per, rows, cols, span }')"
echo "frames: $TOTAL @ ${FPS} fps -> $NSHEETS sheet(s) ${TCOLS}x${ROWS} @ ${WIDTH}px"

BASE=$(basename "$FILE"); BASE="${BASE%.*}"
OUTS=()
k=0
while [ "$k" -lt "$NSHEETS" ]; do
  START=$(awk -v k="$k" -v s="$SPAN" 'BEGIN{printf "%.3f", k*s}')
  if [ "$NSHEETS" -eq 1 ]; then OUT="$OUTDIR/${BASE}-sheet.jpg"; else OUT="$OUTDIR/${BASE}-sheet-$((k+1)).jpg"; fi
  # Input-side -ss (fast keyframe seek) resets pts to 0: the drawtext offset
  # puts the ABSOLUTE time back on every frame (%{pts:hms:<offset>}).
  OFF=$(awk -v s="$START" 'BEGIN{printf "%d", s}')
  VF="fps=${FPS},scale=${WIDTH}:-2,drawtext=text='%{pts\\:hms\\:${OFF}}':x=8:y=8:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=4,tile=${TCOLS}x${ROWS}"
  ERR=$(ffmpeg -v error -y -ss "$START" -t "$SPAN" -i "$FILE" -vf "$VF" -frames:v 1 -q:v 3 "$OUT" 2>&1)
  if [ $? -ne 0 ] || [ ! -s "$OUT" ]; then
    fail "ffmpeg failed on sheet $((k+1)): ${ERR:0:300}"
  fi
  OUTS+=("$OUT")
  k=$((k+1))
done

echo "STATUS: ok"
echo "OUTPUT: $(printf '%s, ' "${OUTS[@]}" | sed 's/, $//')"
echo "DETAIL: audio: $AUDIO_LINE — $NSHEETS sheet(s) ${TCOLS}x${ROWS} @ ${WIDTH}px, ${TOTAL} frames"
exit 0

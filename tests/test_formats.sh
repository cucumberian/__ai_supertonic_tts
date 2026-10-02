#!/usr/bin/env bash
# End-to-end check of every response_format the running server advertises.
#
# Runs on the host (needs ffprobe) against a live container. It discovers the
# format list from the server itself — the 400 body for a bogus format lists
# the supported ones — so it can never drift from what is actually deployed.
#
#   ./tests/test_formats.sh                       # against 127.0.0.1:7788
#   BASE=http://other-host:7788 ./tests/test_formats.sh
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:7788}"
TEXT="${TEXT:-Hello. This is a check of the audio formats this server can encode.}"
MODEL="${MODEL:-supertonic-3}"

command -v ffprobe >/dev/null || { echo "ffprobe is required" >&2; exit 2; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# codec:rate:channels we expect per format. Anything the server offers that is
# not listed here is reported as unexpected rather than silently passing.
expect_for() {
  case "$1" in
    wav)  echo "pcm_s16le:44100:1" ;;
    flac) echo "flac:44100:1" ;;
    ogg)  echo "vorbis:44100:1" ;;
    mp3)  echo "mp3:44100:1" ;;
    opus) echo "opus:48000:1" ;;
    *)    echo "" ;;
  esac
}

probe() { # file -> "codec:rate:channels"
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=codec_name,sample_rate,channels -of csv=p=0 "$1" \
    | tr ',\n' '::' | sed 's/:$//'
}

echo "== $BASE"
HEALTH="$(curl -fsS -m 10 "$BASE/v1/health")" || { echo "server not reachable" >&2; exit 1; }
echo "health: $HEALTH"

# Self-discovery: ask for a format that cannot exist and read the allowlist
# back out of the error message.
BOGUS="$(curl -s -m 10 -X POST "$BASE/v1/audio/speech" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"input\":\"x\",\"response_format\":\"__probe__\"}")"
FORMATS="$(printf '%s' "$BOGUS" | sed -n 's/.*set response_format to one of: \([^"]*\).*/\1/p' \
  | tr -d ' ' | tr ',' ' ')"
[ -n "$FORMATS" ] || { echo "could not read the allowlist from: $BOGUS" >&2; exit 1; }
echo "advertised formats: $FORMATS"
echo

fails=0
for fmt in $FORMATS; do
  out="$WORK/$fmt.out"
  code="$(curl -s -m 120 -o "$out" -w '%{http_code}' -D "$WORK/$fmt.hdr" \
    -X POST "$BASE/v1/audio/speech" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"input\":\"$TEXT\",\"response_format\":\"$fmt\"}")"
  if [ "$code" != "200" ]; then
    printf 'FAIL %-5s HTTP %s  %s\n' "$fmt" "$code" "$(head -c 200 "$out")"
    fails=$((fails + 1)); continue
  fi

  got="$(probe "$out")"
  want="$(expect_for "$fmt")"
  mime="$(sed -n 's/^[Cc]ontent-[Tt]ype: *//p' "$WORK/$fmt.hdr" | tr -d '\r')"
  dur="$(sed -n 's/^[Xx]-[Aa]udio-[Dd]uration: *//p' "$WORK/$fmt.hdr" | tr -d '\r')"
  size="$(wc -c <"$out" | tr -d ' ')"

  if [ -n "$want" ] && [ "$got" != "$want" ]; then
    printf 'FAIL %-5s %s (expected %s)  %s B\n' "$fmt" "$got" "$want" "$size"
    fails=$((fails + 1))
  elif [ -z "$want" ]; then
    printf 'WARN %-5s %s  %s B  (no expectation on record)\n' "$fmt" "$got" "$size"
  else
    printf 'ok   %-5s %-22s %s B  %-10s dur=%ss\n' "$fmt" "$got" "$size" "$mime" "$dur"
  fi
done

# An OpenAI client that sends no response_format at all used to be the common
# breakage (OpenAI defaults to mp3, which this server rejected).
code="$(curl -s -m 120 -o "$WORK/default.out" -w '%{http_code}' -X POST \
  "$BASE/v1/audio/speech" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"input\":\"$TEXT\"}")"
if [ "$code" = "200" ]; then
  printf 'ok   default (no response_format) -> %s\n' "$(probe "$WORK/default.out")"
else
  printf 'FAIL default (no response_format) HTTP %s\n' "$code"; fails=$((fails + 1))
fi

# The native namespace must accept the same formats as the OpenAI alias.
code="$(curl -s -m 120 -o "$WORK/native.opus" -w '%{http_code}' -X POST \
  "$BASE/v1/tts" -H 'Content-Type: application/json' \
  -d "{\"text\":\"$TEXT\",\"voice\":\"M1\",\"response_format\":\"opus\"}")"
if [ "$code" = "200" ]; then
  printf 'ok   /v1/tts response_format=opus -> %s\n' "$(probe "$WORK/native.opus")"
else
  printf 'FAIL /v1/tts response_format=opus HTTP %s  %s\n' "$code" "$(head -c 200 "$WORK/native.opus")"
  fails=$((fails + 1))
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "PASS all advertised formats encode as expected"
else
  echo "FAIL $fails check(s) failed"
  exit 1
fi
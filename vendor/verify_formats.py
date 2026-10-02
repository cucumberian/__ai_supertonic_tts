"""Build-time gate for the vendored audio encoder.

Run against the installed package AFTER ``vendor/audio.py`` has replaced
``supertonic/server/audio.py``. It does not merely check that the format names
exist — it encodes a real tone into every advertised format and reads the
container back, so a build fails here rather than on a client's first request
if a version bump moves upstream's API, drops ``soxr``, or changes what
libsndfile is willing to encode.

Exit 0 = the image is safe to run. Non-zero = do not ship it.
"""

from __future__ import annotations

import io
import sys

import numpy as np
import soundfile as sf

from supertonic.server.audio import (
    SUPPORTED_FORMATS,
    UnsupportedAudioFormat,
    coerce_response_format,
    encode_audio,
    format_to_mime,
)

# What the vendored module promises clients. Extra formats upstream or a later
# bump adds are fine; losing one of these is a regression for someone.
REQUIRED = {
    # name: (soundfile container, subtype, sample rate it must come back at)
    "wav": ("WAV", "PCM_16", 44100),
    "flac": ("FLAC", "PCM_16", 44100),
    "ogg": ("OGG", "VORBIS", 44100),
    "mp3": ("MP3", "MPEG_LAYER_III", 44100),
    # Opus is rate-restricted by libsndfile, so it must arrive resampled.
    "opus": ("OGG", "OPUS", 48000),
}

# The model's native rate as configured in this image.
NATIVE_RATE = 44100


def fail(msg: str) -> None:
    print(f"FAIL {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    missing = REQUIRED.keys() - set(SUPPORTED_FORMATS)
    if missing:
        fail(f"formats missing from SUPPORTED_FORMATS: {sorted(missing)}")

    # The OpenAI default every client sends, plus case/whitespace tolerance.
    for raw, want in (("mp3", "mp3"), ("MP3", "mp3"), (" Opus ", "opus"), (None, "wav")):
        got = coerce_response_format(raw)
        if got != want:
            fail(f"coerce_response_format({raw!r}) -> {got!r}, expected {want!r}")
    try:
        coerce_response_format("aac")
    except UnsupportedAudioFormat:
        pass
    else:
        fail("unknown format 'aac' was accepted; the allowlist is not enforced")

    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(NATIVE_RATE) / NATIVE_RATE)).astype(
        np.float32
    )

    for fmt, (want_container, want_subtype, want_rate) in REQUIRED.items():
        try:
            body = encode_audio(tone, NATIVE_RATE, fmt)
        except Exception as exc:  # noqa: BLE001
            fail(f"encode_audio({fmt!r}) raised {type(exc).__name__}: {exc}")
        if not body:
            fail(f"encode_audio({fmt!r}) produced an empty body")

        # Read the container back: proves the bytes really are the codec and
        # rate we advertise, not just that a call returned something.
        try:
            with sf.SoundFile(io.BytesIO(body)) as handle:
                container, subtype, rate, channels = (
                    handle.format,
                    handle.subtype,
                    handle.samplerate,
                    handle.channels,
                )
        except Exception as exc:  # noqa: BLE001
            fail(f"{fmt}: produced bytes libsndfile cannot open ({exc})")

        if container != want_container:
            fail(f"{fmt}: container {container!r}, expected {want_container!r}")
        if subtype != want_subtype:
            fail(f"{fmt}: subtype {subtype!r}, expected {want_subtype!r}")
        if rate != want_rate:
            fail(f"{fmt}: sample rate {rate}, expected {want_rate}")
        if channels != 1:
            fail(f"{fmt}: {channels} channels, expected mono")

        mime = format_to_mime(fmt)
        if not mime.startswith("audio/"):
            fail(f"{fmt}: mime {mime!r} is not an audio type")
        print(f"ok   {fmt:5s} -> {container}/{subtype} {rate}Hz mono {len(body):>7d} B  {mime}")

    print(f"PASS vendored encoder verified: {', '.join(SUPPORTED_FORMATS)}")


if __name__ == "__main__":
    main()
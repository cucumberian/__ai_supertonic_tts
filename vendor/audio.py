"""Audio encoding helpers for the local TTS server.

VENDORED OVERRIDE — this file replaces
``supertonic/server/audio.py`` of the pinned ``supertonic[serve]==1.3.1``
distribution at image build time (see ``Dockerfile``). Everything else from
upstream is used untouched.

Why this file exists
--------------------
Upstream only offers ``wav``, ``flac`` and ``ogg`` (Vorbis) and rejects
``mp3``/``opus`` with HTTP 400 ``unsupported_response_format``. That breaks
every client that speaks the OpenAI Audio Speech API literally, because the
OpenAI default for ``response_format`` is ``mp3``.

Upstream's own exclusion note says the two rejections have different causes:

* ``mp3`` — "only formats reachable through soundfile (libsndfile) at the
  model's native 44.1 kHz are supported, so the server adds no extra system
  dependencies". That premise is out of date: the libsndfile bundled with
  ``soundfile>=0.13`` (1.2.x in this image) encodes MPEG Layer III natively,
  at 44.1 kHz, with no extra dependency at all.
* ``opus`` — "libsndfile's OGG/OPUS encoder only accepts 8/12/16/24/48 kHz,
  and we'd rather error clearly than ship a broken format. Re-add it once we
  have a resampling step." Upstream asked for exactly one thing: a resampler.
  So we add one (``soxr``, a wheel with no system libraries) and re-add Opus
  at 48 kHz, which is also the container Telegram renders as a native voice
  note.

The public surface (``SUPPORTED_FORMATS``, ``UnsupportedAudioFormat``,
``format_to_mime``, ``encode_audio``, ``duration_seconds``,
``coerce_response_format``) is unchanged, so ``supertonic/server/routes.py``
imports it unmodified.

Public surface contract with ``routes.py`` — do not rename or drop:
    SUPPORTED_FORMATS, UnsupportedAudioFormat, format_to_mime,
    encode_audio, duration_seconds, coerce_response_format
"""

from __future__ import annotations

import io
from typing import Optional, Tuple

import numpy as np
import soundfile as sf

try:  # soxr is only needed for the rate-restricted formats (Opus).
    import soxr
except ImportError:  # pragma: no cover - the image pins soxr
    soxr = None  # type: ignore[assignment]


# fmt -> (soundfile format, soundfile subtype, mime, allowed sample rates).
# ``allowed sample rates`` is None for anything libsndfile will take at the
# model's native rate, or a tuple when the codec restricts it — such formats
# are resampled to the closest allowed rate before encoding.
# Keep this in sync with ``SUPPORTED_FORMATS`` consumers in ``routes.py``.
_FORMATS = {
    "wav": ("WAV", "PCM_16", "audio/wav", None),
    "flac": ("FLAC", "PCM_16", "audio/flac", None),
    "ogg": ("OGG", "VORBIS", "audio/ogg", None),
    # libsndfile 1.2.x ships an MPEG Layer III encoder; it is the only MP3
    # subtype it accepts, and it works at the model's native 44.1 kHz.
    "mp3": ("MP3", "MPEG_LAYER_III", "audio/mpeg", None),
    # Opus is rate-restricted by libsndfile; 48 kHz matches what VoIP and
    # Telegram voice notes use.
    "opus": ("OGG", "OPUS", "audio/ogg", (8000, 12000, 16000, 24000, 48000)),
}

SUPPORTED_FORMATS = tuple(_FORMATS.keys())


class UnsupportedAudioFormat(ValueError):
    """Raised when the caller asks for a format we cannot encode."""


def _entry(fmt: str) -> Tuple[str, str, str, Optional[Tuple[int, ...]]]:
    entry = _FORMATS.get(fmt)
    if entry is None:
        raise UnsupportedAudioFormat(fmt)
    return entry


def format_to_mime(fmt: str) -> str:
    return _entry(fmt)[2]


def target_sample_rate(fmt: str, sample_rate: int) -> int:
    """The rate ``fmt`` will actually be encoded at.

    Equals ``sample_rate`` for codecs with no restriction, otherwise the
    codec-supported rate closest to it (44100 -> 48000 for Opus).
    """
    allowed = _entry(fmt)[3]
    if not allowed or sample_rate in allowed:
        return sample_rate
    return min(allowed, key=lambda rate: (abs(rate - sample_rate), rate))


def _resample(wav: np.ndarray, sample_rate: int, target_rate: int) -> np.ndarray:
    """Band-limited resample of a mono float waveform.

    ``soxr`` is pinned in the image; if it is somehow absent we fail loudly
    rather than emitting a format at a rate the codec will reject.
    """
    if soxr is None:  # pragma: no cover
        raise UnsupportedAudioFormat(
            f"cannot resample {sample_rate} Hz -> {target_rate} Hz: "
            "the 'soxr' package is not installed"
        )
    # HQ keeps the passband clean; TTS output is short so the cost is noise.
    return np.asarray(soxr.resample(wav, sample_rate, target_rate, quality="HQ"))


def encode_audio(wav: np.ndarray, sample_rate: int, fmt: str) -> bytes:
    """Encode a synthesized waveform into ``fmt`` bytes.

    Args:
        wav: ndarray of shape ``(1, num_samples)`` or ``(num_samples,)`` —
            the shape produced by :meth:`supertonic.TTS.synthesize`.
        sample_rate: model sample rate (e.g. 44100).
        fmt: one of :data:`SUPPORTED_FORMATS`.
    """
    sf_format, subtype, _, _allowed = _entry(fmt)

    if wav.ndim == 2:
        # soundfile expects (frames,) or (frames, channels). The pipeline
        # returns (1, num_samples), so squeeze the leading singleton.
        wav = wav.squeeze(0)
    wav = np.ascontiguousarray(wav)

    rate = target_sample_rate(fmt, sample_rate)
    if rate != sample_rate:
        wav = _resample(wav, sample_rate, rate)

    buf = io.BytesIO()
    sf.write(buf, wav, rate, format=sf_format, subtype=subtype)
    return buf.getvalue()


def duration_seconds(wav: np.ndarray, sample_rate: int) -> float:
    return float(wav.shape[-1]) / float(sample_rate)


def coerce_response_format(value: Optional[str]) -> str:
    """Validate and normalize a user-supplied ``response_format``.

    ``None`` → ``"wav"`` (sensible default for local-host integrations). An
    unsupported value raises :class:`UnsupportedAudioFormat` so handlers can
    return a 400 with a stable error code.
    """
    if value is None:
        return "wav"
    v = value.lower().strip()
    if v not in _FORMATS:
        raise UnsupportedAudioFormat(value)
    return v
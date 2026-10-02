# Supertonic TTS server

Local HTTP text-to-speech: [`supertonic`](https://pypi.org/project/supertonic/)
(Supertone Supertonic-3) behind `supertonic serve`, in a container.

- Native API: `POST /v1/tts`, plus `/v1/health`, `/v1/styles`, `/v1/tts/batch`
- OpenAI Audio Speech-compatible alias: `POST /v1/audio/speech`
- Model + cache live in `./models` (gitignored), CPU inference, no GPU
- Listens on `:7788`

```bash
docker compose up -d --build
curl -s localhost:7788/v1/health
./tests/test_formats.sh
```

Synthesize:

```bash
curl -X POST localhost:7788/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"supertonic-3","input":"hello","voice":"M1","response_format":"opus"}' \
  -o voice.ogg
```

## Audio formats

`response_format` accepts **`wav`** (default), **`mp3`**, **`opus`**,
**`ogg`** (Vorbis) and **`flac`**. All are mono.

| format | container/codec | sample rate | mime | use for |
| --- | --- | --- | --- | --- |
| `wav` | PCM s16le | 44.1 kHz | `audio/wav` | lossless, editing |
| `mp3` | MPEG Layer III | 44.1 kHz | `audio/mpeg` | OpenAI clients default to this |
| `opus` | OGG / Opus | **48 kHz** | `audio/ogg` | Telegram voice notes, chat |
| `ogg` | OGG / Vorbis | 44.1 kHz | `audio/ogg` | generic OGG consumers |
| `flac` | FLAC | 44.1 kHz | `audio/flac` | lossless but small |

`opus` is resampled 44.1 → 48 kHz on the way out (Opus cannot encode 44.1 kHz).
Everything else is encoded at the model's native rate.

Note that `opus` and `ogg` share the `audio/ogg` mime — if a client must tell
them apart it has to look at the codec, not the content type.

## Why `vendor/audio.py` exists

Upstream `supertonic[serve]==1.3.1` ships only `wav`, `flac` and `ogg`, and
answers **HTTP 400 `unsupported_response_format`** to anything else. Since the
OpenAI Audio Speech API defaults `response_format` to `mp3`, any client written
against OpenAI failed against this server until it was pinned to `wav`
explicitly.

Upstream's own code comment gives the two reasons, and they have different
answers:

- **mp3** — excluded to avoid extra dependencies. That premise is stale: the
  libsndfile bundled with `soundfile` in this image (1.2.2) encodes MPEG Layer
  III natively at 44.1 kHz, so MP3 costs one dictionary entry and no dependency.
- **opus** — excluded because libsndfile's Opus encoder only accepts
  8/12/16/24/48 kHz, "re-add it once we have a resampling step". So this image
  adds the resampling step (`soxr`, a wheel with no system libraries) and
  re-enables Opus at 48 kHz.

`vendor/audio.py` is a vendored, extended copy of
`supertonic/server/audio.py` that keeps upstream's public surface exactly
(`SUPPORTED_FORMATS`, `UnsupportedAudioFormat`, `format_to_mime`,
`encode_audio`, `duration_seconds`, `coerce_response_format`), so
`supertonic/server/routes.py` imports it unmodified. The `Dockerfile` copies it
over the installed module at build time; nothing else from upstream is touched.

### Upgrading `supertonic`

The patch is anchored to `==1.3.1` by behaviour, not by line numbers — but it
does replace a whole upstream file. `RUN python /tmp/verify_formats.py` in the
`Dockerfile` therefore encodes a real tone into every advertised format and
reads each container back, so a bump that moves upstream's API, drops `soxr`, or
changes what libsndfile accepts **fails the build** instead of failing a client
at runtime. When bumping the pin:

1. raise `supertonic[serve]==X` in the `Dockerfile`;
2. re-read upstream's `server/audio.py` and fold anything new into
   `vendor/audio.py`;
3. let the build-time gate and `./tests/test_formats.sh` be the verdict.

Dropping `vendor/` and the two `COPY`/`RUN` layers returns the image to stock
upstream behaviour (`wav`/`flac`/`ogg` only) — the vendored module is the whole
delta.

## Tests

- `vendor/verify_formats.py` — build-time, no model needed: encodes a tone into
  every format and asserts container, subtype, rate and channel count.
- `tests/test_formats.sh` — against a running server: reads the allowlist back
  out of the server's own 400 response, synthesizes real speech in each format,
  and `ffprobe`s the result. `BASE=http://host:7788 ./tests/test_formats.sh`.

## Deployment: two checkouts of this repo

The live container (`supertonic-tts`, port `7788`) is **not** run from this
directory. It is composed from `/opt/intel480/ai/supertonic-tts`, which is a
second checkout of the same remote and holds the downloaded model in its
`models/`.

Deploying a change therefore means getting it into that checkout and rebuilding
there:

```bash
cd /opt/intel480/ai/supertonic-tts && git pull && docker compose up -d --build
```

Running `docker compose up -d` in a fresh checkout instead will try to claim the
same `container_name: supertonic-tts` and re-download ~400 MB of model weights
into its own empty `models/`. To try a build without disturbing the live one,
run it on another port against the existing weights:

```bash
docker run --rm -p 127.0.0.1:7799:7788 \
  -v /opt/intel480/ai/supertonic-tts/models:/models:ro \
  -e SUPERTONIC_CACHE_DIR=/models/supertonic \
  <image> 
```
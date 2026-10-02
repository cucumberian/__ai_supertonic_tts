FROM python:3.11-slim

# soxr: band-limited 44.1 kHz -> 48 kHz resampler. It exists only to satisfy
# libsndfile's Opus encoder, which accepts 8/12/16/24/48 kHz and nothing else
# (the model is 44.1 kHz). A manylinux wheel with no system libraries, so the
# image stays slim.
RUN pip install --no-cache-dir "supertonic[serve]==1.3.1" "soxr==1.1.0"

# Vendored encoder: adds `mp3` and `opus` to `response_format` (see
# vendor/audio.py for why). The destination is resolved through the installed
# package itself, so this keeps working if the base image moves site-packages.
#
# The build then verifies the result by encoding a real tone into every
# advertised format and reading each container back. A version bump that moves
# upstream's API, or an image without soxr, fails the build here instead of
# failing a client's first request at runtime.
COPY vendor/audio.py /tmp/vendor_audio.py
COPY vendor/verify_formats.py /tmp/verify_formats.py
RUN cp /tmp/vendor_audio.py "$(python -c 'import supertonic.server.audio as a; print(a.__file__)')" \
    && python /tmp/verify_formats.py \
    && rm /tmp/vendor_audio.py /tmp/verify_formats.py

EXPOSE 7788
ENTRYPOINT ["supertonic"]
CMD ["serve", "--host", "0.0.0.0", "--port", "7788", "--model", "supertonic-3", "--log-level", "info"]
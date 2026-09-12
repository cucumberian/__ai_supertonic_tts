FROM python:3.11-slim

RUN pip install --no-cache-dir "supertonic[serve]==1.3.1"

EXPOSE 7788
ENTRYPOINT ["supertonic"]
CMD ["serve", "--host", "0.0.0.0", "--port", "7788", "--model", "supertonic-3", "--log-level", "info"]

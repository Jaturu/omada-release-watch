FROM python:3.13-alpine

LABEL org.opencontainers.image.title="omada-release-watch"
LABEL org.opencontainers.image.description="Queries a catalog of TP-Link Omada Controller releases and fetches artifacts."

RUN addgroup -S appgroup \
    && adduser -S -G appgroup -h /home/appuser appuser

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# pip is not needed at runtime, and its vendored packages carry advisories.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall --yes pip

COPY omada_release_watch /app/omada_release_watch
COPY omada-release-watch.py /app/omada-release-watch.py
COPY config.example.yaml /app/config.example.yaml
# --version reads this at runtime, and the release tag is built from it.
COPY VERSION /app/VERSION

RUN chmod +x /app/omada-release-watch.py \
    && mkdir -p /data \
    && chown -R appuser:appgroup /app /home/appuser /data

USER appuser

# Catalog and config are read from here. Mount a volume so --refresh persists.
WORKDIR /data

ENV HOME=/home/appuser
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["/app/omada-release-watch.py"]

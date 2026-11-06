FROM python:3.13-alpine AS build

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# pip is not needed at runtime, and its vendored packages carry advisories.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall --yes pip

# Drop stdlib modules whose shared libraries the runtime stage does not ship.
RUN cd /usr/local/lib/python3.13 \
    && rm -rf ensurepip idlelib tkinter turtledemo pydoc_data \
        lib-dynload/_sqlite3.* lib-dynload/_gdbm.* lib-dynload/_dbm.* \
        lib-dynload/_ctypes.* lib-dynload/_curses.* lib-dynload/_curses_panel.* \
        lib-dynload/readline.* lib-dynload/_tkinter.* \
    && find . -name __pycache__ -type d -prune -exec rm -rf {} +

FROM alpine:3.24

# Only the libraries the interpreter and the pinned wheels load at runtime.
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates tzdata libbz2 libcrypto3 libssl3 libuuid xz-libs zlib

COPY --from=build /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=build /usr/local/lib/libpython3.13.so.1.0 /usr/local/lib/libpython3.13.so.1.0
COPY --from=build /usr/local/lib/python3.13 /usr/local/lib/python3.13
RUN ln -s python3.13 /usr/local/bin/python3

LABEL org.opencontainers.image.title="omada-release-watch"
LABEL org.opencontainers.image.description="Queries a catalog of TP-Link Omada Controller releases and fetches artifacts."

RUN addgroup -S appgroup \
    && adduser -S -G appgroup -h /home/appuser appuser

WORKDIR /app

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

#!/usr/bin/env sh
# Fetch the latest Omada controller artifact using the published image.
# The clone is mounted as /data, so the catalog it ships with is used and
# downloads/ lands here owned by you. Arguments replace the default filters.
set -eu

cd "$(dirname "$0")"
mkdir -p downloads

IMAGE="${OMADA_RELEASE_WATCH_IMAGE:-docker.io/jaturu/omada-release-watch}"

if [ "$#" -eq 0 ]; then
  set -- --kind stable --platform linux --package tgz
fi

exec docker run --rm --pull always \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v "$PWD:/data" \
  "$IMAGE" \
  --refresh --fetch --latest "$@"

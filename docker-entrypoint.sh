#!/usr/bin/env sh
set -eu

# Runtime data lives in the persistent /app/storage volume. Model files are
# supplied separately through the panel upload flow or a mounted volume.
mkdir -p \
  /app/storage/models \
  /app/storage/datasets \
  /app/storage/snapshots \
  /app/storage/test_results \
  /app/storage/backups

exec "$@"
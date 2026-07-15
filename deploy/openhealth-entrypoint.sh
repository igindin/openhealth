#!/bin/sh
# Populate the runtime dir (/data) with the app's static files, symlinked from
# the image, so the stdlib bridge serves statics + data from a single --dir —
# while the real health data (data.local.json, data/index/, .openhealth) stays
# on the mounted volume. Mirrors the local ~/health-os/dashboard symlink setup.
set -e

APP=/app/ui/web
DATA=/data

for f in index.html dashboard.html dashboard-v2.html manifest.webmanifest sw.js assets; do
  ln -sfn "$APP/$f" "$DATA/$f"
done
mkdir -p "$DATA/data/index" "$DATA/.openhealth"

# 0.0.0.0 is safe here: the port is unpublished and only Caddy can reach it.
exec python /app/ui/web/server.py --host 0.0.0.0 --port 8770 --dir "$DATA"

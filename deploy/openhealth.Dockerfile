# OpenHealth bridge — a stdlib-only Python server, so the image is tiny and has
# zero pip dependencies. Build context is the repo root:
#   docker build -f deploy/openhealth.Dockerfile -t openhealth-bridge .
# Usually built for you by deploy/docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN chmod +x /app/deploy/openhealth-entrypoint.sh

# Agent config + memory live on the mounted data volume, not in the image.
ENV OPENHEALTH_HOME=/data/.openhealth
EXPOSE 8770

# The entrypoint symlinks the app's static files into /data, then starts the
# bridge on 0.0.0.0 — which is safe ONLY because nothing publishes this port:
# it is reachable exclusively through the Caddy reverse proxy (TLS + auth).
ENTRYPOINT ["/app/deploy/openhealth-entrypoint.sh"]

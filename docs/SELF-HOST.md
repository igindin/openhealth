# Self-host: OpenHealth + Hermes on a server

OpenHealth runs in two modes:

- **Local (the default):** the bridge on `127.0.0.1`, `OpenHealth.command` or the `.app`, and data never leaves the machine. Nothing in this document is needed for it.
- **Self-host (this document):** OpenHealth and [Hermes](https://github.com/NousResearch/hermes) on your own server. You read it through the web UI or through Telegram, and both entry points write to the same health database.

Both modes run from the same code. Self-host is a separate profile; the local mode loses nothing.

## Roles

- **OpenHealth** owns health: the engine (recovery/HRV/insights/protocols), the health database (`health_os.sqlite3`: sources / artifacts / records), the web UI, the journal.
- **Hermes** owns the platform: the messenger gateway (Telegram and others), cron (scheduling), session memory, identity via pairing, and an OpenAI-compatible LLM proxy.

They talk over HTTP only. The OpenHealth health database is the single source of truth for health; Hermes keeps its own session database (`state.db`) for conversations. The two are never merged.

## Topology

```
                       ┌───────────────── your server (docker compose) ─────────────────┐
   browser ── :443 ──▶ │  Caddy  (TLS + Basic Auth)                                     │
                       │    └── reverse_proxy ──▶ openhealth:8770  (web UI + /api)      │
                       │                              └── health_os.sqlite3  ◀── one DB │
   Telegram ─────────▶ │  hermes-gateway  (bot + cron)                                  │
                       │    └── incoming ──▶ POST openhealth:8770/api/intake ──▶ DB     │
                       │  hermes-proxy :8645  (OpenAI-compatible LLM)                   │
                       │    └── OpenHealth uses it as its LLM engine                    │
                       │  volumes: health_data (/data)   hermes_data (/opt/data)        │
                       └────────────────────────────────────────────────────────────────┘
```

## One database

Any source (a web check-in, Telegram through Hermes, a webhook) posts an **IntakeEnvelope** to `POST /api/intake`. The bridge validates it, writes a `ContextNote` record into the health database and mirrors the raw envelope to disk (`data/intake/<channel>/…`, an immutable provenance copy). That is why "entered in Telegram, visible on the web" holds: both entry points write the same index.

The envelope contract is `schemas/intake-envelope.schema.json` (required fields: `submission_id`, `submitted_at`, `channel`, `author`; optional `text`, `location`, `attachments`, `tags`, `metadata`).

## Quick start

You need a server with Docker, a domain (an A record pointing at the server) and ports 80/443 open.

```bash
# 1. Build the Hermes image once (Hermes ships its own Dockerfile).
docker build -t hermes-agent /path/to/hermes-agent

# 2. Configure Hermes: the LLM provider and (optionally) the Telegram bot.
#    Done inside the hermes_data volume — see the Hermes docs (hermes login / setup).

# 3. Fill in the OpenHealth config.
cp deploy/.env.example deploy/.env
#    domain, Basic Auth login + password HASH, bot token. To hash the password:
docker run --rm caddy:2 caddy hash-password --plaintext 'strong-password'

# 4. Bring the stack up.
docker compose -f deploy/docker-compose.yml up -d
```

Open `https://<domain>` and enter the Basic Auth login and password. Done.

### Testing locally on a Mac (without Docker Desktop)

A real self-host server is Linux (a VPS): Docker (or Podman) with `docker compose` runs natively there, and nothing beyond the package is needed. Docker Desktop and similar tools are not required for the server.

If you only want to run the OpenHealth image locally on a Mac before rolling out, Docker Desktop is not required either — pick a lightweight runtime:

- **colima** (`brew install colima docker`) - an ordinary Docker daemon in a light VM;
- **Apple `container`** (open sourced at WWDC 2025) - more power-efficient on Apple Silicon, with a separate light VM per container.

Running a single image (colima):

```bash
colima start
docker build -f deploy/openhealth.Dockerfile -t openhealth-bridge .
docker run --rm -p 8770:8770 -v "$PWD/oh-data:/data" openhealth-bridge
# open http://localhost:8770  (health: http://localhost:8770/api/health)
```

One caveat: Apple `container` handles single OCI images well, but a full multi-service `docker compose` (Caddy + OpenHealth + Hermes) relies on Docker/Podman, the same runtime you get on a Linux server. Running the single OpenHealth image locally without an external TLS domain and an LLM provider will not bring up Caddy and Hermes in full, and that is expected: those two services are configured for a specific server.

## Security (required reading)

This is medical data. The security model is simple and strict:

- **The OpenHealth bridge has no authentication of its own.** Its local mode relied on `127.0.0.1`. On a server it binds `0.0.0.0`, but **the port is not published** (there is no `ports:` entry for `openhealth` in the compose file), so it is unreachable from outside. Caddy is the only way in.
- **Caddy provides TLS + Basic Auth.** Without valid credentials there is no way through. Never add `ports:` to the `openhealth` service and never expose 8770.
- **No secrets in the repository.** `deploy/.env` is gitignored; the password is stored as a hash; tokens are passed through the environment.
- **No multi-user support.** Self-host is built for one person (you). Handing access to several people with data isolation is a large separate task and is not covered here.
- Want it stricter: add an IP allowlist in Caddy, client certificates (mTLS), or access through a VPN/SSH tunnel only.

## LLM through Hermes

OpenHealth talks to the Hermes proxy as an OpenAI-compatible endpoint (`/v1/chat/completions`) rather than through `hermes -z` (that interactive one-shot can hang while the gateway is starting). Configuration:

- `OPENHEALTH_LLM_BASE_URL=http://hermes-proxy:8645` (already in `.env.example`);
- in the UI: **Settings → Agent → Hermes** (or `POST /api/config {"agent":"hermes","base_url":"http://hermes-proxy:8645"}`).

Any bearer token works: the proxy substitutes the provider's real credentials from `hermes_data`. If the Hermes provider is not configured (`hermes proxy status` reports "not logged in"), LLM parsing will not run; OpenHealth then falls back to its own agent (claude/codex) if one is present in the image. LLM through Hermes is an optional layer, not a requirement.

## What exists and what comes next

Phase 0 (done and verified): `--host` on the bridge, `POST /api/intake` (the "one database" seam) — the round trip is verified: a telegram envelope lands in the health index as a `ContextNote` (`indexed: true`); LLM mode via hermes-proxy; and this deploy skeleton (`docker compose config` is valid). The OpenHealth image (`deploy/openhealth.Dockerfile`) builds cleanly and, run as a container with the `/data` volume mounted, really does serve the web UI (`<title>OpenHealth</title>`), static assets and `/api/*` (health returns a build stamp) — smoke-tested locally. The engine's Telegram bot (`python3 -m openhealth.telegram_bot run`) already carries `/checkin`, `/today`, `/ask`; with `--bridge-url http://openhealth:8770` plain intake is indexed in real time. A live run needs: a bot token (`~/.openhealth/telegram.token`), Docker running for `up`, and a working LLM provider (for `/ask`).

The phases ahead:
1. Telegram through the Hermes gateway as an alternative to our own bot: incoming → `/api/intake`; the `/today` and `/ask` commands → the OpenHealth data/agent API → a reply in chat (needs a bot token + gateway config).
2. cron through Hermes: a daily rebuild/sync and a morning insight in Telegram.
3. An identity/pairing bridge (Hermes user → OpenHealth context) + an access audit log.
4. Polish: backup/restore, healthchecks, a one-command installer.

# Integrations: direct-API provider catalog

OpenHealth connects to your devices **directly, over each vendor's own API**, in
the spirit of source-aware open-wearables tooling: you create your own API
credentials on the vendor's developer portal, OpenHealth pulls your data with
them, and everything lands in your **local** knowledge base. No middleman
aggregator, no third-party cloud account, no resale of your data. Where a vendor
offers no official API we say so plainly and document the honest fallback (file
export or a local bridge) instead of pretending.

Three honesty levels are used throughout (`status` in the catalog):

- **supported** — a connector ships in this repo today.
- **planned** — a real, self-serve API exists; the connector is not written yet.
- **export_only** — no official open API. The documented route is a file export
  or a local bridge (often Home Assistant or Gadgetbridge), never a fake API.

The machine-readable source of truth is
[`openhealth/data/providers.json`](../openhealth/data/providers.json), loadable
via `openhealth.providers` (`load_providers()`, `get_provider(id)`,
`validate_catalog()`). This document is the human rendering of the same content.

**Disclaimers.** Respect each vendor's rate limits and Terms of Service: these
are *personal-use* integrations, not products built on someone's data. Unofficial
endpoints (marked clearly below) can break without notice and may be
ToS-sensitive — use them at your own risk. Portal URLs and limits were checked
against public documentation in June 2026 and can drift; the `docs_url` links
are authoritative.

**Credential convention.** Secrets live in environment variables following
`OPENHEALTH_<PROVIDER>_<CREDENTIAL>`, e.g. `OPENHEALTH_WHOOP_CLIENT_ID`,
`OPENHEALTH_AWAIR_TOKEN`. Never commit them.

## Overview

| Provider | Category | Auth | Status | Connector |
|---|---|---|---|---|
| WHOOP | tracker | OAuth2 | **supported** | `openhealth/whoop.py` (live API) |
| Oura Ring | tracker | OAuth2 | **supported** | `connectors/oura.py` (export; live pull planned) |
| Garmin | tracker | OAuth2 (business-gated) | **supported** | `connectors/garmin.py` (export) |
| Apple Health | hub | file export | **supported** | `connectors/apple_health.py` |
| Fitbit (Google) | tracker | OAuth2 | planned | — |
| Polar | tracker | OAuth2 | planned | — |
| Suunto | tracker | OAuth2 + subscription key | planned | — |
| COROS | tracker | OAuth2 (application form) | planned | — |
| Withings | scale / BP / sleep | OAuth2 | planned | — |
| Dexcom CGM | cgm | OAuth2 | planned | — |
| Awair | air | developer token | planned | — |
| Airthings | air | OAuth2 (client credentials) | planned | — |
| Netatmo | air | OAuth2 | planned | — |
| Home Assistant | hub (universal local bridge) | local token | planned | — |
| Amazfit / Zepp | tracker | — | export_only | export / Gadgetbridge |
| Xiaomi (Mi Fitness) | tracker | — | export_only | export / Gadgetbridge / HA |
| Samsung Health | tracker | — | export_only | in-app export |
| Google Health Connect | hub | — | export_only | on-device export |
| FreeStyle Libre | cgm | — | export_only | LibreView CSV (unofficial API exists) |
| Levels & similar | cgm | — | export_only | connect underlying CGM |
| Omron | bp | — | export_only | OMRON connect CSV |
| Eufy scales | scale | — | export_only | app export / HA BLE |
| Eve / HomeKit | air | local | export_only | HA HomeKit/Matter bridge |
| Eight Sleep | sleep | — | export_only | data request / unofficial HA |
| Todoist | todo | personal API token (PAT) | **supported** | `connectors/todoist.py` (live API) |
| TickTick | todo | OAuth2 | planned | — |
| Things 3 | todo | local | export_only | URL scheme / AppleScript / local DB |
| Notion (task databases) | todo | integration token | planned | — |

28 providers: 5 supported, 12 planned, 11 export_only.

---

## Trackers

### WHOOP — supported

- Portal: <https://developer-dashboard.whoop.com/> (app creation) · Docs portal: <https://developer.whoop.com/> · Docs: <https://developer.whoop.com/docs/developing/getting-started/> · API: `https://api.prod.whoop.com/developer/v2`
- Data: recovery, HRV, resting HR, sleep, strain, workouts, cycles, respiratory rate, SpO2, skin temp.

Create credentials:

1. Sign in at https://developer-dashboard.whoop.com/ with your WHOOP account (the dashboard where apps are created).
2. Create a Team (once), then **Create App** (up to 5 apps per account).
3. Scopes: `read:profile`, `read:recovery`, `read:cycles`, `read:sleep` (`read:workout` optional). If your app is **not** granted some of these (e.g. no `read:profile` / `read:body_measurement`), request only the granted ones — otherwise the authorize step returns `invalid_scope`. Set `OPENHEALTH_WHOOP_SCOPES` (space- or comma-separated) or pass `--scope` to `whoop-auth-url`, and run `whoop-sync --no-profile --no-body-measurements` to skip endpoints you lack access to.
4. Add a redirect URL, e.g. `http://localhost:8765/callback` — it must match the OAuth request exactly.
5. Copy Client ID and Client Secret (the secret is server-side only).
6. Export `OPENHEALTH_WHOOP_CLIENT_ID`, `OPENHEALTH_WHOOP_CLIENT_SECRET`, `OPENHEALTH_WHOOP_REDIRECT_URI` (optionally `OPENHEALTH_WHOOP_SCOPES`), then run `openhealth whoop-auth-url` and `openhealth whoop-exchange-code` to finish the flow; `openhealth whoop-sync` pulls data.

Rate limits: per-app defaults around 100 req/min and 10,000/day — a daily sync uses a handful of calls.

### Oura Ring — supported (export connector; live OAuth2 pull planned)

- Portal: <https://cloud.ouraring.com/oauth/applications> · Docs: <https://cloud.ouraring.com/docs/authentication> · API: `https://api.ouraring.com/v2`
- Data: sleep, readiness, activity, HRV, resting HR, temperature, SpO2, respiratory rate, workouts.

Create credentials:

1. Sign in at cloud.ouraring.com.
2. Open **My Applications** (`/oauth/applications`) and create an application.
3. Set a redirect URI (whitelist), e.g. `http://localhost:8765/callback`.
4. Copy Client ID / Client Secret and run the OAuth2 code flow; call `/v2/usercollection/*` with `Authorization: Bearer <token>`.

Honest note: legacy Personal Access Tokens were **deprecated in December 2025** — OAuth2 is the only auth method now. Today the repo ships a file-export connector (`connectors/oura.py`); env vars `OPENHEALTH_OURA_CLIENT_ID` / `OPENHEALTH_OURA_CLIENT_SECRET` are reserved for the live client.

### Garmin — supported (export connector; official API is business-gated)

- Portal: <https://developer.garmin.com/gc-developer-program/health-api/> · API: `https://apis.garmin.com`
- Data: sleep, HRV, resting HR, stress, Body Battery, steps, workouts, respiration, SpO2.

Honest note: the Garmin Health API requires an approved **Garmin Connect Developer Program** application — granted to companies/institutions (company-domain email, business use), reviewed in about two business days. It is free, but not aimed at personal hobby projects.

Personal route (what our connector uses): account.garmin.com → **Export Your Data** (or per-metric CSVs from Garmin Connect) → feed files to `connectors/garmin.py`.

### Fitbit (Google) — planned

- Portal: <https://dev.fitbit.com/apps> · Docs: <https://dev.fitbit.com/build/reference/web-api/> · API: `https://api.fitbit.com`
- Data: sleep, HR, HRV, steps, SpO2, breathing rate, skin temp, weight, activity.

Create credentials:

1. Sign in at dev.fitbit.com with the Google account that owns your Fitbit data.
2. **Register a new app** at `dev.fitbit.com/apps`.
3. Choose OAuth application type **Personal** — unlocks intraday series for your own account.
4. Set redirect URI (e.g. `http://localhost:8765/callback`), save, copy Client ID / Secret.
5. OAuth2 Authorization Code + PKCE; scopes: `sleep heartrate activity weight oxygen_saturation`.

Rate limits: **150 requests/hour per user** — batch and cache. Env: `OPENHEALTH_FITBIT_CLIENT_ID`, `OPENHEALTH_FITBIT_CLIENT_SECRET`.

### Polar — planned

- Portal: <https://admin.polaraccesslink.com/> · Docs: <https://www.polar.com/accesslink-api/> · API: `https://www.polaraccesslink.com/v3`
- Data: sleep, Nightly Recharge, HRV, continuous HR, activity, exercises.

Create credentials:

1. Sign in at admin.polaraccesslink.com with your regular Polar Flow account (any Flow user can create a client).
2. **Create client**: application details + authorization redirect URL.
3. Copy Client ID / Client Secret.
4. OAuth2 code flow, then register the user via `/v3/users` before pulling data.

Env: `OPENHEALTH_POLAR_CLIENT_ID`, `OPENHEALTH_POLAR_CLIENT_SECRET`.

### Suunto — planned

- Portal: <https://apizone.suunto.com/> · Docs: <https://apizone.suunto.com/how-to-start> · API: `https://cloudapi.suunto.com`
- Data: workouts, daily activity, sleep.

Create credentials:

1. Register at apizone.suunto.com (Azure API Management portal).
2. Subscribe to the **Development API** product; keys appear under Profile → *Your subscriptions*.
3. Configure your OAuth app in the profile settings; authorization server: `cloudapi-oauth.suunto.com`.
4. Send `Ocp-Apim-Subscription-Key` on every Cloud API request plus the user's Bearer token.

Env: `OPENHEALTH_SUUNTO_SUBSCRIPTION_KEY`, `OPENHEALTH_SUUNTO_CLIENT_ID`, `OPENHEALTH_SUUNTO_CLIENT_SECRET`.

### COROS — planned

- Application form: <https://support.coros.com/hc/en-us/articles/17085887816340-Submitting-an-API-Application>
- Data: workouts, daily activity, sleep, HR.

Honest note: no self-serve portal — you submit an API application via the COROS Help Center, and credentials plus docs arrive after approval. Fallback without approval: export FIT files from the COROS app.

### Amazfit / Zepp — export_only

Honest note: the Zepp Open Platform (`dev.huami.com`) targets enterprise partners; Zepp OS developer docs cover on-watch apps, not personal cloud data. Routes: Zepp app GDPR export; **Gadgetbridge** (local BLE, no cloud); or sync into a platform you can read.

### Xiaomi (Mi Fitness / Mi Band) — export_only

Honest note: no official open API. Routes: Mi Fitness GDPR export; **Gadgetbridge** (fully local, exportable DB); for Xiaomi/Aqara *home sensors* use **Home Assistant** as the bridge (see below) — that path is first-class.

### Samsung Health — export_only

Honest note: the on-device Data SDK is Android-only and the server API is **partner-only**. Routes: Samsung Health → Settings → *Download personal data*; or sync to Health Connect and export there.

## Hubs and bridges

### Apple Health — supported (export)

Honest note: there is **no Apple Health cloud API by design**; HealthKit is on-device. Route: Health app → profile picture → **Export All Health Data** → feed `export.xml` to `connectors/apple_health.py`. A future on-device companion could read HealthKit live.

### Google Health Connect — export_only

Honest note: Health Connect is an **on-device** Android API; the old Google Fit REST API is deprecated/being shut down — do not build on it. Routes: on-device exporter apps; or the Home Assistant companion app pushing selected sensors to your HA instance. Fitbit cloud data is *not* behind Health Connect — use the Fitbit entry.

### Home Assistant — planned (the universal local bridge)

- Docs: <https://developers.home-assistant.io/docs/api/rest/> · API: `http://homeassistant.local:8123/api` (your LAN)

Why it matters: HA turns Xiaomi/Aqara, HomeKit/Eve, BLE scales and air monitors into readable local entities with history — the perfect local-first feeder for OpenHealth.

Create a token:

1. In the HA UI click your user name (bottom left) → **Security** tab.
2. **Long-lived access tokens** → *Create token*; copy immediately (shown once, valid 10 years).
3. Set `OPENHEALTH_HOME_ASSISTANT_URL` and `OPENHEALTH_HOME_ASSISTANT_TOKEN`.
4. Read `/api/states` and `/api/history/period/<ts>` with `Authorization: Bearer <token>`.

No vendor rate limits — it is your own server on your own LAN.

## Scales and blood pressure

### Withings — planned (the open-API workhorse: scale + BP + sleep mat + watch)

- Portal: <https://developer.withings.com/> · Docs: <https://developer.withings.com/api-reference/> · API: `https://wbsapi.withings.net`
- Data: weight, body composition, blood pressure, HR, ECG, sleep, SpO2, temperature.

Create credentials:

1. Create a regular Withings account, sign in at developer.withings.com, open the dashboard.
2. Create an application (free public-cloud plan suffices for personal use); set a callback URL.
3. Copy Client ID / Consumer Secret.
4. OAuth2 web flow, then call `/measure`, `/v2/sleep`, `/v2/heart`.

Rate limits: about 120 req/min per client. Env: `OPENHEALTH_WITHINGS_CLIENT_ID`, `OPENHEALTH_WITHINGS_CLIENT_SECRET`.

### Omron — export_only

Honest note: partner-only API, no self-serve portal. Route: OMRON connect app → export readings as CSV. If you want BP behind a real open API, Withings BPM is the alternative.

### Eufy scales — export_only

Honest note: Anker/Eufy publishes no API. Routes: EufyLife app export; community BLE readings into Home Assistant. Withings is the open-API alternative.

## CGM (glucose)

### Dexcom — planned

- Portal: <https://developer.dexcom.com/> · Docs: <https://developer.dexcom.com/docs/dexcom/getting-started/> · API: `https://api.dexcom.com` (sandbox: `https://sandbox-api.dexcom.com`, EU: `api.eu.dexcom.com`)
- Data: estimated glucose values (EGVs), events, calibrations, alerts, devices.

Create credentials:

1. Register at developer.dexcom.com — sandbox access is immediate, no approval gate.
2. Create an app (name, description, redirect URI) → Client ID / Secret.
3. Develop against the sandbox (simulated users), then run OAuth2 against production.
4. Pull `/v3/users/self/egvs`. Know the limit: data is **retrospective with a delay**, not a real-time stream.

Env: `OPENHEALTH_DEXCOM_CLIENT_ID`, `OPENHEALTH_DEXCOM_CLIENT_SECRET`.

### FreeStyle Libre (Abbott) — export_only

Honest note: **no official public API.** Official route: LibreView (libreview.com) → *Download glucose data* CSV. Unofficial route (at your own risk, may break, ToS-sensitive): the LibreLinkUp endpoints (`api-eu.libreview.io` and regional variants) used by community tools like `nightscout-librelink-up`, authenticated with LibreLinkUp follower credentials. For a supported CGM API, see Dexcom.

### Levels, January AI and similar — export_only

Honest note: these apps sit on top of Dexcom/Libre sensors and expose no public API of their own. Connect the underlying sensor directly; some apps offer in-app CSV export.

## Air quality and environment

### Awair — planned

- Portal: <https://developer.getawair.com/> · Docs: <https://docs.developer.getawair.com/> · API: `https://developer-apis.awair.is/v1`
- Data: temperature, humidity, CO2, VOC, PM2.5, Awair score.

Create a token:

1. Log in at developer.getawair.com with your Awair account.
2. Request an Access Token — **Hobbyist tier is approved automatically**.
3. Set `OPENHEALTH_AWAIR_TOKEN`; call `/v1/users/self/devices` and `.../air-data/latest` with the Bearer token.

Rate limits: Hobbyist tier has daily quotas — poll every 15+ minutes.

### Airthings — planned

- Portal: <https://dashboard.airthings.com/integrations/api-integration> · Docs: <https://developer.airthings.com/docs/api> · API: `https://ext-api.airthings.com/v1`
- Data: radon, CO2, VOC, PM, humidity, temperature, pressure.

Create credentials:

1. Sign in at dashboard.airthings.com → Integrations → **API Clients**.
2. Create a client with scope `read:device:current_values`; store Client ID / Secret.
3. OAuth2 **client credentials** grant: `POST https://accounts-api.airthings.com/v1/token`.
4. Call `/v1/devices` and `/v1/devices/{sn}/latest-samples`.

Rate limits: consumer API is limited (~120 req/hour) — sample every few minutes at most. Env: `OPENHEALTH_AIRTHINGS_CLIENT_ID`, `OPENHEALTH_AIRTHINGS_CLIENT_SECRET`.

### Netatmo — planned

- Portal: <https://dev.netatmo.com/apps/createanapp> · Docs: <https://dev.netatmo.com/apidocumentation/weather> · API: `https://api.netatmo.com`
- Data: CO2, temperature, humidity, noise, pressure (Weather Station, Healthy Home Coach).

Create credentials:

1. Sign in at dev.netatmo.com with your Netatmo account.
2. Create an app at `/apps/createanapp` — Client ID / Secret are generated immediately.
3. For personal use, the portal's **Token Generator** mints a token directly: pick scopes (`read_station`, `read_homecoach`) and generate.
4. Call `/api/getstationsdata` or `/api/gethomecoachsdata` with the Bearer token.

Env: `OPENHEALTH_NETATMO_CLIENT_ID`, `OPENHEALTH_NETATMO_CLIENT_SECRET`.

### Eve / HomeKit devices — export_only (local)

Honest note: Eve is deliberately cloud-free (HomeKit/Thread/Matter only). Route: pair with Home Assistant via the [HomeKit Device integration](https://www.home-assistant.io/integrations/homekit_controller/) or Matter, then read through the HA API. A fully local chain — exactly the spirit of this project.

## Sleep hardware

### Eight Sleep — export_only

Honest note: no official public API; community projects reverse-engineer the app's cloud API and can break anytime. Routes: personal data request to support (GDPR/CCPA); or the unofficial Home Assistant integration read via the HA bridge.

## Tasks (todo)

Why todo services belong in a health knowledge base: a completed task is a
dated behavior signal. Closing "Тренировка", "Morning run" or "Записаться к
врачу" is real evidence of something the journal should know about. The
connector therefore turns **completed tasks of the day into journal
candidates** — keyword-filtered suggestions a human reviews, never auto-logged
facts — and reads today's active tasks as schedule-load context.

Privacy: everything stays local. Task content is fetched directly from the
vendor API with your own token, filtered on your machine, and only the
candidates you approve enter the journal. Nothing is sent to any third party.

### Todoist — supported

- Portal: <https://developer.todoist.com/> · Docs: <https://developer.todoist.com/rest/v2/> · API: `https://api.todoist.com/rest/v2` (+ Sync v9 `completed/get_all` for history)
- Data: completed tasks (per day), active tasks due today, projects, labels → journal candidates.

Create a token (personal API token — the lowest-friction PAT route, no OAuth app needed):

1. Open Todoist → **Settings → Integrations → Developer** tab.
2. Copy the **API token** shown there (full account access — keep it local, never commit it).
3. Set `OPENHEALTH_TODOIST_TOKEN` (or write the token to `~/.openhealth/todoist.token`).

What we pull: `fetch_completed(date)` reads the day's completed tasks via Sync
v9 `completed/get_all` (the REST API has no completed history), paginated;
`fetch_today_tasks()` reads active tasks due today via REST v2;
`health_candidates(tasks)` filters by RU + EN word-prefix stems (тренир, спорт,
зал, бег, йог, массаж, врач, анализ, сон, медит, прогул / walk, run, gym,
workout, yoga, doctor, sleep, meditat, …) plus `health` / `fitness` labels.
Without a token the connector raises `TodoistNotConfigured` with these same
steps instead of returning silent empties.

Rate limits: REST v2 allows **450 requests per 15 minutes** per user; a daily
pull uses a handful of calls.

### TickTick — planned

- Portal: <https://developer.ticktick.com/> · Docs: <https://developer.ticktick.com/docs#/openapi> · API: `https://api.ticktick.com/open/v1`

Create credentials:

1. Register an app at developer.ticktick.com (**Manage Apps**) — Client ID / Client Secret are issued there.
2. Set an OAuth redirect URL, e.g. `http://localhost:8765/callback`.
3. Run the OAuth2 code flow with scope `tasks:read`, then call the Open API with the Bearer token.

Env: `OPENHEALTH_TICKTICK_CLIENT_ID`, `OPENHEALTH_TICKTICK_CLIENT_SECRET`. Open
API quotas are not publicly documented in detail — poll once or twice a day.

### Things 3 — export_only (local)

Honest note: **no cloud API** — Things Cloud is sync-only with no public
endpoints. Local routes on macOS: the [`things:///` URL scheme](https://culturedcode.com/things/support/articles/2803573/)
(can add/show tasks, cannot read completed history); AppleScript automation or
the local Things SQLite database for reading completed to-dos; or File →
Export. Fully local — which fits this project, just without a cloud connector.

### Notion (task databases) — planned

- Portal: <https://www.notion.so/my-integrations> · Docs: <https://developers.notion.com/> · API: `https://api.notion.com/v1`

Create credentials:

1. Create an **internal integration** at notion.so/my-integrations and copy the integration token.
2. Share your task database(s) with the integration (database page → connections) — without this the API sees nothing.
3. Set `OPENHEALTH_NOTION_TOKEN` and query the data source with a filter on the status/checkbox property (use `data_source_id`, not the legacy `database_id`).

Rate limit: about 3 requests/second per integration — paginate gently.

Orchestrator contract (future endpoint, not implemented yet): `GET
/api/todos?date=YYYY-MM-DD` → `{"completed": [...], "candidates": [...]}` where
`completed` is the `fetch_completed()` output and `candidates` is
`health_candidates(completed)`.

---

## Adding a new connector

1. Pick a `planned` provider; its catalog entry already lists the portal, auth and endpoints.
2. Follow the existing connector pattern (`connectors/oura.py`, `openhealth/whoop.py`): pure stdlib, clean-room from public docs, Observation-shaped output, provenance preserved.
3. Update the provider's `status`/`connector` fields in `openhealth/data/providers.json` — `tests/test_providers.py` keeps the catalog honest.

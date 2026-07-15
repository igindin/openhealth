# Self-host: OpenHealth + Hermes на сервере

OpenHealth умеет жить в двух режимах:

- **Локальный (по умолчанию):** мост на `127.0.0.1`, `OpenHealth.command` или `.app`, данные не уходят с машины. Ничего из этого документа для него не нужно.
- **Self-host (этот документ):** OpenHealth и [Hermes](https://github.com/NousResearch/hermes) на твоём сервере. Смотришь через веб-UI или через Telegram, обе точки пишут одну базу здоровья.

Оба режима работают из одного кода. Self-host это отдельный профиль, локальный ничего не теряет.

## Роли

- **OpenHealth** несёт здоровье: движок (recovery/HRV/инсайты/протоколы), health-базу (`health_os.sqlite3`: sources / artifacts / records), веб-UI, журнал.
- **Hermes** несёт платформу: мессенджер-gateway (Telegram и др.), cron (планировщик), session-память, identity через pairing, OpenAI-совместимый LLM-proxy.

Они общаются только по HTTP. Health-база OpenHealth это единственный источник правды по здоровью; у Hermes своя session-база (`state.db`) для переписки. Их не сливают.

## Топология

```
                       ┌──────────────── твой сервер (docker compose) ─────────────────┐
   браузер ── :443 ──▶ │  Caddy  (TLS + Basic Auth)                                     │
                       │    └── reverse_proxy ──▶ openhealth:8770  (веб-UI + /api)      │
                       │                              └── health_os.sqlite3  ◀── одна база│
   Telegram ─────────▶ │  hermes-gateway  (бот + cron)                                  │
                       │    └── входящее ──▶ POST openhealth:8770/api/intake ──▶ база    │
                       │  hermes-proxy :8645  (OpenAI-совместимый LLM)                   │
                       │    └── OpenHealth берёт его как движок LLM                      │
                       │  тома: health_data (/data)   hermes_data (/opt/data)           │
                       └───────────────────────────────────────────────────────────────┘
```

## Одна база

Любой источник (веб-чек-ин, Telegram через Hermes, вебхук) шлёт **IntakeEnvelope** на `POST /api/intake`. Мост валидирует его, кладёт запись `ContextNote` в health-базу и зеркалит сырой конверт на диск (`data/intake/<channel>/…`, неизменяемая провенанс-копия). Поэтому «внёс в телеге - видно в вебе»: обе точки пишут один индекс.

Контракт конверта: `schemas/intake-envelope.schema.json` (обязательные поля: `submission_id`, `submitted_at`, `channel`, `author`; опционально `text`, `location`, `attachments`, `tags`, `metadata`).

## Быстрый старт

Нужен сервер с Docker, доменом (A-запись на сервер) и открытыми портами 80/443.

```bash
# 1. Собрать образ Hermes один раз (у Hermes свой Dockerfile).
docker build -t hermes-agent /path/to/hermes-agent

# 2. Настроить Hermes: провайдер LLM и (опц.) Telegram-бот.
#    Делается внутри тома hermes_data — см. доку Hermes (hermes login / setup).

# 3. Заполнить конфиг OpenHealth.
cp deploy/.env.example deploy/.env
#    домен, Basic-Auth логин + ХЕШ пароля, токен бота. Хеш пароля:
docker run --rm caddy:2 caddy hash-password --plaintext 'сильный-пароль'

# 4. Поднять стек.
docker compose -f deploy/docker-compose.yml up -d
```

Открой `https://<домен>`, введи Basic-Auth логин и пароль. Готово.

### Локальный тест на Mac (без Docker Desktop)

Реальный сервер под self-host это Linux (VPS): там Docker (или Podman) с `docker compose` работают нативно, ничего ставить сверх пакета не нужно. Docker Desktop и подобное для сервера не требуются.

Если просто хочешь прогнать образ OpenHealth локально на Mac перед выкатыванием, Docker Desktop тоже не обязателен, бери лёгкий рантайм:

- **colima** (`brew install colima docker`) - обычный Docker-демон в лёгкой VM;
- **Apple `container`** (open source с WWDC 2025) - энергоэффективнее на Apple Silicon, отдельная лёгкая VM на контейнер.

Пример прогона одного образа (colima):

```bash
colima start
docker build -f deploy/openhealth.Dockerfile -t openhealth-bridge .
docker run --rm -p 8770:8770 -v "$PWD/oh-data:/data" openhealth-bridge
# открой http://localhost:8770  (health: http://localhost:8770/api/health)
```

Оговорка: Apple `container` хорошо гоняет одиночные OCI-образы; полный многосервисный `docker compose` (Caddy + OpenHealth + Hermes) держится на Docker/Podman, то есть на том же рантайме, что и на Linux-сервере. Локальный прогон одного образа OpenHealth без внешнего TLS-домена и LLM-провайдера ожидаемо не поднимет Caddy и Hermes целиком, это нормально: те два сервиса настраиваются под конкретный сервер.

## Безопасность (читать обязательно)

Это медицинские данные. Модель безопасности простая и жёсткая:

- **Мост OpenHealth не имеет своей авторизации.** Его локальный режим полагался на `127.0.0.1`. На сервере он поднят на `0.0.0.0`, но **порт не публикуется** (в compose нет `ports:` у `openhealth`), поэтому снаружи он недостижим. Единственный вход это Caddy.
- **Caddy даёт TLS + Basic Auth.** Без валидного логина внутрь не попасть. Никогда не добавляй `ports:` сервису `openhealth` и не открывай 8770 наружу.
- **Секреты не в репозитории.** `deploy/.env` в gitignore; пароль хранится хешем; токены проходят через env.
- **Мультиюзера нет.** Self-host рассчитан на одного человека (тебя). Раздавать доступ нескольким людям с изоляцией данных это отдельная большая задача, здесь её нет.
- Хочешь строже: добавь IP-allowlist в Caddy, ключ-клиентский сертификат (mTLS) или доступ только через VPN/SSH-туннель.

## LLM через Hermes

OpenHealth ходит в Hermes-proxy как в OpenAI-совместимый эндпоинт (`/v1/chat/completions`), а не через `hermes -z` (тот интерактивный one-shot может подвисать на старте gateway). Настройка:

- `OPENHEALTH_LLM_BASE_URL=http://hermes-proxy:8645` (уже в `.env.example`);
- в UI: **Настройки → Агент → Hermes** (или `POST /api/config {"agent":"hermes","base_url":"http://hermes-proxy:8645"}`).

Любой bearer-токен подходит: proxy подставляет реальные креды провайдера из `hermes_data`. Если провайдер Hermes не настроен (`hermes proxy status` показывает «not logged in»), LLM-разбор не поедет; OpenHealth тогда использует свой агент (claude/codex), если он есть в образе. LLM через Hermes это опциональный слой, не обязательный.

## Что уже есть и что дальше

Фаза 0 (сделано и проверено): `--host` у моста, `POST /api/intake` (шов «одна база») — round-trip проверен: telegram-конверт ложится в health-индекс как `ContextNote` (`indexed: true`); режим LLM через hermes-proxy; этот deploy-скелет (`docker compose config` валиден). Образ OpenHealth (`deploy/openhealth.Dockerfile`) собирается чисто и, поднятый контейнером со смонтированным томом `/data`, реально отдаёт веб-UI (`<title>OpenHealth</title>`), статику и `/api/*` (health возвращает build-штамп) — смоук-тест пройден локально. Telegram-бот движка (`python3 -m openhealth.telegram_bot run`) уже несёт `/checkin`, `/today`, `/ask`; с `--bridge-url http://openhealth:8770` plain-intake индексируется в реальном времени. Для живого запуска нужны: bot-токен (`~/.openhealth/telegram.token`), запущенный Docker для `up`, и рабочий LLM-провайдер (для `/ask`).

Дальше по фазам:
1. Telegram через Hermes-gateway как альтернатива своему боту: входящее → `/api/intake`; команды `/today`, `/ask` → data/agent API OpenHealth → ответ в чат (нужен bot-токен + конфиг gateway).
2. cron через Hermes: ежедневный rebuild/sync и утренний инсайт в Telegram.
3. Мост identity/pairing (пользователь Hermes → контекст OpenHealth) + audit-лог доступа.
4. Полировка: backup/restore, healthchecks, one-command installer.

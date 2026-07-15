# OpenHealth дашборд — запуск как приложение (без ручного localhost)

Чтобы не запускать `python3 -m http.server` каждый раз и чтобы дашборд не падал — три уровня, от простого к нативному.

## 1. Двойной клик (просто)
`OpenHealth.command` — двойной клик в Finder. Поднимает сервер в фоне (переживает закрытие окна) и открывает дашборд. Если сервер уже работает — просто открывает.

```
chmod +x OpenHealth.command   # один раз
```

## 2. Автозапуск-сервис (не падает, всегда доступен)
`com.openhealth.dashboard.plist` — launchd-агент: стартует при логине, перезапускается если упал (`KeepAlive`). Дашборд всегда на `http://localhost:8770`.

Установка (один раз, заменить путь и загрузить):
```
WEB_DIR="$(pwd)"
sed "s#__WEB_DIR__#${WEB_DIR}#" com.openhealth.dashboard.plist > ~/Library/LaunchAgents/com.openhealth.dashboard.plist
launchctl load ~/Library/LaunchAgents/com.openhealth.dashboard.plist
```
Выгрузить: `launchctl unload ~/Library/LaunchAgents/com.openhealth.dashboard.plist`.

> Это меняет системную конфигурацию (автозапуск). Ставить осознанно, своими руками.

## 3. Нативное приложение — OpenHealth.app (готово)
Полноценный macOS `.app` без Electron/Tauri и без зависимостей — генерируется скриптом `make_macos_app.py` (stdlib + встроенные macOS-инструменты):

```
make app-install     # соберёт ui/web/dist/OpenHealth.app и положит в ~/Applications
```

При запуске приложение: если сервер на 8770 не отвечает — поднимает `server.py` в фоне (подхватывает WHOOP-креды из `~/health-os/.env.whoop.local`, если файл есть; лог в `~/Library/Logs/OpenHealth.log`), дожидается ответа и открывает дашборд отдельным окном в app-режиме Chrome (своя иконка в Dock, без браузерного хрома); без Chrome — в браузере по умолчанию. Иконка рендерится из `assets/icon.svg` (qlmanage → sips → iconutil, с graceful-фолбэком).

Ограничение: в launcher зашит абсолютный путь текущего чекаута — после переноса репозитория перезапусти `make app-install`.

Будущее (отдельный desktop-батч): встроенный webview (Tauri) + Local Claude/Codex passthrough для агентных действий.

Реальные данные (`data.local.json`) живут ТОЛЬКО локально у пользователя (в его health-папке), в этот репозиторий не попадают. Дашборд читает их при наличии, иначе показывает demo.

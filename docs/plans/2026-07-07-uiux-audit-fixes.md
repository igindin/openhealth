# Plan: UI/UX audit fixes + sync integration (2026-07-07)

**Goal:** Закрыть все 15 пунктов UI/UX аудита (Chrome, живые данные) и 4 разрыва интеграции-синхронизации веб-аппа с движком.

**Architecture:** Всё в `~/Projects/openhealth`: V1 `ui/web/dashboard.html` (основной скин), `ui/web/assets/oh-i18n.js`, V2 `ui/web/dashboard-v2.html`, мост `ui/web/server.py`. Данные уже общие через bridge (`/api/journal/*`, `/api/sync`, `/api/data`); чиним читающую сторону UI и подачу состояния.

**Tech Stack:** vanilla JS single-file + GSAP, Python stdlib bridge, pytest.

**Created:** 2026-07-07

**Success Signals:** повторный проход аудита не находит P0; отметка за 6 июля видна в Day Feed на чистом origin; при открытии с данными >24ч дашборд сам синкается; 654+ тестов зелёные.

**DON'T DO:** не трогаем V2-миграцию разделов, движок расчётов (кроме avg-бага тренда), Hermes-фазу 1, мультиюзер. Никаких коммитов без отдельной команды.

**Verify First:** сервер 8770 — старый процесс (без `/api/intake`); проверки, требующие нового моста, гонять на скретч-порту.

---

## Волна 1 — доверие к данным и синхронизация (P0 + разрывы A-D)

### Task 1: Добивать анимации при возврате видимости вкладки
**File:** `ui/web/dashboard.html`
**Action:** Рядом с блоком `REDUCED_MOTION` добавить: на `visibilitychange→visible` и `focus` пробегать активные GSAP-твины и `progress(1)`. Кольцо/каунтеры перестанут застревать на «5» вместо 22 в фоновом/перекрытом окне.
**Verify:** Chrome: открыть вкладку в фоне, сфокусировать — цифры и opacity сразу финальные.

### Task 2: Чип свежести данных в топбаре
**File:** `ui/web/dashboard.html`
**Action:** Возле recovery-пилюли чип возраста данных из `DATA._meta.generatedAt`/`DATA.date`: «сегодня» тихий; «N дн назад» янтарный, клик → `go('sync')`. Данные старше 24ч больше не молчат.
**Verify:** подменить generatedAt в консоли → чип янтарный; клик ведёт в Data Sources.

### Task 3: Авто-синк при открытии
**File:** `ui/web/dashboard.html`
**Action:** На загрузке: bridge online && дата данных < сегодня && антидребезг (`openhealth.autosync.last` > 6ч назад) → `POST /api/sync?days=3`; успех → перезагрузка DATA + тост «Данные обновлены»; ошибка → тихий фолбэк `POST /api/rebuild`. Использовать `OHNotify.toast`.
**Verify:** со старым снапшотом открыть страницу — в Network уходит /api/sync, после — свежая дата в футере.

### Task 4: Day Feed читает серверный журнал (разрыв №1)
**File:** `ui/web/dashboard.html` (`renderTimeline`, `tlDayEventsHTML`)
**Action:** Рендерить ленту сразу из localStorage, параллельно `GET /api/journal/range?start=&end=` за окно ленты; серверные дни мержить в localStorage (`setIfEmpty`-семантика по дням) и перерисовать чипы. Отметка, сделанная с другого устройства/из Telegram, становится видимой.
**Verify:** очистить localStorage → Timeline: 6 июля показывает чип «Лечь до 23:30» (лежит на сервере), не «без отметок».

### Task 5: Окно ленты до реального «сегодня» (разрыв №2)
**File:** `ui/web/dashboard.html` (`renderTimeline`)
**Action:** Ряды строить от `todayKey()` назад на 14 дней; recovery-значение искать по смещению от даты выгрузки, где данных нет — нейтральный чип «—». Внести за вчера и увидеть вчера — работает даже до синка трекера.
**Verify:** при снапшоте от 5 июля лента начинается с 7 июля (сегодня), 6-7 без recovery, но с отметками.

### Task 6: Глушение демо-данных (аудит №1)
**Files:** `ui/web/dashboard.html`, `ui/web/assets/oh-registry.js` (если чип там)
**Action:** Плиткам с провенансом «демо» — класс `is-demo` (CSS: desaturate + opacity .55); секции, где все плитки демо, — один баннер «Раздел на демо-данных — нужен intraday-синк» + CTA в Data Sources. Демо перестаёт маскироваться под реальное.
**Verify:** Sleep/Stress: плитки приглушены, баннер есть; Workouts (реальные) не тронут.

### Task 7: Trends — оси, зоны, честное среднее (аудит №4)
**File:** `ui/web/dashboard.html` (рендер трендов)
**Action:** (а) подписи min/mid/max по Y у recovery/HRV; (б) зонные полосы фоном recovery (≥67 зел., 34-66 янт., <34 красн.); (в) среднее считать только по реальным точкам (пропуски с forward-fill исключить — сейчас HRV «120 мс» при сегодняшних 62); (г) смягчить сглаживание (ложные плато).
**Verify:** avg HRV совпадает с ручным расчётом по данным `/api/data`; на графике видны значения оси.

### Task 8: Methodologies — честная загрузка (аудит №5)
**File:** `ui/web/dashboard.html` (`renderMethodology`)
**Action:** Таймаут 4с → 10с; по catch — состояние ошибки с кнопкой «Повторить» вместо вечного спиннера.
**Verify:** заблокировать эндпоинт (devtools offline) → через 10с ошибка с retry.

### Task 9: Версия моста в /api/health (разрыв №3)
**Files:** `ui/web/server.py`, `ui/web/dashboard.html`, `tests/test_bridge_server.py`
**Action:** `BUILD = "YYYY-MM-DD"` константа → `/api/health {..., build}`; UI при расхождении мажорных возможностей (нет build либо старее ожидаемого) показывает бейдж «bridge устарел — перезапусти OpenHealth.command» в Diagnostics и футере. Тест на поле build.
**Verify:** `curl /api/health` содержит build; pytest зелёный.

## Волна 2 — язык и консистентность (P1)

### Task 10: i18n-добивка EN + фикс артефакта
**File:** `ui/web/assets/oh-i18n.js` (+bump `?v=4` в обоих скинах)
**Action:** Добавить: зонные фразы word() (красная/зелёная), ярлыки колец Vitals («Восстановление»→Recovery), базовые строки Day Pulse, ярлыки формы Meds (НАЗВАНИЕ/ВАКЦИНА и RU-плейсхолдеры «утро», «сам назначил»), футер («данные на», «Локально · твои данные…» уже есть — сверить). Исправить «Daily strain (Strain)» → «Daily strain».
**Verify:** EN-режим: Today красная/зелёная зона на английском; кольца Vitals одноязычны; Meds-форма одноязычна.

### Task 11: Biomarkers — слить дубль-колонки (аудит №7)
**File:** `ui/web/dashboard.html` (рендер таблицы биомаркеров)
**Action:** Одна колонка «Диапазон»: референс, и рядом «опт. X-Y» только когда оптимум отличается. Освободившуюся ширину — названию и шкале.
**Verify:** в строках с равными диапазонами значение не дублируется; High/Optimal статусы не сломаны.

### Task 12: Workouts — автовыбор и аффорданс (аудит №8)
**File:** `ui/web/dashboard.html`
**Action:** При входе автоselect последнего дня (деталь заполнена сразу), `selected`-состояние строки, hover, подпись шкалы бара «strain 0-21».
**Verify:** вход в Workouts — правая панель сразу с метриками последнего дня.

## Волна 3 — полировка (P2)

### Task 13: Day Pulse — поле ICS вместо инструкции про POST (аудит №9)
**File:** `ui/web/dashboard.html`
**Action:** Инпут «ICS-ссылка» + кнопка «Подключить» → `POST /api/calendar {ics_url}` (эндпоинт есть); шаги Google/Apple оставить как подсказку, строку про «POST /api/calendar с JSON» убрать.
**Verify:** ввод ссылки → 200, карточка переключается в состояние «подключён».

### Task 14: Research — state-aware empty (аудит №10)
**File:** `ui/web/dashboard.html`
**Action:** Если `BRIDGE.ok` — текст «Запусти дип-ресёрч с экрана Биомаркеры», упоминание «нужен запущенный bridge» только при офлайне.
**Verify:** при онлайн-bridge противоречия в копи нет.

### Task 15: Journal — чек-ин выше настройки (аудит №11)
**File:** `ui/web/dashboard.html`
**Action:** Блок сегодняшнего чек-ина перенести первым, «Настройка отслеживания» — ниже. Action-first.
**Verify:** вход в Journal — сверху отметки за выбранный день.

### Task 16: Protocols — дедуп дисклеймера (аудит №12)
**File:** `ui/web/dashboard.html`
**Action:** Дисклеймер «самонаблюдение, не лечение» рендерить один раз под сеткой, из карточек убрать.
**Verify:** на экране один дисклеймер при двух карточках.

### Task 17: Timeline — схлопывать пустые серии (аудит №13)
**File:** `ui/web/dashboard.html` (`renderTimeline`)
**Action:** ≥3 подряд дней без отметок → одна строка «N дней без отметок» (раскрывается кликом).
**Verify:** на текущих данных лента ужимается, дни с отметками не свёрнуты.

### Task 18: V2 — плавающий бар и дата (аудит №14)
**File:** `ui/web/dashboard-v2.html`
**Action:** `padding-bottom` контенту под бар; в шапке подпись «данные за <дата>» вместо подачи даты выгрузки как сегодняшней.
**Verify:** «Срочное сегодня» не перекрыто; дата подписана как дата данных.

### Task 19: Мелочи — Stress-деления, Meds-шеврон (аудит №15)
**File:** `ui/web/dashboard.html`
**Action:** Гейджи Stress: риски зон на дуге + диапазон под значением; select'ам Meds-формы — стрелка (CSS `background-image` chevron) и `cursor:pointer`.
**Verify:** визуально: гейджи читаются при малых значениях, селекты отличимы от инпутов.

---

## Финальная проверка (VERIFY волна)

1. `python3 -m pytest -q` — всё зелёное (654+).
2. `node -c assets/oh-i18n.js`; `python3 -c "ast.parse(server.py)"`.
3. Chrome-проход: Today (обе зоны), Timeline (серверные отметки+сегодня), Trends (оси/зоны/avg), Sleep (демо приглушено), Biomarkers, Workouts, Day Pulse, Journal, Protocols, Methodologies, V2 — на свежих данных, светлая+тёмная.
4. Паритет-тест скинов (`tests/test_dashboard_parity.py`) не сломан.

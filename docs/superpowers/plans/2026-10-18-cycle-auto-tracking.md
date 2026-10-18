# Автоматический учёт менструального цикла — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить ручной выбор «дня цикла» корзинками на непрерывный счёт от события «месячные начались» (модель Flow/Flo): якорь-событие → вычисляемые день/фаза/прогноз → задержка как алерт врачу.

**Architecture:** Новый чистый модуль `cycle_engine.py` (арифметика цикла, без БД и Telegram). `database.py` получает таблицу `cycle_events`, кэш `athletes.last_period_start`, флаг `athletes.menarche_status` и переключает обучение длины цикла на события. Шаги опроса (одноразовый якорь, ежедневный тап) живут в `handlers_athlete.py`/`handlers_survey.py` тонкой обёрткой над чистаемыми хелперами (построители клавиатур/парсеры — чистые функции). Поверхности отчётов (`daily_report`, `coach_week_summary`, `show_cycle_info`, trend-alerts) читают одно и то же состояние из движка.

**Tech Stack:** Python 3.12 (сервер; локально 3.14), stdlib + уже используемые (python-telegram-bot, sqlite3, pytest). Новый рантайм-зависимостей нет.

**Spec:** `docs/superpowers/specs/2026-10-18-cycle-auto-tracking-design.md`

## Global Constraints

- Только существующие зависимости; stdlib-стиль проекта, комментарии на русском.
- Тексты кнопок/сообщений — русские, `parse_mode="Markdown"` как в текущих handler'ах.
- Не трогать бэкап-копии (`bot_bak_*`, `bot_prod_*`, `_prod_bot.py`, `database_prod_*`).
- Миграции — идемпотентные `try ALTER / except pass` внутри `_create_tables()` (стиль database.py:324-328).
- Тесты кладём в `tests/`, прогон `python -m pytest tests -q` из корня репо обязан оставаться зелёным (сейчас 47 passed).
- История `daily_wellness` (784 строки с корзинками) не переписывается.
- Пороги: задержка = 7 дней после прогноза; аменорея = ≥90 дней от якоря; ручная корректировка дня допускает 1..L+30.
- События цикла пишутся в момент тапа (не в `_finish_survey`).

## Review Focus

1. Повторный тап «🩸 Да» в тот же день — не создаёт второй `start`-event на ту же дату (идемпотентность), и не сбрасывает цикл «на второй день».
2. Девушка пропустила 3 опроса подряд — счётчик продолжает идти от якоря (дни не теряются и не обнуляются пропуском).
3. Ввод даты якоря в будущем или дальше 90 дней назад — вежливый отказ с подсказкой, тихого сохранения нет.
4. Девушка без якоря, но с старой корзинной историей — в «Мой цикл» и отчётах нет смеси вычисленного ряда со старыми корзинами (stored показываются только до первой даты якоря).
5. Медиана научила длине 32 вместо prior 28 — текущий день не прыгает (он от даты якоря), и сразу после смены длины не возникает ложной «задержки» (прогноз пересчитывается от той же даты).

---

### Task 1: cycle_engine — чистая арифметика цикла

**Files:**
- Create: `cycle_engine.py`
- Test: `tests/test_cycle_engine.py`

**Interfaces:**
- Produces:
  - `cycle_day(start: date, today: date) -> int` — `(today-start).days + 1`, без оборачивания; `today < start` → ValueError.
  - `next_period_eta(start: date, length: int) -> date` — `start + length дней`.
  - `delay_days(start: date, length: int, today: date) -> int` — дней сверх прогноза, 0 если не задержка.
  - `status(start: Optional[date], length: int, today: date) -> str` — `'no_anchor' | 'amenorrhea' | 'delay' | 'ok'` (amenorrhea: `(today-start).days >= 90`; delay: `delay_days > 7`; приоритет amenorrhea выше).
  - `cycle_step_for(gender: Optional[str], start: Optional[date], menarche_status: Optional[str]) -> str` — `'skip' | 'anchor' | 'daily'`: male/None → 'skip'; female + menarche_status in ('pre','skip') → 'skip'; female без якоря → 'anchor'; иначе 'daily'.
  - константы `DELAY_THRESHOLD_DAYS = 7`, `AMENORRHEA_DAYS = 90`, `DAY_CORRECTION_MARGIN = 30`.
  - `parse_day_input(text: str, length: int) -> Optional[int]` — день для корректировки, допуск `1..length + DAY_CORRECTION_MARGIN`.

- [ ] **Step 1: Написать падающие тесты** `tests/test_cycle_engine.py`: cycle_day: старт 01.10, сегодня 01.10 → 1; 03.10 → 3; today<start → ValueError; без wrapping (старт 01.09, цикл 28, сегодня 18.10 → 48). delay_days: (start, L=28, today=start+30) → 3; today=start+28 → 0. status: None→'no_anchor'; start+100→'amenorrhea'; start+36 при L=28 → 'delay'; start+20 → 'ok'; start+33 при L=28 (delay=5) → 'ok'. cycle_step_for: таблица 6 комбинаций из сигнатуры выше. parse_day_input: '18' при L=28 → 18; '50' при L=28 → None; '58' при L=28 (28+30) → 58; 'abc' → None; '0' → None.
- [ ] **Step 2: Прогон, убедиться что модуль отсутствует** (`pytest tests/test_cycle_engine.py -q` → ModuleNotFoundError).
- [ ] **Step 3: Реализовать `cycle_engine.py`** — минимально под тесты, docstring на русском.
- [ ] **Step 4: pytest зелёный (файл), затем `python -m pytest tests -q` — весь зелёный.**
- [ ] **Step 5: Commit** `git add cycle_engine.py tests/test_cycle_engine.py; git commit -m "feat(cycle): pure cycle arithmetic engine (day/eta/delay/status)"`

### Task 2: БД — таблица cycle_events, якорь, menarche_status

**Files:**
- Modify: `database.py` (`_create_tables` ~320-346 по образцу ALTER; методы рядом с `get_cycle_history` :373)
- Test: `tests/test_db_cycle_events.py`

**Interfaces:**
- Consumes: стиль Database из Task 1 ничего не требует.
- Produces:
  - схема: `cycle_events(id, athlete_id NOT NULL, event_date DATE NOT NULL, type TEXT NOT NULL CHECK in ('start','anchor','end'), source TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(athlete_id, event_date, type))`; `athletes.last_period_start DATE` (ALTER try/except); `athletes.menarche_status TEXT` ('pre'|'skip', NULL = не отвечала).
  - `add_cycle_event(athlete_id: int, event_date: date, type: str = 'start', source: str = 'survey_button') -> bool` — INSERT OR IGNORE (идемпотентность Review Focus №1) + обновляет `athletes.last_period_start = max(существующий, event_date)` при 'start'/'anchor'.
  - `get_last_period_start(athlete_id: int) -> Optional[date]`.
  - `get_cycle_events(athlete_id: int, types: tuple = ('start','anchor')) -> List[date]` — по возрастанию.
  - `set_menarche_status(athlete_id: int, status: Optional[str]) -> bool`.

- [ ] **Step 1: Тесты** (tmp_path БД, register_athlete): add_cycle_event('start') → get_last_period_start возвращает дату; повторный тап той же датой → второй строки нет, метод вернул True; 'anchor' тоже задвигает кэш; два события подряд — кэш = более позднее; add_cycle_event с датой раньше кэша НЕ двигает кэш назад; set_menarche_status('pre') → видно в get_athlete_by_telegram_id.
- [ ] **Step 2: Убедиться в падении** (нет таблицы/методов).
- [ ] **Step 3: Реализовать** миграцию по образцу first_name (строки 324-328).
- [ ] **Step 4: pytest весь зелёный.**
- [ ] **Step 5: Commit** `"feat(db): cycle_events table, last_period_start cache, menarche_status"`

### Task 3: Обучение длины цикла на cycle_events

**Files:**
- Modify: `database.py:update_cycle_length_from_history` (:625-663)
- Test: `tests/test_db_cycle_length.py`

**Interfaces:**
- Consumes: Task 2 `get_cycle_events`.
- Produces: та же сигнатура `update_cycle_length_from_history(athlete_id) -> Optional[int]`, но интервалы между `get_cycle_events(athlete_id)`; правило окно 15-45 и медиана, запись в `cycle_length_default`, порог нормы 21-35 — без изменений. Вызывается теперь при любом 'start'/'anchor'-событии (Task 5), не только при stored cycle_day==1.

- [ ] **Step 1: Тесты:** 3 anchor/start-события с интервалами 26 и 30 → медиана 28 в cycle_length_default; 2 события → None (мало); интервал 10 дней фильтруется окном; **Review Focus №5:** после обучения медиана=32, `cycle_day(start, start+40)` не меняется от записи длины (день зависит от start, не от length) и `status(..., today=start+33)` остаётся 'ok' (задержка считается от новой длины — delay 1).
- [ ] **Step 2: Падение** (старый источник — daily_wellness.cycle_day=1 → None).
- [ ] **Step 3: Реализовать** замену источника запроса, остальная логика сохраняется.
- [ ] **Step 4: pytest весь зелёный** (старые тесты streak не затронуты).
- [ ] **Step 5: Commit** `"refactor(db): cycle length learning from cycle_events"`

### Task 4: Чистые хелперы шагов опроса (клавиатуры, тексты, парсер даты якоря)

**Files:**
- Create: `cycle_ui.py` (только данные/тексты/валидация, никаких telegram-объектов)
- Test: `tests/test_cycle_ui.py`

**Interfaces:**
- Consumes: Task 1 engine.
- Produces:
  - `parse_date_ru(text: str, today: date) -> Optional[date]` — «ДД.ММ»/«ДД.ММ.ГГГГ»; год догадка по окну: полученная дата > today → сдвиг года назад; дальше `AMENORRHEA_DAYS+7` назад → None. Для Review Focus №3.
  - `anchor_buttons(today: date) -> List[List[tuple]]` — «строка из (text, callback_data)»:
    Сегодня(`anchor_0`), Вчера(`anchor_1`), 2 дня назад(`anchor_2`), 3 дня назад(`anchor_3`),
    4 дня назад(`anchor_4`), 5 дней назад(`anchor_5`), ✏️ Ввести дату(`anchor_custom`),
    Пока не было(`anchor_pre`), Нет данных(`anchor_skip`) — кнопки 0-5 покрывают неделю,
    остальное ручной ввод; точность ±1 день здесь не критична (якорь всё равно уточняется
    событием «начались» в ближайшие дни).
  - `anchor_text(target: date, today: date) -> str` — человекочитаемо «5 окт».
  - `daily_cycle_keyboard(day: int, phase: Optional[PhaseProtocol], eta: date, status: str) -> List[List[tuple]]` — `[🩸 Да, начались (cyc_start)] [Нет (cyc_no)] [✏️ Исправить (cyc_fix)]`; при status delay/amenorrhea первой строкой добавляется кнопка «🗓 Отметить пропущенное начало (cyc_anchor_again)».
  - `daily_cycle_text(name, day, phase, eta, delay, learned_length) -> str` — сообщение шага.
  - `report_cycle_line(day, phase_key, eta, delay_days, length, learned: bool) -> str` — строка для отчётов/врача: «День N · 🌙 Лютеиновая · след. ~ДД.ММ · ⚠️ задержка X дн. · цикл L дн. (по истории|вручную)»; дляamenorrhea — «🚫 ≥90 дней без менструации — RED-S»; для 'pre'/'skip'/'no_anchor' → None.

- [ ] **Step 1: Тесты:** parse_date_ru('05.10') при today=18.10.2026 → 2026-10-05; ('25.12') → сдвиг в 2025 (будущее отсечено); ('01.01.2099') → None; ('18.1', мусор с точкой внутри) → None; ('90.01') → None. anchor_buttons: 9 кнопок, callback'ы с префиксом `anchor_`. daily_cycle_text содержит «День 14» и «~01.11»; report_cycle_line при delay=0 без «задержка», при delay=10 с «⚠️»; при no_anchor/pre/skip → None. anchor_buttons: 8 кнопок, callback'ы с префиксом `anchor_`.
- [ ] **Step 2: Падение** (нет модуля).
- [ ] **Step 3: Реализовать `cycle_ui.py`.**
- [ ] **Step 4: pytest зелёный.**
- [ ] **Step 5: Commit** `"feat(cycle): pure UI helpers (anchor parsing, keyboards, report line)"`

### Task 5: Одноразовый вопрос-якорь + ежедневный тап в опросе

**Files:**
- Modify: `handlers_athlete.py` (ask_cycle_day/set_cycle_day :961-1063 — переписать; helpers вызова kb self.kb уже есть)
- Modify: `handlers_survey.py:_route_after_survey` (:263-303 — ветка girls по `cycle_step_for`)
- Modify: `bot.py` (callback-роутинг ~:1414-1427; текстовые шаги ~:916-961)
- Test: `tests/test_cycle_flow_helpers.py` (чистая часть: выбор шага, сохранение события — через Database + fake state; Telegram-вызовы не тестируем, обёртки тонкие)

**Interfaces:**
- Consumes: Task 1 `cycle_step_for`; Task 2 `add_cycle_event`/`set_menarche_status`; Task 4 хелперы; Task 3 — вызов `update_cycle_length_from_history()` сразу после записи 'start'/'anchor'.
- Produces (для bot.py):
  - callback'ы: `anchor_0..anchor_5, anchor_custom, anchor_pre, anchor_skip, cyc_start, cyc_no, cyc_fix, cyc_anchor_again`.
  - шаг текстового ввода: `state["step"] = "survey_anchor_date"` — обработчик в bot.py: parse_date_ru → валид или сохранение; существующий шаг `survey_cycle` остаётся для «✏️ Исправить», но валидация через `parse_day_input` (1..L+30).
  - `_save_survey` (handlers_survey :340-352) — в data для cycle_day/cycle_phase пишутся вычисленные движком значения (stored-совместимость Excel).

- [ ] **Step 1: Тесты чистой части:** `cycle_step_for` уже покрыт Task 1; здесь — тест на Database-уровне: «Да, начались» (`add_cycle_event(today)` + `update_cycle_length_from_history`) → get_last_period_start == today; два тапа подряд → одна строка (Review Focus №1); `set_menarche_status('pre')` → `cycle_step_for` → 'skip'; «Нет» тап ничего не пишет.
- [ ] **Step 2: Падение** (нет интеграции).
- [ ] **Step 3: Реализовать:** в `_route_after_survey` замена `if gender == "female"` блока на step = `cycle_step_for(...)`; новый `ask_cycle_anchor()` (текст + anchor_buttons); `set_cycle_anchor()` обработчик callback'ов anchor_*; `ask_cycle_day` переписать на daily_cycle_keyboard/daily_cycle_text; `set_cycle_day` → ветки cyc_start/cyc_no/cyc_fix; в bot.py роутинг callback'ов и текстовый шаг survey_anchor_date; текстовый ввод survey_cycle перевести на parse_day_input.
- [ ] **Step 4: pytest весь зелёный; `py_compile` всех изменённых файлов.**
- [ ] **Step 5: Commit** `"feat(survey): event-based cycle tracking replaces bucket buttons (anchor + one tap)"`

### Task 6: Экран «Мой цикл» — непрерывный ряд

**Files:**
- Modify: `handlers_athlete.py:show_cycle_info` (:894-958), `database.py:get_cycle_history` (:373-380, не удаляя: до первого якоря — stored)

**Interfaces:**
- Consumes: Task 1-4.
- Produces: непрерывная лесенка + прогноз + статус; Review Focus №4 — stored-корзины не смешиваются с вычисленными: ряд = вычисленные дни на даты с якоря, плюс stored только для дат до первой anchor/start-даты.

- [ ] **Step 1: Тест** на Database-уровне helper `build_cycle_display(events: List[date], stored: List[Dict], today) -> List[Tuple[str, int, str]]` (положить в `cycle_ui.py`): при якорях [01.09, 29.09] и stored с 01.10 по 05.10 (корзины) вывод 05.10 → день 7 без «день 3/8» из stored-мусора после даты якоря; stored до первого якоря остаётся.
- [ ] **Step 2: Падение.**
- [ ] **Step 3: Реализовать helper + переписать show_cycle_info (текст через report_cycle_line + ряд).**
- [ ] **Step 4: pytest зелёный.**
- [ ] **Step 5: Commit** `"feat(cycle): My-cycle screen shows computed continuous ladder"`

### Task 7: Отчёты и алерты: daily_report, coach_week_summary, trend-паттерны

**Files:**
- Modify: `handlers_admin.py:daily_report` (:264-298)
- Modify: `handlers_admin.py:coach_week_summary` (:841+)
- Modify: `bot.py:_check_trend_alerts` (:1637-1701 — паттерны 6-7 после паттерна 5)

**Interfaces:**
- Consumes: Task 1 status/delay/next_period_eta, Task 4 report_cycle_line.
- Produces: в daily_report девушки идут с циклической строкой после имени (тренеры: в coach_week_summary строка «День N · фаза · ⚠️ задержка X» у каждой девушки списка команды); паттерн 6 `⚠️ задержка` (>7 дней — врач/владелец), паттерн 7 `🚫 ≥90 дней` (RED-S), только 'active' якорные девушки; алерты не дублируются ежедневно: повтор не чаще раза в 7 дней на девушку (map в bot_settings `cycle_alert_<aid>_<kind>=last_date`).

- [ ] **Step 1: Тест:** helper `cycle_alert_lines(athletes, today, last_sent: Dict[str,str]) -> List[str]` (положить в cycle_ui.py) — из fixture-атлетов: задержка 10 → одна строка; повтор при last_sent вчера → пусто; amenorrhea → своя строка; 'no_anchor'/'pre'/'skip' → не участвуют.
- [ ] **Step 2: Падение.**
- [ ] **Step 3: Реализовать** helper + встраивание в `_check_trend_alerts` (с записью last_sent через set_setting), строки в daily_report и coach_week_summary (без last_sent-дедупа — пассивный вывод).
- [ ] **Step 4: pytest зелёный.**
- [ ] **Step 5: Commit** `"feat(reports): computed cycle lines + delay/amenorrhea doctor alerts"`

### Task 8: Финальная проверка и чек-лист приёмки

- [ ] **Step 1:** `python -m pytest tests -q` — все зелёные (baseline 47 + новые), вывод без новых предупреждений.
- [ ] **Step 2:** `python -m py_compile bot.py database.py handlers_athlete.py handlers_survey.py handlers_admin.py cycle_engine.py cycle_ui.py config.py gamification.py`.
- [ ] **Step 3:** smoke-скрипт `tests/smoke_cycle_flow.py` (запуск руками, не pytest): in-memory БД → якорь → 3 «Нет» тапа (дни растут) → «Да» (перезапуск, длина обучилась после 2 якорей) → сдвиг today на +36 → status delay. Вывод текстом.
- [ ] **Step 4:** README-секция «Менструальный цикл» (2-3 абзаца: модель событий, чем отличается от прежних корзин, что видят врач/тренер).
- [ ] **Step 5:** Commit `"docs+smoke: cycle tracking acceptance"`. Деплой на сервер — руками владельца (напомнить: новые файлы `cycle_engine.py`, `cycle_ui.py` обязательны к копированию, иначе ImportError).

## Правки по итогам ревью «брата» (2026-10-18, после Tasks 1-4)

1. `report_cycle_line` из T4 переименован/перепроектирован в `status_line(start, length, today, learned=False)` — сам считает день/статус/фазу (handler'ы тоньше). T7 вызывать его.
2. Новый API в database.py: `correct_cycle_anchor(athlete_id, new_date) -> bool` — ретро-коррекция: стирает start/anchor позже new_date, ставит кэш, снимает menarche_status. В T5 ветки `anchor_custom` и `cyc_anchor_again` при дате раньше кэша вызывают его, иначе `add_cycle_event`.
3. `add_cycle_event`: будущая дата → False; 'start' пересиливает 'anchor' той же даты, anchor при существующем start того же дня — игнор.
4. Окно `parse_date_ru` = ровно `AMENORRHEA_DAYS` (90), строка про +7 выше недействительна (спека первее плана).
5. Флаг источника длины: `athletes.cycle_length_learned INTEGER DEFAULT 0`, обучение ставит 1; T5 при ручной смене длины обнуляет, T6/T7 читают для «(по истории)/(вручную)».
6. Медиана чётного числа: округление .5 вверх (`(a+b+1)//2`).
7. `anchor_text(target)` — без today.
8. `get_cycle_events(types=())` → [] (защита от `IN ()` на старых sqlite).
9. PRAGMA foreign_keys НЕ включаем глобально — scope creep, риск для существующих вставок на проде; сироты теоретически возможны, accepted.
10. Миграция после ALTER проверяет колонки через PRAGMA table_info и пишет logger.error при отсутствии (без raise).

## Self-review checklist (заполнить по факту выполнения)

- [ ] Spec coverage: каждая секция дизайна → задача.
- [ ] Steps однозначны (одна вещь на шаг).
- [ ] Имена/типы совпадают между задачами (проверить `report_cycle_line`, `cycle_step_for`, `add_cycle_event`).
- [ ] Review Focus 1-5 покрыты тестами (1: T5, 2: T5 smoke, 3: T4 parse, 4: T6, 5: T3).
- [ ] План не длиннее кода в разы.

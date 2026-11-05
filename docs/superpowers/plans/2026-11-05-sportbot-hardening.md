# Харденинг и дашборд «Спортсмен ЧБК» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть критические дыры доступа, сделать рассылки/напоминания надёжными, добавить дашборд «🔔 На сегодня» (врач — все, тренер — свои команды) и перевести деплой на git.

**Architecture:** точечные правки в существующей структуре (bot.py + handlers_*.py mixin'ы + database.py), новые чистые модули `access_control.py` и `dashboard.py` с pure-функциями, чтобы логика прав и сборки экрана тестировалась без PTB-рантайма. Серверная часть — ops-шаги по SSH (агент выполняет, владелец подтверждает каждый деплой).

**Tech Stack:** Python 3.12, python-telegram-bot 22.8 (asyncio), SQLite WAL, pytest (162 теста, tmp_path-БД, без токена), systemd, bash.

**Spec:** `docs/superpowers/specs/2026-11-05-sportbot-hardening-design.md`

## Global Constraints

- Ветка `feature/cycle-auto-tracking`; каждый таск — отдельный коммит; деплой только через `deploy.sh` (появится в Task 8).
- Тесты запускаются без токена и прод-БД: `python -m pytest tests/ -x -q` (config.py:9 — env default ""). Новых зависимостей не добавлять (async-тесты — через `asyncio.run` в синхронных тестах).
- `.env` и `data/` никогда не коммитятся (проверено .gitignore — не регрессировать).
- UI-тексты в существующем стиле: Markdown, разделитель `━━━…`, эмодзи как в config.EMOJIS.
- Часовой пояс напоминаний/«сегодня» — `REMINDER_TZ` из БД-настроек (bot.py:1565), тренды/недельные — `Asia/Yekaterinburg` (bot.py:1902).
- Роли: `_is_full_access` (bot.py:579) = ADMIN_TELEGRAM_IDS ∪ doctors; `_is_admin_or_coach` (bot.py:593); scope — `_scoped_athletes` (bot.py:597), `db.get_coach_teams(user_id)`, `db.get_team_coaches(team)` (database.py:1671).
- Не трогать: `_prod_bot.py`, `bot_prod_*.py`, `bot_bak_*.py`, `database_prod_*.py` (gitignored-копии), поведение спортсменов (никаких новых полей опроса).
- Прод живой: любой рестарт сервиса — только с подтверждением владельца.

## Review Focus

1. Злоумышленник шлёт сырой callback `ban_5` / `bc_overview_all` / `bc_import_to_3` без роли → ожидает «⛔ Нет доступа», данные не меняются. (Task 1)
2. Тренер чужой команды открывает `bc_view_<id>` или дашборд → видит только свои команды, чужих нет ни в списках, ни в карточках. (Tasks 1, 4)
3. Спортсмен заблокировал бота → рассылка не падает, флаг выставлен, врач уведомлён ровно один раз, при `/start` флаг сбрасывается. (Task 2)
4. Рестарт бота в 21:10 после напоминания в 20:30 → сегодняшняя рассылка НЕ пропускается и НЕ дублируется. (Task 5)
5. Мусорный ввод анкеты: возраст «10», дата «99.99.9999», телефон «abc» → вежливый отказ, в БД ничего не сохраняется. (Task 5)

---

### Task 1: Default-deny гейт callback'ов (`access_control.py`)

**Files:**
- Create: `access_control.py`
- Modify: `bot.py:1166-1177` (callback_handler, перед роутингом), `handlers_body_comp.py:260` (bc_view_athlete — scope), `handlers_body_comp.py:939` (_bc_import_to_athlete — роль)
- Test: `tests/test_access_control.py`

**Interfaces:**
- Consumes: `self._is_full_access(user_id)`, `self._is_admin_or_coach(user_id)` (bot.py:579, 593), `self.db.get_coach_teams(user_id)`.
- Produces: `required_role_for(callback_data: str) -> str | None` — `'full'` | `'coach'` | `None`; `CALLBACK_ROLE_PREFIXES: list[tuple[str, str]]` (префикс → роль, порядок: длинные префиксы раньше коротких). На это опираются Task 4 (`dash_today` → `'coach'`).

- [ ] **Step 1: Написать провальный тест**

```python
# tests/test_access_control.py
from access_control import required_role_for

def test_sensitive_prefixes_require_full_access():
    for d in ("ban_5", "unban_3", "ban_menu", "bc_overview_all", "bc_import_to_7",
              "del_2", "delconfirm_2", "delete_athlete", "admin_manage", "qview_1",
              "athlete_list", "export_csv", "reset_account"):
        assert required_role_for(d) == "full", d

def test_coach_prefixes():
    for d in ("bc_team_ЧБК", "bc_view_9", "dash_today", "coach_team_view"):
        assert required_role_for(d) == "coach", d

def test_athlete_callbacks_free():
    for d in ("main_menu", "do_survey", "my_stats", "chart_7", "bc_my_trends", "consent_accept"):
        assert required_role_for(d) is None, d
```

- [ ] **Step 2: Прогнать — падает** (`ModuleNotFoundError: access_control`). Run: `python -m pytest tests/test_access_control.py -v`

- [ ] **Step 3: Реализовать `access_control.py`**

Перечислить ВСЕ ветки диспетчера `callback_handler` (bot.py:1180–1480, grep `elif d`) и классифицировать каждую: admin/doctor/ban/delete/qview/export/reset/bc-чужие-данные → `'full'`; командные просмотры и карточки → `'coach'`; собственные flow спортсмена → `None`. Сомнительный префикс → `'full'` (default-deny по духу спеки). Матчинг: `data == prefix` или `data.startswith(prefix)`, сортировка по убыванию длины префикса (`bc_import_to_` раньше `bc_`). Точный список веток исполнитель выводит из кода; тест выше — нижняя граница, расширить тест полным перечнем чувствительных префиксов из диспетчера.

- [ ] **Step 4: Прогнать — проходит.** Run: `python -m pytest tests/ -x -q` (все 162+ новых)

- [ ] **Step 5: Встроить гейт в диспетчер (bot.py, после rate-limit ~1166, до роутинга)**

```python
from access_control import required_role_for
# в callback_handler, после logger и до try:
_req = required_role_for(d)
if _req == "full" and not self._is_full_access(q.from_user.id):
    logger.warning(f"Access denied (full required): {d}")
    await q.answer("⛔ Нет доступа", show_alert=True); return
if _req == "coach" and not self._is_admin_or_coach(q.from_user.id):
    logger.warning(f"Access denied (coach required): {d}")
    await q.answer("⛔ Нет доступа", show_alert=True); return
```

Точечные проверки внутри хендлеров НЕ удалять (defense in depth).

- [ ] **Step 6: Scope в `bc_view_athlete` (handlers_body_comp.py:260)**

После извлечения `athlete_id`: если `not self._is_full_access(user_id)` — проверить `athlete["team"] in set(self.db.get_coach_teams(user_id))`, иначе «⛔ Нет доступа» + warning-лог. (Роль уже проверена гейтом; здесь — scope.)

- [ ] **Step 7: Роль в `_bc_import_to_athlete` (handlers_body_comp.py:939)**

Первая строка тела: `if not self._is_full_access(update.effective_user.id): await q.answer("⛔ Нет доступа", show_alert=True); return`. Self-flow спортсмена (bot.py:453–475, `_import_scale_csv`) не трогать — там athlete_id из собственного профиля.

- [ ] **Step 8: Тест scope-хелпера**

Добавить в `tests/test_access_control.py` тест на DB-уровне (tmp_path БД, `db.register_athlete(...)`, `db.add_coach`/`assign` — взять реальные методы из database.py:1642-1671): тренер команды А не проходит проверку `team in get_coach_teams` для спортсмена команды Б.

- [ ] **Step 9: Все тесты + commit**

Run: `python -m pytest tests/ -x -q` → PASS.
`git add access_control.py bot.py handlers_body_comp.py tests/test_access_control.py && git commit -m "feat(security): default-deny role gate for callbacks + coach scope on bc_view/import"`

---

### Task 2: Автоотписка заблокировавших бота (`bot_blocked`)

**Files:**
- Modify: `database.py` (миграция ~230-255 по существующему паттерну ALTER-in-try, новые методы рядом с ban_athlete:1033), `bot.py:1581-1606` (цикл напоминаний), `bot.py:1482` (_send_admin — не ломать), cmd_start (сброс флага)
- Test: `tests/test_bot_blocked.py`

**Interfaces:**
- Produces: `db.set_bot_blocked(athlete_id: int, flag: int = 1) -> bool`; `db.get_bot_blocked(athlete_id) -> int`; колонка `athletes.bot_blocked INTEGER DEFAULT 0`. Task 4 читает `bot_blocked` у строк `get_all_athletes()`.

- [ ] **Step 1: Провальный тест**

```python
# tests/test_bot_blocked.py — DB-часть
def test_bot_blocked_roundtrip(tmp_path):
    from database import Database
    db = Database(db_path=str(tmp_path / "t.db"))
    db.register_athlete(777001, "u", "Тест Тестов", "U17", "ЧБК")
    aid = db.get_athlete_by_telegram_id(777001)["id"]
    assert db.get_bot_blocked(aid) == 0
    db.set_bot_blocked(aid, 1)
    assert db.get_bot_blocked(aid) == 1
    assert db.get_all_athletes()[0].get("bot_blocked") == 1  # поле видно в выдаче
```

- [ ] **Step 2: Прогнать — падает** (нет метода/колонки).
- [ ] **Step 3: Реализовать в database.py** — миграция `ALTER TABLE athletes ADD COLUMN bot_blocked INTEGER DEFAULT 0` (try/except pass, как соседние); `set_bot_blocked`/`get_bot_blocked` параметризованные; `get_all_athletes` (database.py:555) — добавить `bot_blocked` в SELECT-колонки.
- [ ] **Step 4: Прогнать — проходит.**
- [ ] **Step 5: Провальный тест рассылки** — `asyncio.run` + stub: `class FakeBot: async def send_message(self, chat_id, text, **kw): if chat_id == 111: raise telegram.error.Forbidden("blocked")`. Вызвать `bot._send_daily_reminder(ctx_stub)` для БД с двумя спортсменами (один «заблокирован»). Assert: флаг выставлен только у 111, второму отправлено, в логе одно уведомление врачу. `SportHealthBot` инстанцировать через `object.__new__(SportHealthBot)` и навесить атрибуты (`db`, `user_states={}`, нужные хелперы) — без сети и токена (паттерн согласовать с существующими tests/, при необходимости вынести тело рассылки в тестируемую функцию `_reminder_loop(bot_obj, athletes, survey_map)`).
- [ ] **Step 6: Реализовать обработку Forbidden**

В цикле напоминаний (bot.py:1581): `except telegram.error.Forbidden:` → `self.db.set_bot_blocked(a["id"], 1)`, `logger.info` (без ФИО, только athlete_id), однократно уведомить `_full_access_ids()` через `_send_admin`: «⚠️ Спортсмен #{id} ({full_name}) заблокировал бота — мониторинг прерван» (уведомление шлётся только при ПЕРЕХОДЕ 0→1). Пропускать `bot_blocked==1` ДО отправки. То же в других массовых рассылках спортсменам (grep `send_message(chat_id=a["telegram_id"]` / `send_photo`): воскресные отчёты bot.py:1781, сводки — кроме `_send_admin` врачам.

- [ ] **Step 7: Сброс флага** — в `cmd_start` после `get_athlete_by_telegram_id`: если `bot_blocked` → `set_bot_blocked(aid, 0)` + уведомление врачу «✅ Спортсмен #{id} снова с ботом».
- [ ] **Step 8: Все тесты + commit** `git commit -m "feat(reliability): bot_blocked flag — автоотписка заблокировавших, уведомление врачу"`

---

### Task 3: Red-flag пуш тренеру команды

**Files:**
- Modify: `handlers_survey.py:498-523`
- Test: `tests/test_red_flag_recipients.py`

**Interfaces:**
- Consumes: `db.get_team_coaches(team) -> List[int]` (database.py:1671), `self._full_access_ids()`.
- Produces: `red_flag_recipients(db, team: str, full_ids: set, sender_id: int) -> list[int]` — новая pure-функция в `handlers_survey.py` (или `access_control.py` — на выбор исполнителя, тест импортирует оттуда же).

- [ ] **Step 1: Провальный тест**: БД tmp_path: спортсмен команды «ЧБК», тренер ЧБК назначен (`get_team_coaches` возвращает его id), врач в `full_ids`. `red_flag_recipients(db, "ЧБК", {doctor_id}, sender_id=athlete_tg)` → содержит trainer_id и doctor_id, не содержит sender; тренер другой команды — не содержит.
- [ ] **Step 2: Прогнать — падает** (нет функции).
- [ ] **Step 3: Реализовать** функцию + в red-flag блоке (handlers_survey.py:519) заменить получателей: `for _aid in red_flag_recipients(self.db, athlete.get("team"), self._full_access_ids(), user_id):` — текст алерта и кнопки (`contact_row`) без изменений. Существующий coach-notify (467-496) НЕ дублировать: red-flag уходит тренеру отдельным сообщением только если `_flags` непустой.
- [ ] **Step 4: Прогнать — проходит** + весь набор.
- [ ] **Step 5: Commit** `git commit -m "feat(alerts): red-flag опроса уходит тренеру команды (scope)"`

---

### Task 4: Дашборд «🔔 На сегодня»

**Files:**
- Create: `dashboard.py`
- Modify: `bot.py` (новый хендлер `show_today_dashboard` + ветка `dash_today` в диспетчере + кнопка в меню), `handlers_athlete.py:22` (show_main_menu — кнопка роли)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: Task 1 (`required_role_for("dash_today") == "coach"`), Task 2 (`bot_blocked`), `db.get_all_athletes()`, `db.get_has_survey_today_map()` (bot.py:1578), `db.get_last_n_days_all(3)` (database.py:587), `db.get_individual_baseline(aid, 30)` (database.py:1232), `cycle_ui.cycle_alert_lines(athletes, today, last_sent)` (cycle_ui.py:240), `ALERT_THRESHOLDS`, `ACTIVE_TEAMS`.
- Produces: `build_today_dashboard(db, user_id: int, scoped_athletes: list[dict], today: date) -> tuple[str, list]` — (Markdown-текст, список рядов кнопок `[(label, callback_data)]`).

- [ ] **Step 1: Провальный тест** (tmp_path БД): сид — 4 спортсмена (2 команды), сегодня: один с `illness_flag` + `pain_nrs=7` (🔴), один с Hooper ниже critical порога (🔴), один сдал в норме, один не сдал + `bot_blocked=1`. Вызов от врача → в тексте: оба красных с ФИО и причиной, тренд-блок, «не сдали» содержит не сдавшего, «заблокировали» содержит blocked. Вызов от тренера команды А → спортсмены команды Б НЕ встречаются в тексте. Кнопки: на каждой красной/жёлтой строке `bc_view_<id>`.
- [ ] **Step 2: Прогнать — падает.**
- [ ] **Step 3: Реализовать `dashboard.py`** — одна batch-сборка (без N+1: `get_last_n_days_all(3)` на всех сразу, baseline — цикл только по спортсменам с данными ≥3 дней). Блоки по спеке: 🔴 красные флаги сегодняшних опросов (illness_flag, analgesics, pain_on_game, pain_nrs≥5, жалобы — обрезка 60 симв., пульс/HRV вне индивидуального коридора 2 дня подряд — логика как в `_check_trend_alerts` bot.py:1665-1679, Hooper < critical/warning порога `ALERT_THRESHOLDS[age_group]`); 🟡 тренды (3 дня подряд из `_check_trend_alerts` + `cycle_alert_lines` — для тренера ТОЛЬКО его команды, решение владельца); ⚪ не сдали сегодня (из scoped minus survey_map) + `bot_blocked=1`. Пустое состояние: «🟢 Тревог нет, N из M сдали опрос». ФИО/команды — только из `scoped_athletes` (scope гарантирован вызывающим).
- [ ] **Step 4: Прогнать — проходит.**
- [ ] **Step 5: Хендлер в bot.py**

```python
async def show_today_dashboard(self, update, ctx):
    user_id = update.effective_user.id
    scoped = self._scoped_athletes(user_id)   # bot.py:597 — врач: все активные, тренер: свои
    import asyncio
    from datetime import date as _date
    text, buttons = await asyncio.to_thread(
        build_today_dashboard, self.db, user_id, scoped, _date.today())
    # 60-с кэш: self._dash_cache = {"key": (frozenset ids scoped, minute//…), "val": (text, buttons)}
    ... edit/reply_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
```

Ветка диспетчера: `elif d == "dash_today": await self.show_today_dashboard(update, ctx)` (роль уже гейтится Task 1). Кнопка «🔔 На сегодня» — в `show_main_menu` (handlers_athlete.py:22) только при `_is_admin_or_coach(user_id)`, первой строкой меню. Кнопка «Обновить» → тот же `dash_today`. Кэш 60 с сбрасывать по «Обновить» не нужно (просто не хранить дольше минуты).

- [ ] **Step 6: Тест меню** — роль видит кнопку, спортсмен нет (unit: извлечь список кнопок show_main_menu сложно — достаточно теста `required_role_for("dash_today") == "coach"` из Task 1 + smoke: вызов хендлера от спортсмена через stub update → «⛔ Нет доступа» от гейта).
- [ ] **Step 7: Все тесты + commit** `git commit -m "feat(dashboard): экран «На сегодня» — красные флаги/тренды/слепые зоны, scope тренера"`

---

### Task 5: Quick wins надёжности

**Files:**
- Modify: `database.py:53` (lock), `bot.py:429, 1767, 1893-1896, 1566/1640, 1950`, `handlers_reports.py` (wb.save-точки :54-473, 771, 1067, 1286), `bot.py:969-997` (валидация анкеты)
- Test: `tests/test_reminder_dedup.py`, `tests/test_questionnaire_validation.py`

**Interfaces:**
- Consumes: `validator.parse_birth_date` (validator.py:30), `validator.validate_age_ge14` (validator.py:41), `validator.sanitize_text` (уже используется bot.py:964).
- Produces: `Database._lock: threading.RLock` (все публичные методы под ним).

- [ ] **Step 1: Провальный тест напоминаний** — `_reminder_should_fire_now(now_local, hour, minute) -> bool` (новая pure-функция в bot.py): `(21:10, 20, 30) → True` (текущий код даёт False — баг `hour>= and minute>=`), `(20:10, 20, 30) → False`, `(20:30, 20, 30) → True`.
- [ ] **Step 2: Прогнать — падает.** Реализовать функцию; заменить условие bot.py:1893 на вызов.
- [ ] **Step 3: Дедуп до цикла** — перенести `self.db.set_setting("reminder_sent_date", _today)` из bot.py:1640 в начало `_send_daily_reminder` (сразу после проверки bot.py:1566). Тест: stub-рассылка, падающая на середине → `reminder_sent_date` уже выставлен, повторный вызов пропускается (no duplicates).
- [ ] **Step 4: 429/RetryAfter** — в цикле напоминаний и red-flag/coach-notify: `except telegram.error.RetryAfter as e: await asyncio.sleep(e.retry_after + 1)` и одна повторная попытка.
- [ ] **Step 5: Провальный тест валидации анкеты** — pure-функции: `validate_questionnaire_age(text) -> tuple[bool, str]` и `validate_questionnaire_birth(text) -> tuple[bool, str]` в bot.py (обёртки над validator.py): `"10"` → False (минимум — 14 per README/validator; расхождение с лейблом «U-14 (12-14 лет)» — решение строителя: единый порог 14 из validate_age_ge14, комментарий в коде); `"99.99.9999"` → False (календарная); `"+7 900 123-45-67"` → True, `"abc"` → False. Подключить в `q_age`/`q_phone`/`q_birth_date` (bot.py:969-997) вместо текущих проверок.
- [ ] **Step 6: Прогнать — падает → реализовать → проходит.**
- [ ] **Step 7: to_thread** — `bot.py:429`: `watch_data = await asyncio.to_thread(parse_watch, text_content, fname, raw_bytes=bytes(content))`; `bot.py:1767`: `path = await asyncio.to_thread(generate_weekly_report, **kwargs)`; handlers_reports.py — обернуть `wb.save(...)` в `await asyncio.to_thread(wb.save, path)` в 4 точках (:54-473, 771, 1067, 1286 — найти по grep `wb.save`).
- [ ] **Step 8: Lock в database.py** — `self._lock = threading.RLock()` в `__init__` (после :53); обернуть тела ВСЕХ публичных методов, трогающих `self.conn`, в `with self._lock:` (механически; вложенные вызовы безопасны — RLock). Тест-дым: два потока по 200 записей `save_survey` одновременно на tmp БД → без исключений, count == 400.
- [ ] **Step 9: `drop_pending_updates=True`** (bot.py:1950) — решение строителя из спеки.
- [ ] **Step 10: Все тесты + commit** `git commit -m "fix(reliability): reminder time-bug/dedup/429, validator wiring, to_thread, db lock"`

---

### Task 6: Гигиена логов (код)

**Files:**
- Modify: `bot.py:1167` (callback-лог), `bot.py:1796`, `handlers_admin.py:668,700`, `handlers_registration.py:23`
- Test: без новых (регрессия — существующие)

- [ ] **Step 1:** `bot.py:1167` → `logger.debug("Callback: %s uid=%s", d, q.from_user.id)` (INFO-поток больше не содержит telegram_id на каждый клик).
- [ ] **Step 2:** В перечисленных точках заменить ФИО+telegram_id на внутренний `athlete_id` (или `uid` без имени); содержимое жалоб в логи не писать нигде (grep `complaints` по logger-строкам — 0 совпадений после правки).
- [ ] **Step 3:** Проверить: `python -m pytest tests/ -x -q` PASS; `grep -n "full_name" bot.py | grep logger` — не остаётся строк с ФИО в INFO.
- [ ] **Step 4: Commit** `git commit -m "chore(logs): убрать PHI (telegram_id, ФИО, жалобы) из INFO-логов"`

---

### Task 7: deploy.sh + подготовка сервера к git-деплою

**Files:**
- Create: `deploy.sh` (в корне репо), `docs/DEPLOY.md`
- Modify: `README.md` (раздел «Деплой»)

**Interfaces:**
- Consumes: Tasks 1–6 закоммичены и запушены в origin/`feature/cycle-auto-tracking`.

- [ ] **Step 1: `deploy.sh`** (исполняется НА сервере в `/opt/telegram-bots/sport-health`): `set -e`; `git fetch --tags`; `git checkout "$1"` (аргумент — тег/ветка, default `feature/cycle-auto-tracking`); `git reset --hard origin/<ref>`; `pip install -r requirements.txt -q` (если изменился); `python3 -m pytest tests/ -x -q` — при провале: `git checkout <предыдущий тег>` + `systemctl restart sport-health` + exit 1; `systemctl restart sport-health`; 30 с ожидания + `systemctl is-active` + `tail healthcheck.log`; при неудаче — откат на предыдущий тег + restart + exit 1. Успех: вывести тег и `is-active`.
- [ ] **Step 2: Тест скрипта локально** — `bash -n deploy.sh` (синтаксис); прогон логики отката описать в docs/DEPLOY.md (на сервере проверится в Task 8).
- [ ] **Step 3: docs/DEPLOY.md** — единственный путь деплоя, правила: правки на сервере запрещены, релиз = тег `vYYYYMMDD-N`, откат = `deploy.sh <старый тег>`, журнал деплоя (дата, тег, кто подтвердил, результат смоука).
- [ ] **Step 4: README.md** — короткий раздел со ссылкой на DEPLOY.md.
- [ ] **Step 5: Commit** `git commit -m "chore(deploy): deploy.sh с тестами и откатом по тегу, DEPLOY.md"`

---

### Task 8: Серверные операции (SSH, каждый шаг — подтверждение владельца)

**Files:** на сервере; локально — журнал `docs/deploy-journal.md` (создать)

- [ ] **Step 1: GitHub-репо → private** (рекомендация принята владельцем «да»): через веб-интерфейс владельцем ИЛИ `gh repo edit --visibility private` при наличии учётки; проверить `git push` ветки `feature/cycle-auto-tracking` + тега `v20261105-1` в origin.
- [ ] **Step 2: Миграция папки на git**: в `/opt/telegram-bots/sport-health` — архивировать мусор: `tar czf /root/_bak_pre_git_$(date +%F).tar.gz bot*.bak* bot_new.py bot_orig.py anketa.db '_bak_2026-10-19' *.png` и удалить их из папки; `git init && git remote add origin <url> && git fetch && git checkout -f feature/cycle-auto-tracking`; `.env`, `data/`, `bot.log`, `backups/`, `*.log` — проверить, что в gitignore и остались на месте; `diff` рабочего bot.py с git-версией ДО checkout (если differs — стоп, разбор с владельцем).
- [ ] **Step 3: `chmod 750`** на `/opt/telegram-bots/sport-health`, `chmod 600 .env`; смоук: `systemctl restart sport-health` (подтверждение владельца) → `is-active` → Telegram `/status` владельцем.
- [ ] **Step 4: Бэкап-юнит**: в `sport-health-backup.service` заменить `cp` на снапшот через `python3 -c "import sqlite3; src=sqlite3.connect('file:data/sport_health.db?mode=rw',uri=True); dst=sqlite3.connect('<dest>'); src.backup(dst); dst.close()"` + `python3 -c "...integrity_check..."` снапшота, при ошибке — не удалять вчерашний, писать в backup.log. Проверить ближайший прогон таймера (03:4x UTC): снапшот свежее и integrity ok.
- [ ] **Step 5: logrotate**: `/etc/logrotate.d/sport-health` — `bot.log healthcheck.log backup.log` weekly, rotate 4, compress, copytruncate, missingok. `logrotate -d` (dry) → `logrotate -f`.
- [ ] **Step 6: Диск**: `journalctl --vacuum-size=200M`; удалить `/var/log/syslog.1`, `/var/log/btmp.1` (после `du`-подтверждения владельцем); целевое: `df -h /` < 70%.
- [ ] **Step 7: Первый деплой через `deploy.sh v20261105-1`** (подтверждение владельца) → pytest на сервере → restart → healthcheck → смоук в Telegram: меню врача («🔔 На сегодня» есть, данные всех), меню тренера (только свои команды), опрос спортсмена (red-flag → пуш врачу И тренеру), `/start` от заблокировавшего (флаг сброшен). Записать в `docs/deploy-journal.md`.
- [ ] **Step 8: Dedicated user `sportbot`** (последним, отдельный рестарт с подтверждением): `useradd -r -s /usr/sbin/nologin sportbot`; `chown -R sportbot:sportbot /opt/telegram-bots/sport-health`; в unit `User=sportbot`; `systemctl daemon-reload && restart`; смоук как в Step 7; при любой проблеме — откат `User=root` + restart.
- [ ] **Step 9: Commit журнала** `git add docs/deploy-journal.md && git commit -m "docs(deploy): журнал миграции сервера на git-деплой"`.

---

## Self-review notes

- Spec coverage: фичи 1,2,4,5,6,7 + серверные фиксы → Tasks 1–8; non-goals не затронуты (consents-проверки нет, шифрование жалоб не трогаем).
- Push тренеру сразу после опроса (поправка владельца): существующий coach-notify (handlers_survey.py:467-496) уже шлёт тренеру итог каждого опроса — Task 3 добавляет red-flag-алерт тренеру; новых пушей рутинных опросов не нужно (уже есть).
- Типы согласованы: `required_role_for` (Task 1) используется в Task 4; `bot_blocked` (Task 2) — в Task 4; `red_flag_recipients` (Task 3) самодостаточна.
- Порядок деплоя: Tasks 1–6 локально → тег → Task 7 → Task 8 (владелец подтверждает каждый рестарт).

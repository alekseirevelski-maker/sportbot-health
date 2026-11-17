# attic/ — карантин ad-hoc скриптов

Сюда сложены (18.11.2026) одноразовые диагностические и «лечащие» скрипты, которые напрямую
открывают прод-БД (`sqlite3` / `Database()` без ревью). Они не отслеживаются git'ом (см.
`.gitignore`: `_*`, `probe_*.py`, `verify_*.py`, `inspect_*.py`, `audit_*.py`, `cleanup_db.py`,
`add_*.py`, `*.sql`, `*_prod_*`, `*_bak_*`) и не попадают на сервер через `deploy.sh`.

## Правила

1. Ничего отсюда не запускать против прода. Эти скрипты — только текст для чтения и отправная
   точка для нормального патча с тестами.
2. Причина карантина — инцидент 2026-11-11 (случайный запуск pytest в корне удалил
   `daily_wellness`/`athletes`/`alerts`) и след ручных правок данных на проде:
   `data/sport_health.db.bak_bcdates_20261012_163950` — копия БД, снятая `_fix_bc_dates.py`
   перед тем, как он правил `record_date` напрямую в `body_composition`.
3. Любая правка данных на проде: скрипт вне этого каталога, сначала копия БД, затем dry-run,
   затем запись — и только по явному согласию владельца.
4. Любая правка кода: коммит → тег → `bash deploy.sh <тег>` (см. `docs/DEPLOY.md`), с падающим
   тестом сначала.

## Что здесь лежит

| Файл | Что делал |
|---|---|
| `_fix_bc_dates.py` | правил `record_date` в `body_composition` на проде (дубли решал удалением) |
| `_verify_bc_fix.py`, `_verify_bc_screens.py`, `_verify_reports.py`, `_verify_flat.py` | прогоны парсера и экранов состава тела по фикстурам |
| `smoke_parser.py`, `_test_parser_fix.py`, `_test_fix.py` | ручные прогоны `scale_csv_parser` |
| `probe_swap.py`, `probe_before.py`, `probe_coaches.py`, `probe_report_map.py` | диагностика привязок и отчётов |
| `audit_db_full.py`, `audit_pulse_20260819.py`, `inspect_orphans.py`, `_diag*.py`, `_check_*.py`, `_limit_check.py`, `_gen_report2.py`, `_inspect_xlsx.py` | съёмные аудиты базы |
| `cleanup_db.py`, `set_reminder_marker.py`, `z5z6_assign.py`, `add_coach_antiponova.py`, `add_ezhov.sql`, `_audit_cycle.sql` | разовые правки данных и привязок |
| `bot_prod_20260819.py`, `database_prod_20260819.py`, `bot_bak_reminder_dedup.py`, `_prod_bot.py` | слепки прода от 19.08 (история, не запускать) |

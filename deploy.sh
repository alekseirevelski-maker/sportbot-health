#!/bin/bash
# deploy.sh — git-деплой бота sport-health НА СЕРВЕРЕ (/opt/telegram-bots/sport-health).
# Запуск:  ./deploy.sh [тег-или-ветка]     (по умолчанию feature/cycle-auto-tracking)
# Единственный разрешённый путь деплоя — см. docs/DEPLOY.md.
#
# Логика: fetch → checkout/reset → pip (если менялся requirements.txt) → pytest
#         → restart sport-health → ожидание is-active (до 30 с) → диагностика.
# При провале тестов, pip или подъёма сервиса — откат на предыдущую ревизию + restart + exit 1.
set -e

SERVICE="sport-health"
REF="${1:-feature/cycle-auto-tracking}"
HEALTHCHECK_LOG="healthcheck.log"
WAIT_SECONDS=30

log() { echo "[deploy] $*"; }

rollback() {
    log "ОТКАТ на предыдущую ревизию ${PREV_SHA} ..."
    git reset --hard "${PREV_SHA}"
    systemctl restart "${SERVICE}" || true
    sleep 3
    log "После отката сервис: $(systemctl is-active "${SERVICE}" || echo unknown)"
}

log "=== Деплой: ${REF} ==="

PREV_SHA="$(git rev-parse HEAD)"
log "Текущая ревизия (точка отката): ${PREV_SHA}"

log "Фаза 1/5: git fetch --tags origin"
git fetch --tags origin

log "Фаза 2/5: git checkout ${REF}"
git checkout "${REF}"
if git rev-parse --verify --quiet "origin/${REF}" >/dev/null; then
    log "Это ветка: git reset --hard origin/${REF}"
    git reset --hard "origin/${REF}"
else
    log "Это тег/коммит: git reset --hard ${REF}"
    git reset --hard "${REF}"
fi
NEW_SHA="$(git rev-parse HEAD)"
log "Целевая ревизия: ${NEW_SHA}"

log "Фаза 3/5: зависимости"
if [ -n "$(git diff --name-only "${PREV_SHA}" "${NEW_SHA}" -- requirements.txt)" ]; then
    log "requirements.txt изменился: pip install -r requirements.txt -q"
    if ! pip install -r requirements.txt -q; then
        log "ОШИБКА: pip install завершился с кодом != 0."
        rollback
        exit 1
    fi
else
    log "requirements.txt не менялся — pip install пропускаю."
fi

log "Фаза 4/5: тесты (python3 -m pytest tests/ -x -q)"
if ! python3 -m pytest tests/ -x -q; then
    log "ОШИБКА: тесты упали — деплой прерван."
    rollback
    exit 1
fi
log "Тесты пройдены."

log "Фаза 5/5: systemctl restart ${SERVICE}"
if ! systemctl restart "${SERVICE}"; then
    log "ОШИБКА: systemctl restart вернул ненулевой код."
    rollback
    exit 1
fi

log "Ожидание запуска сервиса (до ${WAIT_SECONDS} с, опрос systemctl is-active)..."
ACTIVE=""
for _ in $(seq 1 "${WAIT_SECONDS}"); do
    if systemctl is-active --quiet "${SERVICE}"; then
        ACTIVE="yes"
        break
    fi
    sleep 1
done

if [ -z "${ACTIVE}" ]; then
    log "ОШИБКА: сервис не стал active за ${WAIT_SECONDS} с."
    log "--- journalctl -u ${SERVICE} -n 30 ---"
    journalctl -u "${SERVICE}" -n 30 --no-pager || true
    rollback
    exit 1
fi

log "--- tail -n 20 ${HEALTHCHECK_LOG} ---"
tail -n 20 "${HEALTHCHECK_LOG}" 2>/dev/null || log "(файл ${HEALTHCHECK_LOG} отсутствует — это нормально сразу после настройки)"
log "--- journalctl -u ${SERVICE} -n 10 ---"
journalctl -u "${SERVICE}" -n 10 --no-pager || true

log "=== УСПЕХ: задеплоено ${REF} (${NEW_SHA}), сервис: $(systemctl is-active "${SERVICE}" || echo unknown) ==="
log "Не забудьте прогнать смоук-чеклист из docs/DEPLOY.md и внести запись в журнал деплоя."

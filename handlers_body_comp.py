"""
Миксин: BodyCompMixin
Автоматически извлечён из bot.py
"""

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from telegram import InlineKeyboardButton
from config import BC_NORMS, BC_RED_FLAGS

SCALE_PROFILE_COUNT = 50
BC_PROFILE_PAGE = 25

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


# Окно просмотра замеров — ОДНО на все экраны (карточка врача/тренера, «состав
# тела» спортсмена, «динамика», «история», блок в профиле). Раньше карточка
# читала 30 дней, а список «кто взвесился» — 999: у 24 из 27 карточка отвечала
# «нет данных», хотя история в базе была. Взвешивания раз в 1-2 месяца → окно сезона.
BC_WINDOW_DAYS = 365
# Список «кто взвесился» — все, у кого хоть когда-то был замер.
BC_LIST_WINDOW_DAYS = 999

# Как подписывать метрику: (ключ, подпись, единица). Единица None = «брать из
# строки»: костную массу приложение весов в одном экспорте отдаёт в кг, в другом
# в %. Неизвестную единицу показываем честно, без «кг»: экран медицинский.
BC_METRICS_SPEC = (
    ("weight_kg", "Вес", "кг"),
    ("bmi", "ИМТ", ""),
    ("body_fat_pct", "Жир", "%"),
    ("muscle_mass_kg", "Мышцы", "%"),
    ("skeletal_muscle_pct", "Скелетные мышцы", "%"),
    ("body_water_pct", "Вода", "%"),
    ("bone_mass_kg", "Кости", None),
    ("visceral_fat_index", "Висц. жир", ""),
    ("subcutaneous_fat_pct", "Подкож. жир", "%"),
    ("lean_mass_kg", "Безжировая", "кг"),
    ("protein_pct", "Белок", "%"),
    ("bmr_kcal", "BMR", "ккал"),
)

# Набор метрик на экране «Состав тела» спортсмена — тот же, что был, но с
# общими подписями/единицами.
BC_ATHLETE_METRIC_KEYS = ("weight_kg", "body_fat_pct", "muscle_mass_kg", "body_water_pct",
                          "bone_mass_kg", "visceral_fat_index", "bmr_kcal")


def _bc_unit_for(key: str, row: dict) -> str:
    """Единица метрики по правилу: из строки для костей, иначе из спецификации."""
    for k, _label, unit in BC_METRICS_SPEC:
        if k != key:
            continue
        if unit is not None:
            return unit
        recorded = (row.get("bone_unit") or "").strip()
        return recorded or "не подтверждена"
    return ""


def format_bc_metrics(row: dict) -> list:
    """Строки метрик по единому правилу: порядок, подписи, единицы.

    None пропускаем (не «0» и не прочерк). Для костей единица берётся из записи;
    если её нет — пометка, что единица не подтверждена.
    """
    lines = []
    for key, label, _unit in BC_METRICS_SPEC:
        v = row.get(key)
        if v is None:
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            continue
        unit = _bc_unit_for(key, row)
        if key == "bmi" and (row.get("bmi_source") or "") == "computed":
            unit = "расчёт"        # ИМТ досчитан из веса и роста, весами не измерен
        if key == "bone_mass_kg" and unit == "не подтверждена":
            lines.append(f"• {label}: *{num:.1f}* (единица не подтверждена)")
        else:
            lines.append(f"• {label}: *{num:.1f}* {unit}".rstrip())
    return lines


def _bc_pairs_for_athlete(latest: dict, prev) -> list:
    """(значение, предыдущее, подпись, единица) для экрана спортсмена."""
    labels = {k: label for k, label, _u in BC_METRICS_SPEC}
    out = []
    for key in BC_ATHLETE_METRIC_KEYS:
        out.append((latest.get(key), (prev or {}).get(key), labels[key],
                    _bc_unit_for(key, latest)))
    return out


def slot_number(name) -> int:
    """Номер пользовательского слота весов из его подписи.

    Приложение MovingLife подписывает слот «Данные пользователя 12», экран
    «Профили весов» создаёт «Профиль 12» — номер один и тот же, строка нет.
    Число считается только как отдельное число (не «2026» из года). 0 = не распознано.
    """
    import re as _re
    m = _re.search(r"(?<!\d)(\d{1,3})(?!\d)", str(name or ""))
    return int(m.group(1)) if m else 0


def scale_profile_lookup(mapping: dict, prof: str):
    """Спортсмен по подписи слота: сначала точное имя, затем номер слота.

    Совпадение по номеру допускается только когда номеру в таблице соответствует
    ровно один спортсмен; иначе не гадаем — это медицинские данные.
    """
    aid = mapping.get(prof)
    if aid is not None:
        return aid
    num = slot_number(prof)
    if not num:
        return None
    hits = {v for k, v in mapping.items() if slot_number(k) == num}
    return hits.pop() if len(hits) == 1 else None


def finalize_scale_metrics(data: dict) -> dict:
    """Досчитать ИМТ, если файл весов его не отдал, и пометить источник.

    bmi_source: 'file' — значение пришло из файла весов; 'computed' — посчитали из
    веса и роста этой же строки. Без роста ИМТ не выдумываем.
    """
    if data.get("bmi") is not None:
        data.setdefault("bmi_source", "file")
        return data
    try:
        w = float(data.get("weight_kg"))
        h = float(data.get("height_cm"))
    except (TypeError, ValueError):
        return data
    if 20 <= w <= 250 and 100 <= h <= 230:
        data["bmi"] = round(w / ((h / 100.0) ** 2), 1)
        data["bmi_source"] = "computed"
    return data


class BodyCompMixin:
    """Миксин для bodycomp."""

    async def bc_overview(self, update, ctx):
        """Обзор: кто из спортсменов имеет данные состава тела — по командам."""
        q = update.callback_query
        await q.answer()
        if not self._is_admin_or_coach(q.from_user.id):
            return

        athletes_with_data = self.db.get_bc_period(days=BC_LIST_WINDOW_DAYS)
        if not athletes_with_data:
            await q.edit_message_text(
                "📈 *Кто взвесился*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Пока ни один спортсмен не взвешивался.\n"
                "Отправь CSV-файл весов для импорта.",
                reply_markup=self.kb([[(f"🔙 Назад", "bc_menu")]])
            )
            return

        # Группируем по спортсменам
        latest_by_athlete = {}
        for row in athletes_with_data:
            aid = row.get("athlete_id")
            if aid not in latest_by_athlete:
                latest_by_athlete[aid] = {
                    "name": row.get("full_name", "?"),
                    "team": row.get("team", "?"),
                    "date": row.get("record_date", "?"),
                    "weight": row.get("weight_kg"),
                    "fat": row.get("body_fat_pct"),
                    "muscle": row.get("muscle_mass_kg"),
                    "count": 0
                }
            latest_by_athlete[aid]["count"] += 1

        # Фильтрация для тренера — только свои команды
        uid = q.from_user.id
        if not self._is_full_access(uid):
            coach_teams = self._coach_teams(uid)
            latest_by_athlete = {k: v for k, v in latest_by_athlete.items()
                                 if v["team"] in coach_teams}

        # Группируем по командам
        by_team = {}
        for aid, info in latest_by_athlete.items():
            by_team.setdefault(info["team"], []).append((aid, info))

        lines = [
            f"📈 *Кто взвесился* ({len(latest_by_athlete)} спортсменов)",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        for team in sorted(by_team.keys()):
            lines.append(f"🏀 *{team}* — {len(by_team[team])} чел.")

        btns = []
        for team in sorted(by_team.keys()):
            btns.append([(f"🏀 {team} ({len(by_team[team])})", f"bc_team_{team}")])
        btns.append([("📊 Все спортсмены", "bc_overview_all")])
        btns.append([("🔙 Назад", "bc_menu")])

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb(btns),
            parse_mode="Markdown"
        )


    async def bc_team_athletes(self, update, ctx):
        """Список спортсменов команды с данными состава тела."""
        q = update.callback_query
        team = q.data.replace("bc_team_", "")
        # Scope: тренер видит только свои команды (роль проверена гейтом
        # диспетчера; админ/врач — без ограничений). Диспетчер уже мог ответить
        # на callback (одноразовый ответ), поэтому алерт может не пройти —
        # делаем отказ видимым ещё и заменой сообщения.
        _uid = q.from_user.id
        if not self._is_full_access(_uid) and team not in set(self.db.get_coach_teams(_uid)):
            logger.warning(f"Access denied (scope bc_team_): user {_uid}, team {team}")
            try:
                await q.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            try:
                await q.edit_message_text("⛔ Нет доступа")
            except Exception:
                pass
            return
        try:
            await q.answer()
        except Exception:
            pass  # обычно «Query is already answered» от диспетчера — не ошибка
        athletes_with_data = self.db.get_bc_period(days=BC_LIST_WINDOW_DAYS)

        latest_by_athlete = {}
        for row in athletes_with_data:
            aid = row.get("athlete_id")
            if row.get("team") != team:
                continue
            if aid not in latest_by_athlete:
                latest_by_athlete[aid] = {
                    "name": row.get("full_name", "?"),
                    "date": row.get("record_date", "?"),
                    "weight": row.get("weight_kg"),
                    "fat": row.get("body_fat_pct"),
                    "muscle": row.get("muscle_mass_kg"),
                    "count": 0
                }
            latest_by_athlete[aid]["count"] += 1

        lines = [
            f"🏀 *{team}*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        btns = []
        for aid, info in sorted(latest_by_athlete.items(), key=lambda x: x[1]["name"]):
            weight = f"{info['weight']:.1f}кг" if info['weight'] else "—"
            fat = f"{info['fat']:.1f}%" if info['fat'] else "—"
            muscle = f"{info['muscle']:.1f}%" if info['muscle'] else "—"
            lines.append(
                f"👤 *{info['name']}* | 📅 {info['date']} | "
                f"⚖️ {weight} | жир {fat} | мышцы {muscle}"
            )
            btns.append([(f"📊 {info['name']}", f"bc_view_{aid}")])

        if not btns:
            lines.append("_Пока нет данных по этой команде._")
        btns.append([("🔙 Назад", "bc_overview")])

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb(btns),
            parse_mode="Markdown"
        )


    async def bc_overview_all(self, update, ctx):
        """Плоский список всех спортсменов с данными (все команды)."""
        q = update.callback_query
        await q.answer()
        athletes_with_data = self.db.get_bc_period(days=BC_LIST_WINDOW_DAYS)

        latest_by_athlete = {}
        for row in athletes_with_data:
            aid = row.get("athlete_id")
            if aid not in latest_by_athlete:
                latest_by_athlete[aid] = {
                    "name": row.get("full_name", "?"),
                    "team": row.get("team", "?"),
                    "date": row.get("record_date", "?"),
                    "weight": row.get("weight_kg"),
                    "fat": row.get("body_fat_pct"),
                    "muscle": row.get("muscle_mass_kg"),
                    "count": 0
                }
            latest_by_athlete[aid]["count"] += 1

        # Фильтрация для тренера
        uid = q.from_user.id
        if not self._is_full_access(uid):
            coach_teams = self._coach_teams(uid)
            latest_by_athlete = {k: v for k, v in latest_by_athlete.items()
                                 if v["team"] in coach_teams}

        lines = [
            f"📈 *Все спортсмены* ({len(latest_by_athlete)})",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        btns = []
        for aid, info in sorted(latest_by_athlete.items(), key=lambda x: x[1]["name"]):
            weight = f"{info['weight']:.1f}кг" if info['weight'] else "—"
            fat = f"{info['fat']:.1f}%" if info['fat'] else "—"
            lines.append(
                f"👤 *{info['name']}* ({info['team']}) | ⚖️ {weight} | жир {fat}"
            )
            btns.append([(f"📊 {info['name']}", f"bc_view_{aid}")])

        btns.append([("🔙 Назад", "bc_overview")])

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb(btns),
            parse_mode="Markdown"
        )


    async def bc_show_profiles(self, update, ctx, page=0):
        """Показать/закрепить сопоставление профилей весов → спортсмены (постранично)."""
        q = update.callback_query
        await q.answer()
        if not self._is_admin_or_coach(q.from_user.id):
            return
        mapping = self.db.get_scale_profiles()
        athletes = {a["id"]: a["full_name"] for a in self.db.get_all_athletes()}
        lines = ["🗂 *Профили весов → спортсмены*\n"]
        if mapping:
            for prof, aid in sorted(mapping.items()):
                name = athletes.get(aid, f"id{aid}")
                lines.append(f"{prof} → *{name}*")
        else:
            lines.append("Пока не закреплено. Выбери профиль и назначь спортсмена.")
        lines.append("\nВыбери профиль, чтобы назначить/изменить спортсмена:")

        total_pages = -(-SCALE_PROFILE_COUNT // BC_PROFILE_PAGE)
        page = max(0, min(page, total_pages - 1))
        s = page * BC_PROFILE_PAGE + 1
        e = min(page * BC_PROFILE_PAGE + BC_PROFILE_PAGE, SCALE_PROFILE_COUNT) + 1
        btns = [[(f"Профиль {n}", f"bc_map_{n}") for n in range(row, min(row + 4, e))]
                for row in range(s, e, 4)]
        nav = []
        if page > 0:
            nav.append((f"◀ {page}", f"bc_page_{page - 1}"))
        if page < total_pages - 1:
            nav.append((f"{page + 2} ▶", f"bc_page_{page + 1}"))
        if nav:
            btns.append(nav)
        btns.append([("🔙 Назад", "bc_menu")])
        await q.edit_message_text("\n".join(lines), reply_markup=self.kb(btns))


    async def bc_page(self, update, ctx):
        """Страница списка профилей весов."""
        q = update.callback_query
        page = max(0, int(q.data.replace("bc_page_", "")))
        await self.bc_show_profiles(update, ctx, page=page)


    async def bc_assign_pick(self, update, ctx):
        """После выбора профиля — список спортсменов для назначения."""
        q = update.callback_query
        await q.answer()
        if not self._is_admin_or_coach(q.from_user.id):
            return
        profile = int(q.data.replace("bc_map_", ""))
        state = self.get_state(q.from_user.id)
        state["data"]["bc_map_profile"] = profile
        athletes = self._scoped_athletes(q.from_user.id)
        if not athletes:
            await q.edit_message_text("Нет спортсменов в доступе.", reply_markup=self.kb([[(f"🔙 Назад", "bc_profiles")]]))
            return
        btns = []
        for a in athletes[:30]:
            btns.append([(f"{a['full_name']}", f"bc_assign_{profile}_{a['id']}")])
        btns.append([("🔙 Назад", "bc_profiles")])
        await q.edit_message_text(f"Профиль *{profile}* → выбери спортсмена:", reply_markup=self.kb(btns))


    async def bc_assign_save(self, update, ctx):
        """Закрепить профиль N за спортсменом."""
        q = update.callback_query
        if not self._is_admin_or_coach(q.from_user.id):
            return
        parts = q.data.split("_")
        profile = int(parts[2]); athlete_id = int(parts[3])
        self.db.set_scale_profile(f"Профиль {profile}", athlete_id, q.from_user.id)
        await q.answer("✅ Профиль закреплён", show_alert=True)
        await self.bc_show_profiles(update, ctx)


    async def bc_view_athlete(self, update, ctx):
        """Карточка состава тела спортсмена (только врач/тренер), расширенные метрики."""
        q = update.callback_query
        if not self._is_admin_or_coach(q.from_user.id):
            logger.warning(f"Access denied (role bc_view_): user {q.from_user.id}")
            try:
                await q.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            try:
                await q.edit_message_text("⛔ Нет доступа")
            except Exception:
                pass
            return
        athlete_id = int(q.data.replace("bc_view_", ""))
        # Scope: тренер видит только спортсменов своих команд (роль проверена
        # гейтом диспетчера; админ/врач — без ограничений). Диспетчер уже мог
        # ответить на callback (одноразовый ответ), поэтому алерт может не
        # пройти — делаем отказ видимым ещё и заменой сообщения.
        _uid = q.from_user.id
        if not self._is_full_access(_uid):
            _a = self.db.get_athlete_by_id(athlete_id)
            if not _a or _a.get("team") not in set(self.db.get_coach_teams(_uid)):
                logger.warning(f"Access denied (scope bc_view_): user {_uid}, athlete {athlete_id}")
                try:
                    await q.answer("⛔ Нет доступа", show_alert=True)
                except Exception:
                    pass
                try:
                    await q.edit_message_text("⛔ Нет доступа")
                except Exception:
                    pass
                return
        try:
            await q.answer()
        except Exception:
            pass  # обычно «Query is already answered» от диспетчера — не ошибка
        history = self.db.get_body_composition(athlete_id, days=BC_WINDOW_DAYS)
        if not history:
            name = "спортсмен"
            try:
                a = self.db.get_athlete_by_id(athlete_id)
                if a:
                    name = a.get("full_name", name)
            except Exception:
                pass
            await q.edit_message_text(
                f"⚖️ *{name}* — пока нет данных составов весов.\n\nОтправь CSV из MovingLife (меню ⚖️ Весы) или взвесь профиль.",
                reply_markup=self.kb([[(f"🔙 Назад", "bc_overview")]])
            )
            return
        name = "спортсмен"
        try:
            a = self.db.get_athlete_by_id(athlete_id)
            if a:
                name = a.get("full_name", name)
        except Exception:
            pass
        lines = [f"⚖️ *Состав тела — {name}*", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        latest = history[0]
        lines.extend(format_bc_metrics(latest))
        lines.append(f"\n📅 Замеров за {BC_WINDOW_DAYS} дней: {len(history)}")
        lines.append(f"(последний: {latest.get('record_date')})")
        # тренд веса
        trend = self.db.get_bc_trend(athlete_id, BC_WINDOW_DAYS)
        wt = trend.get("weight_kg", [])
        if len(wt) >= 2:
            d = wt[-1][1] - wt[0][1]
            lines.append(f"📈 Вес 30дн: {wt[0][1]:.1f} → {wt[-1][1]:.1f} кг ({d:+.1f})")
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb([[(f"🔙 Назад", "athlete_list")]])
        )


    def _scale_summary_line(self, r):
        """Одна строка сводки по замеру состава тела."""
        parts = []
        if r.get("weight_kg") is not None:
            parts.append(f"вес {r['weight_kg']:.0f}кг")
        if r.get("body_fat_pct") is not None:
            parts.append(f"жир {r['body_fat_pct']:.1f}%")
        if r.get("muscle_mass_kg") is not None:
            parts.append(f"мышцы {r['muscle_mass_kg']:.1f}%")
        if r.get("body_water_pct") is not None:
            parts.append(f"вода {r['body_water_pct']:.0f}%")
        date_s = str(r.get("record_date") or "?")
        return f"  📅 {date_s}: " + ", ".join(parts) if parts else f"  📅 {date_s}: —"


    def _scale_athlete_label(self, athlete_id) -> str:
        a = self.db.get_athlete_by_id(athlete_id)
        return (a.get("full_name") if a and a.get("full_name") else f"#{athlete_id}")

    async def _import_scale_records(self, update, ctx, records, forced_athlete_id=None,
                                    preview_only=False):
        """Разложить CSV записей весов по спортсменам (опционально — сохранить).

        Возвращает (записано, пропущено, unmapped-профили, rows, foreign-профили).

        Правило одно для всех ролей:
         * врач/админ (full access) раскладывает всю базу и пишет сразу;
         * тренер пишет только своих спортсменов и только после подтверждения
           раскладки: preview_only=True считает раскладку, ничего не записывая
           (в «saved» тогда считается «будет записано»);
         * строки чужих команд уходят в foreign и не пишутся никогда — раньше их
           можно было молча залить в чужую историю;
         * профиль без привязки — unmapped, тоже не пишется.
        """
        user_id = update.effective_user.id
        mapping = self.db.get_scale_profiles()
        full_access = self._is_full_access(user_id)
        scope_ids = None if full_access else {a["id"] for a in self._scoped_athletes(user_id)}
        saved = 0
        skipped = 0
        unmapped = []
        foreign = []
        rows = []
        height_cache = {}
        for r in records:
            prof = r.get("device_profile")
            # athlete_id: либо явный (forced), либо через маппинг профиля
            athlete_id = forced_athlete_id
            if athlete_id is None and prof:
                athlete_id = scale_profile_lookup(mapping, prof)
            if athlete_id is None:
                # нет привязки — копим на маппинг
                if prof and prof not in unmapped:
                    unmapped.append(prof)
                skipped += 1
                if preview_only:
                    rows.append(f"• {prof or 'без профиля'} → профиль не закреплён, "
                                f"записано не будет")
                continue
            if scope_ids is not None and athlete_id not in scope_ids:
                tag = prof or f"#{athlete_id}"
                if tag not in foreign:
                    foreign.append(tag)
                skipped += 1
                if preview_only:
                    rows.append(f"• {prof or 'без профиля'} → ⛔ чужая команда "
                                f"({self._scale_athlete_label(athlete_id)}), "
                                f"записано не будет")
                continue
            # рост из анкеты (кэш)
            if athlete_id not in height_cache:
                try:
                    qd = self.db.get_questionnaire(athlete_id)
                    height_cache[athlete_id] = qd.get("height") if qd else None
                except Exception:
                    height_cache[athlete_id] = None
            data = dict(r)
            if height_cache.get(athlete_id):
                data.setdefault("height_cm", height_cache[athlete_id])
            if preview_only:
                saved += 1
                rows.append(f"• {prof or 'без профиля'} → "
                            f"{self._scale_athlete_label(athlete_id)}: "
                            f"{self._scale_summary_line(r).strip()}")
                continue
            data = finalize_scale_metrics(data)
            # recorded_by — отдельный аргумент сохранения; раньше его клали только в
            # data-словарь, из-за чего автор замера в проде не записан НИ РАЗУ.
            ok = self.db.save_body_composition(athlete_id, r.get("record_date"), data,
                                               source="csv", recorded_by=user_id,
                                               device_profile=prof)
            if ok:
                saved += 1
                rows.append(f"👤 {athlete_id}: {self._scale_summary_line(r)}")
            else:
                skipped += 1
        return saved, skipped, unmapped, rows, foreign

    # ==================== АНАЛИЗ СОСТАВА ТЕЛА + УВЕДОМЛЕНИЯ ====================


    def _get_bc_status(self, value, norms_key, gender="male"):
        """Получить статус показателя: ✅ норма, ⚠️ погранично, 🚨 отклонение."""
        if value is None:
            return ""
        norms = BC_NORMS.get(self._current_age_group, BC_NORMS.get("Pro", {}))
        gender_norms = norms.get(gender, norms.get("male", {}))
        norm_range = gender_norms.get(norms_key)
        if not norm_range:
            return ""
        low, high = norm_range
        if low <= value <= high:
            return "✅"
        elif value < low * 0.9 or value > high * 1.1:
            return "🚨"
        else:
            return "⚠️"


    def _get_bc_status_text(self, value, norms_key, gender="male", unit=""):
        """Получить строку показателя со статусом."""
        if value is None:
            return None
        status = self._get_bc_status(value, norms_key, gender)
        return f"{value:.1f}{unit} {status}"


    def _bc_prev_measurement(self, latest, history):
        """Предыдущий замер для тренда. История идёт ПО УБЫВАНИЮ даты, поэтому «самый
        старый» — это history[-1], а предыдущий для тренда — первый со строго более
        ранней датой, чем последний замер (иначе брался сам последний и дельта = +0.0)."""
        last_d = str((latest or {}).get("record_date") or "")
        for h in history or []:
            if str(h.get("record_date") or "") < last_d:
                return h
        return None


    def _build_athlete_bc_report(self, latest, prev, athlete):
        """Построить сообщение анализа состава тела для спортсмена."""
        if not latest:
            return "⚖️ Нет данных для анализа."

        date_s = latest.get("record_date", "?")
        gender = athlete.get("gender", "male")
        age_group = athlete.get("age_group", "Pro")
        self._current_age_group = age_group

        lines = [
            f"⚖️ *Твой анализ состава тела*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {date_s}\n",
            f"*📊 Ключевые показатели:*"
        ]

        # 5 ключевых метрик
        weight = latest.get("weight_kg")
        fat = latest.get("body_fat_pct")
        muscle = latest.get("muscle_mass_kg")
        water = latest.get("body_water_pct")
        bmr = latest.get("bmr_kcal")

        if weight is not None:
            lines.append(f"• Вес: *{weight:.1f}* кг")
        if fat is not None:
            fat_status = self._get_bc_status(fat, "body_fat_pct", gender)
            lines.append(f"• Жир: *{fat:.1f}%* {fat_status}")
        if muscle is not None:
            lines.append(f"• Мышцы: *{muscle:.1f}* %")
        if water is not None:
            water_status = self._get_bc_status(water, "body_water_pct", gender)
            lines.append(f"• Вода: *{water:.0f}%* {water_status}")
        if bmr is not None:
            lines.append(f"• Базовый метаболизм: *{bmr}* ккал")

        # Тренд (если есть предыдущий замер)
        base = self._bc_prev_measurement(latest, prev)
        if base:
            older = base
            lines.append(f"\n*📈 Изменения с прошлого замера ({older.get('record_date')}):*")
            if weight is not None and older.get("weight_kg") is not None:
                d = weight - older["weight_kg"]
                lines.append(f"• Вес: {older['weight_kg']:.1f} → {weight:.1f} ({d:+.1f})")
            if fat is not None and older.get("body_fat_pct") is not None:
                d = fat - older["body_fat_pct"]
                emoji = "✅" if abs(d) <= 2 else ("⚠️" if abs(d) <= 5 else "🚨")
                lines.append(f"• Жир: {older['body_fat_pct']:.1f}% → {fat:.1f}% ({d:+.1f}%) {emoji}")
            if muscle is not None and older.get("muscle_mass_kg") is not None:
                d = muscle - older["muscle_mass_kg"]
                emoji = "✅" if d >= -0.5 else ("⚠️" if d >= -1.5 else "🚨")
                lines.append(f"• Мышцы: {older['muscle_mass_kg']:.1f}% → {muscle:.1f}% ({d:+.1f}%) {emoji}")
            if water is not None and older.get("body_water_pct") is not None:
                d = water - older["body_water_pct"]
                lines.append(f"• Вода: {older['body_water_pct']:.0f}% → {water:.0f}% ({d:+.0f}%)")

        # Рекомендации
        recs = []
        if water is not None and water < 55:
            recs.append("⚠️ Вода ниже нормы — пей больше воды!\nПей ~2.5-3 л воды в день, особенно перед тренировкой.")
        if fat is not None:
            fat_norms = BC_NORMS.get(age_group, BC_NORMS.get("Pro", {})).get(gender, {})
            fat_high = fat_norms.get("body_fat_pct", (0, 100))[1]
            if fat > fat_high:
                recs.append(f"⚠️ Жир выше нормы ({fat_high}%). Снизь калорийность рациона.")
        if muscle is not None and base and base.get("muscle_mass_kg") and muscle - base["muscle_mass_kg"] < -1.5:
            recs.append("🚨 Потеря мышц! Обратись к врачу.")

        if recs:
            lines.append(f"\n*💡 Рекомендации:*")
            lines.extend(recs)
        else:
            lines.append(f"\n💡 *Всё в порядке! Продолжай в том же духе.*")

        lines.append(f"\n⚠️ _Это не медицинское заключение._")
        return "\n".join(lines)


    def _build_admin_bc_report(self, latest, prev, athlete):
        """Построить сообщение для врача (админа) — полный анализ."""
        if not latest:
            return "⚖️ Нет данных для анализа."

        date_s = latest.get("record_date", "?")
        name = athlete.get("full_name", "?")
        team = athlete.get("team", "?")
        age_group = athlete.get("age_group", "?")
        gender = athlete.get("gender", "male")
        self._current_age_group = age_group

        lines = [
            f"⚖️ *Новый замер — {name}*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🏀 {team} | {age_group} | {date_s}\n",
            f"*📊 Состав тела:*"
        ]

        # Все метрики
        weight = latest.get("weight_kg")
        fat = latest.get("body_fat_pct")
        muscle = latest.get("muscle_mass_kg")
        water = latest.get("body_water_pct")
        bone = latest.get("bone_mass_kg")
        visceral = latest.get("visceral_fat_index")
        bmr = latest.get("bmr_kcal")
        amr = latest.get("amr_kcal")

        parts = []
        if weight is not None:
            parts.append(f"Вес: *{weight:.1f}* кг")
        if fat is not None:
            parts.append(f"Жир: *{fat:.1f}%*")
        if muscle is not None:
            parts.append(f"Мышцы: *{muscle:.1f}* %")
        if parts:
            lines.append(" | ".join(parts))

        parts2 = []
        if water is not None:
            parts2.append(f"Вода: *{water:.0f}%*")
        if bone is not None:
            parts2.append(f"Кости: *{bone:.2f}* кг")
        if visceral is not None:
            parts2.append(f"Висц. жир: *{visceral:.0f}*")
        if parts2:
            lines.append(" | ".join(parts2))

        parts3 = []
        if bmr is not None:
            parts3.append(f"BMR: *{bmr}* ккал")
        if amr is not None:
            parts3.append(f"AMR: *{amr}* ккал")
        if parts3:
            lines.append(" | ".join(parts3))

        # Тренд — от предыдущего замера
        base = self._bc_prev_measurement(latest, prev)
        if base:
            older = base
            lines.append(f"\n*📈 Тренд с прошлого замера ({older.get('record_date')}):*")
            trend_parts = []
            if weight is not None and older.get("weight_kg") is not None:
                d = weight - older["weight_kg"]
                trend_parts.append(f"Вес {d:+.1f}")
            if fat is not None and older.get("body_fat_pct") is not None:
                d = fat - older["body_fat_pct"]
                trend_parts.append(f"жир {d:+.1f}%")
            if muscle is not None and older.get("muscle_mass_kg") is not None:
                d = muscle - older["muscle_mass_kg"]
                trend_parts.append(f"мышцы {d:+.1f}%")
            if trend_parts:
                lines.append(" | ".join(trend_parts))

        # Красные флаги
        flags = []
        if fat is not None and base and base.get("body_fat_pct"):
            d = abs(fat - base["body_fat_pct"])
            if d > BC_RED_FLAGS["fat_change_pct"]:
                flags.append(f"⚠️ Жир изменился на {d:.1f}% — возможен артефакт. Перевзвеси!")
        if muscle is not None and base and base.get("muscle_mass_kg"):
            d = muscle - base["muscle_mass_kg"]
            if d < -BC_RED_FLAGS["muscle_loss_pct"]:
                flags.append(f"🚨 Потеря мышц: {d:+.1f}%!")
        if water is not None and water < BC_RED_FLAGS["water_critical_pct"]:
            flags.append(f"⚠️ Критическое обезвоживание: {water:.0f}%!")
        if visceral is not None and visceral > BC_RED_FLAGS["visceral_risk"]:
            flags.append(f"🔴 Высокий висцеральный жир: {visceral:.0f}")

        if flags:
            lines.append(f"\n*🚨 Красные флаги:*")
            lines.extend(flags)

        lines.append(f"\n_⚠️ GARLYN: жир занижает ~3-4% (Potter+3%)_")
        return "\n".join(lines)


    def _build_coach_bc_report(self, latest, prev, athlete):
        """Построить сообщение для тренера — краткая сводка без медицинских деталей."""
        if not latest:
            return "⚖️ Нет данных для анализа."

        name = athlete.get("full_name", "?")
        weight = latest.get("weight_kg")
        muscle = latest.get("muscle_mass_kg")

        parts = []
        if weight is not None:
            parts.append(f"⚖️ *{weight:.1f}* кг")
        if muscle is not None:
            parts.append(f"мышцы *{muscle:.1f}* %")

        trend_parts = []
        base = self._bc_prev_measurement(latest, prev)
        if base:
            if weight is not None and base.get("weight_kg") is not None:
                d = weight - base["weight_kg"]
                trend_parts.append(f"{d:+.1f} кг")
            if muscle is not None and base.get("muscle_mass_kg") is not None:
                d = muscle - base["muscle_mass_kg"]
                trend_parts.append(f"мышцы {d:+.1f}%")

        # Определяем статус
        status = "✅ Норма"
        if muscle is not None and base and base.get("muscle_mass_kg") and muscle - base["muscle_mass_kg"] < -1.5:
            status = "🚨 Внимание!"

        lines = [
            f"📋 *Замер — {name}*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━",
            " | ".join(parts) if parts else "—",
        ]
        if trend_parts:
            lines.append(f"📈 Тренд: {' | '.join(trend_parts)}")
        lines.append(status)
        return "\n".join(lines)


    async def _analyze_and_notify_scale(self, update, athlete, records):
        """Анализ состава тела + уведомления врачу и тренеру после импорта CSV."""
        aid = athlete["id"]

        # Получаем текущий и предыдущий замер
        latest = self.db.get_latest_body_composition(aid)
        all_history = self.db.get_body_composition(aid, days=90)

        if not latest:
            return

        # --- Текст для спортсмена ---
        athlete_text = self._build_athlete_bc_report(latest, all_history, athlete)
        try:
            await update.message.reply_text(
                athlete_text, parse_mode="Markdown",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )
        except Exception as e:
            logger.error(f" Athlete BC report error: {e}")

        # --- Уведомление врачу (админу) ---
        admin_text = self._build_admin_bc_report(latest, all_history, athlete)
        try:
            for admin_id in self._full_access_ids():
                if admin_id != athlete.get("telegram_id"):
                    await self._send_admin(admin_id, admin_text)
        except Exception as e:
            logger.error(f"Admin BC notify error: {e}")

        # --- Уведомление тренеру ---
        team = athlete.get("team")
        if team:
            coach_text = self._build_coach_bc_report(latest, all_history, athlete)
            try:
                coaches = self.db.get_team_coaches(team)
                full_ids = self._full_access_ids()
                for cid in coaches:
                    if cid == athlete.get("telegram_id") or cid in full_ids:
                        continue
                    await self._send_admin(cid, coach_text)
            except Exception as e:
                logger.error(f"Coach BC notify error: {e}")


    async def _ask_height_for_records(self, update, ctx, records):
        """Если у записей нет роста в анкете — просим ввести один раз."""
        # берём первый athlete_id с отсутствующим ростом
        mapping = self.db.get_scale_profiles()
        state = self.get_state(update.effective_user.id)
        for r in records:
            prof = r.get("device_profile")
            aid = mapping.get(prof) if prof else None
            if aid is None:
                continue
            qd = self.db.get_questionnaire(aid)
            if qd and qd.get("height"):
                continue
            state["step"] = "bc_height"
            state["data"]["bc_pending"] = records
            state["data"]["bc_height_athlete"] = aid
            await update.message.reply_text(
                f"📏 У спортсмена (id {aid}) нет роста в анкете.\n"
                f"Введи рост в см числом (например: 180):"
            )
            return True
        return False


    async def _bc_height_save(self, update, ctx, user_id, text):
        """Сохранить введённый рост в анкету и завершить импорт."""
        try:
            h = float(text.strip().replace(",", "."))
            if not (80 <= h <= 240):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Введи рост числом от 80 до 240 см (например: 180):")
            return
        state = self.get_state(user_id)
        athlete_id = state.get("data", {}).get("bc_height_athlete")
        # пишем рост в анкету (обновить существующую или создать направление)
        try:
            self.db.conn.execute(
                "UPDATE questionnaires SET height = ? WHERE athlete_id = ?", (int(h), athlete_id)
            )
            self.db.conn.commit()
        except Exception as e:
            logger.error(f"set height: {e}")
        records = state.get("data", {}).get("bc_pending") or []
        self.clear_state(user_id)
        if records:
            saved, skipped, unmapped, rows, foreign = await self._import_scale_records(
                update, ctx, records, forced_athlete_id=None)
            head = [f"✅ Рост сохранён: {int(h)} см", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            if rows:
                head.append(f"⚖️ Сохранено: *{saved}* записей состава тела")
            if skipped:
                head.append(f"⚠️ Пропущено: {skipped}")
            if foreign:
                head.append("⛔ Чужие профили: " + ", ".join(foreign[:5]))
            await update.message.reply_text(
                "\n".join(head),
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"✅ Рост сохранён: {int(h)} см.",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]])
            )


    async def body_comp_menu(self, update, ctx):
        """Меню состава тела для спортсмена — последний замер, дельта, навигация."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return
        latest = self.db.get_latest_body_composition(athlete["id"])
        if not latest:
            await q.edit_message_text(
                "⚖️ *Состав тела*\n\nУ тебя пока нет данных с весов.\n"
                "Загрузи CSV-файл из приложения MovingLife или попроси тренера взвесить тебя.",
                reply_markup=self.kb([
                    [(f"📤 Загрузить данные с весов", "scale_upload_start")],
                    [(f"🔙 Назад", "view_today")]
                ]),
                parse_mode="Markdown"
            )
            return

        # Дельта с предыдущего замера
        history = self.db.get_body_composition(athlete["id"], days=BC_WINDOW_DAYS)
        prev = history[1] if len(history) > 1 else None

        def delta_str(cur, prv, unit=""):
            if cur is None or prv is None:
                return ""
            d = float(cur) - float(prv)
            if abs(d) < 0.05:
                return " (—)"
            arrow = "▲" if d > 0 else "▼"
            sign = "+" if d > 0 else ""
            return f" ({arrow} {sign}{d:.1f}{unit})"

        lines = [
            f"⚖️ *Состав тела*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
            f"📅 Последний замер: *{latest.get('record_date')}*\n"
        ]
        for v, pv, label, unit in _bc_pairs_for_athlete(latest, prev):
            if v is not None:
                lines.append(f"• {label}: *{float(v):.1f}* {unit}"
                             f"{delta_str(v, pv)}")
        if prev:
            lines.append(f"\n_Дельта с {prev.get('record_date')}_")

        lines.append(
            "\n📖 *Легенда:*\n"
            "• Вес — масса тела (кг)\n"
            "• Жир — % подкожного жира\n"
            "• Мышцы — мышечная масса (% от массы тела)\n"
            "• Вода — % воды в организме\n"
            "• Кости — костная масса; единицу берём из файла весов (кг или %)\n"
            "• Висц. жир — висцеральный жир (0-20)\n"
            "• BMR — базовый метаболизм (ккал/день)\n"
            "• AMR — активный метаболизм (ккал/день)"
        )

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb([
                [(f"📤 Загрузить данные с весов", "scale_upload_start")],
                [(f"📊 Динамика", "bc_my_trends")],
                [(f"📜 История (10 замеров)", "body_comp_history")],
                [(f"📈 Графики", "my_charts")],
                [(f"🔙 Назад", "view_today")]
            ]),
            parse_mode="Markdown"
        )


    async def body_comp_history(self, update, ctx):
        """История последних 10 замеров состава тела (для спортсмена)."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return
        history = self.db.get_body_composition(athlete["id"], days=BC_WINDOW_DAYS)
        if not history:
            await q.edit_message_text(
                "📜 *История состава тела*\n\nПока нет данных.",
                reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]]),
                parse_mode="Markdown"
            )
            return
        recent = history[:10]
        lines = [f"📜 *История состава тела* (последние {len(recent)} замеров)\n"]
        for r in recent:
            date_s = r.get("record_date", "?")
            parts = []
            if r.get("weight_kg") is not None:
                parts.append(f"вес {r['weight_kg']:.1f}кг")
            if r.get("body_fat_pct") is not None:
                parts.append(f"жир {r['body_fat_pct']:.1f}%")
            if r.get("muscle_mass_kg") is not None:
                parts.append(f"мышцы {r['muscle_mass_kg']:.1f}%")
            if r.get("body_water_pct") is not None:
                parts.append(f"вода {r['body_water_pct']:.0f}%")
            if r.get("bone_mass_kg") is not None:
                parts.append(f"кости {r['bone_mass_kg']:.2f}кг")
            lines.append(f"📅 {date_s}: " + (", ".join(parts) if parts else "—"))
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]]),
            parse_mode="Markdown"
        )

    # ==================== ОТЧЁТ ПО СОСТАВУ ТЕЛА (EXCEL) ====================


    async def bc_my_trends(self, update, ctx):
        """Динамика всех замеров состава тела для спортсмена."""
        q = update.callback_query
        await q.answer()
        athlete = self.db.get_athlete_by_telegram_id(q.from_user.id)
        if not athlete:
            return
        history = self.db.get_body_composition(athlete["id"], days=BC_WINDOW_DAYS)
        if not history:
            await q.edit_message_text(
                "📊 *Динамика*\n\nПока нет данных. Загрузи CSV-файл.",
                reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]])
            )
            return

        lines = [
            f"📊 *Динамика — {athlete.get('full_name', '?')}*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        key_metrics = [
            ("⚖️ Вес", "weight_kg", "кг"),
            ("🥩 Жир", "body_fat_pct", "%"),
            ("💪 Мышцы", "muscle_mass_kg", "%"),
            ("💧 Вода", "body_water_pct", "%"),
        ]

        for label, key, unit in key_metrics:
            vals = [(r.get("record_date"), r.get(key)) for r in history if r.get(key) is not None]
            if not vals:
                continue
            lines.append(f"*{label}:*")
            for i, (date, val) in enumerate(reversed(vals)):
                v = float(val)
                if i > 0:
                    prev_val = float(vals[-(i)][1]) if i < len(vals) else None
                    if prev_val is not None:
                        d = v - prev_val
                        if abs(d) < 0.05:
                            dtext = "(—)"
                        else:
                            arrow = "▲" if d > 0 else "▼"
                            sign = "+" if d > 0 else ""
                            dtext = f"({arrow} {sign}{d:.1f})"
                    else:
                        dtext = ""
                else:
                    dtext = ""
                lines.append(f"  {date}: *{v:.1f}* {unit} {dtext}")
            lines.append("")

        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]]),
            parse_mode="Markdown"
        )


    async def bc_slot_choose(self, update, ctx):
        """Спортсмен выбрал свой слот весов: запоминаем и дописываем его строки."""
        q = update.callback_query
        try:
            await q.answer()
        except Exception:
            pass
        user_id = q.from_user.id
        athlete = self.db.get_athlete_by_telegram_id(user_id)
        state = self.get_state(user_id)
        profiles = (state.get("data") or {}).get("bc_slot_profiles") or []
        records = (state.get("data") or {}).get("bc_slot_records") or []
        raw_idx = str(q.data).rsplit("_", 1)[-1]
        idx = int(raw_idx) if raw_idx.isdigit() else -1

        if not athlete or not records or idx < 0 or idx >= len(profiles):
            # после рестарта RAM-state пуст — молчать нельзя
            self.clear_state(user_id)
            await q.edit_message_text(
                "⚖️ Файл уже не в памяти (возможно, бот перезагружался).\n"
                "Отправь выгрузку с весов заново.",
                reply_markup=self.kb([[(f"📤 Загрузить данные с весов", "scale_upload_start")],
                                      [(f"🔙 Назад", "body_comp_menu")]]))
            return

        prof = profiles[idx]
        claimed = scale_profile_lookup(self.db.get_scale_profiles(), prof)
        declared = self.db.get_slot_claims().get(prof)
        if (claimed is not None and claimed != athlete["id"]) or \
                (declared is not None and declared != athlete["id"]):
            await q.edit_message_text(
                f"⚠️ Профиль «{prof}» уже закреплён за другим спортсменом.\n\n"
                "Выбери другой свой профиль, а если не уверен — попроси врача "
                "проверить закрепление профилей весов.",
                reply_markup=self.kb([[(f"🔙 Назад", "body_comp_menu")]]))
            return

        self.db.set_scale_slot(athlete["id"], prof, user_id)
        mine_records = [r for r in records if (r.get("device_profile") or "") == prof]
        self.clear_state(user_id)

        saved = 0
        for r in mine_records:
            data = dict(r)
            try:
                qd = self.db.get_questionnaire(athlete["id"])
                if qd and qd.get("height"):
                    data.setdefault("height_cm", qd.get("height"))
            except Exception:
                pass
            data = finalize_scale_metrics(data)
            if self.db.save_body_composition(athlete["id"], r.get("record_date"), data,
                                             source="csv", recorded_by=user_id,
                                             device_profile=r.get("device_profile")):
                saved += 1

        await q.edit_message_text(
            f"✅ Запомнил: твой профиль — «{prof}».\n"
            f"Записано замеров: {saved}. В следующий раз спрашивать не буду.",
            reply_markup=self.kb([[(f"⚖️ Состав тела", "body_comp_menu")]]))
        if saved:
            upd = SimpleNamespace(message=q.message, callback_query=None,
                                  effective_user=SimpleNamespace(id=user_id))
            await self._analyze_and_notify_scale(upd, athlete, mine_records)


    async def scale_upload_start(self, update, ctx):
        """Инструкция для спортсмена по загрузке CSV весов."""
        q = update.callback_query
        await q.answer()
        # «Жду твой файл» без шага было обещанием в нишу: текст вместо файла
        # обрабатывает ветка wait_scale_csv в handle_text.
        state = self.get_state(q.from_user.id)
        state["step"] = "wait_scale_csv"
        await q.edit_message_text(
            "📤 *Загрузка данных с весов*\n\n"
            "Отправь CSV-файл из приложения *MovingLife*: "
            "Открой приложение → Профиль → Экспорт данных → CSV\n\n"
            "⏳ Жду твой файл...",
            reply_markup=self.kb([
                [InlineKeyboardButton("🔙 Назад", callback_data="body_comp_menu")]
            ]),
            parse_mode="Markdown"
        )


    async def _bc_import_to_athlete(self, update, ctx):
        """Сохранение CSV без профиля на выбранного спортсмена."""
        # Импорт чужих данных — только полный доступ (админ/врач)
        if not self._is_full_access(update.effective_user.id):
            logger.warning(f"Access denied (bc_import_to_): {update.effective_user.id}")
            try:
                await update.callback_query.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            return
        q = update.callback_query
        await q.answer()
        athlete_id = int(q.data.replace("bc_import_to_", ""))
        athlete = self.db.get_athlete_by_id(athlete_id)
        if not athlete:
            await q.edit_message_text("❌ Спортсмен не найден.")
            return
        state = self.get_state(q.from_user.id)
        records = state.get("data", {}).get("pending_scale_records", [])
        if not records:
            await q.edit_message_text("❌ Данные устарели. Отправь CSV заново.")
            return
        self.clear_state(q.from_user.id)
        saved = 0
        for r in records:
            data = dict(r)
            try:
                qd = self.db.get_questionnaire(athlete_id)
                if qd and qd.get("height"):
                    data.setdefault("height_cm", qd.get("height"))
            except Exception:
                pass
            data = finalize_scale_metrics(data)
            if self.db.save_body_composition(athlete_id, r.get("record_date"), data,
                                             source="csv", recorded_by=q.from_user.id,
                                             device_profile=r.get("device_profile")):
                saved += 1
        if saved:
            await q.edit_message_text(f"✅ Сохранено *{saved}* записей для {athlete['full_name']}")
            await self._analyze_and_notify_scale(update, athlete, records)
        else:
            await q.edit_message_text("⚠️ Не удалось сохранить данные.",
                                      reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))


    def _scale_inbox_dir(self) -> str:
        """Каталог архива исходников — рядом с ТОЙ БД, к которой обращён этот
        экземпляр, а не с config.DB_PATH.

        Иначе тесты (в том числе pytest внутри deploy.sh на сервере) пишут
        подставные CSV в боевой каталог данных — так и случилось 17.11.
        """
        from pathlib import Path
        from scale_archive import INBOX_SUBDIR
        return str(Path(self.db.db_path).parent / INBOX_SUBDIR)

    async def bc_import_confirm(self, update, ctx):
        """Тренер подтвердил раскладку группового CSV — пишем только своих."""
        q = update.callback_query
        if not self._is_admin_or_coach(q.from_user.id):
            logger.warning(f"Access denied (bc_import_confirm): {q.from_user.id}")
            try:
                await q.answer("⛔ Нет доступа", show_alert=True)
            except Exception:
                pass
            return
        try:
            await q.answer()
        except Exception:
            pass
        state = self.get_state(q.from_user.id)
        records = (state.get("data") or {}).get("pending_scale_records") or []
        self.clear_state(q.from_user.id)
        if not records:
            # после рестарта RAM-state пуст: молча «ничего не сделал» — плохой ответ
            await q.edit_message_text(
                "⚖️ Данные устарели (возможно, бот перезагружался).\n"
                "Отправь CSV заново.",
                reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
            return
        saved, skipped, unmapped, rows, foreign = await self._import_scale_records(
            update, ctx, records)
        head = ["⚖️ *Импорт весов (CSV)*", "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"✅ Записано: *{saved}* записей"]
        if skipped:
            head.append(f"⚠️ Не записано: *{skipped}* (чужие команды / профиль без привязки)")
        if foreign:
            head.append("⛔ Чужие профили: " + ", ".join(foreign[:5]))
        if unmapped:
            head.append("🗂 Не закреплено: " + ", ".join(unmapped[:5]))
        head.append("")
        head.extend(rows[:12])
        if len(rows) > 12:
            head.append(f"… и ещё {len(rows) - 12}")
        await q.edit_message_text("\n".join(head),
                                  reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]),
                                  parse_mode="Markdown")

    async def bc_import_cancel(self, update, ctx):
        """Отмена тренерского импорта: в базу не попадает ничего."""
        q = update.callback_query
        try:
            await q.answer()
        except Exception:
            pass
        self.clear_state(q.from_user.id)
        await q.edit_message_text("❌ Импорт отменён — в базу не записано ничего.\n"
                                  "Можно отправить файл заново.",
                                  reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))


    async def scale_import_menu(self, update, ctx):
        """Пункт меню врача/тренера — веса и состав тела."""
        q = update.callback_query
        await q.answer()
        if not self._is_admin_or_coach(q.from_user.id):
            await q.edit_message_text("🔒 Нет доступа.", reply_markup=self.kb([[(f"🏠 Главное меню", "main_menu")]]))
            return
        await q.edit_message_text(
            "⚖️ *Весы и состав тела*\n\n"
            "Весы GARLYN Bodyscan Master (MovingLife).\n\n"
            "1️⃣ Взвесь группу на весах (до 50 профилей).\n"
            "2️⃣ Открой MovingLife → *Экспорт истории* → CSV.\n"
            "3️⃣ Отправь *CSV-файл сюда* — бот разложит записи по спортсменам.\n\n"
            "🔹 *Профили весов* — закрепи «Профиль N» за спортсменом, чтобы бот раскладывал автоматически.\n"
            "🔹 Замеры за один день перезаписываются (хранится последний).",
            reply_markup=self.kb([
                [(f"📈 Кто взвесился", "bc_overview")],
                [(f"🗂 Профили весов", "bc_profiles")],
                [(f"🔙 Назад", "main_menu")]
            ])
        )


    def _bc_trend_block(self, athlete_id):
        """Короткий блок тренда массы из весов (для профиля спортсмена — только вес, без психологизации)."""
        try:
            trend = self.db.get_bc_trend(athlete_id, days=BC_WINDOW_DAYS)
            w = trend.get("weight_kg", [])
            if len(w) < 2:
                return ""
            first = w[0]; last = w[-1]
            if first[1] is None or last[1] is None:
                return ""
            delta = last[1] - first[1]
            arrow = "▴" if delta > 0 else ("▾" if delta < 0 else "•")
            return (f"⚖️ *Вес по весам (30 дн):* {first[1]:.1f} → {last[1]:.1f} кг "
                    f"{arrow} ({delta:+.1f})\n\n")
        except Exception as e:
            logger.error(f"bc trend block: {e}")
            return ""

    # ==================== ОБНОВЛЕНИЕ ВЕСА ====================



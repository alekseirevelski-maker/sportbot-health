"""
Миксин: ReportsMixin
Автоматически извлечён из bot.py
"""

import logging
from typing import TYPE_CHECKING
from database import ACTIVE_TEAMS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot import SportHealthBot


class ReportsMixin:
    """Миксин для reports."""

    async def generate_xlsx_report(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        days = int(q.data.replace("report_period_", ""))
        state = self.get_state(uid)
        team = state.get("data", {}).get("report_team", "all")
        # Тренеру запрещён «все команды»
        if not self._is_full_access(uid):
            if team == "all" or team not in self._coach_teams(uid):
                await q.edit_message_text("🔒 Нет доступа к этой команде.",
                                           reply_markup=self.kb([[(f"🔙 Главное меню", "main_menu")]]))
                return
        team_filter = None if team == "all" else team

        period_names = {1: "последний замер", 14: "2 недели", 30: "месяц", 90: "3 месяца", 999: "всё время"}

        wb = Workbook()

        # ---- Стили ----
        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin")
        )
        good_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        warn_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        crit_fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
        # Лёгкая зебра для блоков дней (чередование) + жирная граница стыка дней
        zebra_fill = PatternFill(start_color="EAF1FB", end_color="EAF1FB", fill_type="solid")
        zebra_hdr_fill = PatternFill(start_color="8EAADB", end_color="8EAADB", fill_type="solid")
        day_side = Side(style="medium", color="1F4E79")
        day_right = Border(left=thin_border.left, right=day_side, top=thin_border.top, bottom=thin_border.bottom)
        day_left = Border(left=day_side, right=thin_border.right, top=thin_border.top, bottom=thin_border.bottom)
        title_font = Font(bold=True, size=14, color="1F4E79")
        subtitle_font = Font(bold=True, size=10, color="808080")
        bold_font = Font(bold=True, size=10)
        normal_font = Font(size=10)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        wrap = Alignment(wrap_text=True, vertical="top")

        def cell(ws, row, col, value, font=None, fill=None, align=None, border=thin_border):
            c = ws.cell(row=row, column=col, value=value)
            if font: c.font = font
            if fill is not None: c.fill = fill
            if align: c.alignment = align
            if border: c.border = border
            return c

        # ---- Получаем данные по дням ----
        # Сводка по командам (средние для шапки) и детальные опросы
        summary_by_team = {}
        athletes = self.db.get_team_stats_period(team_filter, days)
        if team_filter is None:
            # «Все команды» = только активные (остальные в БД сохраняются, но скрыты)
            _active = set(ACTIVE_TEAMS)
            athletes = [a for a in athletes if a.get("team") in _active]
        for a in athletes:
            t = a.get("team", "?")
            summary_by_team.setdefault(t, []).append(a)

        # Детальные опросы по дням
        wellness_rows = self.db.get_wellness_by_period(days, team_filter)
        if team_filter is None:
            wellness_rows = [r for r in wellness_rows if r.get("team") in set(ACTIVE_TEAMS)]

        # Группируем детальные опросы по командам
        rows_by_team = {}
        for row in wellness_rows:
            t = row.get("team", "?")
            rows_by_team.setdefault(t, []).append(row)

        # Команды для листов
        all_teams = sorted(set(list(summary_by_team.keys()) + list(rows_by_team.keys())))
        if not all_teams:
            await q.edit_message_text("❌ Нет данных за выбранный период.", reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        # ---- ЛИСТ СВОДКА (первый) ----
        ws0 = wb.active
        ws0.title = "Сводка"
        ws0.merge_cells("A1:H1")
        cell(ws0, 1, 1, "📋 ЧБК — СВОДНЫЙ ОТЧЁТ", font=title_font, border=None)
        ws0.merge_cells("A2:H2")
        cell(ws0, 2, 1, f"Период: {period_names[days]} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}", font=subtitle_font, border=None)

        r = 4
        sum_headers = ["Команда", "Спортсменов", "С опросами", "Опросов", "Участие %", "Сон ср.", "Утомл. ср."]
        for ci, h in enumerate(sum_headers, 1):
            cell(ws0, r, ci, h, font=hf, fill=hfl, align=hdr_align)
        r += 1

        for t in all_teams:
            ta = summary_by_team.get(t, [])
            rows = rows_by_team.get(t, [])
            total = len(ta)
            with_data = sum(1 for a in ta if a.get("days_count", 0) > 0)
            if total > 0 and with_data > 0:
                # средние из сводки
                avg_s = sum((a.get("avg_sleep") or 0) for a in ta if a.get("avg_sleep")) / sum(1 for a in ta if a.get("avg_sleep"))
                avg_f = sum((a.get("avg_fatigue") or 0) for a in ta if a.get("avg_fatigue")) / sum(1 for a in ta if a.get("avg_fatigue"))
                particip = round(with_data / total * 100) if total else 0
            else:
                avg_s = avg_f = 0
                particip = 0
            cell(ws0, r, 1, t, font=bold_font)
            cell(ws0, r, 2, total, align=center)
            cell(ws0, r, 3, with_data, align=center)
            cell(ws0, r, 4, len(rows), align=center)
            cell(ws0, r, 5, f"{particip}%", align=center)
            cell(ws0, r, 6, f"{avg_s:.1f}", align=center, fill=self._xl_score_fill(avg_s))
            cell(ws0, r, 7, f"{avg_f:.1f}", align=center, fill=self._xl_score_fill(avg_f))
            r += 1

        # Итоговая строка по всем командам
        total_ath = sum(len(summary_by_team.get(t, [])) for t in all_teams)
        total_rows = sum(len(rows_by_team.get(t, [])) for t in all_teams)
        cell(ws0, r, 1, "ИТОГО", font=Font(bold=True, size=11))
        cell(ws0, r, 2, total_ath, font=bold_font, align=center)
        cell(ws0, r, 3, "-", align=center)
        cell(ws0, r, 4, total_rows, font=bold_font, align=center)
        for ci in range(5, 8):
            cell(ws0, r, ci, "-", align=center)
        r += 1

        # эмблема клуба в правом верхнем углу листа «Сводка»
        self._add_logo_to_ws(ws0, anchor="J3", size=90)

        for ci in range(1, 9):
            ws0.column_dimensions[chr(64+ci)].width = 18

        # ---- ЛИСТЫ ПО КОМАНДАМ: детальные опросы по дням ----
        for t in all_teams:
            ws = wb.create_sheet(title=t[:31])
            ta = summary_by_team.get(t, [])
            rows = rows_by_team.get(t, [])

            r = 1
            ws.merge_cells("A1:L1")
            cell(ws, r, 1, f"🏀 ЧБК — Опросы по дням: {t}", font=title_font, border=None)
            r += 1
            ws.merge_cells(f"A{r}:L{r}")
            cell(ws, r, 1, f"Период: {period_names[days]} | Дата отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Всего спортсменов: {len(ta)}", font=subtitle_font, border=None)
            r += 1

            # ===== БЛОК: МАТРИЦА «ИГРОК-СТРОКА, ДЕНЬ-СТОЛБЕЦ, ПОДСТОЛБЦЫ=ПОКАЗАТЕЛИ» =====
            from openpyxl.utils import get_column_letter
            # Ключи row: full_name, team, age_group, athlete_id, survey_date,
            # sleep_score, stress_score, fatigue_score, muscle_soreness, mood_score,
            # resting_hr, hrv_ms, had_training, sRPE_score, cycle_day, cycle_phase, complaints
            metrics = ["Сон", "Утомл", "Крепатура", "Боль", "Пульс", "sRPE", "Hooper"]
            namedays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

            def _m_hindex(r):
                v = [r.get("sleep_score"), r.get("fatigue_score"),
                     r.get("muscle_soreness")]
                v = [x for x in v if x is not None]
                return sum(v) if v else None

            # собираем по игроку: name -> {date: row}; даты — общий упорядоченный набор
            athlete_days = {}
            all_dates = []
            for row in rows:
                name = row.get("full_name", "?")
                d = row.get("survey_date")
                athlete_days.setdefault(name, {})[str(d)] = row
                if str(d) not in all_dates:
                    all_dates.append(str(d))
            all_dates.sort()

            # заголовок команды уже есть на r-2 (merge A:L). Дальше легенда + шапка.
            # ЛЕГЕНДА (с цветными ●)
            n_total_cols = 1 + len(all_dates) * len(metrics) + 1  # Игрок | дни*метрики | Ср.
            legend_top = n_total_cols if n_total_cols > 13 else 13
            r += 1
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=legend_top)
            cell(ws, r, 1, "ЛЕГЕНДА   (шкалы: Сон/Утомл/Крепатура 1-7, Боль NRS 0-10, sRPE 1-10, Hooper 3-21)", font=Font(bold=True, size=11, color="1F4E79"), border=None)
            r += 1

            circ_colors = ["1E7B1E", "BF8F00", "C00000"]
            # Строка 3: шкала 1-7
            cell(ws, r, 1, "Шкала 1-7:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq1 = [(circ_colors[0], "6-7 норма"), (circ_colors[1], "4-5 вни.мание"), (circ_colors[2], "<4 критично")]
            sc = 3
            for col_c, lab in seq1:
                cc = cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            r += 1
            # Строка: Пульс + Hooper + пропуск
            cell(ws, r, 1, "Пульс:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq2 = [(circ_colors[2], ">70"), (circ_colors[1], "61-70"), (circ_colors[0], "<=60")]
            sc = 3
            for col_c, lab in seq2:
                cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            cell(ws, r, 12, "Hooper:", font=Font(size=9, bold=True, color="404040"), border=None)
            seq3 = [(circ_colors[0], ">=17"), (circ_colors[1], "12-16"), (circ_colors[2], "<12")]
            sc = 14
            for col_c, lab in seq3:
                cell(ws, r, sc, "●", font=Font(bold=True, size=11, color=col_c), border=None)
                cell(ws, r, sc + 1, lab, font=normal_font, border=None)
                sc += 3
            cell(ws, r, 23, "Пропуск:", font=Font(size=9, bold=True, color="404040"), border=None)
            cc = cell(ws, r, 25, "", fill=gray_fill)
            cell(ws, r, 26, "не заполнен · «Ср.»=Hooper", font=normal_font, border=None)
            r += 1

            # Двухуровневая шапка: строка дат (SR_DATE), строка метрик (SR_METR)
            SR_DATE = r
            SR_METR = r + 1
            # Игрок — объединён вертикально по двум строкам шапки
            ws.merge_cells(start_row=SR_DATE, start_column=1, end_row=SR_METR, end_column=1)
            cell(ws, SR_DATE, 1, "Игрок", font=hf, fill=hfl, align=hdr_align)
            col = 2
            for di, dt in enumerate(all_dates):
                try:
                    from datetime import datetime as _ddt
                    wd = namedays[_ddt.strptime(dt, "%Y-%m-%d").weekday()]
                    datelabel = dt[5:] + "\n" + wd
                except Exception:
                    datelabel = dt[5:]
                # лёгкая зебра: чётные блоки дней — светло-голубой фон у ячейки даты (и её блока)
                hdr_fill = zebra_hdr_fill if di % 2 == 1 else hfl
                ws.merge_cells(start_row=SR_DATE, start_column=col, end_row=SR_DATE, end_column=col + len(metrics) - 1)
                cell(ws, SR_DATE, col, datelabel, font=hf, fill=hdr_fill, align=hdr_align)
                # жирная граница справа у последней колонки блока дня (визуальный стык дней)
                last_col = col + len(metrics) - 1
                for k, m in enumerate(metrics):
                    bd = day_right if (col + k) == last_col else thin_border
                    cell(ws, SR_METR, col + k, m, font=Font(bold=True, size=8, color="FFFFFF"), fill=hfl, align=hdr_align, border=bd)
                    if (col + k) == last_col:
                        # жирный правый борт и у ячейки даты
                        dc = ws.cell(row=SR_DATE, column=last_col)
                        dc.border = Border(left=thin_border.left, right=day_side, top=thin_border.top, bottom=thin_border.bottom)
                col += len(metrics)
            # колонка «Ср.» объединена по двум строкам
            ws.merge_cells(start_row=SR_DATE, start_column=col, end_row=SR_METR, end_column=col)
            cell(ws, SR_DATE, col, "Ср.", font=hf, fill=hfl, align=hdr_align)
            SRCOL = col
            r = SR_METR + 1

            ws.freeze_panes = f"B{r}"

            if not athlete_days:
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=SRCOL)
                cell(ws, r, 1, "Нет опросов за данный период.", font=Font(italic=True, color="808080"), border=None)
                r += 1
            else:
                for name in sorted(athlete_days.keys()):
                    daysmap = athlete_days[name]
                    # по умолчанию числовые значения; пустой день = серый
                    col = 2
                    ho_list = []
                    for dt in all_dates:
                        row = daysmap.get(dt)
                        if row is None:
                            for k in range(len(metrics)):
                                cc = cell(ws, r, col + k, "", border=thin_border)
                                cc.fill = gray_fill
                                if k == len(metrics) - 1:
                                    cc.border = day_right
                            col += len(metrics)
                            continue
                        for k, m in enumerate(metrics):
                            if m == "Сон": v = row.get("sleep_score")
                            elif m == "Утомл": v = row.get("fatigue_score")
                            elif m == "Крепатура": v = row.get("muscle_soreness")
                            elif m == "Боль": v = row.get("pain_nrs")
                            elif m == "Пульс": v = row.get("resting_hr")
                            elif m == "sRPE": v = row.get("sRPE_score")
                            elif m == "Hooper": v = _m_hindex(row)
                            v = v if v is not None else ""
                            cc = cell(ws, r, col + k, v, align=center)
                            if isinstance(v, (int, float)):
                                if m == "Пульс":
                                    cc.fill = self._xl_hr_fill(v)
                                elif m == "Боль":
                                    cc.fill = self._xl_pain_fill(v)
                                elif m == "sRPE":
                                    cc.fill = self._xl_srpe_fill(v)
                                elif m == "Hooper":
                                    cc.fill = self._xl_hooper_fill(v)
                                else:
                                    cc.fill = self._xl_score_fill(v)
                                if m == "Hooper":
                                    ho_list.append(v)
                        # жирная граница справа у последней колонки блока дня (стык дней)
                        last_col = col + len(metrics) - 1
                        ws.cell(row=r, column=last_col).border = day_right
                        col += len(metrics)
                    # средний Hooper в колонке «Ср.»
                    if ho_list:
                        av = round(sum(ho_list) / len(ho_list), 1)
                        cc = cell(ws, r, SRCOL, av, align=center, font=Font(bold=True, size=10))
                        cc.fill = self._xl_hooper_fill(av)
                    else:
                        cell(ws, r, SRCOL, "", border=thin_border)
                    # имя игрока слева
                    cell(ws, r, 1, name, font=bold_font)
                    r += 1

            # ширина колонок
            ws.column_dimensions["A"].width = 22
            first_big = min(len(all_dates) * len(metrics) + 2, 60)
            for i in range(2, first_big):
                cl = get_column_letter(i)
                if ws.column_dimensions[cl].width < 5.2:
                    ws.column_dimensions[cl].width = 5.2
            ws.column_dimensions[get_column_letter(SRCOL)].width = 8

            # эмблема клуба в правом верхнем углу листа команды (после последней колонки матрицы)
            self._add_logo_to_ws(ws, anchor=f"{get_column_letter(SRCOL + 2)}{SR_DATE}", size=80)

        # ---- Лист «Легенда»: расшифровка показателей (для тренера) ----
        ws_leg = wb.create_sheet("Легенда")
        ws_leg.column_dimensions["A"].width = 28
        ws_leg.column_dimensions["B"].width = 95
        _lr = 1
        _c = ws_leg.cell(_lr, 1, "ЧТО ЗНАЧИТ КАЖДЫЙ ПОКАЗАТЕЛЬ")
        _c.font = Font(bold=True, size=13, color="1F4E79"); _lr += 1
        _c = ws_leg.cell(_lr, 1, "Расшифровка показателей в отчёте по дням (что это и как читать).")
        _c.font = Font(size=9, italic=True, color="808080"); _lr += 2
        legend_rows = [
            ("ХУПЕР (Hooper)", "Индекс готовности = Сон (1-7) + Утомление (1-7) + Крепатура (1-7). Итог 3-21. Чем выше — тем лучше готов к нагрузке. 17-21 — отличная готовность, 12-16 — средняя, 3-11 — низкая (риск перегрузки/болезни)."),
            ("sRPE", "Субъективная оценка нагрузки на тренировке, 1-10 (RPE — насколько тяжело было). 1-4 — лёгкая, 5-7 — средняя, 8-10 — тяжёлая. Высокий sRPE несколько дней подряд = пора снизить нагрузку."),
            ("ПУЛЬС ПОКОЯ (RHR)", "Ударов в минуту, измеряется утром сразу после пробуждения, лёжа. Чем ниже — тем тренированнее сердце. Рост пульса покоя на 5-10 уд/мин к личной норме = возможная перегрузка или начало болезни. >70 — внимание, >80 — риск."),
            ("HRV (вариабельность)", "Вариабельность сердечного ритма, мс. Отражает баланс нервной системы и уровень восстановления. Чем выше — тем лучше организм восстановился. Источник — умные часы/биоимпеданс (в ежедневном опросе не измеряется)."),
            ("КРЕПАТУРА", "Мышечная болезненность (DOMS) после нагрузки, 1-7. 7 = мышцы не болят, 1 = сильная боль. Низкий балл = мышцы не восстановились."),
            ("БОЛЬ NRS", "Оценка боли по шкале 0-10: 0 = боли нет, 10 = невыносимая. 1-4 — лёгкая, 5 и выше — красный флаг: показать врачу."),
            ("СОН / УТОМЛЕНИЕ", "Шкалы 1-7, где 7 = отличный сон / бодрость, 1 = плохой сон / упадок сил. Входят в расчёт Хупера."),
        ]
        for _col1, _col2 in legend_rows:
            _c = ws_leg.cell(_lr, 1, _col1); _c.font = Font(bold=True, size=10)
            _c = ws_leg.cell(_lr, 2, _col2); _c.font = Font(size=10); _c.alignment = Alignment(wrap_text=True, vertical="top")
            _lr += 1
        _lr += 1
        _c = ws_leg.cell(_lr, 1, "ЦВЕТА"); _c.font = Font(bold=True, size=11); _lr += 1
        for _col, _lab in [("E2EFDA", "Зелёный — норма"), ("FFF2CC", "Жёлтый — внимание"),
                           ("F8D7DA", "Красный — критично / нужен осмотр"), ("F2F2F2", "Серый — нет данных (опрос не пройден)")]:
            _c = ws_leg.cell(_lr, 1, "●")
            _c.fill = PatternFill(start_color=_col, end_color=_col, fill_type="solid")
            _c.font = Font(bold=True, size=10)
            _c = ws_leg.cell(_lr, 2, _lab); _c.font = Font(size=10)
            _lr += 1

        # Убираем move_worksheet - Сводка уже первый лист
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"report_{date.today().strftime('%Y%m%d')}.xlsx",
            caption=f"📊 Excel-отчёт за {period_names.get(days, f'{days} дн.')} — детализация по дням"
        )

    # ==================== МЕНЮ СОСТАВА ТЕЛА (СПОРТСМЕН) ====================


    async def generate_bc_xlsx_report(self, update, ctx):
        """Генерация Excel-отчёта по данным биоимпедансных весов (состав тела)."""
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        days = int(q.data.replace("report_bc_period_", ""))
        state = self.get_state(uid)
        # Если был выбор команды — используем (report_body_comp可以选择所有或 конкретную)
        team = state.get("data", {}).get("bc_report_team", "all")
        if not self._is_full_access(uid):
            if team == "all" or team not in self._coach_teams(uid):
                await q.edit_message_text("🔒 Нет доступа к этой команде.",
                                           reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
                return
        team_filter = None if team == "all" else team

        period_names = {1: "последний замер", 14: "2 недели", 30: "месяц", 90: "3 месяца", 999: "всё время"}

        wb = Workbook()

        # ---- Стили (те же что в generate_xlsx_report) ----
        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin")
        )
        title_font = Font(bold=True, size=14, color="1F4E79")
        subtitle_font = Font(bold=True, size=10, color="808080")
        bold_font = Font(bold=True, size=10)
        normal_font = Font(size=10)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

        def cell(ws, row, col, value, font=None, fill=None, align=None, border=thin_border):
            c = ws.cell(row=row, column=col, value=value)
            if font: c.font = font
            if fill is not None: c.fill = fill
            if align: c.alignment = align
            if border: c.border = border
            return c

        # ---- Получаем данные ----
        rows = self.db.get_bc_period(days, team_filter)
        if team_filter is None:
            _active = set(ACTIVE_TEAMS)
            rows = [r for r in rows if r.get("team") in _active]

        if not rows:
            await q.edit_message_text("❌ Нет данных состава тела за выбранный период.",
                                       reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
            return

        # Группируем по командам
        by_team = {}
        for r in rows:
            t = r.get("team", "?")
            by_team.setdefault(t, []).append(r)

        all_teams = sorted(by_team.keys())

        # ---- Лист «Сводка» ----
        ws0 = wb.active
        ws0.title = "Сводка"
        ws0.merge_cells("A1:E1")
        cell(ws0, 1, 1, "⚖️ ЧБК — ОТЧЁТ ПО СОСТАВУ ТЕЛА", font=title_font, border=None)
        ws0.merge_cells("A2:E2")
        cell(ws0, 2, 1,
             f"Период: {period_names.get(days, f'{days} дн.')} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
             font=subtitle_font, border=None)

        r = 4
        sum_headers = ["Команда", "Спортсменов", "Ср. вес (кг)", "Ср. жир (%)", "Ср. мышцы (кг)"]
        for ci, h in enumerate(sum_headers, 1):
            cell(ws0, r, ci, h, font=hf, fill=hfl, align=hdr_align)
        r += 1

        for t in all_teams:
            team_rows = by_team[t]
            # Последний замер каждого спортсмена — для средних
            latest_by_athlete = {}
            for row in team_rows:
                aid = row.get("athlete_id")
                if aid not in latest_by_athlete:
                    latest_by_athlete[aid] = row
            n_athletes = len(latest_by_athlete)
            weights = [v["weight_kg"] for v in latest_by_athlete.values()
                       if v.get("weight_kg") is not None]
            fats = [v["body_fat_pct"] for v in latest_by_athlete.values()
                    if v.get("body_fat_pct") is not None]
            muscles = [v["muscle_mass_kg"] for v in latest_by_athlete.values()
                       if v.get("muscle_mass_kg") is not None]
            avg_w = sum(weights) / len(weights) if weights else 0
            avg_f = sum(fats) / len(fats) if fats else 0
            avg_m = sum(muscles) / len(muscles) if muscles else 0

            cell(ws0, r, 1, t, font=bold_font)
            cell(ws0, r, 2, n_athletes, align=center)
            cell(ws0, r, 3, f"{avg_w:.1f}" if weights else "—", align=center)
            cell(ws0, r, 4, f"{avg_f:.1f}" if fats else "—", align=center)
            cell(ws0, r, 5, f"{avg_m:.1f}" if muscles else "—", align=center)
            r += 1

        # Итоговая строка
        total_ath = sum(
            len({row["athlete_id"] for row in by_team[t]
                 if row.get("athlete_id")})
            for t in all_teams
        )
        cell(ws0, r, 1, "ИТОГО", font=Font(bold=True, size=11))
        cell(ws0, r, 2, total_ath, font=bold_font, align=center)
        for ci in range(3, 6):
            cell(ws0, r, ci, "—", align=center)
        r += 1

        self._add_logo_to_ws(ws0, anchor="G3", size=90)

        for ci in range(1, 6):
            ws0.column_dimensions[chr(64 + ci)].width = 18

        # ---- Листы по командам: матрица (игрок × дата, подстолбцы=метрики) ----
        bc_metrics = ["Вес", "% Жира", "Мышцы (кг)", "Вода", "BMR"]
        metric_cols = ["weight_kg", "body_fat_pct", "muscle_mass_kg", "body_water_pct", "bmr_kcal"]

        for t in all_teams:
            team_rows = by_team[t]
            ws = wb.create_sheet(title=t[:31])
            ws.merge_cells("A1:L1")
            cell(ws, 1, 1, f"⚖️ Состав тела — {t}", font=title_font, border=None)
            r = 2
            ws.merge_cells(f"A{r}:L{r}")
            cell(ws, r, 1,
                 f"Период: {period_names.get(days, f'{days} дн.')} | "
                 f"Дата отчёта: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                 font=subtitle_font, border=None)
            r = 4

            # Собираем данные: игрок → {дата → row}
            athlete_days = {}
            all_dates = []
            for row in team_rows:
                name = row.get("full_name", "?")
                d = str(row.get("record_date", ""))
                athlete_days.setdefault(name, {})[d] = row
                if d not in all_dates:
                    all_dates.append(d)
            all_dates.sort()

            # Двухуровневая шапка: строка дат (SR_DATE), строка метрик (SR_METR)
            SR_DATE = r
            SR_METR = r + 1
            ws.merge_cells(start_row=SR_DATE, start_column=1, end_row=SR_METR, end_column=1)
            cell(ws, SR_DATE, 1, "Игрок", font=hf, fill=hfl, align=hdr_align)
            col = 2
            for dt in all_dates:
                ws.merge_cells(start_row=SR_DATE, start_column=col,
                               end_row=SR_DATE, end_column=col + len(bc_metrics) - 1)
                cell(ws, SR_DATE, col, dt, font=hf, fill=hfl, align=hdr_align)
                for k, m in enumerate(bc_metrics):
                    cell(ws, SR_METR, col + k, m,
                         font=Font(bold=True, size=8, color="FFFFFF"),
                         fill=hfl, align=hdr_align)
                col += len(bc_metrics)
            ws.merge_cells(start_row=SR_DATE, start_column=col,
                           end_row=SR_METR, end_column=col)
            cell(ws, SR_DATE, col, "Ср.", font=hf, fill=hfl, align=hdr_align)
            SRCOL = col
            r = SR_METR + 1

            ws.freeze_panes = f"B{r}"

            if not athlete_days:
                ws.merge_cells(start_row=r, start_column=1,
                               end_row=r, end_column=SRCOL)
                cell(ws, r, 1, "Нет данных за данный период.",
                     font=Font(italic=True, color="808080"), border=None)
                r += 1
            else:
                for name in sorted(athlete_days.keys()):
                    daysmap = athlete_days[name]
                    col = 2
                    avg_vals = {mc: [] for mc in metric_cols}
                    for dt in all_dates:
                        row_data = daysmap.get(dt)
                        if row_data is None:
                            for k in range(len(bc_metrics)):
                                c = ws.cell(row=r, column=col + k, value="")
                                c.border = thin_border
                                c.fill = gray_fill
                            col += len(bc_metrics)
                            continue
                        for k, mc in enumerate(metric_cols):
                            v = row_data.get(mc)
                            if v is not None:
                                v = float(v)
                                avg_vals[mc].append(v)
                                cell(ws, r, col + k, round(v, 1), align=center)
                            else:
                                cell(ws, r, col + k, "", align=center)
                        col += len(bc_metrics)
                    # Средние в колонке «Ср.»
                    avgs = []
                    for mc in metric_cols:
                        if avg_vals[mc]:
                            avgs.append(f"{sum(avg_vals[mc]) / len(avg_vals[mc]):.1f}")
                        else:
                            avgs.append("—")
                    cell(ws, r, SRCOL, ", ".join(avgs),
                         align=center, font=bold_font)
                    cell(ws, r, 1, name, font=bold_font)
                    r += 1

            # Ширина колонок
            ws.column_dimensions["A"].width = 22
            for ci in range(2, min(SRCOL + 2, 60)):
                cl = get_column_letter(ci)
                if ws.column_dimensions[cl].width < 5.2:
                    ws.column_dimensions[cl].width = 5.2
            ws.column_dimensions[get_column_letter(SRCOL)].width = 22

        # ---- Лист «Легенда» ----
        ws_leg = wb.create_sheet("Легенда")
        ws_leg.column_dimensions["A"].width = 28
        ws_leg.column_dimensions["B"].width = 95
        _lr = 1
        _c = ws_leg.cell(_lr, 1, "ЧТО ЗНАЧИТ КАЖДЫЙ ПОКАЗАТЕЛЬ (СОСТАВ ТЕЛА)")
        _c.font = Font(bold=True, size=13, color="1F4E79"); _lr += 1
        _c = ws_leg.cell(_lr, 1, "Расшифровка показателей биоимпедансного анализа (GARLYN Bodyscan Master).")
        _c.font = Font(size=9, italic=True, color="808080"); _lr += 1
        _c = ws_leg.cell(_lr, 1, "⚠️ Данные скорректированы +3% к % жира (Potter et al., 2021) для приближения к DXA.")
        _c.font = Font(size=9, italic=True, color="CC6600"); _lr += 2
        legend_rows = [
            ("ВЕС (кг)", "Масса тела в килограммах. Контроль динамики (рост/снижение) важен для планирования нагрузки."),
            ("% ЖИРА (body fat %)", "Процент жировой ткани. Норма для юных спортсменов: 10-18% (м), 18-26% (ж). Скорректировано +3% (Potter et al., 2021). GARLYN систематически занижает жир на ~3-4% относительно DXA."),
            ("МЫШЕЧНАЯ МАССА (кг)", "Суммарная масса скелетных мышц. Рост = прогресс силы. Снижение при перегрузке/болезни."),
            ("ВОДА (body water %)", "Общий объём воды в теле. Норма: 55-65% (м), 50-60% (ж). Снижение <55% = обезвоживание (риск травм, снижение восстановления)."),
            ("BMR (ккал)", "Базовый метаболизм — количество калорий, которые организм тратит в покое. Рассчитан из безжировой массы. Для баскетболистов 15-18: ~1600-2200 ккал."),
            ("КОСТИ (bone mass кг)", "Минеральная плотность костей. Стабильна у молодых; резкое снижение — красный флаг. Норма Ca: 1300 мг/сут + витамин D 600-1000 МЕ."),
            ("ВИСЦЕРАЛЬНЫЙ ЖИР", "Жир вокруг органов (индекс 1-12). ≤4 — норма, 5-9 — повышен, ≥10 — высокий риск метаболических нарушений."),
            ("ПОДКОЖНЫЙ ЖИР (%)", "Процент подкожного жира. Коррелирует с общим % жира, но не заменяет его."),
            ("БЕЗЖИРОВАЯ МАССА (кг)", "Fat-free mass (FFM) = вес - жир. Включает мышцы, кости, воду, органы."),
            ("БЕЛОК (%)", "Процент белка в теле. Белок — строительный материал для мышц. Норма: 16-20%."),
            ("AMR (ккал)", "Активный метаболизм = BMR × коэффициент активности. Для баскетболистов: ~2800-3500 ккал."),
        ]
        for _col1, _col2 in legend_rows:
            _c = ws_leg.cell(_lr, 1, _col1); _c.font = Font(bold=True, size=10)
            _c = ws_leg.cell(_lr, 2, _col2)
            _c.font = Font(size=10)
            _c.alignment = Alignment(wrap_text=True, vertical="top")
            _lr += 1

        _lr += 1
        _c = ws_leg.cell(_lr, 1, "ЦВЕТОВОЕ КОДИРОВАНИЕ")
        _c.font = Font(bold=True, size=11, color="1F4E79"); _lr += 1
        color_legend = [
            ("🟢 Зелёный", "Показатель в пределах нормы для возраста и пола"),
            ("🟡 Жёлтый", "Пограничное значение — стоит обратить внимание"),
            ("🔴 Красный", "Отклонение от нормы — требуется консультация врача"),
        ]
        for _col1, _col2 in color_legend:
            _c = ws_leg.cell(_lr, 1, _col1); _c.font = Font(bold=True, size=10)
            _c = ws_leg.cell(_lr, 2, _col2)
            _c.font = Font(size=10)
            _lr += 1

        _lr += 1
        _c = ws_leg.cell(_lr, 1, "ИСТОЧНИКИ")
        _c.font = Font(bold=True, size=11, color="1F4E79"); _lr += 1
        sources = [
            "1. Looney DP et al. (2024) Reliability, biological variability, and accuracy of MF-BIA. Frontiers in Nutrition, 11:1491931",
            "2. Dupertuis YM et al. (2025) BIA instruments: how do they differ. Curr Opin Clin Nutr Metab Care, 28(5):379-387",
            "3. Iblasi R et al. (2025) Pre-season and in-season body composition by BIA in football. Frontiers in Nutrition, 12:1657855",
            "4. Potter AW et al. (2021) +3% body fat correction for BIA vs DXA",
            "5. ESPEN guidelines for BIA measurement standardization (Dupertuis et al., 2025)",
        ]
        for src in sources:
            _c = ws_leg.cell(_lr, 1, src)
            _c.font = Font(size=9, color="666666")
            ws_leg.merge_cells(start_row=_lr, start_column=1, end_row=_lr, end_column=2)
            _lr += 1

        # ---- Сохраняем и отправляем ----
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"bc_report_{date.today().strftime('%Y%m%d')}.xlsx",
            caption=f"⚖️ Excel-отчёт по составу тела за {period_names.get(days, f'{days} дн.')}"
        )

    # ==================== ОТЧЁТ ПО УМНЫМ ЧАСАМ (EXCEL) ====================


    async def generate_watch_xlsx_report(self, update, ctx):
        """Генерация Excel-отчёта по данным умных часов."""
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        days = int(q.data.replace("report_watch_period_", ""))
        state = self.get_state(uid)
        team = state.get("data", {}).get("watch_report_team", "all")
        if not self._is_full_access(uid):
            if team == "all" or team not in self._coach_teams(uid):
                await q.edit_message_text("🔒 Нет доступа к этой команде.",
                                           reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
                return
        team_filter = None if team == "all" else team

        period_names = {7: "7 дней", 30: "30 дней", 90: "90 дней"}

        # ---- Получаем данные ----
        watch_rows = self.db.get_watch_data_period(days, team_filter)
        if team_filter is None:
            _active = set(ACTIVE_TEAMS)
            watch_rows = [r for r in watch_rows if r.get("team") in _active]

        if not watch_rows:
            await q.edit_message_text("❌ Нет данных часов за выбранный период.",
                                       reply_markup=self.kb([[(f"🔙 Назад", "report_watches")]]))
            return

        # Группируем по командам
        rows_by_team = {}
        for row in watch_rows:
            t = row.get("team", "?")
            rows_by_team.setdefault(t, []).append(row)

        all_teams = sorted(rows_by_team.keys())

        # ---- Стилы (как в generate_xlsx_report) ----
        wb = Workbook()
        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin")
        )
        gray_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
        title_font = Font(bold=True, size=14, color="1F4E79")
        subtitle_font = Font(bold=True, size=10, color="808080")
        bold_font = Font(bold=True, size=10)
        normal_font = Font(size=10)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        def cell(ws, row, col, value, font=None, fill=None, align=None, border=thin_border):
            c = ws.cell(row=row, column=col, value=value)
            if font: c.font = font
            if fill is not None: c.fill = fill
            if align: c.alignment = align
            if border: c.border = border
            return c

        # ---- ЛИСТ СВОДКА ----
        ws0 = wb.active
        ws0.title = "Сводка"
        ws0.merge_cells("A1:F1")
        cell(ws0, 1, 1, "⌚ ЧБК — СВОДНЫЙ ОТЧЁТ ПО ЧАСАМ", font=title_font, border=None)
        ws0.merge_cells("A2:F2")
        cell(ws0, 2, 1, f"Период: {period_names.get(days, f'{days} дн.')} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
             font=subtitle_font, border=None)

        r = 4
        sum_headers = ["Команда", "Спортсменов", "Ср. пульс", "Ср. сон (ч)", "Ср. шаги", "Записей"]
        for ci, h in enumerate(sum_headers, 1):
            cell(ws0, r, ci, h, font=hf, fill=hfl, align=hdr_align)
        r += 1

        for t in all_teams:
            rows = rows_by_team.get(t, [])
            n_athletes = len(set(row["athlete_id"] for row in rows))
            hr_vals = [row["resting_hr"] for row in rows if row.get("resting_hr") is not None]
            sleep_vals = [row["sleep_hours"] for row in rows if row.get("sleep_hours") is not None]
            steps_vals = [row["steps"] for row in rows if row.get("steps") is not None]

            avg_hr = round(sum(hr_vals) / len(hr_vals)) if hr_vals else None
            avg_sleep = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else None
            avg_steps = round(sum(steps_vals) / len(steps_vals)) if steps_vals else None

            cell(ws0, r, 1, t, font=bold_font)
            cell(ws0, r, 2, n_athletes, align=center)
            cell(ws0, r, 3, avg_hr if avg_hr else "—", align=center)
            cell(ws0, r, 4, avg_sleep if avg_sleep else "—", align=center)
            cell(ws0, r, 5, avg_steps if avg_steps else "—", align=center)
            cell(ws0, r, 6, len(rows), align=center)
            r += 1

        # Итого
        cell(ws0, r, 1, "ИТОГО", font=Font(bold=True, size=11))
        cell(ws0, r, 2, sum(len(set(row["athlete_id"] for row in rows_by_team.get(t, []))) for t in all_teams), font=bold_font, align=center)
        cell(ws0, r, 6, len(watch_rows), font=bold_font, align=center)
        for ci in range(3, 6):
            cell(ws0, r, ci, "—", align=center)
        r += 1

        self._add_logo_to_ws(ws0, anchor="H3", size=90)

        for ci in range(1, 7):
            ws0.column_dimensions[chr(64 + ci)].width = 18

        # ---- ЛИСТЫ ПО КОМАНДАМ: матрица Игрок-День-Показатели ----
        from openpyxl.utils import get_column_letter as _gcl

        metrics = ["Пульс", "Сон", "Шаги", "Стресс", "SpO2", "HRV"]
        metric_keys = ["resting_hr", "sleep_hours", "steps", "stress", "spo2", "hrv"]

        for t in all_teams:
            rows = rows_by_team.get(t, [])
            ws = wb.create_sheet(title=t[:31])

            r = 1
            ws.merge_cells("A1:N1")
            cell(ws, r, 1, f"⌚ ЧБК — Данные часов: {t}", font=title_font, border=None)
            r += 1
            ws.merge_cells(f"A{r}:N{r}")
            cell(ws, r, 1,
                 f"Период: {period_names.get(days, f'{days} дн.')} | Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Записей: {len(rows)}",
                 font=subtitle_font, border=None)
            r += 1

            # Собираем по игроку: athlete_id -> {date_str: row}
            athlete_map = {}
            all_dates = []
            for row in rows:
                aid = row["athlete_id"]
                ds = str(row.get("record_date", ""))
                athlete_map.setdefault(aid, {"name": row.get("full_name", "?"), "days": {}})
                athlete_map[aid]["days"][ds] = row
                if ds not in all_dates:
                    all_dates.append(ds)
            all_dates.sort()

            # Двухуровневая шапка: даты (объединённые) + метрики под каждой датой
            SR_DATE = r
            SR_METR = r + 1

            # Игрок — объединён
            ws.merge_cells(start_row=SR_DATE, start_column=1, end_row=SR_METR, end_column=1)
            cell(ws, SR_DATE, 1, "Игрок", font=hf, fill=hfl, align=hdr_align)

            col = 2
            date_start_cols = {}
            for di, dt in enumerate(all_dates):
                try:
                    dt_short = dt[5:10].replace("-", ".")  # MM.DD
                except Exception:
                    dt_short = dt
                date_start_cols[dt] = col
                ws.merge_cells(start_row=SR_DATE, start_column=col,
                               end_row=SR_DATE, end_column=col + len(metrics) - 1)
                cell(ws, SR_DATE, col, dt_short, font=Font(bold=True, size=9, color="FFFFFF"),
                     fill=PatternFill(start_color="5B9BD5", end_color="5B9BD5", fill_type="solid"),
                     align=hdr_align)
                for mi, mname in enumerate(metrics):
                    cell(ws, SR_METR, col + mi, mname, font=Font(bold=True, size=8, color="FFFFFF"),
                         fill=PatternFill(start_color="9DC3E6", end_color="9DC3E6", fill_type="solid"),
                         align=hdr_align)
                col += len(metrics)

            # Средняя по игроку
            ws.merge_cells(start_row=SR_DATE, start_column=col,
                           end_row=SR_DATE, end_column=col + len(metrics) - 1)
            cell(ws, SR_DATE, col, "Ср.", font=Font(bold=True, size=9, color="FFFFFF"),
                 fill=PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid"),
                 align=hdr_align)
            for mi, mname in enumerate(metrics):
                cell(ws, SR_METR, col + mi, mname, font=Font(bold=True, size=8, color="FFFFFF"),
                     fill=PatternFill(start_color="8EAADB", end_color="8EAADB", fill_type="solid"),
                     align=hdr_align)
            avg_col = col
            col += len(metrics)

            r = SR_METR + 1

            # Строки игроков
            zebra = False
            for aid in sorted(athlete_map.keys(), key=lambda x: athlete_map[x]["name"]):
                info = athlete_map[aid]
                row_fill = PatternFill(start_color="F7F9FC", end_color="F7F9FC", fill_type="solid") if zebra else None
                cell(ws, r, 1, info["name"], font=Font(bold=True, size=10), fill=row_fill)

                sums = {mk: [] for mk in metric_keys}
                for dt in all_dates:
                    base_col = date_start_cols[dt]
                    drow = info["days"].get(dt)
                    for mi, mk in enumerate(metric_keys):
                        val = drow.get(mk) if drow else None
                        c = ws.cell(row=r, column=base_col + mi, value=val)
                        c.border = thin_border
                        c.alignment = center
                        c.font = normal_font
                        if row_fill:
                            c.fill = row_fill
                        if val is not None:
                            sums[mk].append(float(val))
                            # Цветовая индикация для пульса
                            if mk == "resting_hr":
                                if val > 70:
                                    c.fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
                                elif val > 60:
                                    c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                                else:
                                    c.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                            elif mk == "stress" and val is not None:
                                if val > 50:
                                    c.fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
                                elif val > 30:
                                    c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                                else:
                                    c.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
                            elif mk == "spo2" and val is not None:
                                if val < 95:
                                    c.fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
                                elif val < 97:
                                    c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
                                else:
                                    c.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

                # Средние значения
                for mi, mk in enumerate(metric_keys):
                    avg_val = round(sum(sums[mk]) / len(sums[mk]), 1) if sums[mk] else None
                    c = ws.cell(row=r, column=avg_col + mi, value=avg_val)
                    c.border = thin_border
                    c.alignment = center
                    c.font = Font(bold=True, size=10)
                    if row_fill:
                        c.fill = row_fill

                # Граница правее средних
                cell(ws, r, avg_col + len(metrics), "", border=thin_border, fill=row_fill)

                r += 1
                zebra = not zebra

            # Ширины колонок
            ws.column_dimensions["A"].width = 22
            for ci in range(2, col + 1):
                cl = _gcl(ci)
                if ws.column_dimensions[cl].width < 8:
                    ws.column_dimensions[cl].width = 8

            self._add_logo_to_ws(ws, anchor=f"{_gcl(col + 2)}{SR_DATE}", size=80)

        # ---- Лист «Легенда» ----
        ws_leg = wb.create_sheet("Легенда")
        ws_leg.column_dimensions["A"].width = 28
        ws_leg.column_dimensions["B"].width = 95
        lr = 1
        c = ws_leg.cell(lr, 1, "ПОКАЗАТЕЛИ УМНЫХ ЧАСОВ")
        c.font = Font(bold=True, size=13, color="1F4E79"); lr += 1
        c = ws_leg.cell(lr, 1, "Расшифровка показателей в отчёте по часам.")
        c.font = Font(size=9, italic=True, color="808080"); lr += 2
        legend_rows = [
            ("ПУЛЬС ПОКОЯ (RHR)", "Уд/мин, измеряется утром. Чем ниже — тем тренированнее. Рост на 5-10 к норме = перегрузка/болезнь. >70 — внимание, >80 — риск."),
            ("СОН (ч)", "Продолжительность сна в часах. Норма 7-9 ч. Менее 6 ч — недосып, снижение восстановления."),
            ("ШАГИ", "Количество шагов за день. Норма для спортсмена >8000. Менее 5000 — малоподвижный день."),
            ("СТРЕСС", "Уровень стресса по данным часов (0-100). >50 — высокий, 30-50 — повышенный, <30 — норма."),
            ("SpO2", "Насыщение крови кислородом (%). Норма 97-100. Менее 95 — пониженное, нужен контроль."),
            ("HRV", "Вариабельность сердечного ритма (мс). Чем выше — тем лучше восстановление."),
        ]
        for col1, col2 in legend_rows:
            c = ws_leg.cell(lr, 1, col1); c.font = Font(bold=True, size=10)
            c = ws_leg.cell(lr, 2, col2); c.font = Font(size=10); c.alignment = Alignment(wrap_text=True, vertical="top")
            lr += 1
        lr += 1
        c = ws_leg.cell(lr, 1, "ЦВЕТА"); c.font = Font(bold=True, size=11); lr += 1
        for col_val, lab in [("E2EFDA", "Зелёный — норма"), ("FFF2CC", "Жёлтый — внимание"),
                             ("F8D7DA", "Красный — критично")]:
            c = ws_leg.cell(lr, 1, "●")
            c.fill = PatternFill(start_color=col_val, end_color=col_val, fill_type="solid")
            c.font = Font(bold=True, size=10)
            c = ws_leg.cell(lr, 2, lab); c.font = Font(size=10)
            lr += 1

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"watch_report_{date.today().strftime('%Y%m%d')}.xlsx",
            caption=f"⌚ Excel-отчёт по часам за {period_names.get(days, f'{days} дн.')}"
        )


    async def export_questionnaires_xlsx(self, update, ctx):
        """Экспорт анкет спортсменов в Excel (админ — все, тренер — свои команды)."""
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        athletes = self._scoped_athletes(uid)
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = "Анкеты"

        hf = Font(bold=True, size=11, color="FFFFFF")
        hfl = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

        # поля анкеты (ключ -> заголовок)
        fields = [
            ("age", "Возраст"), ("gender", "Пол"), ("phone", "Телефон"), ("birth_date", "Дата рождения"), ("position", "Позиция"),
            ("level", "Уровень"), ("experience", "Стаж"), ("height", "Рост"),
            ("weight", "Вес"), ("trauma_12m", "Травмы 12мес"),
            ("trauma_12m_detail", "Травмы детали"), ("zones", "Проблемные зоны"),
            ("pain_now", "Боль сейчас"), ("pain_now_detail", "Боль детали"),
            ("chronic", "Рецидивы"), ("chronic_detail", "Рецидивы детали"),
            ("surgery", "Операции"), ("surgery_detail", "Операции детали"), ("surgery_date", "Операции даты"),
            ("meds", "Лекарства/БАДы"), ("meds_detail", "БАДы детали"),
            ("allergies", "Аллергии"), ("allergies_detail", "Аллергии детали"),
            ("train_count", "Трен/нед"), ("train_duration", "Длит. трен"),
            ("season", "Сезон"), ("form_score", "Форма"), ("sleep_score", "Сон (анкета)"),
            ("warmup", "Разминка"), ("recovery", "Восстановление"),
            ("water", "Вода л"), ("diet", "Питание"), ("pre_meal", "За сколько ест"),
            ("supplements", "Спортпит"), ("motivation", "Мотивация"),
            ("stress", "Внешний стресс"), ("match_state", "Состояние перед матчем"),
            ("reinjury_fear", "Страх травмы"), ("goal", "Цель"), ("wish", "Пожелания"),
        ]

        # PII поля для маскировки в экспорте
        PII_FIELDS = {"phone", "birth_date", "surgery_date"}

        # ---- Определяем блоки анкеты (для читабельной двухуровневой шапки) ----
        # блок: (название, цвет, список ключей)
        blocks = [
            ("ОБЩИЕ ДАННЫЕ", "4472C4", ["age","gender","phone","birth_date","position","level","experience","height","weight"]),
            ("ТРАВМЫ / ЗДОРОВЬЕ", "70AD47", ["trauma_12m","trauma_12m_detail","zones","pain_now","pain_now_detail",
                                                "chronic","chronic_detail","surgery","surgery_detail","surgery_date",
                                                "meds","meds_detail","allergies","allergies_detail"]),
            ("ТРЕНИРОВКИ", "ED7D31", ["train_count","train_duration","season","form_score","sleep_score","warmup","recovery"]),
            ("ПИТАНИЕ", "FFC000", ["water","diet","pre_meal","supplements"]),
            ("ПСИХОЛОГИЯ", "A9A9A9", ["motivation","stress","match_state","reinjury_fear","goal","wish"]),
        ]
        # индекс начала каждой колонки-поля: базовые (ФИО,Команда,Группа,Статус) = 1-4, поля с 5
        key_col = {}
        ci = 5
        for bl in blocks:
            for k in bl[2]:
                key_col[k] = ci
                ci += 1
        total_cols = ci - 1  # 4 базовых + все поля

        def _col_letter(n):
            return get_column_letter(n)

        ws.merge_cells(f"A1:{_col_letter(total_cols)}1")
        c = ws.cell(row=1, column=1, value="🏀 ЧБК — АНКЕТЫ СПОРТСМЕНОВ")
        c.font = Font(bold=True, size=14, color="1F4E79")
        ws.merge_cells(f"A2:{_col_letter(total_cols)}2")
        c = ws.cell(row=2, column=1, value=f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Всего: {len(athletes)}")
        c.font = Font(bold=True, size=10, color="808080")

        # ---- Двухуровневая шапка: строка 4 = блоки, строка 5 = поля ----
        r_hdr = 4
        # базовые колонки 1-4
        base_block_cols = [("СПОРТСМЕН", "4472C4", 4)]
        # блоки-колонки
        block_ranges = []
        cur = 5
        for bname, bcolor, keys in blocks:
            n_keys = len(keys)
            block_ranges.append((bname, bcolor, cur, cur + n_keys - 1))
            cur += n_keys
        # мержим блоки в строке 4
        for bname, bcolor, c0, c1 in block_ranges:
            ws.merge_cells(start_row=r_hdr, start_column=c0, end_row=r_hdr, end_column=c1)
        # базовые 4 колонки объединяем в "Спортсмен"
        ws.merge_cells(start_row=r_hdr, start_column=1, end_row=r_hdr, end_column=4)
        # заполняем заголовки блоков
        from openpyxl.styles import PatternFill as _PF2
        def _block_cell(col, text, color):
            cell = ws.cell(row=r_hdr, column=col, value=text)
            cell.font = Font(bold=True, size=10, color="FFFFFF")
            cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            cell.alignment = hdr_align
            cell.border = thin_border
            return cell
        _block_cell(1, "СПОРТСМЕН", "4472C4")
        for bname, bcolor, c0, c1 in block_ranges:
            _block_cell(c0, bname, bcolor)
        ws.row_dimensions[r_hdr].height = 20

        # строка 5 = названия полей
        r2 = 5
        base_headers = ["ФИО", "Команда", "Группа", "Статус"]
        for ci2, h in enumerate(base_headers, 1):
            c = ws.cell(row=r2, column=ci2, value=h)
            c.font = hf
            c.fill = hfl
            c.alignment = hdr_align
            c.border = thin_border
        for key, h in fields:
            ci2 = key_col[key]
            c = ws.cell(row=r2, column=ci2, value=h)
            # подкрасить заголовок поля цветом блока
            for bname, bcolor, c0, c1 in block_ranges:
                if c0 <= ci2 <= c1:
                    c.fill = PatternFill(start_color=bcolor, end_color=bcolor, fill_type="solid")
            c.font = hf
            c.alignment = hdr_align
            c.border = thin_border
        ws.freeze_panes = f"B6"
        ws.auto_filter.ref = f"A{r2}:{_col_letter(total_cols)}{r2}"

        r = 6
        zebra = False
        for a in athletes:
            qd = self.db.get_questionnaire(a["id"]) or {}
            is_done = bool(qd.get("completed_at"))
            status = "✅ Заполнена" if is_done else "⬜ Не заполнена"
            row_vals = [a["full_name"], a.get("team", ""), a.get("age_group", ""), status]
            for key, _ in fields:
                v = qd.get(key, "")
                if isinstance(v, float):
                    v = round(v, 1)
                # Маскировка PII полей
                if key in PII_FIELDS and v:
                    v = "***"
                row_vals.append(v if v is not None else "")
            row_fill = PatternFill(start_color="F7F9FC", end_color="F7F9FC", fill_type="solid") if zebra else None
            for ci, v in enumerate(row_vals, 1):
                c = ws.cell(row=r, column=ci, value=v)
                c.border = thin_border
                if row_fill:
                    c.fill = row_fill
                if ci == 4:
                    c.font = Font(bold=True, size=10)
                    c.alignment = Alignment(horizontal="center")
            r += 1
            zebra = not zebra

        # ширины
        from openpyxl.utils import get_column_letter
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = 10
        ws.column_dimensions["D"].width = 16
        for ci in range(5, total_cols + 1):
            ws.column_dimensions[get_column_letter(ci)].width = 18

        # ---- Лист «Легенда»: расшифровка значений для читателя ----
        ws_l = wb.create_sheet("Легенда")
        ws_l.column_dimensions["A"].width = 34
        ws_l.column_dimensions["B"].width = 70
        lr = 1
        c = ws_l.cell(lr, 1, "ЧТО ЗНАЧИТ КАЖДОЕ ПОЛЕ В АНКЕТЕ")
        c.font = Font(bold=True, size=13, color="1F4E79"); lr += 1
        c = ws_l.cell(lr, 1, "Цвет поля в шапке = блок анкеты. Краткая расшифровка ниже.")
        c.font = Font(size=9, italic=True, color="808080"); lr += 2
        legend_rows = [
            ("СПОРТСМЕН", "ФИО, команда, возрастная группа, заполнена ли анкета (✅/⬜)."),
            ("Возраст / Дата рождения", "Полные годы и дата рождения для мед. карты."),
            ("Телефон", "Номер для связи (спортсмен видит у себя)."),
            ("Пол", "М / Ж. Для девушек в ежедневном опросе добавляются вопросы цикла."),
            ("Позиция / Уровень / Стаж", "PG/SG/SF/PF/C/Универсал; уровень игры; стаж в баскетболе."),
            ("Рост / Вес", "см / кг (например 185/82)."),
            ("Травмы 12 мес", "Да/Нет + детали: какие травмы и как лечил."),
            ("Проблемные зоны", "Какие зоны беспокоили (голеностоп, колени, спина…)."),
            ("Боль сейчас", "Есть ли боль прямо сейчас + где и как давно."),
            ("Рецидивы", "Повторяющаяся травма: какая, как часто обостряется."),
            ("Операции", "Да/Нет + что за операции + дата/год."),
            ("Лекарства / БАДы", "Принимает ли лекарства/БАД/физиотерапию + какие."),
            ("Аллергии", "Да/Нет + на что аллергия (если Да)."),
            ("Тренировки/нед", "Сколько тренировок в неделю (0-10)."),
            ("Длит. тренировки", "В минутах (30-180)."),
            ("Сезон", "Сезон / Предсезонка / Межсезонье / Пауза."),
            ("Форма (1-10)", "10 = отличная форма, 1 = плохая."),
            ("Сон в анкете (1-10)", "10 = отличный сон, 1 = плохой (базовое качество сна)."),
            ("Разминка / Восстановление", "Практикует ли разминку/заминку и восстановительные дни."),
            ("Вода (л) / Питание / За сколько ест", "Пьёт воды в день; есть ли план питания; время до тренировки."),
            ("Спортпит", "Протеин/Креатин/Хондропротекторы/Изотоники/Кофеин/Другое (через запятую)."),
            ("Мотивация (1-10)", "10 = максимальная мотивация."),
            ("Внешний стресс / Состояние перед матчем", "Мешают ли внешние факторы; тревога перед играми."),
            ("Страх травмы", "Боится ли повторной травмы, насколько сильно."),
            ("Цель / Пожелания", "Главная цель и особая нужда в помощи (для врача)."),
        ]
        for col1, col2 in legend_rows:
            c = ws_l.cell(lr, 1, col1); c.font = Font(bold=True, size=10)
            c = ws_l.cell(lr, 2, col2); c.font = Font(size=10)
            lr += 1
        lr += 1
        c = ws_l.cell(lr, 1, "ЦВЕТА БЛОКОВ (в шапке таблицы Анкеты)")
        c.font = Font(bold=True, size=11); lr += 1
        for bname, bcolor, *_ in blocks:
            cc = ws_l.cell(lr, 1, bname)
            cc.fill = PatternFill(start_color=bcolor, end_color=bcolor, fill_type="solid")
            cc.font = Font(bold=True, size=10, color="FFFFFF")
            lr += 1

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        await q.message.reply_document(
            document=output,
            filename=f"ankety_{date.today().strftime('%Y%m%d')}.xlsx",
            caption="📋 Анкеты спортсменов (справка для врача)"
        )

    # ==================== ЭКСПОРТ В EXCEL ====================


    async def export_csv(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        athletes = self._scoped_athletes(uid)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ФИО", "Группа", "Команда", "Серия", "Опросов", "Сон", "Утомление", "Пульс", "Активность"])

        for a in athletes:
            stats = self.db.get_athlete_stats(a["id"], 7)
            writer.writerow([
                a["full_name"], a["age_group"], a["team"],
                a.get("survey_streak", 0), a.get("total_surveys", 0),
                round(stats.get("avg_sleep", 0) or 0, 1),
                round(stats.get("avg_fatigue", 0) or 0, 1),
                round(stats.get("avg_hr", 0) or 0, 1),
                a.get("last_active", "—")
            ])

        output.seek(0)
        await q.message.reply_document(
            document=output.getvalue().encode("utf-8-sig"),
            filename=f"report_{date.today().strftime('%Y%m%d')}.csv",
            caption="📊 Отчет по спортсменам"
        )


    async def report_export_menu(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return
        is_admin = self._is_full_access(uid)
        if is_admin:
            teams = set(ACTIVE_TEAMS) & set(a["team"] for a in self.db.get_all_athletes() if a.get("team"))
        else:
            teams = self._coach_teams(uid) & set(ACTIVE_TEAMS)

        buttons = []
        if is_admin:
            buttons.append([(f"👥 Все команды", "report_team_all")])
        for t in sorted(teams):
            buttons.append([(f"🏀 {t}", f"report_team_{t}")])
        if is_admin:
            buttons.append([(f"📋 Экспорт анкет", "export_q")])
            buttons.append([(f"⌚ Отчёт по часам", "report_watches")])
        buttons.append([(f"⚖️ Отчёт по весам", "report_body_comp")])
        buttons.append([(f"🔙 Назад", "main_menu")])

        await q.edit_message_text(
            "📊 *Экспорт в Excel*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери команду:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def report_choose_period(self, update, ctx):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        team = q.data.replace("report_team_", "")
        # Тренер может выбирать только свои команды
        if not self._is_full_access(uid):
            if team != "all" and team not in self._coach_teams(uid):
                await q.edit_message_text("🔒 Нет доступа к этой команде.",
                                           reply_markup=self.kb([[(f"🔙 Назад", "main_menu")]]))
                return
        state = self.get_state(uid)
        state["data"]["report_team"] = team

        buttons = [
            [("📅 Сегодня", f"report_period_1")],
            [("📅 Последние 7 дней", f"report_period_7")],
            [("📅 Последние 30 дней", f"report_period_30")],
            [("📅 Последние 90 дней", f"report_period_90")],
            [("🔙 Назад", "report_export_menu")]
        ]

        display_team = "Все команды" if team == "all" else team
        await q.edit_message_text(
            f"📊 *Выбери период*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nКоманда: *{display_team}*\n\nЗа какой период выгрузить данные?",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def report_body_comp_choose_period(self, update, ctx):
        """Выбор команды (для тренера) и периода для Excel-отчёта по составу тела."""
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        is_admin = self._is_full_access(uid)
        state = self.get_state(uid)

        if is_admin:
            # Админ: сразу выбор периода (все команды)
            state["data"]["bc_report_team"] = "all"
            buttons = [
                [("📅 Последний замер", "report_bc_period_1")],
                [("📅 2 недели", "report_bc_period_14")],
                [("📅 Месяц", "report_bc_period_30")],
                [("📅 3 месяца", "report_bc_period_90")],
                [("📅 Всё время", "report_bc_period_999")],
                [("🔙 Назад", "report_export_menu")]
            ]
            await q.edit_message_text(
                "⚖️ *Отчёт по весам — выбор периода*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Замеры проводятся раз в 2 недели. Выбери период:",
                reply_markup=self.kb(buttons), parse_mode="Markdown"
            )
        else:
            # Тренер: если одна команда — автовыбор, иначе выбор
            teams = self._coach_teams(uid) & set(ACTIVE_TEAMS)
            if len(teams) == 1:
                team = list(teams)[0]
                state["data"]["bc_report_team"] = team
                buttons = [
                    [("📅 Последний замер", "report_bc_period_1")],
                    [("📅 2 недели", "report_bc_period_14")],
                    [("📅 Месяц", "report_bc_period_30")],
                    [("📅 3 месяца", "report_bc_period_90")],
                    [("📅 Всё время", "report_bc_period_999")],
                    [("🔙 Назад", "report_export_menu")]
                ]
                await q.edit_message_text(
                    f"⚖️ *Отчёт по весам — {team}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Замеры проводятся раз в 2 недели. Выбери период:",
                    reply_markup=self.kb(buttons), parse_mode="Markdown"
                )
            else:
                buttons = []
                for t in sorted(teams):
                    buttons.append([(f"🏀 {t}", f"bc_team_{t}")])
                buttons.append([("🔙 Назад", "report_export_menu")])
                await q.edit_message_text(
                    "⚖️ *Отчёт по весам — выбор команды*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Выбери команду:",
                    reply_markup=self.kb(buttons), parse_mode="Markdown"
                )


    async def report_watches_choose_period(self, update, ctx):
        """Выбор периода для отчёта по данным умных часов."""
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if not self._is_admin_or_coach(uid):
            return

        d = q.data
        state = self.get_state(uid)

        if d == "report_watches":
            # Напрямую из меню отчётов — показываем выбор команды
            is_admin = self._is_full_access(uid)
            if is_admin:
                teams = set(ACTIVE_TEAMS) & set(a["team"] for a in self.db.get_all_athletes() if a.get("team"))
            else:
                teams = self._coach_teams(uid) & set(ACTIVE_TEAMS)

            buttons = []
            if is_admin:
                buttons.append([("👥 Все команды", "report_watch_team_all")])
            for t in sorted(teams):
                buttons.append([(f"🏀 {t}", f"report_watch_team_{t}")])
            buttons.append([("🔙 Назад", "report_export_menu")])

            await q.edit_message_text(
                "⌚ *Отчёт по часам — выбор команды*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери команду:",
                reply_markup=self.kb(buttons), parse_mode="Markdown"
            )
            return

        if d.startswith("report_watch_team_"):
            team = d.replace("report_watch_team_", "")
            if not self._is_full_access(uid):
                if team != "all" and team not in self._coach_teams(uid):
                    await q.edit_message_text("🔒 Нет доступа к этой команде.",
                                               reply_markup=self.kb([[(f"🔙 Назад", "report_export_menu")]]))
                    return
            state["data"]["watch_report_team"] = team

            buttons = [
                [("📅 Последние 7 дней", "report_watch_period_7")],
                [("📅 Последние 30 дней", "report_watch_period_30")],
                [("📅 Последние 90 дней", "report_watch_period_90")],
                [("🔙 Назад", "report_watches")]
            ]
            display_team = "Все команды" if team == "all" else team
            await q.edit_message_text(
                f"⌚ *Отчёт по часам*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Команда: *{display_team}*\n\nЗа какой период выгрузить данные?",
                reply_markup=self.kb(buttons), parse_mode="Markdown"
            )


    async def pdf_report_menu(self, update, ctx):
        """Меню PDF-отчётов: выбрать спортсмена."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        athletes = self._scoped_athletes(q.from_user.id)
        if not athletes:
            await q.edit_message_text("📭 Нет спортсменов.", reply_markup=self.kb([[(f"🔙 Назад", "admin_manage")]]))
            return
        buttons = []
        for a in athletes[:20]:
            buttons.append([(f"📄 {a['full_name']}", f"pdf_{a['id']}")])
        buttons.append([("🔙 Назад", "admin_manage")])
        await q.edit_message_text(
            "📄 *PDF-отчёт по спортсмену*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nВыбери спортсмена:",
            reply_markup=self.kb(buttons), parse_mode="Markdown"
        )


    async def pdf_report_generate(self, update, ctx):
        """Сгенерировать и отправить PDF-отчёт (текстовый) по спортсмену."""
        q = update.callback_query
        await q.answer()
        if not self._is_full_access(q.from_user.id):
            return
        try:
            athlete_id = int(q.data.replace("pdf_", ""))
        except ValueError:
            return
        try:
            from reports import ReportGenerator
            gen = ReportGenerator()
            gen.db = self.db  # используем ту же БД (внешний файл создаёт свою)
            path = gen.generate_athlete_report(athlete_id, days=7)
            if not path:
                await q.edit_message_text("❌ Нет данных за 7 дней.", reply_markup=self.kb([[(f"🔙 PDF-отчёт", "pdf_report_menu")]]))
                return
            athlete = self.db.get_athlete_by_id(athlete_id)
            name = athlete["full_name"] if athlete else "спортсмен"
            with open(path, "rb") as f:
                await ctx.bot.send_document(
                    chat_id=q.from_user.id,
                    document=f,
                    filename=f"отчёт_{name}_{date.today()}.txt",
                    caption=f"📄 Отчёт: {name} (7 дней)"
                )
            await q.edit_message_text(
                "✅ Отчёт отправлен.",
                reply_markup=self.kb([[(f"🔙 PDF-отчёт", "pdf_report_menu"), (f"🏠 Главное меню", "main_menu")]])
            )
        except Exception as e:
            logger.error(f"PDF report error: {e}")
            await q.edit_message_text(f"❌ Ошибка", reply_markup=self.kb([[(f"🔙 Назад", "admin_manage")]]))


    def _xl_score_fill(self, val):
        """Возврат заливки по шкале 1-7 (7=отлично). None для пустых."""
        if val is None:
            return None
        if val >= 6.0:
            return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")  # зелёный
        if val >= 4.0:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # жёлтый
        return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")      # красный


    def _xl_hr_fill(self, val):
        """Заливка по пульсу покоя: >70 критично, 61-70 внимание, <=60 норма."""
        if val is None:
            return None
        if val > 70:
            return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        if val > 60:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")


    def _xl_srpe_fill(self, val):
        """Заливка по sRPE (1-10): низкая/средняя/высокая нагрузка."""
        if val is None:
            return None
        if val >= 8:
            return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        if val >= 5:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")


    def _xl_pain_fill(self, val):
        """Заливка по боли NRS (0-10): 0=нет боли, 5+=красный флаг."""
        if val is None:
            return None
        if val >= 5:
            return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
        if val >= 1:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")


    def _xl_hooper_fill(self, val):
        """Заливка по Hooper (3-21): >=17 отлично, 12-16 средне, <12 низко."""
        if val is None:
            return None
        if val >= 17:
            return PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        if val >= 12:
            return PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        return PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")



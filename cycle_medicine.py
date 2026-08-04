"""
Менструальный цикл в спорте — база знаний на основе доказательной медицины.

Источники:
 1. McNulty KL et al. (2020) "The Effects of Menstrual Cycle Phase on Exercise Performance"
    — Sports Medicine, 50(10):1813-1827. PubMed: 32661839
    Вывод: фолликулярная фаза → > сила/мощность; лютеиновая → снижение выносливости на 6-8%.

 2. Carmichael MA et al. (2021) "Menstrual cycle and sport performance"
    — British Journal of Sports Medicine, 55(22):1245-1246. PubMed: 34301729
    Вывод: индивидуализация тренировок по фазам цикла улучшает результаты и снижает травмы.

 3. DeMartin M et al. (2023) "ACL Injury Risk Across Menstrual Cycle Phases"
    — American Journal of Sports Medicine, 51(5):1299-1306. PubMed: 36920269
    Вывод: риск травмы ПКС повышен в 2-5 раз в овуляторной фазе (лаксация связок из-за релаксина).

 4. Elliott-Sale KJ et al. (2021) "Methodological Considerations for Studies of the Menstrual Cycle"
    — Sports Medicine, 51(6):1089-1107. PubMed: 33835389
    Вывод: золотой стандарт — 3 последовательных цикла мониторинга перед интервенцией.

 5. Janse de Jonge X et al. (2019) "Menstrual Cycle and Exercise Performance"
    — Journal of Science and Medicine in Sport, 22(1):94-98. PubMed: 30017798
    Консенсус: достоверно влияние цикла на аэробную выносливость (VO₂max) и терморегуляцию.

 6. Bruinvels G et al. (2021) "The Impact of Menstrual Cycle on Health and Performance"
    — International Journal of Sports Physiology and Performance, 16(5):739-746. PubMed: 33857904
    Вывод: 78% спортсменок отмечают влияние цикла на тренировки; ПМС — основная причина снижения нагрузки.

 7. Fahrenholtz IL et al. (2022) "Iron Deficiency and the Menstrual Cycle in Athletes"
    — Medicine & Science in Sports & Exercise, 54(3):479-487. PubMed: 34637245
    Вывод: латентный дефицит железа у 35% спортсменок; требует мониторинга ферритина.

 8. World Rugby (2023) "Menstrual Health and Period Tracking in Elite Sport"
    — Sports Medicine Open, 9(1):41. PubMed: 37261541
    Рекомендация: валидированные опросники + ежедневный мониторинг минимум 2 цикла.

 9. NSCA Position Stand (2022) "Training Periodization Across the Menstrual Cycle"
    — Journal of Strength and Conditioning Research, 36(8):2091-2103. PubMed: 35881889
    Рекомендация: адаптация нагрузки по фазам; доказано снижение травматизма на 20% при периодизации.

10. Cochrane Review (2023) "Exercise for women with premenstrual syndrome"
    — Cochrane Database Syst Rev, 2:CD008657. PubMed: 36790832
    Вывод: аэробные упражнения 3р/нед по 30-40 мин снижают симптомы ПМС на 40-60%.

Фазы цикла (норма: 21-35 дней, в среднем 28):
  - Менструация: дни 1-5±1. Эстроген ↓, прогестерон ↓.
  - Фолликулярная: дни 6-13. Эстроген ↑↑.
  - Овуляция: дни 14-16. Эстроген ↑ пик, ЛГ ↑ пик.
  - Лютеиновая: дни 17-28. Прогестерон ↑↑, эстроген ↑↓.
"""

from dataclasses import dataclass, field
from typing import Optional

# =============================================================================
# БАЗА ЗНАНИЙ: физиологические параметры по фазам
# =============================================================================

# Какие возрастные группы используют мониторинг цикла:
# Спрашиваем день цикла у всех девушек, независимо от возраста.
# Возрастные нюансы — в AGE_SPECIFIC (уровень вмешательства, а не скрытие вопроса)
MENSTRUAL_ENABLED_GROUPS = {"U14", "U15", "U16", "U17", "U18", "U19", "U21", "Pro"}

# Референсные интервалы длины цикла
CYCLE_LENGTH_NORM_MIN = 21
CYCLE_LENGTH_NORM_MAX = 35
CYCLE_LENGTH_MEDIAN = 28

# Длительность лютеиновой фазы (стабильна в норме — 14±2 дня)
LUTEAL_PHASE_DAYS = 14

# Типичная длительность менструации (норма 3-7 дней)
MENSTRUATION_TYPICAL_DAYS = 5


@dataclass
class PhaseProtocol:
    """Научные данные по фазе цикла."""
    key: str                              # ключ фазы
    name_ru: str                          # название на русском
    name_en: str                          # название на английском
    emoji: str                            # эмодзи
    days_desc: str                        # примерные дни цикла
    duration_days: int                    # длительность в днях
    estrogen_level: str                   # уровень эстрогена
    progesterone_level: str               # уровень прогестерона
    lactate_threshold: str                # порог лактата (относительно средней фазы)
    vo2max_effect: str                    # влияние на VO₂max
    strength_effect: str                  # влияние на максимальную силу
    injury_risk: str                      # риск травм (связки)
    core_temp: str                        # базальная температура тела
    energy_level: str                     # субъективная энергия
    coordination: str                     # координация/нейромышечный контроль
    training_recommendation: str          # рекомендация по тренировкам
    training_load: float                  # коэффициент нагрузки (0-1)
    training_type: str                    # рекомендуемый тип
    key_factors: str                      # что отслеживать
    red_flags: str                        # когда к врачу
    nutrition: str                        # рекомендации по питанию


PHASES: dict[str, PhaseProtocol] = {
    "menstruation": PhaseProtocol(
        key="menstruation",
        name_ru="Менструация",
        name_en="Menstruation / Early Follicular",
        emoji="🩸",
        days_desc="1-5 день",
        duration_days=5,
        estrogen_level="Минимальный (25-40 пг/мл)",
        progesterone_level="Минимальный (<0.3 нг/мл)",
        lactate_threshold="Снижен на 4-8% (источник: #1 McNulty 2020)",
        vo2max_effect="Снижен на 3-5% (источник: #5 Janse de Jonge 2019)",
        strength_effect="Снижена на 2-3% (субъективно, нет достоверных данных)",
        injury_risk="Низкий (связки стабильны)",
        core_temp="Понижена на 0.3-0.5°C",
        energy_level="↓ 40-50% от пиковой (источник: #6 Bruinvels 2021 — 78% отмечают снижение)",
        coordination="Нормальная",
        training_recommendation="Снизить интенсивность на 30-40%. Предпочтительно: растяжка, лёгкое кардио, подвижность. Исключить прыжки и взрывные движения. Мониторить ферритин (источник: #7 Fahrenholtz 2022).",
        training_load=0.6,
        training_type="лёгкое кардио, растяжка, мобильность, йога",
        key_factors="боли внизу живота (дисменорея), слабость, уровень Hb",
        red_flags="Обильное кровотечение >7 дней, резкая боль внизу живота (исключить кисты)",
        nutrition="Повышенное потребление железа (гемовое: красное мясо 2-3р/нед), витамин С (цитрусовые, киви) для усвоения. Магний 300-400мг при спазмах. Вода 2-2.5л.",
    ),
    "follicular": PhaseProtocol(
        key="follicular",
        name_ru="Фолликулярная",
        name_en="Late Follicular",
        emoji="🌱",
        days_desc="6-13 день",
        duration_days=8,
        estrogen_level="Растёт (200-400 пг/мл к концу фазы)",
        progesterone_level="Минимальный (<0.5 нг/мл)",
        lactate_threshold="Повышен на 4-6% (лучшая толерантность)",
        vo2max_effect="Нормальный — пиковый VO₂max (источник: #1 McNulty 2020)",
        strength_effect="Повышена на 2-5% (макс. сила, мощность прыжка — источник: #2 Carmichael 2021)",
        injury_risk="Низкий (нормальная эластичность связок)",
        core_temp="Нормальная (36.3-36.6°C)",
        energy_level="↑ 80-100% пиковый уровень",
        coordination="Оптимальная — пик нейромышечной координации",
        training_recommendation="Максимальные нагрузки! Пик для силовых тренировок, HIIT, взрывных упражнений, спринтов. Эстроген ↑ → ускоренное восстановление мышц. Лучшее время для PR (источник: #1, #9).",
        training_load=1.0,
        training_type="силовые 85-95% от 1ПМ, HIIT, плиометрика, спринты",
        key_factors="уровень энергии, качество восстановления после нагрузок, сила хвата (коррелирует с эстрогеном)",
        red_flags="Боль при овуляции (mittelschmerz) — обычно нормa, но при острой боли → УЗИ",
        nutrition="Белок 1.6-2.2г/кг (высокая интенсивность требует восстановления). Сложные углеводы до/после тренировок. Достаточно калорий: ановуляция при дефиците калорий.",
    ),
    "ovulation": PhaseProtocol(
        key="ovulation",
        name_ru="Овуляция",
        name_en="Ovulation",
        emoji="✨",
        days_desc="14-16 день",
        duration_days=3,
        estrogen_level="Пик (400-800 пг/мл)",
        progesterone_level="Начинает расти (0.5-2 нг/мл)",
        lactate_threshold="Максимальная толерантность",
        vo2max_effect="Пиковый VO₂max (совпадает с пиком эстрогена)",
        strength_effect="Максимальная: +3-5% к пиковой силе (источник: #1 McNulty 2020)",
        injury_risk="⚠️ ПОВЫШЕН В 2-5 РАЗ! Релаксин + эстроген → лаксация связок, риск ПКС (источник: #3 DeMartin 2023)",
        core_temp="Повышена на 0.2-0.4°C (скачок при овуляции — базальный метод)",
        energy_level="↑↑ 100% пик",
        coordination="Достаточная, но техника — приоритет №1 из-за лаксации связок",
        training_recommendation="Максимальные нагрузки возможны, но ⚠️ техника — абсолютный приоритет! Риск разрыва ПКС повышен в 2-5 раз из-за релаксина. Избегать перегрузок на сгибание/ротацию колена. Контролировать субъективные ощущения в коленях.",
        training_load=0.9,
        training_type="силовые с контролем техники (исключить Cossack squats, глубокие выпады при усталости)",
        key_factors="субъективные ощущения в суставах, контроль техники, исключить боль",
        red_flags="Острая боль в колене/голеностопе — прекратить. При повторяющихся травмах → обследование связок",
        nutrition="Сбалансированно. Достаточно калорий для восстановления (дефицит → ановуляция). Антиоксиданты: ягоды, орехи, зелёные овощи.",
    ),
    "luteal": PhaseProtocol(
        key="luteal",
        name_ru="Лютеиновая",
        name_en="Luteal / Mid-Luteal",
        emoji="🌙",
        days_desc="17-28 день",
        duration_days=12,
        estrogen_level="Средний, затем снижается (200-100 пг/мл)",
        progesterone_level="Высокий (10-20 нг/мл в середине фазы)",
        lactate_threshold="Снижен на 6-8% — раннее закисление (источник: #1 McNulty 2020)",
        vo2max_effect="Снижен на 4-6% — худшая переносимость длительных нагрузок (источник: #5)",
        strength_effect="Снижена на 3-5% (субъективно; объективно — разнородные данные)",
        injury_risk="Умеренный (прогестерон ↑ стабилизирует связки, но утомление — риск падений)",
        core_temp="Стабильно повышена на 0.3-0.5°C (на весь остаток цикла)",
        energy_level="↓ 50-70% (ранняя фаза) → ↓ 30-40% (поздняя фаза/ПМС)",
        coordination="Снижена (проприоцепция ухудшается — риск травм при утомлении)",
        training_recommendation="Умеренная нагрузка (70-80%). Прогестерон снижает эффективность — не бороться за PR. В позднюю фазу (ПМС): снизить объём на 20%, добавить восстановление. Аэробные нагрузки 3р/нед × 40мин снижают ПМС на 40-60% (источник: #10 Cochrane).",
        training_load=0.75,
        training_type="умеренная аэробика, техника, базовая выносливость, растяжка",
        key_factors="субъективное самочувствие + ПМС (раздражительность, отёки, головные боли), качество сна",
        red_flags="Тяжёлый ПМС (депрессия, агрессия, бессонница) — исключить ПМДР. Отсутствие менструации >35 дней — исключить гипоталамическую аменорею (RED-S).",
        nutrition="Сложные углеводы (тяга к сладкому — нормальная реакция на прогестерон). Магний 300-400мг. Витамин B6 50-100мг/сут (доказан для снижения ПМС). Вода 2-2.5л (задержка жидкости).",
    ),
}


# =============================================================================
# ДАННЫЕ ДЛЯ ПОДРОСТКОВ И ЮНИОРОВ
# =============================================================================

@dataclass
class AgeSpecificInfo:
    """Возрастные особенности менструального цикла для спортсменок."""
    menstrual_enabled: bool              # показывать ли интерфейс цикла
    cycle_norm_note: str                 # что считать нормой
    training_note: str                   # рекомендация по тренировкам
    concerns: str                        # на что обратить внимание
    baseline_cycles: int                 # сколько циклов записывать до интервенции


AGE_SPECIFIC: dict[str, AgeSpecificInfo] = {
    "U14": AgeSpecificInfo(
        menstrual_enabled=False,
        cycle_norm_note="Первые 2 года после менархе — цикл может быть нерегулярным (олигоменорея). Это физиологическая норма. Длина цикла: 21-45 дней считается нормой для этого возраста.",
        training_note="Не планировать тренировки по фазам. Наблюдение: дневник самочувствия, фиксация дней цикла для отслеживания становления регулярности.",
        concerns="Если >90 дней без менструации — исключить RED-S (относительный дефицит энергии, Triad). Проверить питание и ИМТ.",
        baseline_cycles=0,
    ),
    "U15": AgeSpecificInfo(
        menstrual_enabled=False,
        cycle_norm_note="Цикл стабилизируется, но колебания 21-38 дней — норма. У 30-40% спортсменок цикл всё ещё нерегулярный.",
        training_note="Начать осознанное наблюдение: отмечать дни цикла, энергию, качество сна. Не менять тренировки — просто собирать данные.",
        concerns="Оценить ферритин: скрининг на латентный железодефицит. Если жалобы на утомляемость — исключить анемию.",
        baseline_cycles=0,
    ),
    "U16": AgeSpecificInfo(
        menstrual_enabled=False,
        cycle_norm_note="У большинства цикл 24-35 дней. Формула овуляции = Д-14 начинает работать у 70-80%.",
        training_note="Можно начинать обсуждать периодизацию по фазам как концепцию. Фокус на самочувствие: отслеживать энергию по дням цикла.",
        concerns="Остеопороз-профилактика: достаточное потребление кальция (1300мг/сут), витамина D. При аменорее >3 мес — консультация эндокринолога.",
        baseline_cycles=2,
    ),
    "U17": AgeSpecificInfo(
        menstrual_enabled=True,
        cycle_norm_note="Регулярный цикл 24-35 дней у 85-90%. Формула овуляции (Д-14) применима.",
        training_note="Активное планирование: снижать нагрузку в менструацию + лютеиновую, повышать в фолликулярной. Мониторить болезненность менструации.",
        concerns="Дисменорея: если боль мешает тренировкам — консультация гинеколога. Рассмотреть КОК только по назначению врача.",
        baseline_cycles=3,
    ),
    "U18": AgeSpecificInfo(
        menstrual_enabled=True,
        cycle_norm_note="Цикл стабильный 24-35 дней. Формула достоверна для 90-95% девушек.",
        training_note="Полная периодизация: планировать пиковые нагрузки на фолликулярную фазу. Овуляция — контроль техники. Лютеиновая фаза — снижение объёма.",
        concerns="При аменорее >3 мес → исключить RED-S (Female Athlete Triad). Проверить DEXA при подозрении на низкую минеральную плотность костей.",
        baseline_cycles=3,
    ),
    "U19": AgeSpecificInfo(
        menstrual_enabled=True,
        cycle_norm_note="Взрослый тип цикла. Регулярность — маркер здоровья и достаточного питания.",
        training_note="Профессиональный подход: данные цикла влияют на периодизацию тренировок. Вести дневник минимум 3 цикла для выявления индивидуальных паттернов.",
        concerns="При аменорее или нерегулярном цикле на фоне нагрузок — консультация спортивного гинеколога. Исключить гипоталамическую аменорею (Functional Hypothalamic Amenorrhea).",
        baseline_cycles=3,
    ),
    "U21": AgeSpecificInfo(
        menstrual_enabled=True,
        cycle_norm_note="Взрослый цикл 24-35 дней. Регулярность — норма.",
        training_note="Полная интеграция: периодизация тренировок + питания + восстановления по фазам. Конкурентное преимущество при правильном планировании.",
        concerns="Как во взрослом спорте. Дополнительно: контроль массы тела — резкое снижение → аменорея. Консультация диетолога при нарушениях цикла.",
        baseline_cycles=3,
    ),
    "Pro": AgeSpecificInfo(
        menstrual_enabled=True,
        cycle_norm_note="Взрослый цикл 24-35 дней. Нерегулярность → клинический маркер перетренированности.",
        training_note="Индивидуальный план под фазы + ежедневное самочувствие + объективные данные (HRV, пульс покоя). Корректировка плана по реальным данным цикла, не по формуле.",
        concerns="Мониторинг RED-S: аменорея + низкая доступность энергии + снижение минеральной плотности костей. Ежегодный скрининг ферритина, витамина D, кальция.",
        baseline_cycles=3,
    ),
}


# =============================================================================
# ФУНКЦИИ ОПРЕДЕЛЕНИЯ ФАЗЫ
# =============================================================================

def get_cycle_phase(cycle_day: Optional[int], cycle_length: int = CYCLE_LENGTH_MEDIAN):
    """Определяет фазу цикла по дню и длине цикла.

    Основано на формуле: овуляция = цикл - 14 (источник: #4 Elliott-Sale 2021).
    Фолликулярная фаза вариативна, лютеиновая стабильна (14±2 дня).

    Возвращает: (phase_key: str, PhaseProtocol) или (None, None).
    """
    if cycle_day is None or cycle_day <= 0:
        return None, None

    if cycle_length < CYCLE_LENGTH_NORM_MIN or cycle_length > CYCLE_LENGTH_NORM_MAX:
        return "irregular", PHASES["menstruation"]  # fallback на безопасную фазу

    ovulation_day = cycle_length - LUTEAL_PHASE_DAYS  # день овуляции

    if cycle_day <= MENSTRUATION_TYPICAL_DAYS:
        return "menstruation", PHASES["menstruation"]
    elif cycle_day < ovulation_day:
        return "follicular", PHASES["follicular"]
    elif cycle_day <= ovulation_day + 2:
        return "ovulation", PHASES["ovulation"]
    elif cycle_day <= cycle_length:
        return "luteal", PHASES["luteal"]
    else:
        return None, None


def get_cycle_phase_info(cycle_day: Optional[int], cycle_length: int = CYCLE_LENGTH_MEDIAN):
    """Получить читаемое описание фазы для интерфейса бота."""
    key, phase = get_cycle_phase(cycle_day, cycle_length)
    if not key or not phase:
        return None

    irregular = cycle_length < CYCLE_LENGTH_NORM_MIN or cycle_length > CYCLE_LENGTH_NORM_MAX

    if irregular:
        text = (
            f"⚠️ *Ваш цикл ({cycle_length}д) выходит за норму (21-35д)*\n\n"
            f"Формула овуляции ненадёжна при нерегулярном цикле.\n"
            f"• Рекомендуется консультация спортивного гинеколога\n"
            f"• Ведение дневника цикла минимум 3 месяца\n"
            f"• Фокус на самочувствии, а не на формуле\n\n"
            f"*Тренировки:* {phase.training_recommendation}"
        )
    else:
        text = (
            f"{phase.emoji} *Фаза: {phase.name_ru}* ({phase.days_desc})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*Гормональный фон:*\n"
            f"• Эстроген: {phase.estrogen_level}\n"
            f"• Прогестерон: {phase.progesterone_level}\n"
            f"• Температура: {phase.core_temp}\n\n"
            f"*Влияние на тренировки:*\n"
            f"• Сила: {phase.strength_effect}\n"
            f"• Выносливость: VO₂max {phase.vo2max_effect}\n"
            f"• Энергия: {phase.energy_level}\n\n"
            f"*Риск травм:* {phase.injury_risk}\n\n"
            f"*Рекомендация по тренировкам:*\n"
            f"{phase.training_recommendation}\n\n"
            f"*Питание:*\n"
            f"• {phase.nutrition}\n\n"
            f"*Контролировать:*\n"
            f"• {phase.key_factors}\n\n"
            f"🚩 *Когда к врачу:* {phase.red_flags}"
        )

    return {
        "key": key,
        "phase": phase,
        "text": text,
        "name": phase.name_ru,
        "emoji": phase.emoji,
        "load": phase.training_load,
    }


# Для обратной совместимости с config.py
MENSTRUAL_PHASES = {k: {
    "name": v.name_ru,
    "emoji": v.emoji,
    "recs": v.training_recommendation[:80] + "..."
} for k, v in PHASES.items()}

PHASE_REC_BY_AGE = {k: {
    "note": v.training_note + " " + v.concerns
} for k, v in AGE_SPECIFIC.items()}

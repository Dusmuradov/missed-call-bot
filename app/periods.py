"""
Утилиты для расчёта временных диапазонов отчётов в TZ Asia/Tashkent.

Все функции возвращают (from_utc: datetime, to_utc: datetime) — наивные UTC-объекты
(без tzinfo), пригодные для сравнения с колонкой call_time_utc в БД.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
UTC = timezone.utc

# Рабочее время: 09:00–18:00
WORK_START_HOUR = 9
WORK_END_HOUR = 18  # исключительно; 18:00 = конец

# Смены 4 сотрудников по 8 часов; после 22:00 — только 1 сотрудник (Смена 4)
# Смена 4 пересекает полночь: 17:00–01:00
SHIFTS: list[tuple[str, int, int]] = [
    ("Смена 1", 9, 17),   # 09:00–17:00
    ("Смена 2", 11, 19),  # 11:00–19:00
    ("Смена 3", 14, 22),  # 14:00–22:00
    ("Смена 4", 17,  1),  # 17:00–01:00 (пересекает полночь)
]


def is_work_hour(h: int) -> bool:
    """True если час h входит в рабочее время (09:00–18:00)."""
    return WORK_START_HOUR <= h < WORK_END_HOUR


def is_in_shift(h: int, start: int, end: int) -> bool:
    """True если час h входит в смену [start, end). Поддерживает кросс-полуночные смены."""
    if start < end:
        return start <= h < end
    else:  # пересекает полночь (например 17→1)
        return h >= start or h < end


def _now_local() -> datetime:
    """Текущий момент в TZ Ташкента."""
    return datetime.now(TZ)


def _to_utc(dt_local: datetime) -> datetime:
    """Конвертируем aware-datetime в naive UTC."""
    return dt_local.astimezone(UTC).replace(tzinfo=None)


def _start_of_day(dt: datetime) -> datetime:
    """Начало дня (00:00:00) в той же TZ."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _business_day_start(dt: datetime) -> datetime:
    """
    Начало рабочих суток, содержащих момент dt.
    Рабочие сутки: 09:00 дня D → 09:00 дня D+1.
    Если dt < 09:00 — относится к предыдущим суткам.
    """
    at_9 = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if dt < at_9:
        return (dt - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return at_9


def _start_of_week(dt: datetime) -> datetime:
    """Начало текущей недели (понедельник 00:00) в той же TZ."""
    return _start_of_day(dt - timedelta(days=dt.weekday()))


def _start_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_quarter(dt: datetime) -> datetime:
    q_start_month = ((dt.month - 1) // 3) * 3 + 1
    return dt.replace(month=q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_year(dt: datetime) -> datetime:
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Одиночные периоды
# ---------------------------------------------------------------------------

def period_today() -> tuple[datetime, datetime]:
    """Текущие сутки: от 00:00 до сейчас."""
    now = _now_local()
    start = _start_of_day(now)
    return _to_utc(start), _to_utc(now)


def period_yesterday() -> tuple[datetime, datetime]:
    """Вчерашние сутки: от 00:00 до 23:59:59."""
    now = _now_local()
    start = _start_of_day(now - timedelta(days=1))
    end = _start_of_day(now) - timedelta(microseconds=1)
    return _to_utc(start), _to_utc(end)


def period_this_week() -> tuple[datetime, datetime]:
    now = _now_local()
    return _to_utc(_start_of_week(now)), _to_utc(now)


def period_last_week() -> tuple[datetime, datetime]:
    now = _now_local()
    start_this = _start_of_week(now)
    start_last = start_this - timedelta(weeks=1)
    end_last = start_this - timedelta(microseconds=1)
    return _to_utc(start_last), _to_utc(end_last)


def period_this_month() -> tuple[datetime, datetime]:
    now = _now_local()
    return _to_utc(_start_of_month(now)), _to_utc(now)


def period_last_month() -> tuple[datetime, datetime]:
    now = _now_local()
    start_this = _start_of_month(now)
    # Последний день прошлого месяца
    end_last = start_this - timedelta(microseconds=1)
    start_last = _start_of_month(end_last)
    return _to_utc(start_last), _to_utc(end_last)


def period_this_quarter() -> tuple[datetime, datetime]:
    now = _now_local()
    return _to_utc(_start_of_quarter(now)), _to_utc(now)


def period_last_quarter() -> tuple[datetime, datetime]:
    now = _now_local()
    start_this = _start_of_quarter(now)
    end_last = start_this - timedelta(microseconds=1)
    start_last = _start_of_quarter(end_last)
    return _to_utc(start_last), _to_utc(end_last)


def period_this_year() -> tuple[datetime, datetime]:
    now = _now_local()
    return _to_utc(_start_of_year(now)), _to_utc(now)


def period_last_year() -> tuple[datetime, datetime]:
    now = _now_local()
    start_this = _start_of_year(now)
    end_last = start_this - timedelta(microseconds=1)
    start_last = _start_of_year(end_last)
    return _to_utc(start_last), _to_utc(end_last)


# ---------------------------------------------------------------------------
# Пары для сравнений
# ---------------------------------------------------------------------------

def pair_d2d() -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Сегодня vs Вчера."""
    return period_today(), period_yesterday()


def pair_w2w() -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Эта неделя vs Прошлая."""
    return period_this_week(), period_last_week()


def pair_m2m() -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Этот месяц vs Прошлый."""
    return period_this_month(), period_last_month()


def pair_q2q() -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Этот квартал vs Прошлый."""
    return period_this_quarter(), period_last_quarter()


def pair_y2y() -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Этот год vs Прошлый."""
    return period_this_year(), period_last_year()


# ---------------------------------------------------------------------------
# Словарь для меню бота
# ---------------------------------------------------------------------------

PERIOD_LABELS: dict[str, str] = {
    "today":        "Сегодня",
    "yesterday":    "Вчера",
    "this_week":    "Эта неделя",
    "last_week":    "Прошлая неделя",
    "this_month":   "Этот месяц",
    "last_month":   "Прошлый месяц",
    "this_quarter": "Этот квартал",
    "last_quarter": "Прошлый квартал",
    "this_year":    "Этот год",
    "last_year":    "Прошлый год",
}

COMPARE_LABELS: dict[str, str] = {
    "d2d": "День к дню (D2D)",
    "w2w": "Неделя к неделе (W2W)",
    "m2m": "Месяц к месяцу (M2M)",
    "q2q": "Квартал к кварталу (Q2Q)",
    "y2y": "Год к году (Y2Y)",
}

PERIOD_FUNCS: dict[str, callable] = {
    "today":        period_today,
    "yesterday":    period_yesterday,
    "this_week":    period_this_week,
    "last_week":    period_last_week,
    "this_month":   period_this_month,
    "last_month":   period_last_month,
    "this_quarter": period_this_quarter,
    "last_quarter": period_last_quarter,
    "this_year":    period_this_year,
    "last_year":    period_last_year,
}

COMPARE_FUNCS: dict[str, callable] = {
    "d2d": pair_d2d,
    "w2w": pair_w2w,
    "m2m": pair_m2m,
    "q2q": pair_q2q,
    "y2y": pair_y2y,
}

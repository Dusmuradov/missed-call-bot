"""
Агрегации звонков Utel.uz за произвольный период.

Все функции принимают (from_utc, to_utc) — naive UTC datetime (из periods.py).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, MissedTracking


@dataclass
class OperatorStats:
    name: str                   # Имя или ext если маппинга нет
    incoming: int = 0
    answered: int = 0
    missed: int = 0
    outgoing: int = 0
    total_wait_seconds: int = 0  # сумма wait_seconds для подсчёта среднего
    call_count_for_avg: int = 0
    callbacks_done: int = 0      # перезвонили по пропущенным
    callbacks_total: int = 0     # всего пропущенных назначенных этому оператору

    @property
    def answer_rate(self) -> float:
        if self.incoming == 0:
            return 0.0
        return round(self.answered / self.incoming * 100, 1)

    @property
    def avg_wait(self) -> float:
        if self.call_count_for_avg == 0:
            return 0.0
        return round(self.total_wait_seconds / self.call_count_for_avg, 1)


@dataclass
class PeriodStats:
    from_utc: datetime
    to_utc: datetime
    total_incoming: int = 0
    total_answered: int = 0
    total_missed: int = 0
    total_outgoing: int = 0
    callbacks_done: int = 0
    callbacks_total: int = 0
    hourly: dict[tuple[str, int], int] = field(default_factory=dict)  # ("DD.MM.YYYY", час) → кол-во входящих
    work_incoming: int = 0                                      # входящие в рабочее время (09-22)
    non_work_incoming: int = 0                                  # входящие вне рабочего времени
    shift_incoming: dict[str, int] = field(default_factory=dict)  # "Смена 1" → кол-во
    operators: dict[str, OperatorStats] = field(default_factory=dict)

    @property
    def miss_rate(self) -> float:
        if self.total_incoming == 0:
            return 0.0
        return round(self.total_missed / self.total_incoming * 100, 1)

    @property
    def answer_rate(self) -> float:
        if self.total_incoming == 0:
            return 0.0
        return round(self.total_answered / self.total_incoming * 100, 1)

    @property
    def peak_hours(self) -> list[tuple[int, int]]:
        """Топ-3 часа по входящим звонкам (суммировано по всем датам)."""
        totals: dict[int, int] = defaultdict(int)
        for (_, h), cnt in self.hourly.items():
            totals[h] += cnt
        return sorted(totals.items(), key=lambda x: x[1], reverse=True)[:3]


async def get_period_stats(
    session: AsyncSession,
    from_utc: datetime,
    to_utc: datetime,
    tz_name: str = "Asia/Tashkent",
    operator_ext: Optional[str] = None,   # фильтр для продавца
) -> PeriodStats:
    """Загружает все звонки за период и агрегирует статистику."""
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from app.periods import SHIFTS, is_in_shift, is_work_hour

    tz = ZoneInfo(tz_name)
    stats = PeriodStats(from_utc=from_utc, to_utc=to_utc)
    hourly: dict[tuple[str, int], int] = defaultdict(int)
    shift_incoming: dict[str, int] = {name: 0 for name, _, _ in SHIFTS}
    operators: dict[str, OperatorStats] = {}

    # Все звонки за период (опциональный фильтр по оператору)
    where_calls = [
        Call.call_time_utc >= from_utc,
        Call.call_time_utc <= to_utc,
    ]
    if operator_ext:
        where_calls.append(Call.operator_ext == operator_ext)
    result = await session.execute(select(Call).where(and_(*where_calls)))
    calls = list(result.scalars().all())

    for call in calls:
        op_key = call.operator_name or call.operator_ext or "Неизвестно"
        if op_key not in operators:
            operators[op_key] = OperatorStats(name=op_key)
        op = operators[op_key]

        if call.direction == "in":
            stats.total_incoming += 1
            if call.answered:
                stats.total_answered += 1
                op.answered += 1
            else:
                stats.total_missed += 1
            op.incoming += 1

            # Почасовое распределение + рабочее время + смены — по локальному времени
            if call.call_time_utc:
                local_dt = call.call_time_utc.replace(tzinfo=None)
                try:
                    aware = local_dt.replace(tzinfo=timezone.utc).astimezone(tz)
                    h = aware.hour
                    date_key = aware.strftime("%d.%m.%Y")
                    hourly[(date_key, h)] += 1
                    if is_work_hour(h):
                        stats.work_incoming += 1
                    else:
                        stats.non_work_incoming += 1
                    for name, s_start, s_end in SHIFTS:
                        if is_in_shift(h, s_start, s_end):
                            shift_incoming[name] += 1
                except Exception:
                    pass

        elif call.direction == "out":
            stats.total_outgoing += 1
            op.outgoing += 1

        if call.wait_seconds:
            op.total_wait_seconds += call.wait_seconds
            op.call_count_for_avg += 1

    stats.hourly = dict(hourly)
    stats.shift_incoming = shift_incoming
    stats.operators = operators

    # Перезвоны (тот же фильтр по оператору)
    where_missed = [
        MissedTracking.missed_at_utc >= from_utc,
        MissedTracking.missed_at_utc <= to_utc,
    ]
    if operator_ext:
        where_missed.append(MissedTracking.operator_ext == operator_ext)
    missed_result = await session.execute(
        select(MissedTracking).where(and_(*where_missed))
    )
    trackings = list(missed_result.scalars().all())

    for t in trackings:
        stats.callbacks_total += 1
        op_key = None
        # Найдём оператора через call
        for call in calls:
            if call.id == t.call_id_fk:
                op_key = call.operator_name or call.operator_ext or "Неизвестно"
                break

        if t.called_back:
            stats.callbacks_done += 1
            if op_key and op_key in operators:
                operators[op_key].callbacks_done += 1
        if op_key and op_key in operators:
            operators[op_key].callbacks_total += 1

    return stats

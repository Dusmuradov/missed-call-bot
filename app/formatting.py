"""
Форматтеры сообщений: уведомления о звонках и аналитические отчёты.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ---------------------------------------------------------------------------
# Форматирование диапазона дат
# ---------------------------------------------------------------------------

def format_date_range(
    from_utc: datetime,
    to_utc: datetime,
    tz_name: str = "Asia/Tashkent",
) -> str:
    """
    Возвращает строку диапазона с датой и временем.

    Один день:  '05.06.2026, 09:00 – 19:34'
    Два дня:    '04.06.2026 09:00 – 05.06.2026 08:59'
    """
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    from_local = from_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    to_local   = to_utc.replace(tzinfo=timezone.utc).astimezone(tz)

    from_date = from_local.strftime("%d.%m.%Y")
    to_date   = to_local.strftime("%d.%m.%Y")
    from_time = from_local.strftime("%H:%M")
    to_time   = to_local.strftime("%H:%M")

    if from_date == to_date:
        return f"{from_date}, {from_time} – {to_time}"
    return f"{from_date} {from_time} – {to_date} {to_time}"


# ---------------------------------------------------------------------------
# Базовые хелперы (существовали ранее)
# ---------------------------------------------------------------------------

def format_phone(raw: Optional[str]) -> str:
    """Форматирует номер телефона в читаемый вид +998 XX XXX XX XX."""
    if not raw:
        return "неизвестен"

    digits = re.sub(r"\D", "", str(raw))

    if len(digits) == 9:
        digits = "998" + digits

    if len(digits) == 12 and digits.startswith("998"):
        cc = digits[0:3]
        op = digits[3:5]
        d1 = digits[5:8]
        d2 = digits[8:10]
        d3 = digits[10:12]
        return f"+{cc} {op} {d1} {d2} {d3}"

    if raw.strip().startswith("+"):
        return raw.strip()
    return f"+{digits}" if digits else raw.strip()


def format_datetime(raw: Any, timezone_str: str = "Asia/Tashkent") -> str:
    """Конвертирует timestamp (UNIX int/str) или ISO-строку в читаемый вид."""
    try:
        tz = ZoneInfo(timezone_str)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    dt: Optional[datetime] = None

    if raw is not None:
        try:
            ts = float(str(raw))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass

        if dt is None and isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz)
            except (ValueError, TypeError):
                pass

    if dt is None:
        dt = datetime.now(tz=timezone.utc)

    dt_local = dt.astimezone(tz)
    return dt_local.strftime("%d.%m.%Y в %H:%M")


def format_wait(seconds: Optional[int]) -> str:
    """Форматирует длительность ожидания в секундах."""
    if seconds is None or seconds < 0:
        return "—"

    if seconds < 60:
        return f"{seconds} сек"

    minutes = seconds // 60
    remainder = seconds % 60

    if remainder == 0:
        return f"{minutes} мин"
    return f"{minutes} мин {remainder} сек"


def build_message(
    caller: Optional[str],
    call_time: Any,
    wait_seconds: Optional[int],
    timezone_str: str = "Asia/Tashkent",
    operator_name: Optional[str] = None,
) -> str:
    """Собирает HTML-текст уведомления о пропущенном звонке."""
    phone_formatted = format_phone(caller)
    time_formatted = format_datetime(call_time, timezone_str)
    wait_formatted = format_wait(wait_seconds)

    op_line = f"Оператор: {operator_name}\n" if operator_name else ""

    return (
        "📞 <b>Пропущенный звонок</b>\n"
        "─────────────────────\n"
        f"Номер:     <code>{phone_formatted}</code>\n"
        f"Время:     {time_formatted}\n"
        f"Ожидание:  {wait_formatted}\n"
        f"{op_line}"
        "─────────────────────\n"
        "☎️ Перезвонить клиенту"
    )


def build_escalation_message(
    caller: Optional[str],
    missed_at: datetime,
    operator_name: Optional[str],
    tracking_id: str,
    timezone_str: str = "Asia/Tashkent",
) -> str:
    """Эскалационное уведомление — не перезвонили через N минут."""
    phone = format_phone(caller)
    time_str = format_datetime(missed_at, timezone_str)
    op = operator_name or "Неизвестно"

    return (
        "🔴 <b>Не перезвонили!</b>\n"
        "─────────────────────\n"
        f"Номер:    <code>{phone}</code>\n"
        f"Время:    {time_str}\n"
        f"Оператор: {op}\n"
        "─────────────────────\n"
        "Нажмите кнопку если перезвонили 👇"
    )


# ---------------------------------------------------------------------------
# Форматтеры отчётов Utel
# ---------------------------------------------------------------------------

def _iter_period_hours(from_utc: datetime, to_utc: datetime, tz_name: str):
    """Генерирует (date_str, hour) для каждого часа в периоде, хронологически."""
    from datetime import timedelta
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    current = (
        from_utc.replace(tzinfo=timezone.utc)
        .astimezone(tz)
        .replace(minute=0, second=0, microsecond=0)
    )
    end = to_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    while current <= end:
        yield current.strftime("%d.%m.%Y"), current.hour
        current += timedelta(hours=1)


def _delta(cur: float, prev: float, unit: str = "", higher_is_better: bool = True) -> str:
    """Форматирует дельту со стрелкой."""
    if prev == 0:
        return f"{cur}{unit} (—)"
    diff = cur - prev
    pct = round(diff / prev * 100, 1)
    arrow = "↑" if diff > 0 else "↓"
    sign = "+" if diff >= 0 else ""
    good = (diff > 0) == higher_is_better
    emoji = "✅" if good else "⚠️"
    return f"{cur}{unit} {emoji} {arrow}{sign}{pct}%"


def format_heatmap(hourly: dict[int, int], tz_name: str = "Asia/Tashkent") -> str:
    """Тепловая карта входящих по часам в виде текста."""
    if not hourly:
        return "  Нет данных"

    max_val = max(hourly.values()) if hourly else 1
    lines = []
    for h in range(24):
        cnt = hourly.get(h, 0)
        bar_len = round(cnt / max_val * 10) if max_val > 0 else 0
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"  {h:02d}:00  {bar}  {cnt}")
    return "\n".join(lines)


def format_utel_period_report(
    stats,  # PeriodStats
    label: str,
    timezone_str: str = "Asia/Tashkent",
) -> str:
    """Отчёт по Utel за период."""
    from app.analytics_utel import PeriodStats
    assert isinstance(stats, PeriodStats)

    peak = stats.peak_hours
    peak_str = ", ".join(f"{h:02d}:00 ({c} зв.)" for h, c in peak) or "—"

    cb_str = (
        f"{stats.callbacks_done}/{stats.callbacks_total}"
        if stats.callbacks_total > 0
        else "—"
    )

    date_str = format_date_range(stats.from_utc, stats.to_utc, timezone_str)

    hourly_lines = ["  <b>Дата; Часы (кол-во)</b>"]
    for d, h in _iter_period_hours(stats.from_utc, stats.to_utc, timezone_str):
        cnt = stats.hourly.get((d, h), 0)
        hourly_lines.append(f"  {d}; {h:02d}:00-{h:02d}:59 ({cnt})")

    # Рабочее/нерабочее время
    work_total = stats.work_incoming + stats.non_work_incoming
    work_pct = round(stats.work_incoming / work_total * 100, 1) if work_total > 0 else 0.0
    non_work_pct = round(stats.non_work_incoming / work_total * 100, 1) if work_total > 0 else 0.0

    # Смены
    shift_lines = []
    for name, count in stats.shift_incoming.items():
        shift_lines.append(f"  {name}: {count}")

    lines = [
        f"📞 <b>Звонки Utel — {label}</b>",
        f"📅 {date_str}",
        "─────────────────────",
        f"Входящих:    {stats.total_incoming}",
        f"Принято:     {stats.total_answered}  ({stats.answer_rate}%)",
        f"Пропущено:   {stats.total_missed}  ({stats.miss_rate}%)",
        f"Исходящих:   {stats.total_outgoing}",
        f"Перезвоны:   {cb_str}",
        "─────────────────────",
        f"<b>Рабочее время (09:00–18:00):</b>",
        f"  В рабочее:    {stats.work_incoming}  ({work_pct}%)",
        f"  Вне рабочего: {stats.non_work_incoming}  ({non_work_pct}%)",
        "<b>По сменам (входящие):</b>",
    ] + shift_lines + [
        "─────────────────────",
        "<b>По часам (входящие):</b>",
    ] + hourly_lines + [
        "─────────────────────",
        "<b>По операторам:</b>",
    ]

    for op in sorted(stats.operators.values(), key=lambda x: x.incoming, reverse=True):
        cb = f"{op.callbacks_done}/{op.callbacks_total}" if op.callbacks_total > 0 else "—"
        lines.append(
            f"  <b>{op.name}</b>: вх {op.incoming} / пр {op.missed} "
            f"({op.answer_rate}%) / исх {op.outgoing} / перезв {cb}"
        )

    return "\n".join(lines)


def format_utel_compare_report(
    stats_cur,  # PeriodStats
    stats_prev,  # PeriodStats
    label_cur: str,
    label_prev: str,
) -> str:
    """Сравнительный отчёт по Utel (текущий vs предыдущий период)."""
    lines = [
        f"📊 <b>Сравнение звонков: {label_cur} vs {label_prev}</b>",
        "─────────────────────────────────────",
        f"Входящих:   {_delta(stats_cur.total_incoming, stats_prev.total_incoming)}",
        f"Принято:    {_delta(stats_cur.total_answered, stats_prev.total_answered)}",
        f"Пропущено:  {_delta(stats_cur.total_missed, stats_prev.total_missed, higher_is_better=False)}",
        f"Исходящих:  {_delta(stats_cur.total_outgoing, stats_prev.total_outgoing)}",
        f"% ответа:   {_delta(stats_cur.answer_rate, stats_prev.answer_rate, unit='%')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Форматтеры AmoCRM
# ---------------------------------------------------------------------------

def format_amocrm_period_report(
    metrics: dict,
    label: str,
    from_utc: Optional[datetime] = None,
    to_utc: Optional[datetime] = None,
    timezone_str: str = "Asia/Tashkent",
) -> str:
    """Отчёт по AmoCRM лидам за период."""
    total = metrics.get("total_leads", 0)
    processed = metrics.get("processed_leads", 0)
    unprocessed = metrics.get("unprocessed_leads", 0)
    conv = metrics.get("conversion_rate", 0.0)

    hourly = metrics.get("hourly", {})

    date_line = ""
    if from_utc and to_utc:
        date_line = f"\n📅 {format_date_range(from_utc, to_utc, timezone_str)}"

    work_h = metrics.get("work_hours", 0)
    non_work_h = metrics.get("non_work_hours", 0)
    shifts = metrics.get("shifts", {})
    work_total = work_h + non_work_h
    work_pct = round(work_h / work_total * 100, 1) if work_total > 0 else 0.0
    non_pct = round(non_work_h / work_total * 100, 1) if work_total > 0 else 0.0

    shift_lines = [f"  {name}: {cnt}" for name, cnt in shifts.items()]

    hourly_lines = ["  <b>Дата; Часы (кол-во)</b>"]
    if from_utc and to_utc:
        for d, h in _iter_period_hours(from_utc, to_utc, timezone_str):
            cnt = hourly.get((d, h), 0)
            hourly_lines.append(f"  {d}; {h:02d}:00-{h:02d}:59 ({cnt})")
    else:
        for (d, h), cnt in sorted(hourly.items()):
            hourly_lines.append(f"  {d}; {h:02d}:00-{h:02d}:59 ({cnt})")

    lines = [
        f"📋 <b>AmoCRM лиды — {label}</b>{date_line}",
        "─────────────────────",
        f"Поступило:     {total}",
        f"Обработано:    {processed}  ({conv}%)",
        f"Не обработано: {unprocessed}",
        "─────────────────────",
        "<b>Рабочее время (09:00–18:00):</b>",
        f"  В рабочее:    {work_h}  ({work_pct}%)",
        f"  Вне рабочего: {non_work_h}  ({non_pct}%)",
        "<b>По сменам:</b>",
    ] + shift_lines + [
        "─────────────────────",
        "<b>По часам:</b>",
    ] + hourly_lines

    return "\n".join(lines)


def format_amocrm_users_report(
    result: dict,
    label: str,
    from_utc: Optional[datetime] = None,
    to_utc: Optional[datetime] = None,
    timezone_str: str = "Asia/Tashkent",
) -> str:
    """Отчёт по сотрудникам AmoCRM за период."""
    date_line = ""
    if from_utc and to_utc:
        date_line = f"\n📅 {format_date_range(from_utc, to_utc, timezone_str)}"

    users = result.get("users", {})
    total_all = result.get("total_leads", 0)

    lines = [
        f"👤 <b>Сотрудники AmoCRM — {label}</b>{date_line}",
        "─────────────────────",
    ]

    if not users:
        lines.append("Нет данных за период")
    else:
        # Сортируем по убыванию лидов
        sorted_users = sorted(users.values(), key=lambda x: x["total"], reverse=True)
        for u in sorted_users:
            name = u["name"]
            total = u["total"]
            processed = u["processed"]
            unprocessed = u["unprocessed"]
            conv = u["conversion_rate"]
            lines.append(
                f"<b>{name}</b>\n"
                f"  Лиды: {total}  |  Обработано: {processed} ({conv}%)  |  Не обраб: {unprocessed}"
            )

    lines += [
        "─────────────────────",
        f"Итого лидов: {total_all}",
    ]
    return "\n".join(lines)


def format_amocrm_compare_report(
    metrics_cur: dict,
    metrics_prev: dict,
    label_cur: str,
    label_prev: str,
    from_cur: Optional[datetime] = None,
    to_cur: Optional[datetime] = None,
    from_prev: Optional[datetime] = None,
    to_prev: Optional[datetime] = None,
    timezone_str: str = "Asia/Tashkent",
) -> str:
    """Сравнительный отчёт AmoCRM."""
    date_cur = f" ({format_date_range(from_cur, to_cur, timezone_str)})" if from_cur and to_cur else ""
    date_prev = f" ({format_date_range(from_prev, to_prev, timezone_str)})" if from_prev and to_prev else ""
    lines = [
        f"📊 <b>Сравнение лидов: {label_cur}{date_cur} vs {label_prev}{date_prev}</b>",
        "─────────────────────────────────────",
        f"Поступило:    {_delta(metrics_cur.get('total_leads', 0), metrics_prev.get('total_leads', 0))}",
        f"Обработано:   {_delta(metrics_cur.get('processed_leads', 0), metrics_prev.get('processed_leads', 0))}",
        f"% обработки:  {_delta(metrics_cur.get('conversion_rate', 0.0), metrics_prev.get('conversion_rate', 0.0), unit='%')}",
    ]
    return "\n".join(lines)

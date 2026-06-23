from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hermes.audit import DealAnalysis
from hermes.llm import get_llm_client
from hermes.skills.loader import call_skill

UTC = timezone.utc
HIGH_VALUE_THRESHOLD = 500_000
INITIAL_STATUS_MARKERS = (
    "нов",
    "неразобран",
    "первич",
    "первый контакт",
    "вход",
    "incoming",
    "lead",
)


@dataclass
class DealError:
    severity: str
    code: str
    message: str
    recommendation: str


def _is_completed(task: dict[str, Any]) -> bool:
    value = task.get("is_completed", task.get("completed", False))
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "completed"}
    return bool(value)


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts, UTC)
        except (OSError, ValueError):
            return None

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    return None


def _open_tasks(tasks: list[dict]) -> list[dict]:
    return [task for task in tasks if not _is_completed(task)]


def _is_overdue(task: dict[str, Any], now: datetime) -> bool:
    complete_till = _parse_timestamp(task.get("complete_till"))
    return complete_till is not None and complete_till < now


def _is_initial_status(status_name: str) -> bool:
    normalized = status_name.lower().strip()
    return any(marker in normalized for marker in INITIAL_STATUS_MARKERS)


def _note_text(note: dict[str, Any]) -> str:
    params = note.get("params") or {}
    return str(
        note.get("text")
        or params.get("text")
        or params.get("comment")
        or note.get("note")
        or ""
    ).strip()


async def _has_unanswered_objection(deal: DealAnalysis, notes: list[dict]) -> bool:
    if not notes:
        return False

    last_note = _note_text(notes[0])
    if not last_note:
        return False

    try:
        result = await call_skill(
            "analyse_conversation",
            {"lead_name": deal.lead_name, "notes_text": last_note},
            {"llm": get_llm_client()},
        )
    except Exception:
        return False

    objections = result.get("client_objections") or []
    return bool(objections)


async def detect_errors(
    deal: DealAnalysis,
    tasks: list[dict] | None = None,
    notes: list[dict] | None = None,
) -> list[DealError]:
    """
    Detect ROP-methodology violations for a single deal.
    Pass tasks=None or notes=None to skip those checks (e.g. in team roll-up).
    """
    errors: list[DealError] = []
    now = datetime.now(UTC)

    if tasks is not None:
        open_tasks = _open_tasks(tasks)
        if not open_tasks:
            errors.append(DealError(
                severity="critical",
                code="no_task",
                message="У сделки нет открытой задачи.",
                recommendation="Поставить следующую задачу по сделке: звонок, сообщение или встреча с конкретным сроком.",
            ))

        if any(_is_overdue(task, now) for task in open_tasks):
            errors.append(DealError(
                severity="critical",
                code="overdue_task",
                message="По сделке есть просроченная задача.",
                recommendation="Закрыть просрочку и сразу назначить новый следующий шаг с понятным дедлайном.",
            ))

    if deal.heat == "hot" and deal.days_inactive > 3:
        errors.append(DealError(
            severity="critical",
            code="stale_hot",
            message="Горячая сделка простаивает больше 3 дней.",
            recommendation="Связаться с клиентом сегодня и зафиксировать конкретное решение или следующий шаг.",
        ))

    if deal.heat == "warm" and deal.days_inactive > 7:
        errors.append(DealError(
            severity="warning",
            code="stale_warm",
            message="Тёплая сделка без активности больше 7 дней.",
            recommendation="Вернуть сделку в работу: уточнить актуальность, потребность и ближайшее действие клиента.",
        ))

    if deal.heat == "cold" and deal.lead_price >= HIGH_VALUE_THRESHOLD:
        errors.append(DealError(
            severity="warning",
            code="cold_high_value",
            message="Высокий чек остыл и может быть потерян.",
            recommendation="Сделать реактивацию с персональным поводом и предложить следующий конкретный шаг.",
        ))

    if _is_initial_status(deal.status_name) and deal.days_inactive > 5:
        errors.append(DealError(
            severity="warning",
            code="stuck_initial",
            message="Сделка застряла в начальном статусе больше 5 дней.",
            recommendation="Квалифицировать клиента и перевести сделку на следующий этап либо закрыть нецелевую заявку.",
        ))

    if notes is not None:
        if await _has_unanswered_objection(deal, notes):
            errors.append(DealError(
                severity="info",
                code="unanswered_objection",
                message="В последней заметке найдено возражение клиента.",
                recommendation="Отработать возражение конкретным ответом и договориться о следующем шаге.",
            ))

        if not notes:
            errors.append(DealError(
                severity="info",
                code="no_contact",
                message="По сделке нет заметок с историей контакта.",
                recommendation="Связаться с клиентом и зафиксировать итог разговора в заметке.",
            ))

    return errors

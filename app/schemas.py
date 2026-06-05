"""
Нормализованная модель webhook-события от Utel.uz.

Поддерживает ДВА формата payload:
  A) Реальный Utel — вложенный: {"time": "...", "data": {"name": "call_saved", "call_history": {...}}}
  B) Плоский (тесты/curl-примеры): {"caller": "...", "status": "...", "direction": "..."}

Для определения поля оператора (extension/dst) включите LOG_LEVEL=DEBUG
и посмотрите "Raw webhook payload" в логах первого реального звонка.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Union

from pydantic import BaseModel, field_validator, model_validator

# Статусы из call_history.status.name которые считаем пропущенными
MISSED_STATUS_NAMES: frozenset[str] = frozenset({
    "not answered",
    "no answer",
    "noanswer",
    "no_answer",
    "missed",
    "unanswered",
    "cancel",
    "busy",
    "abandon",
})

# Номера статусов из call_history.status.number которые считаем пропущенными
MISSED_STATUS_NUMBERS: frozenset[int] = frozenset({2, 3, 4, 5})

# Имена входящих типов
INCOMING_TYPE_NAMES: frozenset[str] = frozenset({
    "incoming", "inbound", "in",
})

# Имена исходящих типов
OUTGOING_TYPE_NAMES: frozenset[str] = frozenset({
    "outgoing", "outbound", "out",
})

# Имена ответных/успешных статусов
ANSWERED_STATUS_NAMES: frozenset[str] = frozenset({
    "answered", "completed", "success", "ok", "connected",
})

# Поля payload Utel, в которых может быть extension оператора (пробуются по порядку)
OPERATOR_FIELD_CANDIDATES: tuple[str, ...] = (
    "dst", "dest", "to", "did", "extension", "agent", "operator", "queue",
)


class UtelWebhook(BaseModel):
    """
    Нормализованная модель входящего webhook от Utel.uz.

    Обрабатывает оба формата:
    A) Вложенный (реальный Utel): data.call_history
    B) Плоский (тесты): caller/status/direction на верхнем уровне
    """

    event_name: Optional[str] = None        # data.name (только в формате A)
    caller: Optional[str] = None            # номер звонящего
    operator_ext: Optional[str] = None      # внутренний номер/extension оператора
    call_time: Optional[Any] = None         # дата/время звонка (строка, UNIX int/float или None)
    wait_seconds: Optional[int] = None      # длительность (сек)
    status_name: Optional[str] = None       # статус в нижнем регистре
    status_number: Optional[int] = None
    type_name: Optional[str] = None         # тип звонка в нижнем регистре
    type_number: Optional[int] = None
    call_id: Optional[str] = None
    # direction: 'in' | 'out' | None — нормализуется из type_name/type_number
    direction: Optional[str] = None
    answered: bool = False

    @model_validator(mode="before")
    @classmethod
    def extract_from_utel(cls, data: Any) -> dict:
        if not isinstance(data, dict):
            return {}

        # ----- Формат A: вложенный (реальный Utel) -----
        raw_data = data.get("data")
        if isinstance(raw_data, dict):
            event_name = raw_data.get("name")
            result: dict = {"event_name": event_name}

            if event_name == "call_saved":
                ch = raw_data.get("call_history") or {}

                result["caller"] = ch.get("src") or ch.get("caller")
                result["call_time"] = ch.get("date_time")
                result["call_id"] = ch.get("call_id") or str(ch.get("id", "")) or None

                duration = ch.get("duration")
                try:
                    result["wait_seconds"] = int(duration) if duration is not None else None
                except (ValueError, TypeError):
                    result["wait_seconds"] = None

                # Статус
                status = ch.get("status") or {}
                if isinstance(status, dict):
                    sn = status.get("name")
                    result["status_name"] = sn.lower().strip() if sn else None
                    result["status_number"] = status.get("number")
                elif isinstance(status, str):
                    result["status_name"] = status.lower().strip()

                # Тип (направление)
                call_type = ch.get("type") or {}
                if isinstance(call_type, dict):
                    tn = call_type.get("name")
                    result["type_name"] = tn.lower().strip() if tn else None
                    result["type_number"] = call_type.get("number")
                elif isinstance(call_type, str):
                    result["type_name"] = call_type.lower().strip()

                # Оператор (extension): пробуем известные поля
                for field in OPERATOR_FIELD_CANDIDATES:
                    val = ch.get(field)
                    if val:
                        result["operator_ext"] = str(val).strip()
                        break

            return result

        # ----- Формат B: плоский (тесты / curl-примеры / fallback) -----
        result = {
            "event_name": "call_saved",  # считаем что это финальное событие
            "caller": data.get("caller") or data.get("src"),
            "call_id": data.get("call_id") or data.get("id"),
        }

        # Время
        result["call_time"] = (
            data.get("call_time")
            or data.get("timestamp")
            or data.get("date_time")
        )

        # Длительность
        wait = data.get("wait") or data.get("duration") or data.get("wait_seconds")
        try:
            result["wait_seconds"] = int(wait) if wait is not None else None
        except (ValueError, TypeError):
            result["wait_seconds"] = None

        # Статус
        sn = data.get("status") or data.get("status_name")
        result["status_name"] = sn.lower().strip() if sn else None
        result["status_number"] = data.get("status_number")

        # Тип / направление
        direction_raw = data.get("direction") or data.get("type") or data.get("type_name")
        if direction_raw:
            result["type_name"] = str(direction_raw).lower().strip()
        else:
            result["type_name"] = None

        # Оператор
        for field in OPERATOR_FIELD_CANDIDATES:
            val = data.get(field)
            if val:
                result["operator_ext"] = str(val).strip()
                break

        return result

    # ---------------------------------------------------------------------------
    # Вычисляемые свойства
    # ---------------------------------------------------------------------------

    @property
    def is_target_event(self) -> bool:
        """True если это событие call_saved."""
        return self.event_name == "call_saved"

    @property
    def is_incoming(self) -> bool:
        """True если звонок входящий."""
        if self.type_name and self.type_name in INCOMING_TYPE_NAMES:
            return True
        if self.type_number == 1:
            return True
        return False

    @property
    def is_outgoing(self) -> bool:
        """True если звонок исходящий."""
        if self.type_name and self.type_name in OUTGOING_TYPE_NAMES:
            return True
        if self.type_number == 2:
            return True
        return False

    @property
    def is_answered_call(self) -> bool:
        """True если звонок был принят (отвечен)."""
        if self.status_name and self.status_name in ANSWERED_STATUS_NAMES:
            return True
        if self.status_number == 1:
            return True
        return False

    @property
    def is_missed(self) -> bool:
        """True если звонок входящий и не был отвечен."""
        if not self.is_incoming:
            return False
        if self.status_name and self.status_name in MISSED_STATUS_NAMES:
            return True
        if self.status_number is not None and self.status_number in MISSED_STATUS_NUMBERS:
            return True
        return False

    @property
    def is_outbound_answered(self) -> bool:
        """True если это исходящий ответный/успешный звонок — вероятный перезвон."""
        return self.is_outgoing and self.is_answered_call

    @property
    def normalized_direction(self) -> str:
        """Возвращает 'in' | 'out' | 'unknown'."""
        if self.is_incoming:
            return "in"
        if self.is_outgoing:
            return "out"
        return "unknown"

    @property
    def call_time_utc(self) -> Optional[datetime]:
        """Пытается распарсить call_time в naive UTC datetime."""
        if self.call_time is None:
            return None
        # Пробуем как UNIX timestamp
        try:
            ts = float(str(self.call_time))
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError, OSError):
            pass
        # Пробуем как ISO-строку
        if isinstance(self.call_time, str):
            try:
                dt = datetime.fromisoformat(self.call_time)
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt  # считаем что уже UTC
            except (ValueError, TypeError):
                pass
        return None

    # Обратная совместимость: старые свойства используются в existing test_webhook.py
    @property
    def status(self) -> Optional[str]:
        return self.status_name

    @property
    def direction(self) -> Optional[str]:
        return self.type_name

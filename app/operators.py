"""
Маппинг внутреннего номера/ext оператора → читаемое имя.

Конфигурируется через переменную окружения OPERATORS_MAP в формате JSON:
    OPERATORS_MAP={"101": "Азиз", "102": "Камола", "103": "Санжар"}

Если переменная не задана или extension не найден — возвращается сам extension
или «Оператор» если extension тоже неизвестен.

Для определения поля extension в payload Utel — включите LOG_LEVEL=DEBUG и
посмотрите "Raw webhook payload" в логах первого реального звонка.
Возможные имена поля: dst, dest, to, did, extension, agent, operator, queue.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_MAP: dict[str, str] = {}


def _load_map() -> dict[str, str]:
    """Загружает маппинг из settings.operators_map (JSON-строка или пустая строка)."""
    raw = getattr(settings, "operators_map", "") or ""
    if not raw:
        return {}
    try:
        return {str(k): str(v) for k, v in json.loads(raw).items()}
    except Exception as exc:
        logger.warning("Could not parse OPERATORS_MAP: %s", exc)
        return {}


def get_operator_name(ext: Optional[str]) -> Optional[str]:
    """
    Возвращает имя оператора по extension/dst.

    Если extension есть но маппинга нет — возвращает сам extension
    (например «101» лучше, чем ничего).
    """
    global _MAP
    if not _MAP:
        _MAP = _load_map()

    if not ext:
        return None

    return _MAP.get(str(ext).strip(), str(ext).strip()) or None


def list_operators() -> dict[str, str]:
    """Возвращает словарь ext → имя из OPERATORS_MAP (для UI-пикеров)."""
    global _MAP
    if not _MAP:
        _MAP = _load_map()
    return dict(_MAP)


def reload() -> None:
    """Перегрузить маппинг из настроек (вызывать при горячем обновлении)."""
    global _MAP
    _MAP = _load_map()


# Поля payload Utel, в которых может быть extension оператора.
# extract_from_utel пробует их по порядку.
OPERATOR_FIELD_CANDIDATES: list[str] = [
    "dst", "dest", "to", "did", "extension", "agent", "operator", "queue",
]

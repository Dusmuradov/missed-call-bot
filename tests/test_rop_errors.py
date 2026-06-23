from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_PLACEHOLDER")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-12345")
os.environ.setdefault("WEBHOOK_SECRET_HEADER", "X-Webhook-Secret")
os.environ.setdefault("TIMEZONE", "Asia/Tashkent")

from hermes.audit import DealAnalysis
from hermes.rop_errors import detect_errors


def _deal(
    *,
    heat: str = "warm",
    days_inactive: int = 1,
    lead_price: float = 100_000,
    status_name: str = "Переговоры",
) -> DealAnalysis:
    return DealAnalysis(
        lead_id=123,
        lead_name="Тестовая сделка",
        contact_name=None,
        heat=heat,
        score=5,
        reason="fixture",
        next_step="",
        days_inactive=days_inactive,
        lead_price=lead_price,
        status_name=status_name,
        amocrm_link="https://example.amocrm.ru/leads/detail/123",
    )


@pytest.mark.asyncio
async def test_no_task_returns_critical_error():
    errors = await detect_errors(_deal(), tasks=[], notes=None)

    assert any(
        error.code == "no_task" and error.severity == "critical"
        for error in errors
    )


@pytest.mark.asyncio
async def test_overdue_task_returns_critical_error():
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    errors = await detect_errors(
        _deal(),
        tasks=[{"is_completed": False, "complete_till": int(yesterday.timestamp())}],
        notes=None,
    )

    assert any(
        error.code == "overdue_task" and error.severity == "critical"
        for error in errors
    )


@pytest.mark.asyncio
async def test_cold_high_value_returns_warning_error():
    errors = await detect_errors(
        _deal(heat="cold", lead_price=500_000),
        tasks=None,
        notes=None,
    )

    assert any(
        error.code == "cold_high_value" and error.severity == "warning"
        for error in errors
    )


@pytest.mark.asyncio
async def test_no_contact_returns_info_error():
    errors = await detect_errors(_deal(), tasks=None, notes=[])

    assert any(
        error.code == "no_contact" and error.severity == "info"
        for error in errors
    )

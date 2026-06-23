from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_PLACEHOLDER")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-12345")
os.environ.setdefault("WEBHOOK_SECRET_HEADER", "X-Webhook-Secret")
os.environ.setdefault("TIMEZONE", "Asia/Tashkent")

from app.amocrm.client import AmocrmClient


@pytest.mark.asyncio
async def test_create_task_skips_duplicate_for_same_text_and_day():
    client = AmocrmClient("example", "token")
    complete_till = 1_800_000_000
    existing = {
        "id": 42,
        "text": "Позвонить клиенту",
        "complete_till": complete_till,
        "is_completed": False,
    }
    client.get_lead_tasks = AsyncMock(return_value=[existing])
    client._post = AsyncMock()

    result = await client.create_task(
        lead_id=123,
        text="  Позвонить   клиенту  ",
        complete_till_ts=complete_till,
        responsible_user_id=777,
    )

    assert result == {"created": False, "duplicate": True, "task": existing}
    client._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_posts_new_lead_task_payload():
    client = AmocrmClient("example", "token")
    client.get_lead_tasks = AsyncMock(return_value=[])
    client._post = AsyncMock(return_value={
        "_embedded": {"tasks": [{"id": 1001, "request_id": "rop-123-test"}]},
    })

    result = await client.create_task(
        lead_id=123,
        text="Позвонить клиенту",
        complete_till_ts=1_800_000_000,
        responsible_user_id=777,
    )

    assert result["created"] is True
    assert result["duplicate"] is False
    client._post.assert_awaited_once()
    path, payload = client._post.await_args.args
    assert path == "/tasks"
    assert payload[0]["entity_id"] == 123
    assert payload[0]["entity_type"] == "leads"
    assert payload[0]["text"] == "Позвонить клиенту"
    assert payload[0]["complete_till"] == 1_800_000_000
    assert payload[0]["responsible_user_id"] == 777
    assert payload[0]["task_type_id"] == 1
    assert payload[0]["request_id"].startswith("rop-123-")

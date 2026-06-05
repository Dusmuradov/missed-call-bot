"""Тесты эндпоинта /webhook/utel через FastAPI TestClient.

Telegram-бот, БД и планировщик замоканы — реальных HTTP-запросов нет.
"""
from __future__ import annotations

import os
import sys

import pytest

# Устанавливаем переменные окружения ДО импорта app (pydantic-settings читает при импорте)
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_PLACEHOLDER")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-12345")
os.environ.setdefault("WEBHOOK_SECRET_HEADER", "X-Webhook-Secret")
os.environ.setdefault("TIMEZONE", "Asia/Tashkent")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

SECRET = "test-secret-12345"
HEADER = "X-Webhook-Secret"

# Плоский payload (используется в тестах — поддерживается через fallback-парсер)
MISSED_PAYLOAD = {
    "caller": "998901234567",
    "timestamp": 1749038400,
    "wait": 45,
    "status": "no answer",
    "direction": "inbound",
    "call_id": "abc-001",
}

# Вложенный payload (реальный Utel)
MISSED_PAYLOAD_NESTED = {
    "time": "2026-06-04T10:00:00",
    "data": {
        "name": "call_saved",
        "call_history": {
            "src": "998901234567",
            "dst": "101",
            "date_time": "2026-06-04T10:00:00",
            "call_id": "nested-001",
            "duration": 30,
            "status": {"name": "no answer", "number": 3},
            "type": {"name": "incoming", "number": 1},
        },
    },
}

OUTBOUND_PAYLOAD = {
    "caller": "998901234567",
    "call_id": "out-001",
    "status": "answered",
    "direction": "outbound",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_lifespan_services(monkeypatch):
    """
    Мокаем все внешние сервисы lifespan:
    - init_db / close_db → no-op async
    - start_scheduler / stop_scheduler → no-op
    - start_polling / stop_polling → no-op async
    - close_bot → no-op async
    """
    monkeypatch.setattr("app.main.start_polling", AsyncMock())
    monkeypatch.setattr("app.main.stop_polling", AsyncMock())
    monkeypatch.setattr("app.main.close_bot", AsyncMock())

    from unittest.mock import patch as _patch
    import app.db as db_module
    import app.scheduler as sched_module

    monkeypatch.setattr(db_module, "init_db", AsyncMock())
    monkeypatch.setattr(db_module, "close_db", AsyncMock())
    monkeypatch.setattr(sched_module, "start_scheduler", MagicMock())
    monkeypatch.setattr(sched_module, "stop_scheduler", MagicMock())


@pytest.fixture(autouse=True)
def mock_db_session(monkeypatch):
    """
    Мокаем get_session и все repository-функции на уровне исходных модулей —
    реальной БД нет в тестах.
    """
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()

    @asynccontextmanager
    async def fake_get_session():
        yield mock_session

    # Мокаем get_session в исходном модуле db (импортируется внутри функций main)
    import app.db as db_module
    monkeypatch.setattr(db_module, "get_session", fake_get_session)

    # Мокаем repository-функции в модуле repository
    import app.repository as repo_module
    monkeypatch.setattr(repo_module, "save_call", AsyncMock(return_value=MagicMock(id="test-id")))
    monkeypatch.setattr(repo_module, "create_missed_tracking", AsyncMock())
    monkeypatch.setattr(repo_module, "find_open_missed", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo_module, "mark_called_back", AsyncMock())
    monkeypatch.setattr(repo_module, "get_tracking_by_id", AsyncMock(return_value=MagicMock(id="test-id")))


@pytest.fixture(autouse=True)
def mock_send(monkeypatch):
    """Мокаем send_notification, чтобы не дёргать реальный Telegram."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.main.send_notification", mock)
    return mock


@pytest.fixture()
def client(mock_lifespan_services, mock_db_session, mock_send):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def test_missing_secret_returns_403(client):
    resp = client.post("/webhook/utel", json=MISSED_PAYLOAD)
    assert resp.status_code == 403


def test_wrong_secret_returns_403(client):
    resp = client.post(
        "/webhook/utel",
        json=MISSED_PAYLOAD,
        headers={HEADER: "wrong-secret"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Missed call (плоский payload) → send notification
# ---------------------------------------------------------------------------

def test_missed_call_sends_notification(client, mock_send):
    resp = client.post(
        "/webhook/utel",
        json=MISSED_PAYLOAD,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["sent"] is True
    mock_send.assert_awaited_once()
    # Проверяем что текст содержит номер
    text_arg = mock_send.call_args[0][0]
    assert "+998 90 123 45 67" in text_arg


# ---------------------------------------------------------------------------
# Missed call (вложенный payload — реальный Utel)
# ---------------------------------------------------------------------------

def test_missed_call_nested_payload(client, mock_send):
    resp = client.post(
        "/webhook/utel",
        json=MISSED_PAYLOAD_NESTED,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["sent"] is True
    text_arg = mock_send.call_args[0][0]
    assert "+998 90 123 45 67" in text_arg


# ---------------------------------------------------------------------------
# Non-missed call → skipped (не is_missed — просто записывается в БД)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["answered", "ANSWERED", "completed", "success"])
def test_answered_call_recorded(client, mock_send, status):
    """Отвеченные входящие звонки записываются в БД, но уведомления нет."""
    payload = {**MISSED_PAYLOAD, "status": status, "call_id": f"skip-{status}"}
    resp = client.post(
        "/webhook/utel",
        json=payload,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Входящий отвеченный — не пропущенный → записан без уведомления
    assert body.get("ok") is True
    mock_send.assert_not_awaited()


def test_outbound_call_recorded(client, mock_send):
    """Исходящий звонок записывается и проверяется матчинг перезвона."""
    resp = client.post(
        "/webhook/utel",
        json=OUTBOUND_PAYLOAD,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    mock_send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Unknown / empty event → skipped
# ---------------------------------------------------------------------------

def test_unknown_event_skipped(client, mock_send):
    """Событие с неизвестным именем (не call_saved) пропускается."""
    payload = {"data": {"name": "call_started", "call": {}}}
    resp = client.post(
        "/webhook/utel",
        json=payload,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["skipped"] is True
    assert resp.json()["reason"] == "not_target_event"
    mock_send.assert_not_awaited()


def test_unknown_status_still_processed(client, mock_send):
    """Неизвестный статус без caller — обрабатывается gracefully."""
    payload = {"caller": "998901234567", "call_id": "unknown-001"}
    resp = client.post(
        "/webhook/utel",
        json=payload,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    # Либо ok (если определилось как пропущенное) либо записано
    assert "ok" in resp.json()


def test_missing_caller_and_time(client, mock_send):
    """Отсутствующие поля не приводят к исключению."""
    payload = {"status": "missed", "call_id": "missing-fields-001"}
    resp = client.post(
        "/webhook/utel",
        json=payload,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    if body.get("ok"):
        # Если был послан — проверяем graceful fallback текста
        if mock_send.called:
            text_arg = mock_send.call_args[0][0]
            assert "неизвестен" in text_arg


# ---------------------------------------------------------------------------
# Deduplication (через БД mock — save_call возвращает None при дубле)
# ---------------------------------------------------------------------------

def test_duplicate_call_id_skipped(client, mock_send, monkeypatch):
    """Второй вызов с тем же call_id возвращает reason=duplicate."""
    call_count = 0

    async def fake_save(session, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(id="test-id")  # первый — новый
        return None  # второй — дубль

    import app.repository as repo_module
    monkeypatch.setattr(repo_module, "save_call", fake_save)

    payload = {**MISSED_PAYLOAD, "call_id": "dup-001"}

    resp1 = client.post("/webhook/utel", json=payload, headers={HEADER: SECRET})
    assert resp1.json()["ok"] is True

    resp2 = client.post("/webhook/utel", json=payload, headers={HEADER: SECRET})
    assert resp2.json()["skipped"] is True
    assert resp2.json()["reason"] == "duplicate"
    assert mock_send.await_count == 1


# ---------------------------------------------------------------------------
# Various missed status spellings (плоский payload)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,call_id", [
    ("noanswer", "s1"),
    ("no_answer", "s2"),
    ("missed", "s3"),
    ("cancel", "s4"),
    ("abandon", "s5"),
    ("busy", "s6"),
])
def test_missed_status_variants(client, mock_send, status, call_id):
    payload = {**MISSED_PAYLOAD, "status": status, "call_id": call_id}
    resp = client.post("/webhook/utel", json=payload, headers={HEADER: SECRET})
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


# ---------------------------------------------------------------------------
# Callback matching (outbound call auto-matches missed tracking)
# ---------------------------------------------------------------------------

def test_outbound_call_matches_missed(client, mock_send, monkeypatch):
    """Исходящий звонок автоматически закрывает открытый missed_tracking."""
    import app.repository as repo_module
    mock_tracking = MagicMock(id="tracking-123")
    monkeypatch.setattr(repo_module, "find_open_missed", AsyncMock(return_value=[mock_tracking]))
    monkeypatch.setattr(repo_module, "get_tracking_by_id", AsyncMock(return_value=mock_tracking))

    resp = client.post(
        "/webhook/utel",
        json=OUTBOUND_PAYLOAD,
        headers={HEADER: SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("type") == "callback_matched"
    assert body.get("count") == 1

"""Юнит-тесты форматтеров. Не требуют сети или Telegram."""
from __future__ import annotations

import pytest
from app.formatting import format_phone, format_datetime, format_wait, build_message


# ---------------------------------------------------------------------------
# format_phone
# ---------------------------------------------------------------------------

class TestFormatPhone:
    def test_12_digit_uzbek(self):
        assert format_phone("998901234567") == "+998 90 123 45 67"

    def test_12_digit_with_plus(self):
        assert format_phone("+998901234567") == "+998 90 123 45 67"

    def test_9_digit_local(self):
        assert format_phone("901234567") == "+998 90 123 45 67"

    def test_with_dashes(self):
        assert format_phone("+998-90-123-45-67") == "+998 90 123 45 67"

    def test_with_spaces(self):
        assert format_phone("+998 90 123 45 67") == "+998 90 123 45 67"

    def test_none(self):
        assert format_phone(None) == "неизвестен"

    def test_empty_string(self):
        assert format_phone("") == "неизвестен"

    def test_unknown_format(self):
        # Для неузбекских номеров не ломаемся
        result = format_phone("+12025551234")
        assert result.startswith("+")


# ---------------------------------------------------------------------------
# format_wait
# ---------------------------------------------------------------------------

class TestFormatWait:
    def test_none(self):
        assert format_wait(None) == "—"

    def test_negative(self):
        assert format_wait(-5) == "—"

    def test_zero(self):
        assert format_wait(0) == "0 сек"

    def test_less_than_minute(self):
        assert format_wait(45) == "45 сек"

    def test_exactly_minute(self):
        assert format_wait(60) == "1 мин"

    def test_two_minutes(self):
        assert format_wait(120) == "2 мин"

    def test_minutes_and_seconds(self):
        assert format_wait(90) == "1 мин 30 сек"

    def test_large(self):
        assert format_wait(185) == "3 мин 5 сек"


# ---------------------------------------------------------------------------
# format_datetime
# ---------------------------------------------------------------------------

class TestFormatDatetime:
    def test_unix_timestamp_int(self):
        # 2026-06-04 12:00:00 UTC → 17:00 Asia/Tashkent (UTC+5)
        ts = 1780574400  # 2026-06-04 12:00:00 UTC
        result = format_datetime(ts, "Asia/Tashkent")
        assert "04.06.2026" in result
        assert "17:00" in result

    def test_unix_timestamp_string(self):
        ts = "1780574400"  # 2026-06-04 12:00:00 UTC
        result = format_datetime(ts, "Asia/Tashkent")
        assert "04.06.2026" in result

    def test_iso_string_with_tz(self):
        result = format_datetime("2026-06-04T12:00:00+00:00", "Asia/Tashkent")
        assert "04.06.2026" in result
        assert "17:00" in result

    def test_iso_string_without_tz(self):
        # Без TZ считается как local (Asia/Tashkent) и выводится как есть
        result = format_datetime("2026-06-04T17:00:00", "Asia/Tashkent")
        assert "04.06.2026" in result
        assert "17:00" in result

    def test_none_returns_current_time(self):
        # Должен вернуть что-то, не упасть
        result = format_datetime(None, "Asia/Tashkent")
        assert "в" in result

    def test_invalid_value_returns_current_time(self):
        result = format_datetime("not-a-date", "Asia/Tashkent")
        assert "в" in result


# ---------------------------------------------------------------------------
# build_message
# ---------------------------------------------------------------------------

class TestBuildMessage:
    def test_full_message(self):
        msg = build_message(
            caller="998901234567",
            call_time=1749038400,
            wait_seconds=45,
            timezone_str="Asia/Tashkent",
        )
        assert "Пропущенный звонок" in msg
        assert "+998 90 123 45 67" in msg
        assert "45 сек" in msg
        assert "<code>" in msg
        assert "Перезвонить" in msg

    def test_missing_fields(self):
        msg = build_message(caller=None, call_time=None, wait_seconds=None)
        assert "неизвестен" in msg
        assert "—" in msg

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)


def verify_webhook_secret(request: Request) -> None:
    """FastAPI dependency: validates the secret token sent by the telephony provider.

    If WEBHOOK_SECRET is empty (provider doesn't support secrets), skips the check.
    Uses hmac.compare_digest for constant-time comparison (timing-attack safe).
    """
    if not settings.webhook_secret:
        return

    provided = request.headers.get(settings.webhook_secret_header, "")
    if not provided:
        logger.warning(
            "Webhook request missing secret header '%s' from %s",
            settings.webhook_secret_header,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing webhook secret header.",
        )

    if not hmac.compare_digest(provided.encode(), settings.webhook_secret.encode()):
        logger.warning(
            "Webhook secret mismatch from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret.",
        )

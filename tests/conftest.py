"""Shared fixtures. All secrets here are fake."""

from __future__ import annotations

from collections.abc import Generator
import logging

import pytest

from custom_components.mcp_log_redact.redact import RedactingHandler, Redactor, install, uninstall

FAKE_SECRET_PATH = "/private_FAKEfakeFAKE0123456789"
FAKE_WEBHOOK_ID = "FAKEwebhookIdFAKE0123456789abcdef"
FAKE_CUSTOM_PATH = "/my-fake-custom-secret-path"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components from this repo."""


@pytest.fixture
def redactor() -> Redactor:
    """A redactor with no known values."""
    return Redactor()


@pytest.fixture
def installed(redactor: Redactor) -> Generator[RedactingHandler]:
    """Install a redacting handler first on root for the test, then remove it."""
    handler = RedactingHandler(redactor)
    install(handler)
    yield handler
    uninstall(handler)


def access_record(
    path: str,
    *,
    method: str = "POST",
    status: int = 200,
    client: str = "127.0.0.1:44312",
) -> logging.LogRecord:
    """Build a record exactly like uvicorn's access logger does."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(client, method, path, "1.1", status),
        exc_info=None,
    )

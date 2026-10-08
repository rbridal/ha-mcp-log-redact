"""Diagnostics: counters only, never the secret values."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import LogRedactConfigEntry
from .const import SCOPED_LOGGERS
from .redact import is_first


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LogRedactConfigEntry
) -> dict[str, Any]:
    """Return handler state and counters."""
    data = entry.runtime_data
    return {
        "handler_first_on_root": is_first(data.handler),
        "records_redacted": data.redactor.records_redacted,
        "known_values_loaded": data.redactor.known_count,
        "scoped_loggers": list(SCOPED_LOGGERS),
    }

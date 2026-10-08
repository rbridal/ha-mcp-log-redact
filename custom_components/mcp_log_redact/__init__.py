"""HA-MCP Log Redact: keep HA-MCP secret paths and webhook ids out of the log."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import HA_MCP_DATA_SECRET_PATH, HA_MCP_DATA_WEBHOOK_ID, HA_MCP_DOMAIN
from .redact import RedactingHandler, Redactor, install, uninstall

_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeData:
    """What a loaded entry owns."""

    redactor: Redactor
    handler: RedactingHandler
    unsubs: list[Any] = field(default_factory=list)


type LogRedactConfigEntry = ConfigEntry[RuntimeData]


def _ha_mcp_secret_values(hass: HomeAssistant) -> list[str]:
    """Secret path and webhook id of every HA-MCP entry (memory only, never logged)."""
    values: list[str] = []
    for entry in hass.config_entries.async_entries(HA_MCP_DOMAIN):
        for key in (HA_MCP_DATA_SECRET_PATH, HA_MCP_DATA_WEBHOOK_ID):
            value = entry.data.get(key)
            if isinstance(value, str):
                values.append(value)
    return values


async def async_setup_entry(hass: HomeAssistant, entry: LogRedactConfigEntry) -> bool:
    """Install the redacting root handler."""
    redactor = Redactor()
    redactor.set_known_values(_ha_mcp_secret_values(hass))
    handler = RedactingHandler(redactor)
    install(handler)
    data = RuntimeData(redactor=redactor, handler=handler)
    entry.runtime_data = data

    @callback
    def _entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        if changed.domain == HA_MCP_DOMAIN:
            # Secrets regenerated, overridden, or the entry added/removed.
            redactor.set_known_values(_ha_mcp_secret_values(hass))
            install(handler)

    data.unsubs.append(async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed))

    @callback
    def _started(_event: Event | None = None) -> None:
        # Anything that rebuilt the root handler list during startup cannot push
        # us behind a real handler: move back to the front.
        install(handler)

    if hass.state is CoreState.running:
        _started()
    else:
        data.unsubs.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started))

    _LOGGER.info(
        "Redacting HA-MCP secret paths and webhook ids in log records (%d known value(s))",
        redactor.known_count,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LogRedactConfigEntry) -> bool:
    """Remove the handler and listeners."""
    data = entry.runtime_data
    for unsub in data.unsubs:
        unsub()
    data.unsubs.clear()
    uninstall(data.handler)
    return True

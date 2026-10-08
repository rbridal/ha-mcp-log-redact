"""Integration tests: config flow, setup/unload, runtime secret refresh, diagnostics."""

from __future__ import annotations

import logging

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_log_redact.const import DOMAIN, TITLE
from custom_components.mcp_log_redact.diagnostics import async_get_config_entry_diagnostics
from custom_components.mcp_log_redact.redact import is_first

from .conftest import FAKE_CUSTOM_PATH, FAKE_SECRET_PATH, FAKE_WEBHOOK_ID

FMT = '%s - "%s %s HTTP/%s" %d'


def _ha_mcp_entry(hass: HomeAssistant, **data: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain="ha_mcp_tools", title="HA-MCP Server", data=data)
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_single_instance(hass: HomeAssistant) -> None:
    """One confirm step creates the entry; a second flow aborts."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {}
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_redacts_and_unload_removes(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Setup puts the handler first and redacts; unload removes it."""
    caplog.set_level(logging.INFO)
    _ha_mcp_entry(hass, secret_path=FAKE_SECRET_PATH, webhook_id=FAKE_WEBHOOK_ID)
    entry = await _setup(hass)
    handler = entry.runtime_data.handler
    assert entry.state is ConfigEntryState.LOADED
    assert is_first(handler)
    assert entry.runtime_data.redactor.known_count == 2

    logging.getLogger("uvicorn.access").info(
        FMT, "127.0.0.1:5", "POST", FAKE_SECRET_PATH, "1.1", 200
    )
    logging.getLogger("homeassistant.components.webhook").info(
        "Received message for unregistered webhook %s from %s", FAKE_WEBHOOK_ID, "cloud"
    )
    assert FAKE_SECRET_PATH not in caplog.text
    assert FAKE_WEBHOOK_ID not in caplog.text
    assert '127.0.0.1:5 - "POST /private_<redacted> HTTP/1.1" 200' in caplog.text

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert handler not in logging.getLogger().handlers
    caplog.clear()
    logging.getLogger("uvicorn.access").info(
        FMT, "127.0.0.1:5", "POST", "/private_AFTERunload", "1.1", 200
    )
    assert "/private_AFTERunload" in caplog.text


async def test_refreshes_when_ha_mcp_secrets_change(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A secret path override saved on HA-MCP is picked up without a reload."""
    caplog.set_level(logging.INFO)
    ha_mcp = _ha_mcp_entry(hass, secret_path=FAKE_SECRET_PATH, webhook_id=FAKE_WEBHOOK_ID)
    entry = await _setup(hass)

    hass.config_entries.async_update_entry(
        ha_mcp, data={"secret_path": FAKE_CUSTOM_PATH, "webhook_id": FAKE_WEBHOOK_ID}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.redactor.known_count == 2

    logging.getLogger("uvicorn.access").info(
        FMT, "127.0.0.1:6", "POST", FAKE_CUSTOM_PATH, "1.1", 200
    )
    assert FAKE_CUSTOM_PATH not in caplog.text
    assert '"POST /<redacted> HTTP/1.1" 200' in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)


async def test_works_without_ha_mcp(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """With no HA-MCP entry the generic patterns still apply."""
    caplog.set_level(logging.INFO)
    entry = await _setup(hass)
    assert entry.runtime_data.redactor.known_count == 0
    logging.getLogger("uvicorn.access").info(
        FMT, "127.0.0.1:7", "POST", FAKE_SECRET_PATH, "1.1", 200
    )
    assert FAKE_SECRET_PATH not in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)


async def test_moves_back_first_on_started(hass: HomeAssistant) -> None:
    """If something got in front of us during startup, HA started puts us back first."""
    hass.set_state(CoreState.starting)
    entry = await _setup(hass)
    handler = entry.runtime_data.handler
    intruder = logging.NullHandler()
    root = logging.getLogger()
    root.handlers.insert(0, intruder)
    try:
        assert not is_first(handler)
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        assert is_first(handler)
    finally:
        root.removeHandler(intruder)
        await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_never_contain_secrets(hass: HomeAssistant) -> None:
    """Diagnostics expose counters only."""
    _ha_mcp_entry(hass, secret_path=FAKE_SECRET_PATH, webhook_id=FAKE_WEBHOOK_ID)
    entry = await _setup(hass)
    logging.getLogger("uvicorn.access").warning(FMT, "c", "POST", FAKE_SECRET_PATH, "1.1", 500)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["handler_first_on_root"] is True
    assert diag["known_values_loaded"] == 2
    assert diag["records_redacted"] >= 1
    text = repr(diag)
    assert FAKE_SECRET_PATH not in text
    assert FAKE_WEBHOOK_ID not in text
    await hass.config_entries.async_unload(entry.entry_id)

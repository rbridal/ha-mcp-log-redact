"""Constants for HA-MCP Log Redact."""

from __future__ import annotations

DOMAIN = "mcp_log_redact"
TITLE = "HA-MCP Log Redact"

# The HA-MCP custom component whose secrets we learn at runtime (values stay in
# memory only and are never logged).
HA_MCP_DOMAIN = "ha_mcp_tools"
HA_MCP_DATA_SECRET_PATH = "secret_path"
HA_MCP_DATA_WEBHOOK_ID = "webhook_id"

# Loggers whose records also get the deeper pass: known-value matching, full
# message rendering for non-string args, and pre-rendered tracebacks.
SCOPED_LOGGERS: tuple[str, ...] = (
    "uvicorn",
    "custom_components.ha_mcp_tools",
    "ha_mcp",
    "mcp",
    "fastmcp",
    "homeassistant.components.webhook",
)

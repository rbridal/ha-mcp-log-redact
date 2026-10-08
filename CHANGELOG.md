# Changelog

## 0.1.0 (2026-10-07)

First release.

- Redacts `/private_…` secret paths and `/api/webhook/<id>` URLs in every new
  Home Assistant log record; uvicorn access lines keep client, method, status.
- Learns HA-MCP's actual secret path and webhook id from its config entry at
  runtime (memory only) and re-reads them when that entry changes.
- Implemented as a no-op handler at the front of the root logger, so it runs
  after all logger-level filters (ha-mcp's `ProbeAccessLogFilter` keeps
  working) and covers child loggers.
- Config flow with no options, single instance. Diagnostics show counters only.

# HA-MCP Log Redact

A tiny Home Assistant integration that keeps the
[HA-MCP](https://github.com/homeassistant-ai/ha-mcp) secret URL out of your
Home Assistant log, while keeping the log lines.

```text
before  INFO (ha-mcp-server) [uvicorn.access] 127.0.0.1:44312 - "POST /private_XXXXXXXXXXXXXXXXXXXXXX HTTP/1.1" 200
after   INFO (ha-mcp-server) [uvicorn.access] 127.0.0.1:44312 - "POST /private_<redacted> HTTP/1.1" 200
```

## Why

The HA-MCP custom component runs its MCP server inside Home Assistant. It
authenticates by URL path: whoever knows `/private_…` (and can reach the
port) can drive Home Assistant through MCP. The server's uvicorn access log
inherits Home Assistant's logging, so with `logger: default: info` every MCP
request writes that path into the log, about 2,000 lines an hour while an
agent is busy. The startup banner also prints the full webhook URLs and the
direct-access URL.

That's fine while the log stays on the box. It isn't fine the day someone
pastes "debug logs" into a forum post or a GitHub issue, which has happened.

Turning `uvicorn.access` down to `warning` works, but you lose the access
lines. This integration keeps every line and rewrites only the secret parts.

## What it redacts

In every new log record:

| Pattern | Becomes |
|---|---|
| `/private_<letters, digits, _ ->` | `/private_<redacted>` |
| `/api/webhook/<id>` | `/api/webhook/<redacted>` |
| HA-MCP's actual secret path, if you set a custom override (e.g. `/my-path`) | `/<redacted>` |
| HA-MCP's actual webhook id appearing on its own | `<redacted>` |

The real secret path and webhook id are read from the HA-MCP config entry
(`ha_mcp_tools`) at runtime and kept in memory only. They are re-read when that
entry changes (regenerate secrets, overrides), and never logged or exposed in
diagnostics.

Access lines keep everything else: client, method, query string, HTTP version
and status. The record keeps its shape: uvicorn's args stay a 5-tuple with only
the path rewritten.

The two URL patterns apply to records from any logger. Exact-value matching,
rendering of non-string arguments, and traceback redaction run only for
HA-MCP-related loggers (`uvicorn`, `custom_components.ha_mcp_tools`, `ha_mcp`,
`mcp`, `fastmcp`, `homeassistant.components.webhook`) to keep the cost down.

## How it works

It adds one handler at the **front of the root logger**. That handler never
writes anything; it only rewrites the record in place. All the real handlers
(Home Assistant's log queue, `system_log`, the journal) come after it, so they
only ever see the redacted record.

Why a root handler and not a `logging.Filter` on `uvicorn.access`:

- A logger filter only sees records logged directly on that logger, not ones
  from child loggers. A filter on `custom_components.ha_mcp_tools` would miss
  `custom_components.ha_mcp_tools.embedded_setup`, which logs the banner.
- Logger filters run in the order they were added. ha-mcp's
  `ProbeAccessLogFilter` (used by its CLI and add-on launchers) compares the
  raw path with the secret path to drop by-design `GET … 405` probe lines. A
  redacting filter that ran first would break that comparison. Root handlers run
  after **all** logger-level filters, whatever order they were added in. The
  tests cover both orders, plus a test showing a logger filter would break the
  probe.

If anything gets in front of it during startup, it moves back to the front
when Home Assistant reports `started`.

## Limits

- Only lines logged **after** the integration loads are redacted. On a restart,
  a few HA-MCP startup lines can land before it loads. Pair it with
  `custom_components.ha_mcp_tools.embedded_setup: warning` (below) to keep the
  banner out entirely.
- `system_log` (Settings → System → Logs, "show full logs") builds tracebacks
  from the exception object itself. A secret inside an exception message from
  HA-MCP may still appear there. The text log and journal get the redacted
  traceback.
- It covers Home Assistant's own process: the in-process HA-MCP server from the
  HA-MCP custom component. The separate HA-MCP **add-on** runs in its own
  container with its own log, which this can't reach.
- It doesn't touch log lines already on disk.

## Install

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/rbridal/ha-mcp-log-redact`, category **Integration**.
2. Download **HA-MCP Log Redact**, then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *HA-MCP Log Redact*
   → Submit. There's nothing to configure, and only one entry is allowed.

Recommended alongside it, in `configuration.yaml`:

```yaml
logger:
  logs:
    # Startup banner with the connect URLs. They're still on HA-MCP's Configure screen.
    custom_components.ha_mcp_tools.embedded_setup: warning
```

## Verify

- Settings → System → Logs → *Show raw logs*, or `ha core logs`: access lines
  now read `"POST /private_<redacted> HTTP/1.1" 200`.
- Settings → Devices & services → HA-MCP Log Redact → ⋮ → **Download
  diagnostics**: `handler_first_on_root: true`, `records_redacted` goes up as
  MCP requests come in, and `known_values_loaded` is 2 with HA-MCP set up. No
  secret values are included.

To remove: delete the entry. The handler is removed immediately, with no restart.

## Upstream

The real fix belongs in ha-mcp itself: a redacting filter in
`src/ha_mcp/log_filters.py`, registered on `uvicorn.access` from
`install_sdk_log_filters()` (which every launcher already calls). It needs to
run after `ProbeAccessLogFilter`. ha-mcp already treats the path as a
credential: `SECURITY.md`, the `[REDACTED_SECRET_PATH]` redaction in
`bug_report_templates.py`, and issue
[#2236](https://github.com/homeassistant-ai/ha-mcp/issues/2236). The plan is to
propose it upstream with this integration as the working reference. Once
ha-mcp ships it, remove this integration.

## Development

```bash
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

Tests use fake secrets only.

## License

MIT

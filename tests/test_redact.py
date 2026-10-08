"""Unit tests for the redaction core (no Home Assistant instance needed)."""

from __future__ import annotations

import logging

import pytest

from custom_components.mcp_log_redact.redact import (
    RedactingHandler,
    Redactor,
    install,
    is_first,
    uninstall,
)

from .conftest import FAKE_CUSTOM_PATH, FAKE_SECRET_PATH, FAKE_WEBHOOK_ID, access_record


def test_access_line_keeps_shape(redactor: Redactor) -> None:
    """Only the path changes; client, method, version and status stay."""
    record = access_record(FAKE_SECRET_PATH)
    assert redactor.redact_record(record)
    assert record.args == ("127.0.0.1:44312", "POST", "/private_<redacted>", "1.1", 200)
    assert record.getMessage() == '127.0.0.1:44312 - "POST /private_<redacted> HTTP/1.1" 200'


def test_access_line_keeps_query_and_suffix(redactor: Redactor) -> None:
    """Anything after the secret segment survives."""
    record = access_record(FAKE_SECRET_PATH + "/settings?tab=1", method="GET", status=405)
    redactor.redact_record(record)
    assert record.args[2] == "/private_<redacted>/settings?tab=1"
    assert record.args[4] == 405


def test_other_paths_untouched(redactor: Redactor) -> None:
    """Lines without a secret are left exactly as they were."""
    record = access_record("/favicon.ico", method="GET", status=404)
    assert not redactor.redact_record(record)
    assert record.args[2] == "/favicon.ico"
    assert redactor.records_redacted == 0


def test_banner_webhook_and_direct_urls(redactor: Redactor) -> None:
    """The HA-MCP startup banner (one multi-line string) is fully redacted."""
    banner = (
        "HA-MCP in-process server is running. Connect URL(s):\n"
        f"- http://192.0.2.10:8123/api/webhook/{FAKE_WEBHOOK_ID}\n"
        f"- https://example.ui.nabu.casa/api/webhook/{FAKE_WEBHOOK_ID}\n"
        f"- http://192.0.2.10:9583{FAKE_SECRET_PATH} (direct access)\n"
        "The webhook URL is the shared secret (no bearer required)."
    )
    record = logging.LogRecord(
        "custom_components.ha_mcp_tools.embedded_setup", logging.INFO, "", 1, banner, None, None
    )
    redactor.redact_record(record)
    text = record.getMessage()
    assert FAKE_WEBHOOK_ID not in text
    assert FAKE_SECRET_PATH not in text
    assert text.count("/api/webhook/<redacted>") == 2
    assert "http://192.0.2.10:9583/private_<redacted> (direct access)" in text


def test_idempotent(redactor: Redactor) -> None:
    """Redacting twice is a no-op the second time."""
    once = redactor.redact_text(f"{FAKE_SECRET_PATH} /api/webhook/{FAKE_WEBHOOK_ID}")
    assert redactor.redact_text(once) == once
    assert once == "/private_<redacted> /api/webhook/<redacted>"


def test_known_values_in_scoped_logger(redactor: Redactor) -> None:
    """A bare webhook id and a custom override path are redacted in scoped loggers."""
    redactor.set_known_values([FAKE_WEBHOOK_ID, FAKE_CUSTOM_PATH + "/"])
    assert redactor.known_count == 2
    record = logging.LogRecord(
        "homeassistant.components.webhook",
        logging.INFO,
        "",
        1,
        "Received message for unregistered webhook %s from %s",
        (FAKE_WEBHOOK_ID, "cloud"),
        None,
    )
    redactor.redact_record(record)
    assert record.getMessage() == "Received message for unregistered webhook <redacted> from cloud"

    custom = access_record(FAKE_CUSTOM_PATH)
    redactor.redact_record(custom)
    assert custom.args[2] == "/<redacted>"


def test_known_values_respect_boundaries(redactor: Redactor) -> None:
    """A known value inside a longer token is not partially rewritten."""
    redactor.set_known_values([FAKE_WEBHOOK_ID])
    text = f"x{FAKE_WEBHOOK_ID}y and {FAKE_WEBHOOK_ID}."
    assert redactor.redact_text(text) == f"x{FAKE_WEBHOOK_ID}y and <redacted>."


def test_short_known_values_ignored(redactor: Redactor) -> None:
    """Short or empty values can never corrupt unrelated text."""
    redactor.set_known_values(["/ha", "abc", "", None, "  "])
    assert redactor.known_count == 0
    assert redactor.redact_text("/happy abc") == "/happy abc"


def test_known_values_only_in_scoped_loggers(redactor: Redactor) -> None:
    """Outside HA-MCP related loggers only the URL patterns apply (keeps it cheap)."""
    redactor.set_known_values([FAKE_WEBHOOK_ID])
    record = logging.LogRecord(
        "homeassistant.components.other",
        logging.INFO,
        "",
        1,
        "id %s at %s",
        (FAKE_WEBHOOK_ID, FAKE_SECRET_PATH),
        None,
    )
    redactor.redact_record(record)
    assert record.args == (FAKE_WEBHOOK_ID, "/private_<redacted>")


def test_mapping_args(redactor: Redactor) -> None:
    """%(name)s style records are handled too."""
    record = logging.LogRecord(
        "ha_mcp.server", logging.INFO, "", 1, "url=%(url)s", ({"url": FAKE_SECRET_PATH},), None
    )
    assert isinstance(record.args, dict)
    redactor.redact_record(record)
    assert record.getMessage() == "url=/private_<redacted>"


class _Url:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def test_object_args_rendered_in_scoped_logger(redactor: Redactor) -> None:
    """Objects that only render the secret at format time are frozen redacted."""
    record = logging.LogRecord(
        "ha_mcp.client",
        logging.WARNING,
        "",
        1,
        "POST %s failed",
        (_Url(f"http://h{FAKE_SECRET_PATH}"),),
        None,
    )
    redactor.redact_record(record)
    assert record.args is None
    assert record.getMessage() == "POST http://h/private_<redacted> failed"


def test_exception_text_redacted(redactor: Redactor) -> None:
    """Tracebacks from scoped loggers are pre-rendered and redacted."""
    try:
        raise RuntimeError(f"cannot reach http://127.0.0.1:9583{FAKE_SECRET_PATH}")
    except RuntimeError as err:
        record = logging.LogRecord(
            "custom_components.ha_mcp_tools.mcp_webhook",
            logging.ERROR,
            "",
            1,
            "relay failed",
            None,
            (type(err), err, err.__traceback__),
        )
    redactor.redact_record(record)
    formatted = logging.Formatter("%(message)s").format(record)
    assert FAKE_SECRET_PATH not in formatted
    assert "/private_<redacted>" in formatted
    assert "RuntimeError" in formatted


def test_handler_never_raises() -> None:
    """A malformed record or a failing redactor never breaks logging."""

    class Boom(Redactor):
        def redact_record(self, record: logging.LogRecord) -> bool:
            raise ValueError("boom")

    handler = RedactingHandler(Boom())
    record = logging.LogRecord("ha_mcp", logging.INFO, "", 1, "%s %s", ("only-one",), None)
    assert handler.handle(record) is True
    assert RedactingHandler(Redactor()).handle(record) is True


def test_install_first_and_uninstall(redactor: Redactor) -> None:
    """install() puts the handler first, even after others were added."""
    root = logging.getLogger()
    other = logging.NullHandler()
    handler = RedactingHandler(redactor)
    try:
        install(handler)
        root.handlers.insert(0, other)
        assert not is_first(handler)
        install(handler)
        assert is_first(handler)
        assert root.handlers.count(handler) == 1
    finally:
        uninstall(handler)
        root.removeHandler(other)
    assert handler not in root.handlers


def test_end_to_end_child_logger(
    installed: RedactingHandler, caplog: pytest.LogCaptureFixture
) -> None:
    """Child loggers are covered (a logger-level filter on the parent would miss them)."""
    caplog.set_level(logging.INFO)
    logging.getLogger("custom_components.ha_mcp_tools.embedded_setup").info(
        "Connect URL: http://192.0.2.10:9583%s (direct access)", FAKE_SECRET_PATH
    )
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "POST", FAKE_SECRET_PATH, "1.1", 202
    )
    assert FAKE_SECRET_PATH not in caplog.text
    assert '127.0.0.1:1 - "POST /private_<redacted> HTTP/1.1" 202' in caplog.text
    assert installed.redactor.records_redacted == 2


class ProbeLikeFilter(logging.Filter):
    """Test double with the same contract as ha-mcp's ProbeAccessLogFilter.

    Drops GET/HEAD 405 access lines on the MCP path by comparing the raw path
    in ``record.args`` with the secret path.
    """

    def __init__(self, mcp_path: str) -> None:
        super().__init__()
        self.mcp_path = mcp_path
        self.dropped = 0

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        method, path, status = args[1], str(args[2]).split("?", 1)[0], args[4]
        if method in ("GET", "HEAD") and status == 405 and path == self.mcp_path:
            self.dropped += 1
            return False
        return True


@pytest.mark.parametrize("probe_first", [True, False])
def test_probe_filter_still_works(
    redactor: Redactor, caplog: pytest.LogCaptureFixture, probe_first: bool
) -> None:
    """Logger filters run before root handlers, whatever order they were added in."""
    caplog.set_level(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    probe = ProbeLikeFilter(FAKE_SECRET_PATH)
    handler = RedactingHandler(redactor)
    try:
        if probe_first:
            access.addFilter(probe)
            install(handler)
        else:
            install(handler)
            access.addFilter(probe)
        fmt = '%s - "%s %s HTTP/%s" %d'
        access.info(fmt, "10.0.0.5:1", "GET", FAKE_SECRET_PATH, "1.1", 405)
        access.info(fmt, "10.0.0.5:1", "POST", FAKE_SECRET_PATH, "1.1", 200)
    finally:
        access.removeFilter(probe)
        uninstall(handler)
    assert probe.dropped == 1
    assert "405" not in caplog.text
    assert '10.0.0.5:1 - "POST /private_<redacted> HTTP/1.1" 200' in caplog.text
    assert FAKE_SECRET_PATH not in caplog.text


def test_logger_filter_would_break_probe(redactor: Redactor) -> None:
    """Why this is a root handler: a redacting logger filter added first defeats the probe."""

    class RedactFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            redactor.redact_record(record)
            return True

    probe = ProbeLikeFilter(FAKE_SECRET_PATH)
    logger = logging.getLogger("tests.contrast.uvicorn.access")
    logger.addFilter(RedactFilter())
    logger.addFilter(probe)
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    logger.warning('%s - "%s %s HTTP/%s" %d', "c", "GET", FAKE_SECRET_PATH, "1.1", 405)
    assert probe.dropped == 0

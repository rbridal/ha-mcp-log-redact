"""Redaction core: a no-op root handler that rewrites secrets in log records.

Why a root handler and not a logger filter
------------------------------------------
* A ``logging.Filter`` on a logger only sees records logged *directly* on that
  logger, not ones propagated from children, so a filter on
  ``custom_components.ha_mcp_tools`` would miss ``...ha_mcp_tools.embedded_setup``.
* Logger filters run in the order they were added. ha-mcp's own
  ``ProbeAccessLogFilter`` (CLI / add-on launchers) compares the raw path with
  the secret path to drop by-design GET 405 probe lines; a redacting filter
  that ran first would make that comparison fail.

Root handlers run after every logger-level filter and level check, in list
order. Inserting this handler at index 0 of the root logger means every other
filter has already seen the original record, and every real handler (HA's
queue handler, system_log, pytest's caplog, ...) only ever sees the redacted
one. The handler never emits anything itself.

Records are mutated in place and keep their shape: ``uvicorn.access`` args stay
a 5-tuple ``(client, method, path, http_version, status)`` with only the path
rewritten. Records logged before the handler was installed are not touched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import contextlib
import logging
import re
import threading
import traceback
from typing import Any

from .const import SCOPED_LOGGERS

PRIVATE_PATH_RE = re.compile(r"/private_[A-Za-z0-9_-]+")
WEBHOOK_PATH_RE = re.compile(r"/api/webhook/[A-Za-z0-9_-]+")
REDACTED_PRIVATE = "/private_<redacted>"
REDACTED_WEBHOOK = "/api/webhook/<redacted>"
REDACTED_PATH = "/<redacted>"
REDACTED_VALUE = "<redacted>"
MARKERS: tuple[str, ...] = ("/private_", "/api/webhook/")
# Known values shorter than this are ignored so a short or common string can
# never corrupt unrelated log text.
MIN_KNOWN_LEN = 8

_PLAIN_ARG_TYPES = (str, int, float, bool, type(None))
_BOUNDARY = "A-Za-z0-9_-"


def _is_scoped(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in SCOPED_LOGGERS)


class Redactor:
    """Holds the patterns and rewrites strings and log records."""

    def __init__(self) -> None:
        self._known_re: re.Pattern[str] | None = None
        self._known_count = 0
        self._lock = threading.Lock()
        self.records_redacted = 0

    @property
    def known_count(self) -> int:
        """Number of known secret values loaded (the values are never exposed)."""
        return self._known_count

    def set_known_values(self, values: Iterable[str | None]) -> None:
        """Load exact secret values (secret path, webhook id) to redact."""
        cleaned: set[str] = set()
        for raw in values:
            if not isinstance(raw, str):
                continue
            value = raw.strip()
            if value.startswith("/"):
                value = value.rstrip("/")
            if len(value) >= MIN_KNOWN_LEN:
                cleaned.add(value)
        if not cleaned:
            self._known_re, self._known_count = None, 0
            return
        # Longest first so an id embedded in a longer value is not half-matched.
        alternation = "|".join(re.escape(v) for v in sorted(cleaned, key=len, reverse=True))
        self._known_re = re.compile(f"(?<![{_BOUNDARY}])(?:{alternation})(?![{_BOUNDARY}])")
        self._known_count = len(cleaned)

    def _has_marker(self, text: str, scoped: bool) -> bool:
        if any(marker in text for marker in MARKERS):
            return True
        known = self._known_re
        return bool(scoped and known is not None and known.search(text))

    def redact_text(self, text: str, *, known: bool = True) -> str:
        """Return ``text`` with secret paths, webhook ids and known values redacted."""
        text = PRIVATE_PATH_RE.sub(REDACTED_PRIVATE, text)
        text = WEBHOOK_PATH_RE.sub(REDACTED_WEBHOOK, text)
        known_re = self._known_re
        if known and known_re is not None:
            text = known_re.sub(
                lambda m: REDACTED_PATH if m.group(0).startswith("/") else REDACTED_VALUE, text
            )
        return text

    def _redact_value(self, value: Any, scoped: bool) -> tuple[Any, bool]:
        if isinstance(value, str) and self._has_marker(value, scoped):
            new = self.redact_text(value, known=scoped)
            return new, new != value
        return value, False

    def redact_record(self, record: logging.LogRecord) -> bool:
        """Rewrite secrets in ``record`` in place. Returns True if anything changed."""
        scoped = _is_scoped(record.name)
        changed = False

        msg = record.msg
        if isinstance(msg, str):
            new_msg, hit = self._redact_value(msg, scoped)
            if hit:
                record.msg, changed = new_msg, True

        args = record.args
        if isinstance(args, tuple) and args:
            new_args = []
            hit_any = False
            for arg in args:
                new_arg, hit = self._redact_value(arg, scoped)
                new_args.append(new_arg)
                hit_any = hit_any or hit
            if hit_any:
                record.args, changed = tuple(new_args), True
        elif isinstance(args, Mapping) and args:
            new_map = {}
            hit_any = False
            for key, arg in args.items():
                new_arg, hit = self._redact_value(arg, scoped)
                new_map[key] = new_arg
                hit_any = hit_any or hit
            if hit_any:
                record.args, changed = new_map, True

        if scoped:
            changed = self._redact_scoped_extras(record) or changed

        if changed:
            with self._lock:
                self.records_redacted += 1
        return changed

    def _redact_scoped_extras(self, record: logging.LogRecord) -> bool:
        changed = False
        args = record.args
        values = args.values() if isinstance(args, Mapping) else (args or ())
        if not isinstance(record.msg, str) or any(
            not isinstance(a, _PLAIN_ARG_TYPES) for a in values
        ):
            # Objects (URLs, exceptions, ...) render their secrets only at
            # format time, so render now and freeze the redacted text.
            try:
                rendered = record.getMessage()
            except Exception:  # a broken record is not ours to fix
                rendered = None
            if rendered is not None and self._has_marker(rendered, True):
                record.msg, record.args = self.redact_text(rendered), None
                changed = True

        if record.exc_info and not record.exc_text:
            with contextlib.suppress(Exception):
                formatted = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
                if self._has_marker(formatted, True):
                    # logging.Formatter reuses exc_text instead of re-formatting.
                    record.exc_text = self.redact_text(formatted)
                    changed = True
        elif isinstance(record.exc_text, str) and self._has_marker(record.exc_text, True):
            record.exc_text = self.redact_text(record.exc_text)
            changed = True
        return changed


class RedactingHandler(logging.Handler):
    """Root handler that only redacts. Never emits, never raises."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__(logging.NOTSET)
        self.redactor = redactor

    def handle(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        with contextlib.suppress(Exception):
            self.redactor.redact_record(record)
        return True

    def emit(self, record: logging.LogRecord) -> None:
        """Nothing to emit; the real handlers further down the list do that."""


def _handlers_lock() -> contextlib.AbstractContextManager[Any]:
    lock = getattr(logging, "_lock", None)
    return lock if lock is not None else contextlib.nullcontext()


def install(handler: RedactingHandler) -> None:
    """Put ``handler`` first on the root logger (moving it if already present)."""
    root = logging.getLogger()
    with _handlers_lock():
        if handler in root.handlers:
            root.handlers.remove(handler)
        root.handlers.insert(0, handler)


def is_first(handler: RedactingHandler) -> bool:
    """True if ``handler`` is the first root handler."""
    handlers = logging.getLogger().handlers
    return bool(handlers) and handlers[0] is handler


def uninstall(handler: RedactingHandler) -> None:
    """Remove ``handler`` from the root logger."""
    logging.getLogger().removeHandler(handler)

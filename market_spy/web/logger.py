"""File and console logging for the SourceIQ web app."""

import os
import traceback
from datetime import datetime, timezone

from market_spy.config import OUTPUT_DIR

ERROR_LOG_FILE = os.path.join(OUTPUT_DIR, "error_log.txt")
REQUEST_LOG_FILE = os.path.join(OUTPUT_DIR, "request_log.txt")
_SEPARATOR = "=" * 72


def _ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log_error(route: str, exc: BaseException) -> None:
    """Write error details to output/error_log.txt and print to console."""
    _ensure_output_dir()
    ts = _utc_timestamp()
    stack = traceback.format_exc()
    if not stack or stack.strip() == "NoneType: None":
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    entry = (
        f"[{ts}]\n"
        f"Route: {route}\n"
        f"Error type: {type(exc).__name__}\n"
        f"Error message: {exc}\n"
        f"Stack trace:\n"
        f"{stack.rstrip()}\n"
        f"{_SEPARATOR}\n"
    )

    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry)
    print(entry, end="", flush=True)


def log_request(method: str, path: str, status_code: int) -> None:
    """Append one line per request to output/request_log.txt and print to console."""
    _ensure_output_dir()
    ts = _utc_timestamp()
    line = f"[{ts}] {method} {path} -> {status_code}\n"

    with open(REQUEST_LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line)
    print(line, end="", flush=True)


def log_event(message: str) -> None:
    """Log an operational event to console (visible in Railway logs)."""
    line = f"[{_utc_timestamp()}] {message}\n"
    print(line, end="", flush=True)

"""Pure helpers for rendering SublimeLinter Cargo diagnostics in a tab."""

from __future__ import annotations

import os
from typing import Iterable, Mapping, Sequence, Tuple


DIVIDER = "─" * 64


def diagnostic_sort_key(item: Mapping[str, object]) -> Tuple[str, int, int, str]:
    filename = str(item.get("filename") or "")
    line = item.get("line")
    start = item.get("start")
    error_type = str(item.get("error_type") or "")
    return (
        os.path.normcase(filename),
        int(line) if isinstance(line, int) else -1,
        int(start) if isinstance(start, int) else -1,
        error_type,
    )


def display_path(item: Mapping[str, object], root: str) -> str:
    """Return a Cargo diagnostic filename relative to the Cargo root."""
    filename = str(item.get("filename") or "")
    try:
        return os.path.relpath(filename, root)
    except (OSError, ValueError):
        return filename


def format_location_line(item: Mapping[str, object]) -> str:
    """Render the compact location/severity/code line for one diagnostic."""
    line = item.get("line")
    start = item.get("start")
    line_number = int(line) + 1 if isinstance(line, int) else 1
    column_number = int(start) + 1 if isinstance(start, int) else 1
    error_type = str(item.get("error_type") or "error").upper()
    code = str(item.get("code") or "")
    code_text = "  {}".format(code) if code else ""
    return "  {}:{:<4}  {:<7}{}".format(
        line_number, column_number, error_type, code_text
    ).rstrip()


def message_lines(item: Mapping[str, object]) -> Sequence[str]:
    """Render the diagnostic message as indented lines beneath its location."""
    message = str(item.get("msg") or "").strip().splitlines()
    if not message:
        return ("          (no diagnostic message)",)
    return tuple("          {}".format(line) for line in message)


def format_diagnostic_line(item: Mapping[str, object], root: str) -> str:
    """Render a conventional one-line diagnostic."""
    filename = display_path(item, root)
    line = item.get("line")
    start = item.get("start")
    line_number = int(line) + 1 if isinstance(line, int) else 1
    column_number = int(start) + 1 if isinstance(start, int) else 1
    error_type = str(item.get("error_type") or "error")
    code = str(item.get("code") or "")
    code_text = "[{}]".format(code) if code else ""
    message = str(item.get("msg") or "").strip().splitlines()
    first_message = message[0] if message else ""
    return "{}:{}:{}: {}{}: {}".format(
        filename, line_number, column_number, error_type, code_text, first_message
    )


def continuation_lines(item: Mapping[str, object]) -> Sequence[str]:
    message = str(item.get("msg") or "").strip().splitlines()
    return tuple("    {}".format(line) for line in message[1:])


def count_errors_and_warnings(items: Iterable[Mapping[str, object]]) -> Tuple[int, int]:
    errors = 0
    warnings = 0
    for item in items:
        error_type = item.get("error_type")
        if error_type == "error":
            errors += 1
        elif error_type == "warning":
            warnings += 1
    return errors, warnings

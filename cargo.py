"""Cargo-specific helpers kept independent from Sublime Text's API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import ntpath
import os
from typing import Any, Iterable, Iterator, Optional, Sequence


COMMAND_PREFIX = ("cargo", "check")
JSON_MESSAGE_FORMAT = "--message-format=json"


@dataclass(frozen=True)
class CargoDiagnostic:
    """One source-backed rustc diagnostic, with zero-based positions."""

    filename: str
    line: int
    column: Optional[int]
    end_line: Optional[int]
    end_column: Optional[int]
    level: str
    message: str
    code: Optional[str]


def find_cargo_root(
    start_path: Optional[str], window_folders: Iterable[str] = ()
) -> Optional[str]:
    """Return the nearest ancestor containing ``Cargo.toml``.

    The active file is preferred.  Window folders are only fallbacks for
    callers that do not have a file path, which keeps this helper usable in
    unit tests and outside Sublime Text.
    """
    candidates = []
    if start_path:
        candidates.append(_start_directory(start_path))
    candidates.extend(folder for folder in window_folders if folder)

    for candidate in candidates:
        current = os.path.abspath(os.path.expanduser(candidate))
        while True:
            if os.path.isfile(os.path.join(current, "Cargo.toml")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return None


def build_cargo_command(args: Sequence[str] = ()) -> tuple[str, ...]:
    """Build Cargo's command while keeping JSON output mandatory and final."""
    return COMMAND_PREFIX + tuple(args) + (JSON_MESSAGE_FORMAT,)


def parse_cargo_json_lines(text: str, project_root: str) -> Iterator[CargoDiagnostic]:
    """Yield source-backed errors and warnings from Cargo's JSON stream.

    Rustc's line and column numbers are one-based.  SublimeLinter stores
    positions as zero-based values, including end positions.
    """
    for raw_line in text.splitlines():
        record = _load_json_object(raw_line)
        if not record or record.get("reason") != "compiler-message":
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        level = message.get("level")
        if level not in ("error", "warning"):
            continue

        span = _primary_source_span(message.get("spans"))
        if span is None:
            # SublimeLinter diagnostics need a real source location.  Notes,
            # help, and global Cargo failures are intentionally not fabricated
            # into arbitrary source locations.
            continue

        filename = span.get("file_name")
        line = _zero_based(span.get("line_start"))
        if not isinstance(filename, str) or line is None:
            continue

        code_data = message.get("code")
        code = code_data.get("code") if isinstance(code_data, dict) else None
        yield CargoDiagnostic(
            filename=_absolute_filename(filename, project_root),
            line=line,
            column=_zero_based(span.get("column_start")),
            end_line=_zero_based(span.get("line_end")),
            end_column=_zero_based(span.get("column_end")),
            level=level,
            message=str(message.get("message") or "").strip(),
            code=code if isinstance(code, str) else None,
        )


def _start_directory(path: str) -> str:
    expanded = os.path.expanduser(path)
    if os.path.isdir(expanded):
        return expanded
    return os.path.dirname(expanded)


def _load_json_object(raw_line: str) -> Optional[dict[str, Any]]:
    raw_line = raw_line.strip()
    if not raw_line.startswith("{"):
        return None
    try:
        value = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _primary_source_span(spans: object) -> Optional[dict[str, Any]]:
    if not isinstance(spans, list):
        return None

    valid_spans = [
        span for span in spans
        if isinstance(span, dict)
        and isinstance(span.get("file_name"), str)
        and _zero_based(span.get("line_start")) is not None
    ]
    for span in valid_spans:
        if span.get("is_primary"):
            return span
    return valid_spans[0] if valid_spans else None


def _zero_based(value: object) -> Optional[int]:
    if not isinstance(value, int) or value < 1:
        return None
    return value - 1


def _absolute_filename(filename: str, project_root: str) -> str:
    # ``ntpath`` makes this safe to unit-test on Unix while preserving native
    # Windows absolute paths when the plugin runs on Windows.
    if os.path.isabs(filename) or ntpath.isabs(filename):
        return os.path.normpath(filename)
    return os.path.normpath(os.path.join(project_root, filename))

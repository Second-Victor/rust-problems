"""Pure helpers for the optional Rust/Cargo status counter."""

from __future__ import annotations

import os
from typing import Iterable, Mapping, Tuple


def count_diagnostics(errors: Iterable[Mapping[str, object]]) -> Tuple[int, int]:
    """Return ``(errors, warnings)`` for SublimeLinter diagnostic dictionaries."""
    error_count = 0
    warning_count = 0
    for item in errors:
        error_type = item.get("error_type")
        if error_type == "error":
            error_count += 1
        elif error_type == "warning":
            warning_count += 1
    return error_count, warning_count


def format_status(error_count: int, warning_count: int) -> str:
    """Format the compact status text used by the original Rust Problems plugin."""
    if error_count == 0 and warning_count == 0:
        return "Rust ✓"
    return "Rust ⊗ {}  ⚠ {}".format(error_count, warning_count)


def is_within(path: str, root: str) -> bool:
    """Return whether *path* is contained by *root*, safely across platforms."""
    try:
        normalized_path = os.path.normcase(os.path.abspath(path))
        normalized_root = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath((normalized_path, normalized_root)) == normalized_root
    except (OSError, ValueError):
        return False

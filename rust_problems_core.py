from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Diagnostic:
    level: str
    message: str
    code: Optional[str]
    file_name: Optional[str]
    line: Optional[int]
    column: Optional[int]
    children: tuple[str, ...] = ()

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    @property
    def is_warning(self) -> bool:
        return self.level == "warning"


def _normalise_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_within(path: str, root: str) -> bool:
    """Return True when path is inside root (or is root)."""
    try:
        return os.path.commonpath([_normalise_path(path), _normalise_path(root)]) == _normalise_path(root)
    except (ValueError, OSError):
        return False


def find_cargo_root(start_path: Optional[str], window_folders: Iterable[str] = ()) -> Optional[str]:
    """Find the nearest Cargo.toml, preferring the active file's ancestors."""
    candidates: list[str] = []

    if start_path:
        start = os.path.abspath(start_path)
        if os.path.isfile(start):
            start = os.path.dirname(start)
        candidates.append(start)

    for folder in window_folders:
        if folder:
            candidates.append(os.path.abspath(folder))

    seen: set[str] = set()
    for candidate in candidates:
        current = candidate
        while current and current not in seen:
            seen.add(current)
            if os.path.isfile(os.path.join(current, "Cargo.toml")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    return None


def _primary_span(diagnostic: dict) -> Optional[dict]:
    spans = diagnostic.get("spans") or []
    for span in spans:
        if span.get("is_primary"):
            return span
    return spans[0] if spans else None


def _child_messages(diagnostic: dict) -> tuple[str, ...]:
    result: list[str] = []
    for child in diagnostic.get("children") or []:
        level = child.get("level") or "note"
        message = (child.get("message") or "").strip()
        if message:
            result.append(f"{level}: {message}")
    return tuple(result)


def parse_cargo_json_lines(
    text: str,
    project_root: str,
    include_external: bool = False,
) -> list[Diagnostic]:
    """Parse Cargo JSON stream output into project diagnostics.

    Cargo emits one JSON object per line with --message-format=json. We keep
    compiler-message records whose diagnostic level is error or warning.
    """
    diagnostics: list[Diagnostic] = []
    seen: set[tuple] = set()

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line or not raw_line.startswith("{"):
            continue

        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if message.get("reason") != "compiler-message":
            continue

        diagnostic = message.get("message") or {}
        level = diagnostic.get("level")
        if level not in ("error", "warning"):
            continue

        span = _primary_span(diagnostic)
        file_name: Optional[str] = None
        line: Optional[int] = None
        column: Optional[int] = None

        if span:
            file_name = span.get("file_name")
            line = span.get("line_start")
            column = span.get("column_start")

            if file_name:
                full_path = file_name if os.path.isabs(file_name) else os.path.join(project_root, file_name)
                if not include_external and not is_within(full_path, project_root):
                    continue

        code_data = diagnostic.get("code")
        code = code_data.get("code") if isinstance(code_data, dict) else None
        text_message = (diagnostic.get("message") or "").strip()

        item = Diagnostic(
            level=level,
            message=text_message,
            code=code,
            file_name=file_name,
            line=line,
            column=column,
            children=_child_messages(diagnostic),
        )
        identity = (item.level, item.code, item.message, item.file_name, item.line, item.column)
        if identity not in seen:
            seen.add(identity)
            diagnostics.append(item)

    diagnostics.sort(
        key=lambda d: (
            0 if d.is_error else 1,
            d.file_name or "",
            d.line or 0,
            d.column or 0,
            d.message,
        )
    )
    return diagnostics


def count_diagnostics(diagnostics: Iterable[Diagnostic]) -> tuple[int, int]:
    errors = 0
    warnings = 0
    for diagnostic in diagnostics:
        if diagnostic.is_error:
            errors += 1
        elif diagnostic.is_warning:
            warnings += 1
    return errors, warnings


def _display_path(file_name: Optional[str], project_root: str) -> Optional[str]:
    if not file_name:
        return None

    full_path = file_name if os.path.isabs(file_name) else os.path.join(project_root, file_name)
    try:
        if is_within(full_path, project_root):
            return os.path.relpath(full_path, project_root)
    except (ValueError, OSError):
        pass
    return file_name


def render_panel(diagnostics: Iterable[Diagnostic], project_root: str) -> str:
    items = list(diagnostics)
    errors, warnings = count_diagnostics(items)

    lines = [
        "RUST PROBLEMS",
        "=" * 72,
        f"Project: {project_root}",
        f"Errors: {errors}    Warnings: {warnings}",
        "",
    ]

    if not items:
        lines.append("No Rust compiler errors or warnings. ✓")
        return "\n".join(lines) + "\n"

    for diagnostic in items:
        level = diagnostic.level
        code = f"[{diagnostic.code}]" if diagnostic.code else ""
        path = _display_path(diagnostic.file_name, project_root)

        if path and diagnostic.line is not None:
            column = diagnostic.column or 1
            lines.append(
                f"{path}:{diagnostic.line}:{column}: {level}{code}: {diagnostic.message}"
            )
        else:
            lines.append(f"{level}{code}: {diagnostic.message}")

        for child in diagnostic.children:
            lines.append(f"    {child}")
        lines.append("")

    return "\n".join(lines) + "\n"

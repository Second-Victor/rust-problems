"""Small status-bar summary driven by SublimeLinter's Cargo diagnostics."""

from __future__ import annotations

import os
from typing import Dict, List, Mapping, Tuple

import sublime

from SublimeLinter.lint import events, persist

from .cargo import find_cargo_root
from .status_core import count_diagnostics, format_status, is_within


STATUS_KEY = "rust_problems"
LINTER_NAME = "cargo"
_checking_roots = set()
_subscribed = False


def plugin_loaded() -> None:
    global _subscribed
    if _subscribed:
        return
    events.subscribe(events.LINT_START, _on_lint_start)
    events.subscribe(events.LINT_RESULT, _on_lint_result)
    events.subscribe(events.LINTER_UNASSIGNED, _on_linter_unassigned)
    events.subscribe(events.LINTER_FAILED, _on_linter_failed)
    events.subscribe(events.FILE_RENAMED, _on_file_renamed)
    _subscribed = True
    sublime.set_timeout(_refresh_all_windows, 0)


def plugin_unloaded() -> None:
    global _subscribed
    if _subscribed:
        events.unsubscribe(events.LINT_START, _on_lint_start)
        events.unsubscribe(events.LINT_RESULT, _on_lint_result)
        events.unsubscribe(events.LINTER_UNASSIGNED, _on_linter_unassigned)
        events.unsubscribe(events.LINTER_FAILED, _on_linter_failed)
        events.unsubscribe(events.FILE_RENAMED, _on_file_renamed)
        _subscribed = False
    _checking_roots.clear()
    for window in sublime.windows():
        for view in window.views():
            view.erase_status(STATUS_KEY)


def _on_lint_start(filename: str, linter_name: str, **kwargs) -> None:
    if linter_name != LINTER_NAME:
        return
    root = find_cargo_root(filename)
    if root:
        _checking_roots.add(_norm(root))
        sublime.set_timeout(lambda: _set_project_status(root, "Rust Problems: checking…"), 0)


def _on_lint_result(
    filename: str, linter_name: str, errors: List[Mapping[str, object]], **kwargs
) -> None:
    if linter_name != LINTER_NAME:
        return
    root = find_cargo_root(filename)
    if root:
        _checking_roots.discard(_norm(root))
    sublime.set_timeout(_refresh_all_windows, 0)


def _on_linter_unassigned(filename: str, linter_name: str, **kwargs) -> None:
    if linter_name == LINTER_NAME:
        sublime.set_timeout(_refresh_all_windows, 0)


def _on_linter_failed(filename: str, linter_name: str, **kwargs) -> None:
    if linter_name != LINTER_NAME:
        return
    root = find_cargo_root(filename)
    if root:
        _checking_roots.discard(_norm(root))
        sublime.set_timeout(lambda: _set_project_status(root, "Rust Problems: check failed"), 0)


def _on_file_renamed(**kwargs) -> None:
    sublime.set_timeout(_refresh_all_windows, 0)


def _refresh_all_windows() -> None:
    for window in sublime.windows():
        _refresh_window(window)


def _refresh_window(window: sublime.Window) -> None:
    counts_by_root: Dict[str, Tuple[int, int]] = {}
    for view in window.views():
        filename = view.file_name()
        if not filename:
            view.erase_status(STATUS_KEY)
            continue
        root = find_cargo_root(filename)
        if not root:
            view.erase_status(STATUS_KEY)
            continue
        root_key = _norm(root)
        if root_key in _checking_roots:
            view.set_status(STATUS_KEY, "Rust Problems: checking…")
            continue
        counts = counts_by_root.get(root_key)
        if counts is None:
            counts = _cargo_counts_for_root(root)
            counts_by_root[root_key] = counts
        view.set_status(STATUS_KEY, format_status(*counts))


def _cargo_counts_for_root(root: str) -> Tuple[int, int]:
    error_count = 0
    warning_count = 0
    for filename, file_errors in list(persist.file_errors.items()):
        if not is_within(filename, root):
            continue
        errors, warnings = count_diagnostics(
            item for item in file_errors if item.get("linter") == LINTER_NAME
        )
        error_count += errors
        warning_count += warnings
    return error_count, warning_count


def _set_project_status(root: str, text: str) -> None:
    for window in sublime.windows():
        for view in window.views():
            filename = view.file_name()
            if filename and is_within(filename, root):
                view.set_status(STATUS_KEY, text)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))

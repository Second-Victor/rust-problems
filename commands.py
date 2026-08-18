"""Rust-facing Problems tab backed entirely by SublimeLinter diagnostics."""

from __future__ import annotations

import os
from typing import List, Mapping, Optional

import sublime
import sublime_plugin

from SublimeLinter.lint import events, persist

from .cargo import find_cargo_root
from .problems_core import (
    DIVIDER,
    count_errors_and_warnings,
    diagnostic_sort_key,
    display_path,
    format_location_line,
    message_lines,
)
from .status_core import is_within


PROBLEMS_VIEW_NAME = "Rust Problems"
PROBLEMS_VIEW_SETTING = "rust_problems_view"
PROBLEMS_ROOT_SETTING = "rust_problems_root"
PROBLEMS_ANNOTATION_KEY = "rust_problems_open_links"
LINTER_NAME = "cargo"
_subscribed = False


def plugin_loaded() -> None:
    global _subscribed
    if _subscribed:
        return
    events.subscribe(events.LINT_RESULT, _on_lint_result)
    events.subscribe(events.LINTER_UNASSIGNED, _on_linter_unassigned)
    events.subscribe(events.LINTER_FAILED, _on_linter_failed)
    events.subscribe(events.FILE_RENAMED, _on_file_renamed)
    _subscribed = True


def plugin_unloaded() -> None:
    global _subscribed
    if _subscribed:
        events.unsubscribe(events.LINT_RESULT, _on_lint_result)
        events.unsubscribe(events.LINTER_UNASSIGNED, _on_linter_unassigned)
        events.unsubscribe(events.LINTER_FAILED, _on_linter_failed)
        events.unsubscribe(events.FILE_RENAMED, _on_file_renamed)
        _subscribed = False


def _on_lint_result(linter_name: str, **kwargs) -> None:
    if linter_name == LINTER_NAME:
        sublime.set_timeout(_refresh_open_tabs, 0)


def _on_linter_unassigned(linter_name: str, **kwargs) -> None:
    if linter_name == LINTER_NAME:
        sublime.set_timeout(_refresh_open_tabs, 0)


def _on_linter_failed(linter_name: str, **kwargs) -> None:
    if linter_name == LINTER_NAME:
        sublime.set_timeout(_refresh_open_tabs, 0)


def _on_file_renamed(**kwargs) -> None:
    sublime.set_timeout(_refresh_open_tabs, 0)


def _refresh_open_tabs() -> None:
    for window in sublime.windows():
        view = _find_problems_view(window)
        if view is not None:
            root = view.settings().get(PROBLEMS_ROOT_SETTING)
            if isinstance(root, str) and root:
                _render_problems(window, view, root, focus=False)


def _find_problems_view(window: sublime.Window) -> Optional[sublime.View]:
    for view in window.views():
        if view.settings().get(PROBLEMS_VIEW_SETTING, False):
            return view
    return None


def _resolve_root(window: sublime.Window) -> Optional[str]:
    active = window.active_view()
    if active is not None:
        stored_root = active.settings().get(PROBLEMS_ROOT_SETTING)
        if isinstance(stored_root, str) and stored_root:
            return stored_root
        root = find_cargo_root(active.file_name(), window.folders())
        if root:
            return root
    existing = _find_problems_view(window)
    if existing is not None:
        stored_root = existing.settings().get(PROBLEMS_ROOT_SETTING)
        if isinstance(stored_root, str) and stored_root:
            return stored_root
    return find_cargo_root(None, window.folders())


def _cargo_diagnostics(root: str) -> List[Mapping[str, object]]:
    diagnostics: List[Mapping[str, object]] = []
    for filename, errors in list(persist.file_errors.items()):
        if not is_within(filename, root):
            continue
        diagnostics.extend(
            item for item in errors if item.get("linter") == LINTER_NAME
        )
    diagnostics.sort(key=diagnostic_sort_key)
    return diagnostics


def _ensure_problems_view(window: sublime.Window, root: str) -> sublime.View:
    view = _find_problems_view(window)
    if view is None:
        view = window.new_file()
        view.set_name(PROBLEMS_VIEW_NAME)
        view.set_scratch(True)
        view.settings().set(PROBLEMS_VIEW_SETTING, True)
        view.settings().set("word_wrap", False)
        try:
            view.assign_syntax(
                "Packages/SublimeLinter-contrib-cargo/Rust Problems.sublime-syntax"
            )
        except Exception:
            pass
    view.settings().set(PROBLEMS_ROOT_SETTING, root)
    return view


def _build_text(root: str, diagnostics: List[Mapping[str, object]]) -> str:
    error_count, warning_count = count_errors_and_warnings(diagnostics)
    project_name = os.path.basename(os.path.normpath(root)) or root
    error_word = "error" if error_count == 1 else "errors"
    warning_word = "warning" if warning_count == 1 else "warnings"
    lines = [
        "Rust Problems", DIVIDER, "", "Project   {}".format(project_name),
        "Root      {}".format(root), "",
        "✕  {} {}      ⚠  {} {}".format(
            error_count, error_word, warning_count, warning_word
        ), "",
    ]
    if not diagnostics:
        lines.extend(["✓  No Cargo errors or warnings.", ""])
        return "\n".join(lines) + "\n"
    current_path = None
    for item in diagnostics:
        path = display_path(item, root)
        if path != current_path:
            if current_path is not None:
                lines.append("")
            lines.append(path)
            current_path = path
        lines.append(format_location_line(item))
        lines.extend(message_lines(item))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_problems(window: sublime.Window, view: sublime.View, root: str, focus: bool) -> None:
    diagnostics = _cargo_diagnostics(root)
    view.erase_regions(PROBLEMS_ANNOTATION_KEY)
    view.set_read_only(False)
    view.run_command("select_all")
    view.run_command("right_delete")
    view.run_command("append", {"characters": _build_text(root, diagnostics), "force": True, "scroll_to_end": False})
    view.sel().clear()
    view.sel().add(sublime.Region(0, 0))
    view.set_read_only(True)
    view.set_scratch(True)
    _decorate_with_open_links(window, view, diagnostics)
    if focus:
        window.focus_view(view)


def _decorate_with_open_links(
    window: sublime.Window, view: sublime.View, diagnostics: List[Mapping[str, object]]
) -> None:
    locations = view.find_all(r"^  \d+:\d+\s+(?:ERROR|WARNING)(?:\s+\S+)?$")
    count = min(len(locations), len(diagnostics))
    if count == 0:
        return
    regions = [view.line(locations[index]) for index in range(count)]
    annotations = ['<a href="{}">Open</a>'.format(index) for index in range(count)]

    def on_navigate(href: str) -> None:
        try:
            index = int(href)
        except ValueError:
            return
        if 0 <= index < len(diagnostics):
            _open_diagnostic(window, diagnostics[index])

    view.add_regions(
        PROBLEMS_ANNOTATION_KEY, regions, scope="", annotations=annotations,
        on_navigate=on_navigate,
    )


def _open_diagnostic(window: sublime.Window, item: Mapping[str, object]) -> None:
    filename = item.get("filename")
    if not isinstance(filename, str) or not filename:
        return
    line = item.get("line")
    start = item.get("start")
    line_number = int(line) + 1 if isinstance(line, int) else 1
    column_number = int(start) + 1 if isinstance(start, int) else 1
    window.open_file(
        "{}:{}:{}".format(filename, line_number, column_number),
        sublime.ENCODED_POSITION,
    )


class RustProblemsShowProblemsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        root = _resolve_root(self.window)
        if not root:
            sublime.error_message("Rust Problems could not find a Cargo.toml for the current project.")
            return
        view = _ensure_problems_view(self.window, root)
        _render_problems(self.window, view, root, focus=True)


class RustProblemsToggleProblemsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        active = self.window.active_view()
        if active is not None and active.settings().get(PROBLEMS_VIEW_SETTING, False):
            self.window.run_command("close_file")
            return
        self.window.run_command("rust_problems_show_problems")


class RustProblemsOpenSublimeLinterProblemsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        self.window.run_command("show_panel", {"panel": "output.SublimeLinter"})

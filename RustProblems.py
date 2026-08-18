from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional

import sublime
import sublime_plugin

from .rust_problems_core import (
    Diagnostic,
    count_diagnostics,
    find_cargo_root,
    is_within,
    parse_cargo_json_lines,
    render_panel,
)


SETTINGS_FILE = "Rust Problems.sublime-settings"
PANEL_NAME = "rust_problems"
PANEL_FULL_NAME = "output." + PANEL_NAME
STATUS_KEY = "rust_problems"
PROBLEMS_VIEW_SETTING = "rust_problems_view"
PROBLEMS_VIEW_NAME = "Rust Problems"
PROBLEMS_SYNTAX = "Packages/Rust Problems/Rust Problems.sublime-syntax"
PROBLEMS_ANNOTATION_KEY = "rust_problems_links"
CONTEXT_HAS_DIAGNOSTICS = "rust_problems_has_diagnostics"


@dataclass
class WindowState:
    window_id: int
    root: Optional[str] = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    checked_once: bool = False
    request_version: int = 0
    process: Optional[subprocess.Popen] = None
    navigation_index: int = -1
    lock: threading.RLock = field(default_factory=threading.RLock)


class RustProblemsManager:
    def __init__(self) -> None:
        self._states: dict[int, WindowState] = {}
        self._states_lock = threading.RLock()

    def state_for(self, window: sublime.Window) -> WindowState:
        with self._states_lock:
            state = self._states.get(window.id())
            if state is None:
                state = WindowState(window_id=window.id())
                self._states[window.id()] = state
            return state

    def remove_window(self, window_id: int) -> None:
        with self._states_lock:
            state = self._states.pop(window_id, None)
        if state:
            self._terminate_process(state)

    def shutdown(self) -> None:
        with self._states_lock:
            states = list(self._states.values())
            self._states.clear()
        for state in states:
            self._terminate_process(state)
        for window in sublime.windows():
            for view in window.views():
                view.erase_status(STATUS_KEY)

    def settings(self) -> sublime.Settings:
        return sublime.load_settings(SETTINGS_FILE)

    def resolve_root(self, window: sublime.Window, view: Optional[sublime.View] = None) -> Optional[str]:
        start_path = None
        if view and view.file_name():
            start_path = view.file_name()
        elif window.active_view() and window.active_view().file_name():
            start_path = window.active_view().file_name()
        return find_cargo_root(start_path, window.folders())

    def ensure_project(self, window: sublime.Window, view: Optional[sublime.View] = None) -> Optional[str]:
        state = self.state_for(window)
        root = self.resolve_root(window, view)
        with state.lock:
            # A scratch Problems tab has no file name. If the user opened a
            # single Rust file rather than a Sublime project/folder, resolving
            # from that scratch tab can return None. Keep the last valid Cargo
            # root instead of discarding all project state.
            if root is None and state.root and os.path.isfile(os.path.join(state.root, "Cargo.toml")):
                root = state.root

            if root != state.root:
                state.root = root
                state.diagnostics = []
                state.checked_once = False
                state.request_version += 1
                state.navigation_index = -1
                self._terminate_process(state)
        self._update_status(window, state)
        return root

    def schedule_check(
        self,
        window: sublime.Window,
        delay_ms: Optional[int] = None,
        view: Optional[sublime.View] = None,
    ) -> None:
        # Resolve the Cargo project from the view that triggered the check when
        # possible. This avoids losing the project root when a scratch Problems
        # tab or an output panel currently has focus.
        root = self.ensure_project(window, view)
        if not root:
            return

        state = self.state_for(window)
        settings = self.settings()
        if delay_ms is None:
            delay_ms = int(settings.get("check_delay_ms", 750))

        with state.lock:
            state.request_version += 1
            version = state.request_version

        self._set_checking_status(window, state)
        sublime.set_timeout_async(
            lambda: self._start_if_latest(window.id(), version),
            max(0, delay_ms),
        )

    def check_now(self, window: sublime.Window) -> None:
        self.schedule_check(window, delay_ms=0)

    def clear(self, window: sublime.Window) -> None:
        state = self.state_for(window)
        with state.lock:
            state.diagnostics = []
            state.checked_once = False
            state.request_version += 1
            state.navigation_index = -1
            self._terminate_process(state)
        self._update_status(window, state)
        text = "Rust Problems cleared. Run ‘Rust Problems: Check Project’ to check again.\n"
        self._write_panel(window, text)
        self._update_problems_view_if_open(window, text)

    # ------------------------------------------------------------------
    # Problems UI
    # ------------------------------------------------------------------

    def show_problems_view(self, window: sublime.Window) -> None:
        """Open/update a normal read-only tab containing all diagnostics."""
        state = self.state_for(window)
        root = self.ensure_project(window)

        if not root:
            text = (
                "RUST PROBLEMS\n"
                + "=" * 72
                + "\nNo Cargo.toml could be found for this window.\n"
            )
            self._write_problems_view(window, text, focus=True)
            return

        with state.lock:
            checked_once = state.checked_once
            diagnostics = list(state.diagnostics)
            running = state.process is not None and state.process.poll() is None

        if not checked_once and not running:
            text = "RUST PROBLEMS\n" + "=" * 72 + f"\nProject: {root}\n\nChecking…\n"
            self._write_problems_view(window, text, focus=True)
            self.check_now(window)
            return

        self._write_problems_view(window, render_panel(diagnostics, root), focus=True, diagnostics=diagnostics)

    def show_output_panel(self, window: sublime.Window) -> None:
        """Keep the classic bottom output panel available as an optional view."""
        state = self.state_for(window)
        root = self.ensure_project(window)
        if not root:
            self._write_panel(
                window,
                "RUST PROBLEMS\n" + "=" * 72 + "\nNo Cargo.toml could be found for this window.\n",
            )
            window.run_command("show_panel", {"panel": PANEL_FULL_NAME})
            return

        with state.lock:
            checked_once = state.checked_once
            diagnostics = list(state.diagnostics)
            running = state.process is not None and state.process.poll() is None

        if not checked_once and not running:
            self._write_panel(
                window,
                "RUST PROBLEMS\n" + "=" * 72 + f"\nProject: {root}\n\nChecking…\n",
            )
            self.check_now(window)
        else:
            self._write_panel(window, render_panel(diagnostics, root))

        window.run_command("show_panel", {"panel": PANEL_FULL_NAME})

    def toggle_panel(self, window: sublime.Window) -> None:
        if window.active_panel() == PANEL_FULL_NAME:
            window.run_command("hide_panel")
        else:
            self.show_output_panel(window)

    def has_navigable_diagnostics(self, window: sublime.Window) -> bool:
        state = self.state_for(window)
        with state.lock:
            return any(self._is_navigable(item) for item in state.diagnostics)

    def navigate(self, window: sublime.Window, direction: int) -> None:
        """Navigate our own diagnostic list instead of Sublime build results.

        This makes F4/Shift+F4 reliable even though diagnostics are produced by
        a plugin rather than by Sublime's built-in exec build target.
        """
        state = self.state_for(window)
        with state.lock:
            root = state.root
            items = [item for item in state.diagnostics if self._is_navigable(item)]
            if not root or not items:
                window.status_message("Rust Problems: no navigable diagnostics")
                return

            if direction >= 0:
                index = (state.navigation_index + 1) % len(items)
            else:
                if state.navigation_index < 0:
                    index = len(items) - 1
                else:
                    index = (state.navigation_index - 1) % len(items)
            state.navigation_index = index
            diagnostic = items[index]

        file_name = diagnostic.file_name or ""
        full_path = file_name if os.path.isabs(file_name) else os.path.join(root, file_name)
        line = diagnostic.line or 1
        column = diagnostic.column or 1
        encoded = f"{full_path}:{line}:{column}"
        window.open_file(encoded, sublime.ENCODED_POSITION)

        code = f" [{diagnostic.code}]" if diagnostic.code else ""
        window.status_message(
            f"Rust problem {index + 1}/{len(items)} — {diagnostic.level}{code}: {diagnostic.message}"
        )

    def open_diagnostic(self, window: sublime.Window, diagnostic_index: int) -> None:
        state = self.state_for(window)
        with state.lock:
            root = state.root
            diagnostics = list(state.diagnostics)
            if not root or diagnostic_index < 0 or diagnostic_index >= len(diagnostics):
                return
            diagnostic = diagnostics[diagnostic_index]
            if not self._is_navigable(diagnostic):
                return

        file_name = diagnostic.file_name or ""
        full_path = file_name if os.path.isabs(file_name) else os.path.join(root, file_name)
        line = diagnostic.line or 1
        column = diagnostic.column or 1
        window.open_file(f"{full_path}:{line}:{column}", sublime.ENCODED_POSITION)

    @staticmethod
    def _is_navigable(diagnostic: Diagnostic) -> bool:
        return bool(diagnostic.file_name and diagnostic.line is not None)

    # ------------------------------------------------------------------
    # Cargo checking
    # ------------------------------------------------------------------

    def _start_if_latest(self, window_id: int, version: int) -> None:
        window = self._find_window(window_id)
        if not window:
            return
        state = self.state_for(window)

        with state.lock:
            if version != state.request_version or not state.root:
                return
            if state.process is not None and state.process.poll() is None:
                return
            root = state.root

        self._run_check(window, state, root, version)

    def _run_check(self, window: sublime.Window, state: WindowState, root: str, version: int) -> None:
        settings = self.settings()
        cargo = self._find_cargo(settings.get("cargo_path", ""))
        if not cargo:
            self._finish_with_error(
                window,
                state,
                version,
                'Cargo executable not found. Set "cargo_path" in Rust Problems settings.',
            )
            return

        args = settings.get(
            "cargo_args",
            ["check", "--workspace", "--message-format=json"],
        )
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            self._finish_with_error(
                window,
                state,
                version,
                'The Rust Problems setting "cargo_args" must be an array of strings.',
            )
            return

        command = [cargo] + list(args)
        env = os.environ.copy()
        cargo_bin = os.path.join(os.path.expanduser("~"), ".cargo", "bin")
        env["PATH"] = cargo_bin + os.pathsep + env.get("PATH", "")
        env["CARGO_TERM_COLOR"] = "never"

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._finish_with_error(window, state, version, f"Unable to start Cargo: {exc}")
            return

        with state.lock:
            state.process = process

        stdout, stderr = process.communicate()
        return_code = process.returncode

        with state.lock:
            if state.process is process:
                state.process = None
            newest_version = state.request_version
            current_root = state.root

        if current_root != root:
            return

        if version != newest_version:
            self._start_if_latest(window.id(), newest_version)
            return

        include_external = bool(settings.get("include_external_diagnostics", False))
        diagnostics = parse_cargo_json_lines(stdout, root, include_external=include_external)

        if return_code != 0 and not diagnostics:
            detail = stderr.strip() or stdout.strip() or f"Cargo exited with status {return_code}."
            self._finish_with_error(window, state, version, detail)
            return

        with state.lock:
            state.diagnostics = diagnostics
            state.checked_once = True
            state.navigation_index = -1

        sublime.set_timeout(lambda: self._publish_results(window.id(), version), 0)

    def _publish_results(self, window_id: int, version: int) -> None:
        window = self._find_window(window_id)
        if not window:
            return
        state = self.state_for(window)
        with state.lock:
            if version != state.request_version or not state.root:
                return
            root = state.root
            diagnostics = list(state.diagnostics)

        text = render_panel(diagnostics, root)
        self._update_status(window, state)

        # Refresh every Problems surface that already exists, regardless of
        # which view currently has keyboard focus. In particular, a visible
        # output panel may not be the active control while the user is editing.
        # Fixed diagnostics therefore disappear as soon as the new cargo check
        # completes, including the transition from one problem to zero.
        self._update_problems_view_if_open(window, text, diagnostics)
        self._update_output_panel_if_created(window, text)

        settings = self.settings()
        errors, warnings = count_diagnostics(diagnostics)
        if settings.get("show_panel_on_error", False) and errors > 0:
            self._write_panel(window, text)
            window.run_command("show_panel", {"panel": PANEL_FULL_NAME})
        elif settings.get("show_panel_on_warning", False) and warnings > 0:
            self._write_panel(window, text)
            window.run_command("show_panel", {"panel": PANEL_FULL_NAME})

    def _finish_with_error(self, window: sublime.Window, state: WindowState, version: int, message: str) -> None:
        with state.lock:
            if version != state.request_version:
                return
            state.checked_once = True
            state.diagnostics = []
            state.navigation_index = -1
            state.process = None

        def publish() -> None:
            current_window = self._find_window(window.id())
            if not current_window:
                return
            self._set_status_for_project_views(current_window, state.root, "Rust Problems: check failed")
            text = (
                "RUST PROBLEMS\n"
                + "=" * 72
                + f"\nProject: {state.root or 'unknown'}\n\nCheck failed:\n{message}\n"
            )
            self._write_panel(current_window, text)
            self._update_problems_view_if_open(current_window, text)
            if self.settings().get("show_panel_on_check_failure", True):
                current_window.run_command("show_panel", {"panel": PANEL_FULL_NAME})

        sublime.set_timeout(publish, 0)

    def _find_cargo(self, configured: str) -> Optional[str]:
        if configured:
            expanded = os.path.expanduser(configured)
            if os.path.isfile(expanded):
                return expanded
            found = shutil.which(expanded)
            if found:
                return found

        found = shutil.which("cargo")
        if found:
            return found

        candidates = [
            os.path.expanduser("~/.cargo/bin/cargo"),
            "/opt/homebrew/bin/cargo",
            "/usr/local/bin/cargo",
        ]
        if os.name == "nt":
            user_profile = os.environ.get("USERPROFILE", "")
            if user_profile:
                candidates.append(os.path.join(user_profile, ".cargo", "bin", "cargo.exe"))

        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    # ------------------------------------------------------------------
    # Status / views
    # ------------------------------------------------------------------

    def _set_checking_status(self, window: sublime.Window, state: WindowState) -> None:
        self._set_status_for_project_views(window, state.root, "Rust Problems: checking…")

    def _update_status(self, window: sublime.Window, state: WindowState) -> None:
        with state.lock:
            root = state.root
            checked_once = state.checked_once
            diagnostics = list(state.diagnostics)
            running = state.process is not None and state.process.poll() is None

        if not root:
            for view in window.views():
                view.erase_status(STATUS_KEY)
            return

        if running:
            text = "Rust Problems: checking…"
        elif not checked_once:
            text = "Rust Problems: ready"
        else:
            errors, warnings = count_diagnostics(diagnostics)
            if errors == 0 and warnings == 0:
                text = "Rust ✓"
            else:
                text = f"Rust ⊗ {errors}  ⚠ {warnings}"

        self._set_status_for_project_views(window, root, text)

    def _set_status_for_project_views(self, window: sublime.Window, root: Optional[str], text: str) -> None:
        for view in window.views():
            file_name = view.file_name()
            if root and file_name and is_within(file_name, root):
                view.set_status(STATUS_KEY, text)
            else:
                view.erase_status(STATUS_KEY)

    def _find_problems_view(self, window: sublime.Window) -> Optional[sublime.View]:
        for view in window.views():
            if view.settings().get(PROBLEMS_VIEW_SETTING, False):
                return view
        return None

    def _write_problems_view(
        self,
        window: sublime.Window,
        text: str,
        focus: bool,
        diagnostics: Optional[list[Diagnostic]] = None,
    ) -> sublime.View:
        view = self._find_problems_view(window)
        if view is None:
            view = window.new_file()
            view.set_name(PROBLEMS_VIEW_NAME)
            view.set_scratch(True)
            view.settings().set(PROBLEMS_VIEW_SETTING, True)
            view.settings().set("word_wrap", False)
            try:
                view.assign_syntax(PROBLEMS_SYNTAX)
            except Exception:
                pass

        view.erase_regions(PROBLEMS_ANNOTATION_KEY)
        view.set_read_only(False)
        view.run_command("select_all")
        view.run_command("right_delete")
        view.run_command("append", {"characters": text, "force": True, "scroll_to_end": False})
        view.sel().clear()
        view.sel().add(sublime.Region(0, 0))
        view.set_read_only(True)
        view.set_scratch(True)

        if diagnostics:
            self._decorate_problems_view(window, view, diagnostics)

        if focus:
            window.focus_view(view)
        return view

    def _decorate_problems_view(
        self,
        window: sublime.Window,
        view: sublime.View,
        diagnostics: list[Diagnostic],
    ) -> None:
        # The rendered view contains one primary location line per diagnostic.
        # Place a clickable "Open" annotation on each navigable location.
        locations = view.find_all(r"^.+?:\d+:\d+:\s+(?:error|warning)(?:\[[^\]]+\])?:")
        diagnostic_indices = [
            index for index, item in enumerate(diagnostics) if self._is_navigable(item)
        ]
        count = min(len(locations), len(diagnostic_indices))
        if count == 0:
            return

        regions = [view.line(locations[i]) for i in range(count)]
        annotations = [
            f'<a href="{diagnostic_indices[i]}">Open</a>' for i in range(count)
        ]

        def on_navigate(href: str) -> None:
            try:
                index = int(href)
            except ValueError:
                return
            current_window = self._find_window(window.id())
            if current_window:
                self.open_diagnostic(current_window, index)

        view.add_regions(
            PROBLEMS_ANNOTATION_KEY,
            regions,
            scope="",
            annotations=annotations,
            on_navigate=on_navigate,
        )

    def _update_problems_view_if_open(
        self,
        window: sublime.Window,
        text: str,
        diagnostics: Optional[list[Diagnostic]] = None,
    ) -> None:
        if self._find_problems_view(window) is not None:
            self._write_problems_view(
                window, text, focus=False, diagnostics=diagnostics
            )

    def _update_output_panel_if_created(self, window: sublime.Window, text: str) -> None:
        """Refresh the Rust Problems output panel if it has been created.

        `find_output_panel()` does not create a hidden panel, so users who only
        use the normal Problems tab do not pay for an output-panel view.
        """
        panel = window.find_output_panel(PANEL_NAME)
        if panel is not None:
            self._write_panel_view(window, panel, text)

    def _write_panel(self, window: sublime.Window, text: str) -> None:
        panel = window.create_output_panel(PANEL_NAME)
        self._write_panel_view(window, panel, text)

    def _write_panel_view(self, window: sublime.Window, panel: sublime.View, text: str) -> None:
        settings = panel.settings()
        settings.set(
            "result_file_regex",
            r"^(.+?):(\d+):(\d+):\s+(?:error|warning)(?:\[[^\]]+\])?:\s+(.*)$",
        )
        state = self.state_for(window)
        if state.root:
            settings.set("result_base_dir", state.root)
        settings.set("word_wrap", True)
        panel.set_read_only(False)
        panel.run_command("select_all")
        panel.run_command("right_delete")
        panel.run_command("append", {"characters": text, "force": True, "scroll_to_end": False})
        panel.set_read_only(True)

    def _terminate_process(self, state: WindowState) -> None:
        process = state.process
        state.process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _find_window(window_id: int) -> Optional[sublime.Window]:
        for window in sublime.windows():
            if window.id() == window_id:
                return window
        return None


_MANAGER: Optional[RustProblemsManager] = None


def manager() -> RustProblemsManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RustProblemsManager()
    return _MANAGER


def plugin_loaded() -> None:
    mgr = manager()
    for window in sublime.windows():
        view = window.active_view()
        if mgr.ensure_project(window, view):
            settings = mgr.settings()
            if settings.get("check_on_project_open", True):
                mgr.schedule_check(window, delay_ms=int(settings.get("initial_check_delay_ms", 300)))


def plugin_unloaded() -> None:
    global _MANAGER
    if _MANAGER is not None:
        _MANAGER.shutdown()
        _MANAGER = None


class RustProblemsEventListener(sublime_plugin.EventListener):
    def on_activated_async(self, view: sublime.View) -> None:
        if view.settings().get(PROBLEMS_VIEW_SETTING, False):
            return
        window = view.window()
        if not window:
            return
        mgr = manager()
        root = mgr.ensure_project(window, view)
        if not root:
            return
        state = mgr.state_for(window)
        with state.lock:
            should_check = not state.checked_once and state.process is None
        if should_check and mgr.settings().get("check_on_project_open", True):
            mgr.schedule_check(window, delay_ms=int(mgr.settings().get("initial_check_delay_ms", 300)))

    def on_post_save_async(self, view: sublime.View) -> None:
        window = view.window()
        file_name = view.file_name()
        if not window or not file_name:
            return

        base_name = os.path.basename(file_name)
        if not (file_name.endswith(".rs") or base_name in ("Cargo.toml", "Cargo.lock")):
            return

        mgr = manager()
        if mgr.settings().get("check_on_save", True):
            mgr.schedule_check(window, view=view)

    def on_query_context(
        self,
        view: sublime.View,
        key: str,
        operator: sublime.QueryOperator,
        operand,
        match_all: bool,
    ) -> Optional[bool]:
        if key != CONTEXT_HAS_DIAGNOSTICS:
            return None

        window = view.window()
        value = bool(window and manager().has_navigable_diagnostics(window))
        expected = bool(operand)

        if operator == sublime.QueryOperator.EQUAL:
            return value == expected
        if operator == sublime.QueryOperator.NOT_EQUAL:
            return value != expected
        return False

    def on_pre_close_window(self, window: sublime.Window) -> None:
        manager().remove_window(window.id())


class RustProblemsCheckProjectCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().check_now(self.window)

    def is_enabled(self) -> bool:
        return manager().resolve_root(self.window) is not None


class RustProblemsShowPanelCommand(sublime_plugin.WindowCommand):
    """Backwards-compatible command name; now opens the normal Problems tab."""

    def run(self) -> None:
        manager().show_problems_view(self.window)


class RustProblemsShowOutputPanelCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().show_output_panel(self.window)

    def is_visible(self) -> bool:
        # Only offer Show when our output panel is not already visible.
        return self.window.active_panel() != PANEL_FULL_NAME


class RustProblemsHideOutputPanelCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        if self.window.active_panel() == PANEL_FULL_NAME:
            self.window.run_command("hide_panel")

    def is_visible(self) -> bool:
        # This command shares the same menu position as Show Output Panel.
        # Sublime calls is_visible() as menus are built, so only the action
        # that makes sense for the current panel state is displayed.
        return self.window.active_panel() == PANEL_FULL_NAME


class RustProblemsTogglePanelCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().toggle_panel(self.window)


class RustProblemsClearCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().clear(self.window)


class RustProblemsNextCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().navigate(self.window, 1)

    def is_enabled(self) -> bool:
        return manager().has_navigable_diagnostics(self.window)


class RustProblemsPreviousCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        manager().navigate(self.window, -1)

    def is_enabled(self) -> bool:
        return manager().has_navigable_diagnostics(self.window)

"""Manager regressions with a minimal Sublime API; no editor required."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

PACKAGE = '_rust_problems_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
sublime = types.ModuleType('sublime')
sublime.ENCODED_POSITION = 1
sublime_plugin = types.ModuleType('sublime_plugin')
sublime_plugin.EventListener = object
sublime_plugin.WindowCommand = object
with patch.dict(sys.modules, sublime=sublime, sublime_plugin=sublime_plugin):
    plugin = importlib.import_module(PACKAGE + '.RustProblems')


class ManagerTests(unittest.TestCase):
    def test_invalid_explicit_cargo_does_not_fall_back(self):
        with patch.object(plugin.os.path, 'isfile', return_value=False), \
                patch.object(plugin.shutil, 'which', return_value=None) as which:
            self.assertIsNone(self.manager._find_cargo('/missing/custom/cargo'))
        which.assert_called_once_with('/missing/custom/cargo')

    def test_non_executable_explicit_cargo_is_rejected(self):
        with patch.object(plugin.os.path, 'isfile', return_value=True), \
                patch.object(plugin.os, 'access', return_value=False), \
                patch.object(plugin.shutil, 'which', return_value=None):
            self.assertIsNone(self.manager._find_cargo('/custom/cargo'))

    def test_explicit_cargo_command_is_resolved_on_path(self):
        with patch.object(plugin.os.path, 'isfile', return_value=False), \
                patch.object(plugin.shutil, 'which', return_value='/tools/cargo'):
            self.assertEqual('/tools/cargo', self.manager._find_cargo('custom-cargo'))

    def setUp(self):
        self.window = Mock()
        self.window.id.return_value = 1
        self.window.views.return_value = []
        self.main_callbacks = []
        self.async_callbacks = []
        sublime.windows = lambda: [self.window]
        sublime.set_timeout = lambda callback, delay=0: self.main_callbacks.append(callback)
        sublime.set_timeout_async = lambda callback, delay=0: self.async_callbacks.append(callback)
        self.manager = plugin.RustProblemsManager()
        self.manager.settings = lambda: {}
        self.manager._set_status_for_project_views = Mock()
        self.manager._write_panel = Mock()
        self.manager._write_problems_view = Mock()
        self.manager._update_problems_view_if_open = Mock()
        self.state = self.manager.state_for(self.window)
        self.state.root = '/project'
        self.manager.resolve_root = lambda window, view=None: self.state.root

    def drain_main(self):
        while self.main_callbacks:
            self.main_callbacks.pop(0)()

    def test_failure_survives_activation_and_reopening(self):
        self.manager._finish_with_error(self.window, self.state, 0, 'broken manifest')
        self.drain_main()
        self.manager.ensure_project(self.window)
        self.assertEqual('Rust Problems: check failed',
                         self.manager._set_status_for_project_views.call_args.args[2])
        self.manager.show_problems_view(self.window)
        self.assertIn('broken manifest', self.manager._write_problems_view.call_args.args[1])
        self.manager.show_output_panel(self.window)
        self.assertIn('broken manifest', self.manager._write_panel.call_args.args[1])

    def test_clear_invalidates_queued_failure(self):
        self.manager._finish_with_error(self.window, self.state, 0, 'old error')
        self.manager.clear(self.window)
        self.manager._write_panel.reset_mock()
        self.drain_main()
        self.manager._write_panel.assert_not_called()
        self.assertIsNone(self.state.failure)

    def test_new_request_invalidates_queued_failure(self):
        self.manager._finish_with_error(self.window, self.state, 0, 'old error')
        self.manager.schedule_check(self.window)
        self.drain_main()
        self.manager._write_panel.assert_not_called()
        self.assertTrue(self.state.checking)

    def test_close_invalidates_callbacks_without_recreating_state(self):
        self.manager._finish_with_error(self.window, self.state, 0, 'old error')
        self.manager.remove_window(1)
        self.drain_main()
        self.manager._publish_results(1, 0)
        self.manager._start_if_latest(1, 0)
        self.manager._write_panel.assert_not_called()
        self.assertEqual({}, self.manager._states)

    def test_checking_does_not_render_a_clean_result(self):
        self.state.checking = True
        self.manager.show_problems_view(self.window)
        text = self.manager._write_problems_view.call_args.args[1]
        self.assertIn('Checking', text)
        self.assertNotIn('No Rust compiler', text)

    def test_saves_during_worker_coalesce_to_latest_request(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def run(window, state, root, version):
            calls.append(version)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            else:
                finished.set()
        self.manager._run_check = run
        # Record threads so cleanup does not depend on scheduling timing.
        real_thread = threading.Thread
        threads = []
        def make_thread(**kwargs):
            thread = real_thread(**kwargs)
            threads.append(thread)
            return thread
        with patch.object(plugin.threading, 'Thread', side_effect=make_thread):
            self.manager.schedule_check(self.window)
            self.async_callbacks.pop(0)()
            try:
                self.assertTrue(entered.wait(2))
                # The callback returned while Cargo was still blocked.
                self.manager.schedule_check(self.window)
                self.manager.schedule_check(self.window)
                for callback in list(self.async_callbacks):
                    callback()
                self.async_callbacks.clear()
                self.assertEqual([1], calls)
            finally:
                release.set()
                threads[0].join(3)
            self.assertEqual(1, len(self.async_callbacks))
            self.async_callbacks.pop(0)()
            self.assertTrue(finished.wait(2))
            threads[-1].join(3)
        self.assertEqual([1, 3], calls)

    def test_clear_cancels_process_and_pending_rerun(self):
        process = Mock()
        process.poll.return_value = None
        self.state.process = process
        self.state.checking = True
        self.state.pending_version = 0
        self.manager.clear(self.window)
        process.terminate.assert_called_once()
        self.manager._start_if_latest(1, 0)
        self.assertFalse(self.state.worker_running)
        self.assertIsNone(self.state.pending_version)

    def test_stale_success_cannot_replace_current_state(self):
        metadata = json.dumps({
            'workspace_root': '/project', 'workspace_members': [], 'packages': [],
        })
        self.manager._find_cargo = lambda configured: 'cargo'
        def capture(state, version, command, root, env, flags):
            if 'metadata' in command:
                return 0, metadata, ''
            self.manager.clear(self.window)
            return 0, '', ''
        self.manager._capture = capture
        self.manager._run_check(self.window, self.state, '/project', 0)
        self.assertFalse(self.state.checked_once)
        self.assertEqual([], self.main_callbacks)

    def test_failed_cargo_with_only_warnings_is_still_a_failure(self):
        metadata = json.dumps({
            'workspace_root': '/project', 'workspace_members': [], 'packages': [],
        })
        warning = json.dumps({'reason': 'compiler-message', 'message': {
            'level': 'warning', 'message': 'unused import', 'spans': [],
        }})
        self.manager._find_cargo = lambda configured: 'cargo'
        self.manager._capture = Mock(side_effect=[
            (0, metadata, ''), (101, warning, 'build script failed'),
        ])
        self.manager._run_check(self.window, self.state, '/project', 0)
        self.assertEqual('build script failed', self.state.failure)

    def test_refresh_preserves_selection_and_viewport(self):
        view = Mock()
        selection = [types.SimpleNamespace(a=8, b=15)]
        regions = Mock()
        regions.__iter__ = Mock(side_effect=lambda: iter(selection))
        view.sel.return_value = regions
        view.viewport_position.return_value = (0, 120)
        view.size.return_value = 10
        self.manager._find_problems_view = lambda window: view
        sublime.Region = lambda a, b: (a, b)
        # Invoke the real method, bypassing the rendering mock from setUp.
        plugin.RustProblemsManager._write_problems_view(
            self.manager, self.window, 'new output', False,
        )
        regions.add.assert_called_once_with((8, 10))
        view.set_viewport_position.assert_called_once_with((0, 120), False)
        self.window.focus_view.assert_not_called()

    @unittest.skipUnless(shutil.which('cargo'), 'Cargo is required for workspace integration')
    def test_real_workspace_paths_and_member_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'Cargo.toml').write_text('[workspace]\nmembers=["a","b"]\nresolver="2"\n')
            for name in ('a', 'b'):
                crate = root / name
                (crate / 'src').mkdir(parents=True)
                (crate / 'Cargo.toml').write_text(
                    '[package]\nname="' + name + '"\nversion="0.1.0"\nedition="2021"\n')
                (crate / 'src/lib.rs').write_text('pub fn demo() { let _x: u32 = "wrong"; }\n')
            self.state.root = str(root / 'a')
            self.manager.settings = lambda: {'cargo_args': ['check', '--workspace', '--offline', '--message-format=json']}
            self.manager._run_check(self.window, self.state, self.state.root, 0)
            self.assertIsNone(self.state.failure)
            self.assertEqual(str(root), self.state.workspace_root)
            paths = {item.file_name for item in self.state.diagnostics if item.file_name}
            self.assertEqual({'a/src/lib.rs', 'b/src/lib.rs'}, paths)
            self.assertTrue(all((root / path).is_file() for path in paths))
            self.manager.open_diagnostic(self.window, 0)
            opened = self.window.open_file.call_args.args[0].rsplit(':', 2)[0]
            self.assertTrue(os.path.isfile(opened))
            self.manager.resolve_root = lambda window, view=None: str(root / 'b')
            previous = list(self.state.diagnostics)
            self.manager.ensure_project(self.window)
            self.assertEqual(previous, self.state.diagnostics)
            # A subsequent successful check replaces failures and diagnostics.
            for name in ('a', 'b'):
                (root / name / 'src/lib.rs').write_text('pub fn demo() {}\n')
            self.state.failure = 'previous failure'
            self.manager._run_check(self.window, self.state, self.state.root, 0)
            self.assertIsNone(self.state.failure)
            self.assertEqual([], self.state.diagnostics)
            self.assertIn('No Rust compiler errors', self.manager._result_text(self.state))


if __name__ == '__main__':
    unittest.main()

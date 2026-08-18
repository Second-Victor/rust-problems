import json
import importlib.util
import os
import tempfile
import types
import unittest

from cargo import build_cargo_command, find_cargo_root, parse_cargo_json_lines


def compiler_message(level, message, spans, code=None):
    return json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "level": level,
                "message": message,
                "code": {"code": code} if code else None,
                "spans": spans,
            },
        }
    )


def span(filename="src/main.rs", line=12, column=9, **extra):
    value = {
        "file_name": filename,
        "line_start": line,
        "line_end": line,
        "column_start": column,
        "column_end": column + 5,
        "is_primary": True,
    }
    value.update(extra)
    return value


class CargoRootTests(unittest.TestCase):
    def test_finds_crate_for_a_file_at_the_crate_root(self):
        with TemporaryCargoProject() as root:
            source = os.path.join(root, "main.rs")
            touch(source)
            self.assertEqual(root, find_cargo_root(source))

    def test_finds_nearest_manifest_for_a_nested_module(self):
        with TemporaryCargoProject() as root:
            source = os.path.join(root, "src", "world", "grid.rs")
            touch(source)
            self.assertEqual(root, find_cargo_root(source))

    def test_prefers_workspace_member_manifest(self):
        with tempfile.TemporaryDirectory() as workspace:
            touch(
                os.path.join(workspace, "Cargo.toml"),
                "[workspace]\nmembers = ['member']\n",
            )
            member = os.path.join(workspace, "member")
            touch(os.path.join(member, "Cargo.toml"), "[package]\nname = 'member'\n")
            source = os.path.join(member, "src", "lib.rs")
            touch(source)
            self.assertEqual(member, find_cargo_root(source))

    def test_returns_none_without_a_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "src", "lib.rs")
            touch(source)
            self.assertIsNone(find_cargo_root(source))


class CargoJsonTests(unittest.TestCase):
    def test_parses_error_warning_codes_and_end_positions(self):
        output = "\n".join(
            [
                compiler_message("error", "mismatched types", [span()], "E0308"),
                compiler_message("warning", "unused import", [span(line=3)], "unused_imports"),
            ]
        )
        diagnostics = list(parse_cargo_json_lines(output, "/project"))
        self.assertEqual(2, len(diagnostics))
        self.assertEqual(("error", "E0308", 11, 8, 11, 13), (
            diagnostics[0].level,
            diagnostics[0].code,
            diagnostics[0].line,
            diagnostics[0].column,
            diagnostics[0].end_line,
            diagnostics[0].end_column,
        ))
        self.assertEqual(("warning", "unused_imports"), (diagnostics[1].level, diagnostics[1].code))

    def test_prefers_primary_span_and_reports_multiple_files(self):
        output = "\n".join(
            [
                compiler_message(
                    "error",
                    "first",
                    [span("src/secondary.rs", is_primary=False), span("src/main.rs")],
                ),
                compiler_message("warning", "second", [span("src/other.rs", line=4)], "dead_code"),
            ]
        )
        diagnostics = list(parse_cargo_json_lines(output, "/project"))
        self.assertEqual(["/project/src/main.rs", "/project/src/other.rs"], [d.filename for d in diagnostics])

    def test_uses_first_valid_span_when_no_primary_span_is_marked(self):
        output = compiler_message(
            "error",
            "fallback span",
            [span("src/first.rs", is_primary=False), span("src/second.rs", is_primary=False)],
        )
        diagnostic = next(parse_cargo_json_lines(output, "/project"))
        self.assertEqual("/project/src/first.rs", diagnostic.filename)

    def test_ignores_noise_non_diagnostics_notes_and_unlocated_messages(self):
        output = "\n".join(
            [
                "Downloading crates ...",
                "{not json}",
                json.dumps({"reason": "build-finished", "success": False}),
                compiler_message("note", "a note", [span()]),
                compiler_message("help", "a suggestion", [span()]),
                compiler_message("error", "global failure", [], "E0000"),
                compiler_message("warning", "no code", [span(line=2)]),
            ]
        )
        diagnostics = list(parse_cargo_json_lines(output, "/project"))
        self.assertEqual(1, len(diagnostics))
        self.assertIsNone(diagnostics[0].code)

    def test_keeps_absolute_and_windows_paths_and_spaces(self):
        output = "\n".join(
            [
                compiler_message("error", "absolute", [span("/tmp/a project/src/main.rs")]),
                compiler_message("error", "windows", [span("C:\\project with spaces\\src\\main.rs", line=2)]),
            ]
        )
        diagnostics = list(parse_cargo_json_lines(output, "/project"))
        self.assertEqual("/tmp/a project/src/main.rs", diagnostics[0].filename)
        self.assertEqual(r"C:\project with spaces\src\main.rs", diagnostics[1].filename)


class CommandAndMetadataTests(unittest.TestCase):
    def test_user_args_are_kept_before_mandatory_json_output(self):
        command = build_cargo_command(["--workspace", "--all-features"])
        self.assertEqual(("cargo", "check"), command[:2])
        self.assertEqual(("--workspace", "--all-features"), command[2:-1])
        self.assertEqual("--message-format=json", command[-1])

    def test_required_package_files_exist_and_old_diagnostics_ui_is_gone(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for filename in (
            "linter.py",
            "cargo.py",
            "status.py",
            "commands.py",
            "problems_core.py",
            "README.md",
            "LICENSE",
            "messages.json",
            "Default.sublime-commands",
            "Main.sublime-menu",
        ):
            self.assertTrue(os.path.isfile(os.path.join(root, filename)), filename)
        for filename in (
            "RustProblems.py",
            "Default.sublime-keymap",
            "Rust Problems.sublime-settings",
        ):
            self.assertFalse(os.path.exists(os.path.join(root, filename)), filename)

    def test_problems_tab_reads_sublimelinter_store_and_adds_open_links(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "commands.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("persist.file_errors.items()", source)
        self.assertIn("view.add_regions(", source)
        self.assertIn("annotations=annotations", source)
        self.assertIn("on_navigate=on_navigate", source)
        self.assertIn("sublime.ENCODED_POSITION", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("cargo check", source)

    def test_rust_problems_menu_opens_the_tab(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "Main.sublime-menu"), encoding="utf-8") as handle:
            menu = json.load(handle)
        rust_menu = menu[0]["children"][1]
        self.assertEqual("Rust Problems", rust_menu["caption"])
        self.assertEqual(2, len(rust_menu["children"]))
        self.assertEqual(
            "rust_problems_show_problems",
            rust_menu["children"][0]["command"],
        )
        self.assertEqual(
            "rust_problems_open_sublime_linter_problems",
            rust_menu["children"][1]["command"],
        )

    def test_native_sublimelinter_panel_command_only_opens_panel(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "commands.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(
            'self.window.run_command("show_panel", {"panel": "output.SublimeLinter"})',
            source,
        )

    def test_pretty_problems_view_has_custom_syntax_and_grouped_layout(self):
        root = os.path.dirname(os.path.dirname(__file__))
        self.assertTrue(os.path.isfile(os.path.join(root, "Rust Problems.sublime-syntax")))
        with open(os.path.join(root, "commands.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("Rust Problems.sublime-syntax", source)
        self.assertIn('"Project   {}"', source)
        self.assertIn('"✕  {} {}      ⚠  {} {}"', source)
        self.assertIn("format_location_line(item)", source)
        self.assertIn("message_lines(item)", source)

    def test_pretty_location_formatter(self):
        from problems_core import format_location_line, message_lines

        item = {
            "line": 11,
            "start": 4,
            "error_type": "error",
            "code": "E0308",
            "msg": "mismatched types\nexpected i32",
        }
        self.assertEqual("  12:5     ERROR    E0308", format_location_line(item))
        self.assertEqual(
            ("          mismatched types", "          expected i32"),
            message_lines(item),
        )


class SublimeLinterAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(__file__))
        fake_framework = types.ModuleType("SublimeLinter")
        fake_lint = types.ModuleType("SublimeLinter.lint")

        class FakeLinter:
            pass

        class FakeLintMatch(dict):
            pass

        class FakePermanentError(Exception):
            pass

        fake_lint.Linter = FakeLinter
        fake_lint.LintMatch = FakeLintMatch
        fake_lint.PermanentError = FakePermanentError
        import sys

        sys.modules["SublimeLinter"] = fake_framework
        sys.modules["SublimeLinter.lint"] = fake_lint
        package = types.ModuleType("_cargo_adapter_test")
        package.__path__ = [root]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(
            "_cargo_adapter_test.linter", os.path.join(root, "linter.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.Cargo = module.Cargo
        cls.FakeLinter = FakeLinter

    def test_uses_the_current_linter_api_and_file_only_mode(self):
        self.assertTrue(issubclass(self.Cargo, self.FakeLinter))
        self.assertIsNone(self.Cargo.regex)
        self.assertEqual("-", self.Cargo.tempfile_suffix)
        self.assertEqual("source.rust", self.Cargo.defaults["selector"])

    def test_linter_command_keeps_the_json_flag_after_sublimelinter_args(self):
        adapter = object.__new__(self.Cargo)
        adapter.settings = {"working_dir": "/project"}
        self.assertEqual(
            ("cargo", "check", "${args}", "--message-format=json"),
            adapter.cmd(),
        )

    def test_cargo_stderr_does_not_mark_linter_failed(self):
        adapter = object.__new__(self.Cargo)

        class Logger:
            def __init__(self):
                self.messages = []

            def info(self, message):
                self.messages.append(message)

        adapter.logger = Logger()
        adapter.notify_failure = lambda: self.fail("Cargo stderr must not mark the linter failed")
        adapter.on_stderr("    Checking demo v0.1.0\n")
        self.assertIn("Checking demo", adapter.logger.messages[0])

    def test_find_errors_yields_cross_file_sublimelinter_matches(self):
        adapter = object.__new__(self.Cargo)
        adapter.get_working_dir = lambda: "/project"
        output = compiler_message(
            "error", "mismatched types", [span("src/other.rs")], "E0308"
        )
        matches = list(adapter.find_errors(output))
        self.assertEqual(1, len(matches))
        self.assertEqual("/project/src/other.rs", matches[0]["filename"])
        self.assertEqual("error", matches[0]["error_type"])
        self.assertEqual("E0308", matches[0]["code"])
        self.assertEqual(
            (11, 8, 11, 13),
            (
                matches[0]["line"],
                matches[0]["col"],
                matches[0]["end_line"],
                matches[0]["end_col"],
            ),
        )


class TemporaryCargoProject:
    def __enter__(self):
        self._directory = tempfile.TemporaryDirectory()
        touch(os.path.join(self._directory.name, "Cargo.toml"), "[package]\nname = 'demo'\n")
        return self._directory.name

    def __exit__(self, *args):
        self._directory.cleanup()


def touch(filename, contents=""):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as handle:
        handle.write(contents)


class StatusCounterTests(unittest.TestCase):
    def test_formats_zero_and_nonzero_counts(self):
        from status_core import format_status

        self.assertEqual("Rust ✓", format_status(0, 0))
        self.assertEqual("Rust ⊗ 3  ⚠ 2", format_status(3, 2))

    def test_counts_only_errors_and_warnings(self):
        from status_core import count_diagnostics

        self.assertEqual(
            (2, 1),
            count_diagnostics([
                {"error_type": "error"},
                {"error_type": "warning"},
                {"error_type": "error"},
                {"error_type": "note"},
            ]),
        )

    def test_is_within_does_not_accept_similar_prefix(self):
        from status_core import is_within

        with tempfile.TemporaryDirectory() as root:
            inside = os.path.join(root, "src", "main.rs")
            touch(inside)
            outside = os.path.join(root + "-other", "main.rs")
            touch(outside)
            self.assertTrue(is_within(inside, root))
            self.assertFalse(is_within(outside, root))


class ProblemsTabFormattingTests(unittest.TestCase):
    def test_formats_navigable_location_and_code(self):
        from problems_core import format_diagnostic_line

        item = {
            "filename": "/project/src/grid.rs",
            "line": 4,
            "start": 7,
            "error_type": "error",
            "code": "E0308",
            "msg": "mismatched types",
        }
        self.assertEqual(
            "src/grid.rs:5:8: error[E0308]: mismatched types",
            format_diagnostic_line(item, "/project"),
        )

    def test_multiline_messages_have_indented_continuations(self):
        from problems_core import continuation_lines

        self.assertEqual(
            ("    second line", "    third line"),
            continuation_lines({"msg": "first line\nsecond line\nthird line"}),
        )

    def test_counts_tab_summary(self):
        from problems_core import count_errors_and_warnings

        self.assertEqual(
            (1, 2),
            count_errors_and_warnings([
                {"error_type": "warning"},
                {"error_type": "error"},
                {"error_type": "warning"},
            ]),
        )

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

    def test_required_package_files_exist_and_old_ui_files_are_gone(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for filename in ("linter.py", "cargo.py", "README.md", "LICENSE", "messages.json"):
            self.assertTrue(os.path.isfile(os.path.join(root, filename)), filename)
        for filename in (
            "RustProblems.py",
            "Default.sublime-commands",
            "Default.sublime-keymap",
            "Main.sublime-menu",
            "Rust Problems.sublime-settings",
            "Rust Problems.sublime-syntax",
        ):
            self.assertFalse(os.path.exists(os.path.join(root, filename)), filename)


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

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from rust_problems_core import (  # noqa: E402
    count_diagnostics,
    find_cargo_root,
    parse_cargo_json_lines,
    render_panel,
)


SAMPLE = r'''{"reason":"compiler-message","package_id":"path+file:///tmp/titan#0.1.0","message":{"rendered":"error[E0308]","$message_type":"diagnostic","children":[{"children":[],"code":null,"level":"note","message":"expected Vec3, found Vec2","rendered":null,"spans":[]}],"code":{"code":"E0308","explanation":null},"level":"error","message":"mismatched types","spans":[{"byte_end":10,"byte_start":2,"column_end":20,"column_start":17,"expansion":null,"file_name":"src/world/grid.rs","is_primary":true,"label":null,"line_end":42,"line_start":42,"suggested_replacement":null,"suggestion_applicability":null,"text":[]} ]}}
{"reason":"compiler-message","package_id":"path+file:///tmp/titan#0.1.0","message":{"rendered":"warning","$message_type":"diagnostic","children":[],"code":{"code":"unused_imports","explanation":null},"level":"warning","message":"unused import","spans":[{"byte_end":10,"byte_start":2,"column_end":10,"column_start":5,"expansion":null,"file_name":"src/world/mod.rs","is_primary":true,"label":null,"line_end":12,"line_start":12,"suggested_replacement":null,"suggestion_applicability":null,"text":[]} ]}}
{"reason":"build-finished","success":false}
'''


class CoreTests(unittest.TestCase):
    def test_parser_counts_errors_and_warnings(self):
        diagnostics = parse_cargo_json_lines(SAMPLE, "/tmp/titan")
        self.assertEqual((1, 1), count_diagnostics(diagnostics))
        self.assertEqual("E0308", diagnostics[0].code)
        self.assertEqual("src/world/grid.rs", diagnostics[0].file_name)
        self.assertEqual(42, diagnostics[0].line)

    def test_render_contains_navigable_location(self):
        diagnostics = parse_cargo_json_lines(SAMPLE, "/tmp/titan")
        panel = render_panel(diagnostics, "/tmp/titan")
        self.assertIn("src/world/grid.rs:42:17: error[E0308]: mismatched types", panel)
        self.assertIn("Errors: 1    Warnings: 1", panel)

    def test_finds_nearest_cargo_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            nested = os.path.join(root, "src", "world")
            os.makedirs(nested)
            with open(os.path.join(root, "Cargo.toml"), "w", encoding="utf-8") as handle:
                handle.write("[package]\nname='demo'\nversion='0.1.0'\n")
            source = os.path.join(nested, "grid.rs")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("fn main() {}\n")
            self.assertEqual(root, find_cargo_root(source, [root]))

    def test_main_menu_anchors_follow_sublime_order(self):
        import json

        menu_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Main.sublime-menu")
        with open(menu_path, "r", encoding="utf-8") as handle:
            menu = json.load(handle)

        anchor_ids = [entry.get("id") for entry in menu]
        self.assertEqual(["tools", "preferences"], anchor_ids)

        tools_children = menu[0]["children"]
        self.assertEqual("lsp", tools_children[0].get("id"))
        self.assertEqual("Rust Problems", tools_children[1].get("caption"))
        self.assertNotIn("id", tools_children[1])


if __name__ == "__main__":
    unittest.main()

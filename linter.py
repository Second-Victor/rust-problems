"""SublimeLinter adapter for Cargo's structured compiler diagnostics."""

from __future__ import annotations

import os
from typing import Iterable

from SublimeLinter.lint import Linter, LintMatch, PermanentError

from .cargo import (
    JSON_MESSAGE_FORMAT,
    build_cargo_command,
    find_cargo_root,
    parse_cargo_json_lines,
)


class Cargo(Linter):
    """Run ``cargo check`` for saved Rust files in their Cargo project."""

    regex = None
    tempfile_suffix = "-"
    defaults = {
        "selector": "source.rust",
    }

    def cmd(self):
        if self.settings.get("working_dir") or self._project_root():
            return build_cargo_command(("${args}",))

        self.logger.info("cargo: skipping file outside a Cargo project")
        self.notify_unassign()
        raise PermanentError("no Cargo.toml found")

    def get_working_dir(self):
        # A user-supplied SublimeLinter working_dir always wins.  Otherwise
        # run in the nearest package manifest directory; Cargo still discovers
        # its enclosing workspace and configuration from there.
        if self.settings.get("working_dir"):
            return super().get_working_dir()
        return self._project_root()

    def run(self, cmd, code):
        # ``tempfile_suffix = '-'`` is required to make this a saved-file-only
        # linter.  Cargo checks a project, not a source file, so bypass the
        # framework's normal file-argument auto-append behavior.
        assert cmd is not None
        return self._communicate(cmd)

    def find_errors(self, output: str) -> Iterable[LintMatch]:
        project_root = self.get_working_dir()
        if not project_root:
            return

        for diagnostic in parse_cargo_json_lines(output, project_root):
            yield LintMatch(
                filename=diagnostic.filename,
                line=diagnostic.line,
                col=diagnostic.column,
                end_line=diagnostic.end_line,
                end_col=diagnostic.end_column,
                error_type=diagnostic.level,
                code=diagnostic.code or "",
                message=diagnostic.message,
            )

    def which(self, cmd):
        found = super().which(cmd)
        if found or cmd != "cargo":
            return found

        # SublimeLinter's PATH/executable settings are authoritative.  These
        # fallbacks only cover GUI-launched Sublime Text instances whose PATH
        # does not include rustup's usual install directory.
        candidates = [
            os.path.expanduser("~/.cargo/bin/cargo"),
            "/opt/homebrew/bin/cargo",
            "/usr/local/bin/cargo",
        ]
        if os.name == "nt":
            user_profile = os.environ.get("USERPROFILE")
            if user_profile:
                candidates.insert(0, os.path.join(user_profile, ".cargo", "bin", "cargo.exe"))

        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _project_root(self):
        return find_cargo_root(self.view.file_name())


# Kept importable for command-construction unit tests and documentation.
__all__ = ["Cargo", "build_cargo_command"]

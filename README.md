# SublimeLinter-contrib-cargo

SublimeLinter integration for Cargo's Rust compiler diagnostics, powered by
`cargo check --message-format=json`.

This is a narrow Cargo adapter. SublimeLinter owns lint scheduling,
highlighting, diagnostics panels, navigation, status messages, filtering, and
per-project configuration.

## Features

- Cargo/rustc errors and warnings surfaced through SublimeLinter
- Structured Cargo JSON parsing — no scraping of human-readable rustc output
- Rust error and lint codes, such as `E0308` and `unused_imports`
- Exact start and end source locations
- Diagnostics from other files reported by the same Cargo check
- Nearest-Cargo-manifest working directory for crate and workspace members
- Standard SublimeLinter settings, including `args`, `executable`, `env`,
  `lint_mode`, `selector`, `working_dir`, `styles`, `filter_errors`, and
  `disable`

## Requirements

- Sublime Text 4 build 4205 or newer
- [SublimeLinter](https://packagecontrol.io/packages/SublimeLinter)
- Rust and Cargo, normally installed through [rustup](https://rustup.rs/)

The package selects Sublime Text's current Python plugin host through
`.python-version` (`3.14`).

## Installation

Package Control installation will be available once the package is accepted.

For local development, clone this repository into Sublime Text's `Packages`
directory with the folder name `SublimeLinter-contrib-cargo`, then restart
Sublime Text. The default `Packages` path is:

| Platform | Packages directory |
| --- | --- |
| macOS | `~/Library/Application Support/Sublime Text/Packages` |
| Linux | `~/.config/sublime-text/Packages` |
| Windows | `%APPDATA%\\Sublime Text\\Packages` |

For example, on macOS:

```sh
git clone https://github.com/Second-Victor/rust-problems.git \
  "$HOME/Library/Application Support/Sublime Text/Packages/SublimeLinter-contrib-cargo"
```

Use **Preferences → Browse Packages…** to confirm a non-default location.

## Configuration

The adapter runs this command from the nearest ancestor containing
`Cargo.toml`:

```text
cargo check [your SublimeLinter args] --message-format=json
```

It intentionally does not force `--workspace`: when opened in a workspace
member, Cargo checks that member by default. Add Cargo options through normal
SublimeLinter settings as needed:

```jsonc
{
    "linters": {
        "cargo": {
            "args": ["--all-features"],
            // Examples: "--workspace", "-p", "my-crate", "--target", "wasm32-wasip2"
            "lint_mode": ["on_save"]
        }
    }
}
```

For a project-specific `.sublime-project` setting, SublimeLinter uses flattened
keys:

```jsonc
{
    "settings": {
        "SublimeLinter.linters.cargo.args": ["--workspace"],
        "SublimeLinter.linters.cargo.lint_mode": ["on_save"]
    }
}
```

The JSON message-format flag is mandatory and placed after user `args`, so this
adapter always receives machine-readable output.

Use the standard `executable` setting to select Cargo explicitly:

```jsonc
{
    "linters": {
        "cargo": {
            "executable": "~/.cargo/bin/cargo"
        }
    }
}
```

SublimeLinter normally resolves executables using its configured PATH. As a
small GUI-launch fallback, this adapter also checks `~/.cargo/bin/cargo`,
`/opt/homebrew/bin/cargo`, and `/usr/local/bin/cargo` after normal resolution.

## Cargo projects and workspaces

The adapter finds the nearest `Cargo.toml` above the saved Rust file and uses
that directory as Cargo's working directory. This avoids checking unrelated
workspace members while letting Cargo discover the enclosing workspace,
configuration, lockfile, and shared target directory. Use `args` with
`--workspace` or `-p` when a broader or different package selection is wanted.

Cargo can emit diagnostics for several files during one check. SublimeLinter
supports cross-file diagnostics: it associates each diagnostic with Cargo's
reported filename and clears stale diagnostics from previously affected files
on the next check. Unsaved cross-file buffers are intentionally not overwritten
by SublimeLinter until they are saved.

Cargo diagnostics without a valid source span, and nested `note`/`help`
records, are not manufactured as editor diagnostics. The primary `error` or
`warning` remains the useful, navigable report.

## rust-analyzer

`LSP-rust-analyzer`/rust-analyzer may already run Cargo checks and publish
compiler diagnostics. Enabling both can mean duplicate diagnostics and extra
Cargo work. Choose one system for compiler diagnostics by disabling this
adapter in SublimeLinter or adjusting rust-analyzer's check configuration; this
package never changes another package's settings automatically.

## Status counters

The former Rust Problems `Rust ⊗ 3  ⚠ 2` counter is deliberately not part of
this adapter. SublimeLinter owns the diagnostics status message, panel, gutter,
annotations, and navigation; recreating a separate counter would reintroduce a
second diagnostics state machine. A Zed-like aggregate counter would be a
better generic SublimeLinter enhancement if the framework adds one in future.

## Troubleshooting

- **Cargo cannot be found:** Set `linters.cargo.executable` to the Cargo path,
  or add its directory to SublimeLinter's platform `paths` setting. This is
  commonly needed when macOS launches Sublime Text from Finder or the Dock.
- **No lint runs:** Cargo requires a saved Rust file and a discoverable
  `Cargo.toml`. The adapter deliberately skips standalone or unsaved `.rs`
  files instead of compiling a temporary file outside the project.
- **Unexpected package selection:** Add `--workspace`, `-p`, feature, target,
  or other Cargo options through `linters.cargo.args`.
- **Need details:** Set SublimeLinter's top-level `"debug": true`, save a
  Rust file, and inspect **View → Show Console** for the exact command,
  working directory, and Cargo output.

## Development

Run the unit tests from the package root:

```sh
python3 -m unittest discover -s tests -v
```

### Manual smoke test

1. Install SublimeLinter and this local package.
2. Open a Cargo project and a saved Rust source file.
3. Introduce a compiler error, save, and confirm SublimeLinter shows it.
4. Fix the error, save, and confirm the diagnostic disappears.
5. Introduce a warning and confirm it uses warning severity.
6. Introduce a diagnostic in another source file and confirm it is attached to
   that file.
7. In a workspace, verify the nearest member is checked by default; add
   `--workspace` in `linters.cargo.args` to check all members.
8. Enable `"debug": true` in SublimeLinter settings when investigating a
   command, PATH, or working-directory issue.

## License

MIT. See [LICENSE](LICENSE).

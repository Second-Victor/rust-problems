# Rust Problems for Sublime Text

A focused Rust diagnostics companion for Sublime Text.

Rust Problems runs `cargo check --workspace --message-format=json`, counts project-wide Rust errors and warnings, and shows the totals in Sublime's status bar.

## Status bar

Examples:

- `Rust ✓`
- `Rust ⊗ 3  ⚠ 7`
- `Rust Problems: checking…`

Sublime's public API allows plugins to place text in the status bar, but it does not expose a click handler for custom `View.set_status()` entries. For that reason the text itself cannot be made into a reliable clickable button without unsupported UI hacks.

## Problems tab

Use **Tools → Rust Problems → Open Problems Tab** or the Command Palette command **Rust Problems: Open Problems Tab**.

This opens a normal, read-only Sublime tab named **Rust Problems** containing all current Cargo errors and warnings. If the tab is already open it updates in place after each Cargo check. Navigable diagnostics also get an **Open** link on the right, so you can click directly through to the source location.

## F4 navigation

- `F4` — next Rust problem
- `Shift+F4` — previous Rust problem

Version 0.2 no longer relies on Sublime's build-result navigation for these keys. The plugin navigates its own parsed Cargo diagnostic list and opens the exact file, line and column directly. The key bindings only take over while a Cargo project has navigable Rust diagnostics, so normal Sublime F4 behaviour remains available otherwise.

## Optional output panel

The original bottom output panel remains available from **Tools → Rust Problems**. The menu is state-aware: it shows **Show Output Panel** while the panel is closed and changes to **Hide Output Panel** while the Rust Problems output panel is visible.

The Command Palette uses a single **Rust Problems: Toggle Output Panel** command.

## Automatic checking

By default Rust Problems checks after saving:

- `.rs` files
- `Cargo.toml`
- `Cargo.lock`

Rapid saves are debounced.

## Settings

Open **Preferences → Package Settings → Rust Problems → Settings**.

Defaults:

```json
{
    "check_on_save": true,
    "check_on_project_open": true,
    "check_delay_ms": 750,
    "initial_check_delay_ms": 300,
    "cargo_path": "",
    "cargo_args": [
        "check",
        "--workspace",
        "--message-format=json"
    ],
    "include_external_diagnostics": false,
    "show_panel_on_error": false,
    "show_panel_on_warning": false,
    "show_panel_on_check_failure": true
}
```

## macOS Cargo discovery

Rust Problems tries `cargo` on PATH and also checks common locations including `~/.cargo/bin/cargo`, `/opt/homebrew/bin/cargo`, and `/usr/local/bin/cargo`.


## Version 0.2.1

- Changed the status-bar error glyph from `✕` to the circled-X glyph `⊗`.
- The status text remains non-clickable because Sublime Text does not expose a click callback for custom `View.set_status()` entries.


## Version 0.2.3

- Made the Tools menu output-panel action state-aware.
- Shows **Show Output Panel** when the Rust Problems panel is closed.
- Shows **Hide Output Panel** when the Rust Problems panel is currently visible.
- Added **Rust Problems: Toggle Output Panel** to the Command Palette.


## v0.2.3

- Fixes stale diagnostics in an already-open Rust Problems tab after a Cargo check completes.
- Refreshes an already-created output panel even when the editor, rather than the panel, has focus.
- Keeps the last valid Cargo root when a scratch Problems tab has focus.
- Resolves save-triggered checks from the file that was actually saved.
- Correctly replaces the final fixed diagnostic with the clean `No Rust compiler errors or warnings. ✓` state.


## v0.2.4

- Removed the F4 / Shift+F4 tip from the bottom of the Rust Problems tab and output rendering.

## v0.2.5

- Fixed the macOS main-menu integration so Rust Problems extends Sublime Text's existing **Tools** menu instead of creating a second top-level **Tools** menu.
- Existing menu anchors now use only Sublime's built-in menu IDs (`tools`, `preferences`, and `package-settings`) so Sublime can merge the package entries correctly.

## v0.2.6

- Fixed macOS main-menu integration by declaring the built-in `Tools` anchor before `Preferences`, matching Sublime Text's main-menu order.
- Keeps `Rust Problems` inside Sublime Text's existing Tools menu without creating a duplicate top-level Tools menu.

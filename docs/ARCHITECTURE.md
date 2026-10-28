# Architecture

How the Bartleby family is put together: one library, bartleby-core, and the products built
from it, BartleBot and BartleBuddy today. Every surface and every lesson lives once, here,
and both apps are built from it. BartleBot's #167 is the plan that gets there, one surface a
stage; this file is the part of it that governs code, and grows as the stages land.

## The tiers

| tier | holds | may import |
|---|---|---|
| `bartleby.shared` | the data every product reads and writes under `%APPDATA%/Bartleby/`: accounts, launch profiles, preferences, the strategy library, the vendor's name, share files | stdlib, pydantic, keyring |
| `bartleby.runtime` | game clients at run time, with no Qt: launching and hooking, the state reader, the arbiter and the one input API, movement, the helpers, the game facts (key bindings, captions, names, realms, sizing), timing and the run trace | `shared`, wizwalker, wizsprinter |
| `bartleby.qt` | theming and icons, buttons and labels, the title bar and native frame, window embedding and the client tile, global hotkeys, Streamer Mode | `shared`, `runtime`, PyQt6 |

Each tier imports only from those before it, so the runtime runs without a GUI and the data
without a game. Core imports neither product. `tests/test_layers.py` holds both rules.

Each tier's `testing/` holds what a product's tests run against core: `SimClient`, a client
with no game behind it (`runtime/testing/sim_client.py`), the guards (`runtime/testing/guards.py`
and `qt/testing/guards.py`) and the render sheet (`qt/testing/render_sheet.py`).

## What stays in a product

Code about a product's own concepts stays in it. In BartleBot that is the canvas, nodes,
the FSM, bot options, the bot library and combat strategies; in BartleBuddy, roles, the
session and the quest map. When a product's need really differs from BartleBot's, for
example Main and Side tiles where BartleBot has one equal grid, the shared module is extended
to cover both, and BartleBot's answers stay identical. It is never forked. A thing neither
product has yet, such as the arbiter, is written here directly.

## The three cuts

A surface moves from BartleBot whole, with its docstring, its tests and its rules, and keeps
its file name. What it needs from BartleBot becomes one named cut, the same three everywhere:

- **A view model.** A shared widget takes a frozen dataclass and emits signals, and never
  holds a wizwalker client. BartleBot builds the view where its client is; BartleBuddy builds
  it from the state reader on the runtime thread.
- **A settings protocol.** A shared widget that reads settings takes a small `Protocol`, as
  `bartleby.runtime.launcher.launch_profile.LaunchSettings` does, and each product's settings
  object answers it. There is no core settings class to subclass: BartleBot's
  `SettingsManager` is 1,239 lines of bot settings BartleBuddy has no use for.
- **The product.** Name, version, accent, logo, data folder, dev mode and build kind arrive as
  one `Product`, never as literals in shared code.

## How a product takes core

The three repos sit side by side under one folder, and each product names core in its
`[tool.uv.sources]`:

- **BartleBuddy**, by local path, editable, while core is in motion. It moves to a pin by
  commit before its first CI build.
- **BartleBot**, from Stage 3 of #167, by git URL and commit, since its release build cannot
  build from a path. Every stage that changes core bumps the pin. A stage branch may use the
  path while it is built; its merge carries the pin.

A change that belongs to both products goes in core, never copied into one. A stage of a
family plan changes the three repos together: a branch in each named for the stage, merged
core first, then BartleBot, then BartleBuddy, each suite green against the others' merged
trees. The stage's issue lives in BartleBot.

## How the rules reach a session

Instructions only help where they load. A Claude Code session loads its repo's root
`CLAUDE.md` at the start, and a subfolder's `CLAUDE.md` when a file under it is read, but
never a file outside the repo it was started in, and its memory is per repo. So:

| rules for | live in | loaded by |
|---|---|---|
| the family | core's `CLAUDE.md` | each product's root `CLAUDE.md`, which imports it (`@../bartleby-core/CLAUDE.md`) |
| Qt code | `bartleby/qt/CLAUDE.md` | BartleBot's `bartlebot/gui/CLAUDE.md` and BartleBuddy's `bartlebuddy/gui/CLAUDE.md` |
| driving a client | `bartleby/runtime/CLAUDE.md` | BartleBot's `bartlebot/nodes/CLAUDE.md` and BartleBuddy's `bartlebuddy/runtime/CLAUDE.md` |
| tests | `tests/CLAUDE.md` | each product's `tests/CLAUDE.md` |
| the user's preferences | `~/.claude/CLAUDE.md` | every session, in every repo |
| landing and planning | the `merge` and `epic-plan` skills, in `~/.claude/skills/` | every session, when the task calls for one |

An import is a relative `@` path at the top of the importing file, so a subfolder's rules
still load only when its files are touched. Claude Code asks once, per repo, before it
follows an import out of the repo; until that is approved, a session sees the product's own
rules alone. `/memory` lists what loaded.

A rule exists once. When one moves here, it is deleted from the product it left, and
`tests/test_rule_files.py` fails on a section heading found in two files across core and the
sibling checkouts that import it, and on a root `CLAUDE.md` of 200 lines or more, since a
root file loads in full every session.

## Guards and render sheets

A rule kept only in a document erodes, so each convention a test can read from the source is
a guard: a function taking a package root and an allowlist, returning the offenders and the
allowlist entries that match nothing. Core runs them over `bartleby/`, and each product over
its own package. An allowlist entry carries the reason it is exempt, and may name a whole
module: BartleBuddy lists each stand-in for a BartleBot surface that way, naming the stage of
#167 that deletes it, so its allowlist empties as the plan lands.

| guard | holds |
|---|---|
| `theme_refresh_offenders`, `mixin_misuse` | a widget that bakes in a theme value re-derives it on a switch |
| `literal_font_sizes` | every font size comes from a token |
| `bare_buttons`, `extra_primaries`, `compound_widget_qss` | the button vocabulary, one primary per surface, no QSS that breaks a compound widget |
| `modals_in_coroutines` | no coroutine opens a modal (the qasync rule) |
| `fixed_key_presses` | every game action is pressed by the account's binding |

A render sheet is a product's surfaces, each built with sample data at a fixed size,
rendered offscreen in the light and dark themes. Each product's `tools/render_sheet.py` lists
its surfaces and writes the PNGs; `--compare` names every surface whose pixels differ from
another run. A stage renders BartleBot's sheet on `master` before it starts and on its branch
before it merges, and nothing outside what the stage names may change.

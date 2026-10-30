# Architecture

How the Bartleby family is put together: one library, bartleby-core, and the products built
from it, BartleBot and BartleBuddy today. Every surface and every lesson lives once, here,
and both apps are built from it. BartleBot's #167 is the plan that gets there, one surface a
stage; this file is the part of it that governs code, and grows as the stages land.

## The tiers

| tier | holds | may import |
|---|---|---|
| `bartleby.shared` | the data every product reads and writes under `%APPDATA%/Bartleby/`: accounts, launch profiles, preferences, the strategy library, the vendor's name, share files; the running product; and two small helpers, a Qt-free signal (`listeners.Listeners`) and a drag's permutation applied (`ordering.apply_order`) | stdlib, pydantic, keyring |
| `bartleby.runtime` | game clients at run time, with no Qt: launching, the client manager (finding, hooking, naming and ordering clients), the state reader, the arbiter and the one input API, movement, the helpers, the game facts (key bindings, captions, names, realms, sizing and tile layouts), timing and the run trace; and the view models a shared widget draws (`views`) | `shared`, wizwalker, wizsprinter |
| `bartleby.qt` | theming and icons, buttons, labels and pills, the title bar and native frame, window embedding and the client tile, global hotkeys, Streamer Mode; cards and their drag reordering, card columns, sidebar sections, popup menus, dialog footers, the art signal and the Log tab; the player cards and their column; the game view with its launch screen and launch cards, Bar A's game panel and action bar, and the screen fit; an app's startup (`app/`) and the runtime bridge, with the crossing a client manager's tiles take | `shared`, `runtime`, PyQt6 |

Each tier imports only from those before it, so the runtime runs without a GUI and the data
without a game. Core imports neither product. `tests/test_layers.py` holds both rules.

Each tier's `testing/` holds what a product's tests run against core: `SimClient`, a client
with no game behind it, and `SimHandler`, the windows a client manager polls (`runtime/testing/sim_client.py`), the guards (`runtime/testing/guards.py`
and `qt/testing/guards.py`), the render sheet (`qt/testing/render_sheet.py`), the startup
check (`qt/testing/startup.py`), a drag on a `DragReorder` card list (`qt/testing/drag_helpers.py`),
and the per-test widget reaping and the `parentless_shows` fixture every repo's `conftest.py` imports (`qt/testing/widget_lifetime.py`).

## What stays in a product

Code about a product's own concepts stays in it. In BartleBot that is the canvas, nodes,
the FSM, bot options, the bot library and combat strategies; in BartleBuddy, control per
task and the team's objective, the session and the quest map. When a product's need really differs from BartleBot's, for
example Main and Side tiles where BartleBot has one equal grid, the shared module is extended
to cover both, and BartleBot's answers stay identical. It is never forked. A thing neither
product has yet, such as the arbiter, is written here directly.

## The three cuts

A surface moves from BartleBot whole, with its docstring, its tests and its rules, and keeps
its file name. What it needs from BartleBot becomes one named cut, the same three everywhere:

- **A view model.** A shared widget takes a frozen dataclass and emits signals, and never
  holds a wizwalker client. BartleBot builds the view where its client is; BartleBuddy builds
  it from the state reader on the runtime thread. The view models are `bartleby.runtime.views`,
  in the runtime tier so that a runtime thread can build one; the first is `PlayerView`.
- **A settings protocol.** A shared widget that reads settings takes a small `Protocol`, as
  `bartleby.runtime.launcher.launch_profile.LaunchSettings` does, and each product's settings
  object answers it. There is no core settings class to subclass: BartleBot's
  `SettingsManager` is 1,239 lines of bot settings BartleBuddy has no use for.
- **The product.** Name, version, accent, logo, data folder, dev mode and build kind arrive as
  one `Product` (`bartleby.shared.product`), never as literals in shared code. Code an entry
  point calls takes it as an argument; code reached from deep in a run, such as a message
  naming the app, reads `current()`, which the entry point sets with `use()` first. It is
  Qt-free, since the shared and runtime tiers need the name and folders too; the logo is a
  path the Qt tier makes an icon of. `tests/test_product_literals.py` fails on a product's
  name or a version written as a string in core.

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

## BartleBot on core

Since Stage 3 of #167 BartleBot imports every module core holds, and holds no copy of one:
`bartlebot/shared/`, its theming, buttons, labels, title bar, client tile and window
embedding, global hotkeys and Streamer Mode, the clients modules core's runtime has, the game
facts, launching, timing, the UI paths and the run trace are core's. Its icons and the three
snapshots core's runtime reads are core's too; its packaged build bundles them under
`bartleby/`, where core's modules look for them. `nodes/node_helpers.py` stays BartleBot's,
with the helpers that read a bot, and imports core's `bartleby.runtime.actions.node_helpers`,
so a node reaches every helper through one module. Since Stage 6, so are its cards
(`CardFrame`, `AddCard`), `DragReorder`, the family pills (`widgets/pills/pill.py`, where its
`tag_pill.py` keeps the bot-tag pills), `SidebarSection`, `RuleHeading`, `LogoDisc`, the layout
helpers, `menu_utils`, `art_signal` and the Log tab. Since Stage 7, so is the client
manager's Qt-free half, below. Since Stage 8, so are the player cards and their column,
`CardColumn` and `PlayerBadge`, also below. Since Stage 9, so are the game view, its launch
screen and launch cards, and the screen fit, below too.

BartleBot supplies what core's copies cut away:

| core reads | BartleBot hands it |
|---|---|
| `launch_profile.configure(settings)`, a `LaunchSettings` | its `SettingsManager`, whenever the singleton is made |
| `streamer_mode.configure(setting)` | a read of `SettingsManager.streamer_mode` |
| `TitleBar(menu_bar, logo)` | its logo |
| `group_manager.form_group(..., safety_delay=)` | the user's Safety Delay at every call; core's default is BartleBot's 3.0 s |
| `run_trace.start(slot_of, directory)` | `app_controller.start_run_trace`, which traces only in developer mode, under `logs/trace/` |
| `product.use(product)`, `configure_logging(product, settings)`, `SingleInstance(product)` | `bartlebot.packaging.product.product()`, built at startup, and its `SettingsManager` |
| `GameAction`, which names the movement keys BartleBuddy holds | `run_probe.PRESSED_ACTIONS`, the actions a run needs keys for |
| `LogPanel(settings)`, a `LogSettings` | its `SettingsManager`; it clears the panel itself as a run starts, when `clear_log_on_run` says to |
| `LogoDisc(diameter, disc_image)` | `gui.logo.logo_disc_image`, its tree on a disc |
| `ClientManager(handler, settings, game_view=)`, a `ClientSettings` and a `TileView` | its `SettingsManager` and its `GameView`, from its own `ClientManager`, a subclass |
| `PlayerColumn(verbs, card_factory=)`, of `PlayerView`s | its `ClientSection`, a subclass, with the Entities and UI Tree verbs and a `ClientCard` per client |
| `GameView(settings, disc_image)`, a `GameViewSettings` | its `SettingsManager` and its logo disc, through `game_view_panel.new_game_view` |
| `GamePanel(game_view, action_bar)`, an `ActionBar` of `BarAction` groups | its `GameViewPanel`, a subclass, with an `AuthoringBar` over `AUTHORING_GROUPS` |
| `screen_fit.configure(settings, panes)`, a `FitSettings` | an adapter reading whichever `SettingsManager` is current, and `gui/window_panes.panes`: its two sidebars at their measured floors and the bot canvas |

### The client manager, in two halves

Core's `bartleby.runtime.clients.client_manager.ClientManager` finds the game windows, hooks
each in the background, ties each to an account (by its launch title or the user id it
reports), learns each account's characters, tracks launches, holds the slot order, and
unhooks and rehooks. It publishes on `Listeners`, which have a Qt signal's `connect` and
`emit`: `clients_changed`, `all_clients_changed`, `wizard_names_changed`,
`clients_reordered` and `clients_removed`. It reads accounts through `ClientSettings` and
hands its tiles to a `TileView`.

BartleBot's `bartlebot/clients/client_manager.py` subclasses it and keeps what a running bot
needs: Qt signals in place of the first three listeners (on a `QObject` of their own), the
machine handed each published list, a reorder refused mid-run (`_refuse_reorder`), the drop
tables following `clients_reordered`, and recovery and relogging. Core reports each lost
client on `clients_removed` as a `Removal` (its window, account, slot and title), after
emptying the slot; BartleBot's `_recover_removed` holds the slot in `_pending_recovery`,
which core's slot code honors, and runs the recovery queue. A subclass, not a wrapper:
recovery reads and writes the same ordering, account map and held slots at every step, and
moving it whole kept every line and its tests as they were.

BartleBuddy uses core's manager as it is, on its runtime thread: its session listens to
`clients_changed`, so a reorder or an unhook reaches the team, and its launches go through
`begin_launch` and `end_launch`. It has no recovery until its phase 3.

### Player cards, drawn from views

Core's `PlayerCard` (`qt/sidebar/player_card.py`) draws a `PlayerView` and holds no client, and
`PlayerColumn` (`qt/sidebar/player_column.py`) is the Players tab: the hooked cards in a
`CardColumn`, the unhooked in a section beneath, and the bulk row. `bartleby/qt/CLAUDE.md`,
"Player cards are drawn from views", has the refresh contract a view's builder keeps.

BartleBot's `bartlebot/gui/sidebar/client_card.py` is a `ClientCard`, core's card with its
client behind it: the reads that build the view, XYZ and Enemy as the view's rows, the
Entities and UI Tree verbs, and the freecam pair under the rows. Its `ClientSection`
subclasses `PlayerColumn` and keeps the refresh loop, the account stripe asked for every tick,
and the permutation its `reorder_clients` takes.

BartleBuddy's Team tab (`bartlebuddy/gui/team_tab.py`) is a `PlayerColumn` of `TeamCard`s,
drawn from the session's `TeamView`: Holder as a row, the leader as a badge and a menu verb,
and a control button on the header that opens a menu of the tasks.

### The game view, in two layouts

Core's `GameView` (`qt/game_embed/game_view.py`) holds the game windows a client manager hands
it as `TileSlot`s, pending tiles for the launches still opening, and, while nothing is
embedded, the launch screen: the accounts and the launch profiles as launch cards
(`launch_cards.py`), each judged by `screen_fit` against the game area and refused with its
reason when it will not fit. `screen_fit` asks `game_sizing`'s arithmetic about the product's
panes, a list of `Pane`s; `docs/WINDOW_SIZING.md`, "Panes and tiles are lists", has the rules.

BartleBot hands it nothing more and gets its equal grid. BartleBuddy hands it a
`tile_layouts.Layout` and whether the user moves each wizard (`set_layout`), so the wizards the
user moves have Main tiles and the rest sit Side; captions per player (`set_captions`), each
naming what holds its wizard; and listens to `caption_clicked` for the wizard's control menu.
Its launch screen is built with `editing=False`, offering no verb, Add card or settings link
until BartleBuddy edits accounts itself. Its client manager runs on the runtime thread, so the
tiles cross to the view as queued signals, through `runtime_bridge.TileCrossing`.

### Bar A, from a list of actions

Above the view sits Bar A, core's `GamePanel` (`qt/game_embed/game_view_panel.py`): the
clients connected on the left, what the product is doing on the right, and between them an
`ActionBar` of the product's own buttons. A product hands groups of `BarAction(id, icon,
tooltip, checkable, menu)` and gets 24px `IconButton`s with a divider between groups, a press
reported by id, a toggle's state shown only as `set_state` reports it, and a menu action's
menu filled by the builder `set_menu` was handed. The bar's height is the buttons', whatever
the list, so every product's Bar A fits `GAME_PANEL_CHROME_PX`.

BartleBot's list is its authoring utilities (`AUTHORING_GROUPS`, with `AuthoringBar` turning
the tools' state into each button's); BartleBuddy's is the helpers, the mode and the tile
layout as menus, and Stop all (`gui/game_panel.py`). Neither writes a button of its own.

A name patched in a BartleBot test is patched where its callers read it, and core's helpers
read core's module: `tests/node_helper_patches.py` patches a helper on both modules at once.
A session log drops `bartleby.` from core's logger names, as it drops the product's own prefix.

## Starting an app

Every app of the family starts the same way, from core's pieces in the same order. BartleBot's
`bartlebot.py` and BartleBuddy's `bartlebuddy.py` are that order, with the app's own steps
between:

1. **`product.use(PRODUCT)`**, first: the running app, for the code that reads
   `bartleby.shared.product.current()`.
2. **The keyring backend** (`keyring_backend.use_windows_backend`), before anything reads a
   password.
3. **Qt's diagnostics** (`qt_paint_debug.install`) and `keep_siblings_alien()`, before the
   `QApplication`.
4. **`SingleInstance(product).claim(argv)`**, once the `QApplication` exists. A later launch
   hands its arguments to the running app and exits here, before the log is started, so it
   neither writes a session log nor prunes a real one.
5. **`configure_logging(product, settings, libraries=...)`**: the session file
   `<slug>_<date>.log` under `product.log_dir`, the ten newest kept, and the header naming the
   app, its version and build, and the libraries the app lists. `settings` answers
   `LogSettings`.
6. **`install_excepthook()`**, before the controller: PyQt6 aborts the process, silently, on
   an exception that escapes a slot without one (BartleBot #154).
7. **The app's controller**, which listens for later launches (`LaunchQueue`, `raise_window`).
8. **`exit_skipping_teardown(code)`** once the event loop returns.

Each product's suite checks its own startup with `qt/testing/startup.py`: its entry point's
logging and hook, then an exception in a Qt slot and one in a worker thread, in a process of
its own, must each be logged at CRITICAL with its traceback while the app runs on.

| module | holds |
|---|---|
| `bartleby.shared.product` | `Product`, `use`, `current` |
| `bartleby.qt.app.logging_setup` | the session file and its header, the GUI bridge (each record as a `LogEntry`), the excepthook, the turn trace |
| `bartleby.qt.app.single_instance` | `SingleInstance`, `LaunchQueue`, `raise_window`; the pipe's name is the vendor's, the product's and the user's |
| `bartleby.qt.app.app_exit` | `exit_skipping_teardown`, past the teardown that crashed in sip on quit |
| `bartleby.qt.app.ui_state` | `UiState`: view state the app may lose, folds and each window's place, in the product's `ui_state.json` |
| `bartleby.qt.debug.qt_paint_debug` | Qt's own messages into the log, with a stack for a QPainter error |
| `bartleby.qt.offscreen_warmup` | the first offscreen render, paid on a show rather than on the first drag |
| `bartleby.qt.runtime_bridge` | `RuntimeThread` and `RuntimeSignals`, for an app whose runtime runs on a thread of its own |

What differs between the two is the loop. BartleBot runs one qasync loop for everything, and
BartleBuddy runs Qt's own `exec()` with its runtime on a `RuntimeThread` (below). So the Log
tab's records reach BartleBot's through `add_gui_callback`, and BartleBuddy's through
`add_gui_callback(signals.log_record.emit, any_thread=True)`: with no asyncio loop on its GUI
thread, a record logged on the runtime thread crosses by the queued signal instead.
BartleBuddy's window comes back where it closed, from `UiState`.

## Where the runtime runs

Anything synchronous on Qt's thread holds every coroutine on a loop that shares it. A tile
resize waits for the game's window thread to answer a move, which BartleBot's #285 measured
at 1.1 to 1.4 s, and an embed at up to 3.6 s. So the two products differ, by what their
runtime does:

- **BartleBuddy runs its runtime on a thread of its own**, a plain asyncio loop
  (`bartleby.qt.runtime_bridge.RuntimeThread`), because a follower steers every `FOLLOW_TICK`. Only
  snapshots cross out, as queued signals, and only commands cross in, as coroutines.
- **BartleBot runs everything on one qasync loop.** Its nodes press, read and wait; nothing
  of its steers on a tick, so a held loop delays a node without walking a wizard off course.

The difference was measured, in Stage 4 of #167, by BartleBuddy's
`tools/probe_follow_jitter.py`: three followers on `SimClient`s under BartleBot's GUI load,
four embedded tiles with one resized every 10 s and each move held 1.2 s, for 5 minutes per
model. On one loop, every resize stopped every follower for 1.27 s, and all 29 broke the
rule that no steering gap through a resize may reach 250 ms. On the runtime thread the GUI
was held as long, and no follower went more than 66 ms without steering. The probe's
docstring has the full table.

What follows for core:

- **A runtime that drives clients on a tick runs off Qt's thread.** The GUI never holds a
  runtime object; it gets the snapshots and view models that cross, which is what the view
  model cut is for. `RuntimeThread` and the crossing are core's, in
  `bartleby.qt.runtime_bridge`, product-free, since Stage 5 of #167.
- **Code in `bartleby.runtime` assumes no Qt and no particular thread**: it runs on whichever
  loop calls it, BartleBot's qasync one or BartleBuddy's own.
- **A tick is 62.5 ms on Windows, not 50.** `asyncio.sleep` wakes on the system's 15.6 ms
  timer, on either loop, so a cadence is rounded up to a whole number of ticks.

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
| `core_copies` | no module of a product is a copy of one of core's: the same file name and, imports and docstrings aside, the same code |
| `product_literals` | no string in core names a product or a version; run by core alone, over `bartleby/` |

A render sheet is a product's surfaces, each built with sample data at a fixed size,
rendered offscreen in the light and dark themes. Each product's `tools/render_sheet.py` lists
its surfaces and writes the PNGs; `--compare` names every surface whose pixels differ from
another run. A stage renders BartleBot's sheet on `master` before it starts and on its branch
before it merges, and nothing outside what the stage names may change.

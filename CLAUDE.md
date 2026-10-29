# bartleby-core

The library every Bartleby product is built from: BartleBot and BartleBuddy today. Python
3.13+, PyQt6, asyncio. It holds the code both apps share and the rules learned building
them, and both apps import both. Started 2026-10-28 from BartleBot `a0c51a71`; BartleBot's
#167 is the plan that moves the rest of BartleBot's shared code here.

## Quick start

```bash
uv sync --all-extras
uv run pytest -n auto
uv run ruff check && uv run ruff format
```

## The tiers

| tier | holds | may import |
|---|---|---|
| `bartleby.shared` | accounts, launch profiles, preferences, the strategy library: the data every product reads | stdlib, pydantic, keyring |
| `bartleby.runtime` | game clients at run time: launching, hooking, the state reader, the arbiter, movement, the helpers, the game facts. No Qt | `shared`, wizwalker, wizsprinter |
| `bartleby.qt` | theming, icons and the shared widgets | `shared`, `runtime`, PyQt6 |

Each tier imports only from those before it, and **core imports neither product**:
`tests/test_layers.py` holds both. A module moved from BartleBot keeps its file name. Test
helpers a product runs are in each tier's `testing/`: the guards and the render sheet.

## Three repos, side by side

BartleBot, BartleBuddy and core are checked out side by side under one folder. Each
product's `[tool.uv.sources]` assumes it, and so do the `@` imports of these rules in each
product's CLAUDE.md files.

- **How a product takes core.** BartleBuddy by local path, editable, while core is in motion.
  BartleBot by git URL and commit, from Stage 3 of #167 on, since its release build cannot
  build from a path; a stage branch may use the path while it is built, and its merge
  carries the pin. A change that belongs to both products goes here, never copied into one.
- **How the rules reach a session.** Each product's root CLAUDE.md imports this file, and
  its GUI, runtime and test CLAUDE.md files import `bartleby/qt/CLAUDE.md`,
  `bartleby/runtime/CLAUDE.md` and `tests/CLAUDE.md`, so a rule loads where the work
  happens, as it did in BartleBot. The user's preferences are in `~/.claude/CLAUDE.md`, and
  the `merge` and `epic-plan` skills in `~/.claude/skills/`. A section heading exists once
  across the three repos (`tests/test_rule_files.py`).
- **A stage of a family plan touches all three.** Each repo gets a branch named for the
  stage, and the merges go core, then BartleBot, then BartleBuddy, each with its suite green.

## Move, never rewrite

A surface BartleBot has is moved here whole, with its docstring, its tests and its rules,
and keeps its file name. What it needs from a product becomes one named cut, the same three
everywhere:

- **A view model.** A shared widget takes a frozen dataclass and emits signals. It never
  holds a wizwalker client; the product builds the view where its client is.
- **A settings protocol.** A shared widget that reads settings takes a small `Protocol`, as
  `launch_profile.LaunchSettings` does, and each product's settings object answers it.
- **The product.** Name, version, accent, logo, data folder, dev mode and build kind arrive
  as one `Product`, never as literals in shared code.

A smaller version written fresh needs a decision on the plan first, with its reason. Phase 1
of BartleBuddy rewrote its surfaces, and lost drag reordering, the account stripe, Streamer
Mode, the excepthook and the theme refresh on the way. When a product's need really
differs, extend the shared module to cover both, and keep BartleBot's answers identical.

## Key conventions

The client runtime's rules are `bartleby/runtime/CLAUDE.md`: **every input goes through an
arbiter lease**, **speed is never written above 2.0x**, every timing constant is in
`bartleby/runtime/timing.py`, and **no log names a wizard or an account's username**.

### No real player data

Never commit real, identifiable player data — wizard names, character ids, account
ids, friend codes, or screenshots showing them. BartleBot is public, and core and BartleBuddy
are or will be, and history is not rewritten on the way — anything committed now
ships then. A real wizard name links the account to whoever wrote the commit.

This applies to test fixtures, sample data, docstrings, comments, log excerpts
pasted into issues, and bot files. It applies to *any* player, not just your own.

Use synthetic values instead. For anything name-shaped, build it from the shipped
tables so it stays realistic and verifiable rather than arbitrary:

```bash
uv run python tools/name_keys.py --decode 56900648   # BartleBot's -> "Luke SkullHunter"
```

BartleBot's `tools/name_key_samples.json` is generated this way: real `m_nameKeys` values for
characters that do not exist, which still decode correctly and still exercise the
real tables. It is tracked, and safe to share.

Tools that capture real values write to gitignored paths, so an accidental commit
is not possible by mistake: BartleBot's `probe_name_behavior.py --record` writes
`tools/name_key_samples.local.json` (`*.local.json`), and `probe_archmastery_ui.py` writes
`probe_archmastery_report.txt` (`probe_*_report.txt`). `name_keys.py --verify` reads both the
tracked fixture and the local file when present, so recordings are still useful locally —
they just cannot leave the machine. Follow that pattern for any new tool that records live
account data: write to `*.local.json`, never append to a tracked file.

### No game assets, either

The same rule, for the same reason, applied to KingsIsle's art: **never commit an
extracted texture, model or sound**, whatever it is of. History is not rewritten, so a
texture committed now ships publicly, and a repo carrying the game's own art invites a
takedown the way one carrying player data invites a link back to the account.

Names and ids are the exception, and the line worth remembering is *text versus
asset*: `dialog_captions.json`, `name_tables.json` and `realm_names.json` in
`bartleby/runtime/game/data/` are all extracted from `Root.wad` and all tracked, because a
snapshot of names is what lets an app talk about the game without shipping any of it. Art
is extracted on the user's own machine, outside any repo, and every lookup of it returning
`None` is the ordinary case (`bartleby/qt/CLAUDE.md`, *Game art is optional*).

### Where a value lives

**Nothing an app writes goes in its install directory**, which an update replaces whole.
It holds the Official content, read-only. The user's own, and every override, log, autosave
and setting, go under `%APPDATA%/Bartleby`.

`Bartleby` is the **vendor**, not any one product: three products are planned under it, and
the accounts, launch profiles and stored passwords belong to the user rather than to any
one of them. So `%APPDATA%/Bartleby/` is the vendor root, per-product data sits beneath it,
and what every product shares lives at the root itself: the account records in
`accounts.json`, the user's preferences in `preferences.json` (both written through
`shared/json_update.py`, which changes one key and never rewrites the file from memory,
since two products may be open at once), among them `game.install_dir`, the game folder
both products launch from (`shared/game_install.py`), and the library of duel strategies,
player strategies and decks in `strategies/` (BartleBot's `docs/COMBAT_CONFIG.md`). The
prefix is defined once, in `bartleby/shared/vendor.py`, and imported — never spelled out
again, on disk or in Credential Manager. Credentials go through `keyring` (Windows
Credential Manager), never to disk. `BartleWorks` is the GitHub organization and is a
separate string on purpose: it lives in URLs, where a rename is a search and replace, and it
keeps `Bartleby`, the in-game tree's own name, off GitHub. It was `The-World-Tree` until
2026-10-16; GitHub redirects the old URLs.

### Use public API across class boundaries

Never access private attributes (names starting with `_`) of another class from outside it.
If you need access to something, add a property or public method to the owning class.
Private attributes are implementation details; reaching into them from outside creates
hidden coupling that breaks on any internal refactor. In BartleBot: `machine._stop_flag.is_set()`
→ `machine.is_stopped`; `scene._region_items` → `scene.region_items`.

### Testing

Every new node, background task, or non-trivial utility class ships with a test
file covering the success path, one failure path, and any timeout or retry
behavior. The exception is pure `QWidget` subclasses that cannot be tested without
a `QApplication`; extract the testable logic into a plain function or class. Runtime
behavior is tested on `bartleby.runtime.testing.SimClient` or a fake client, never the
game. Tests mirror the tree. `tests/CLAUDE.md` has the ways a suite goes slow or flaky.

## Reference libraries

| Library | Local Location | Repo URL | Description |
|---|---|---|---|
| WizWalker | `C:\GitHub\wizwalker` | https://github.com/BartleWorks/wizwalker | Low-level memory hooking. Ours. Still tracks `LaurenzLikeThat/wizwalker` for offset fixes, not the `Deimos-Wizard101` original. |
| WizSprinter | `C:\GitHub\wizsprinter` | https://github.com/BartleWorks/wizsprinter | Safe wrappers and the combat DSL. Ours. Pull selectively from `Deimos-Wizard101/wizsprinter`; their `main` is no longer our baseline. |
| Deimos | `C:\GitHub\Deimos-Wizard101` | https://github.com/Deimos-Wizard101/Deimos-Wizard101 | An existing Wizard101 botting tool. Examples of interfacing with WizWalker and WizSprinter, plus some custom utilities. |

WizWalker and WizSprinter are **ours**, pinned by exact commit, the same pins in core and
every product. `docs/UPSTREAM.md` before changing a pin or chasing a memory offset after a
game patch.

## Ruff

Line length 120, target py313. Rules: E, W, F, I, UP, B. Intentional ignores: B905 (zip
strict=), E402 (import order), E501 (line length), B008 (default args). The same in every
repo.

## Where the rest lives

| file | covers |
|---|---|
| `bartleby/qt/CLAUDE.md` | the family's GUI rules: theming, popup menus, the title bar, buttons, headings, account colors, parenting, game art, offscreen renders, qasync, task lifetime, window sizing, tile slots, the player card refresh, keyboard reordering |
| `bartleby/runtime/CLAUDE.md` | the arbiter, timing, logging, pressing keys, cancellation, multi-client coordination, zones, closing windows, modal dialogs, NPC dialogue, UI paths |
| `tests/CLAUDE.md` | keeping a suite fast, CI-only failures, widget lifetime, one QApplication per worker |
| `docs/ARCHITECTURE.md` | the tiers, what lives where, the three cuts, how a product takes core, how rules reach a session |
| `docs/WIZARD101.md` | the game rules that shape design: characters, battles, fleeing, level scaling, marks, Challenge Mode, sigils, banks, and combat as players fight it |
| `docs/UI_TEXT.md` | writing anything a user reads: the three layers of a setting, voice, words, capitals, messages, and the rules of their own for setup's pages and the node help |
| `docs/WINDOW_SIZING.md` | the (resolution, UI size) ladder, which XML file holds which, why an unfittable launch is refused, and resizing a running client (UI size, Deimos' forcer without its drag border, the window, the camera) |
| `docs/KEY_BINDINGS.md` | the player's key bindings: why they are read from each client's memory and not the file, finding the list once and reading it each time, a key's name to its code and the two names to recheck, and what a press can use: a bare key only, never one the chat line shares |
| `docs/WALKING.md` | moving now that the server resets a teleport: what it allows and what the family will not do, planning a route from a zone's own files, walking it by the account's keys around live mobs, every measurement so far, and what is not done; read it before touching movement |
| `docs/RELOGGING.md` | why a client slows down, and the two ways to clear it |
| `docs/PLAYER_REALMS.md` | telling whether players share a realm by sight, the record of who does, the Player Realms option and what blocks Separate, and where each node checks and repairs |
| `docs/UPSTREAM.md` | wizwalker/wizsprinter forks, offset fixes, bumping a pin |
| `docs/GAME_UPDATES.md` | a game update on the Test realm: its branches and when they merge, comparing the installs, the snapshots, which hooks break, capturing a type dump, replacing a broken pattern, and the record of each update |
| `docs/PROCESS.md` | issues, labels, milestones, the backlog board |

The moved docs still describe BartleBot where it is their only user so far, and name its
paths; the stage that moves the code they govern updates them.

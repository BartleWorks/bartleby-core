# Changelog

What each release of bartleby-core changes for a product that takes it. A product moving
its pin reads the **Breaking** list of every release it moves past: that list is what it
has to fix, whichever product the change was made for. `docs/UPSTREAM.md`, *Releasing
core*, says how a release is cut.

Versions are SemVer, with Cargo's rule while core is 0.x: a minor bump (0.2.0 to 0.3.0)
can break a product, and a patch bump (0.2.0 to 0.2.1) breaks nothing.

## Unreleased

### Fixed

- The render sheet frees what a surface `deleteLater`'d before it grabs, as the running
  app's event loop does, so Quick Launch draws each card once instead of twice (#11). A
  product's sheet changes only where a surface was drawing widgets it had replaced.

## 0.2.0

Core's first release. Everything after `1b1caab` (`bartlebot-pin-20261101b`), the commit
BartleBot pins. Most of it is BartleBuddy's live client resize (BartleBuddy #3) and its
multibox roles and views (BartleBuddy #14).

### Breaking

- **The layout presets are gone.** `tile_layouts.FOCUS`, `PAIR`, `GRID`, `PRESETS` and
  `layout_for` are replaced by `ONE_LARGE` and `ALL_EQUAL`. `Layout` stays, and still takes
  any count of Main tiles, so a product can build its own.
- **`grid_for` moved** from `game_sizing` to `tile_layouts`. `game_sizing` still imports
  it, so the old import works, but import it from `tile_layouts`.
- **A stand-in `EmbeddedWindow`** in a product's tests needs `set_input_enabled(enabled)`
  and `sync_input()` once its tile refits or swallows clicks.

BartleBot uses none of these: none of the names is in `bartlebot/`, `tests/` or `tools/`,
and its suite (9,142) passed against each merge that made them, with core installed editable.

### Changed

- Core's version comes from its release tag, through `hatch-vcs`, and a product pins
  `tag = "v0.2.0"` where it pinned `rev`. A commit past a tag builds as the next patch,
  marked as a development build (`0.2.1.dev3+g1a2b3c4`).
- A detached game keeps the frame style it had: `restored_style` no longer adds
  `WS_THICKFRAME`, so a game launched without a resize border can't be dragged to a size
  the game never applied. BartleBot sees this once it takes 0.2.0.
- `advance_dialog_once`, `advance_dialogue` and `AutoDialogue` take `decline=`. The
  default still declines a quest offer, so a call without it does what it did.

### Added

- **Live client resize:** `ClientResizer`; the rungs in `game_sizing` (`aspect_family`,
  `fitting_rung`, `rung_tiles`, `tile_targets`); `GameView.set_layout(by_rung=, cap=,
  at_once=)`, `game_size_wanted`, `game_resized`, `window_detached`; `ClientTile.refit`,
  which runs on a worker thread; `EmbeddedWindow.reembed`.
- **Multibox:** `Follower(wait_for_move=, hold=, port_when_far=, walking_speed=)`;
  `SpeedLedger`, one record of each wizard's speed for `SpeedBoost` and `Follower`; pill
  roles and the Active mark; swap first; the keyboard target, the player's focus,
  `give_keyboard_back` and `bring_to_front`; `paths.CHAT_EDIT_PATH`;
  `global_hotkeys.binding_refusals` and `game_key_clashes`;
  `ActionBar.set_state(visible=, icon=)`; the role, anchor and equal-tiles icons.
- **Testing:** `SimClient`'s `SimMemory` and `SimVideoManager`.

### Fixed

- `Session.stop` could hang forever: a cancel landing in the arbiter's give-back left the
  client claimed.
- `ClientInput.release_all` swallowed its caller's cancel, so a stopped follower could walk
  on.

## 0.1.0

Every commit up to `1b1caab`. Core had no release of its own then: its version was a fixed
0.1.0, and BartleBot pinned commits, each tagged `bartlebot-pin-YYYYMMDD`. Those tags stay,
since older BartleBot commits pin them.

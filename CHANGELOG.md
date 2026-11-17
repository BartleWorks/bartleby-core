# Changelog

What each release of bartleby-core changes for a product that takes it. A product moving
its pin reads the **Breaking** list of every release it moves past: that list is what it
has to fix, whichever product the change was made for. `docs/UPSTREAM.md`, *Releasing
core*, says how a release is cut.

Versions are SemVer, with Cargo's rule while core is 0.x: a minor bump (0.2.0 to 0.3.0)
can break a product, and a patch bump (0.2.0 to 0.2.1) breaks nothing.

## 0.5.0

Launching on the 2026-11-16 build: the login sent through the client's memory, and Space for
Play. A minor release for its wizwalker pin, which a product that pins wizwalker moves too.

### Breaking

- **wizwalker is pinned at `a1559cf`** (`bartlebot-pin-20261116c`), where it was `78929a9`. A
  product that pins wizwalker itself pins the same commit: the launcher imports
  `wizwalker.login`, which earlier pins do not have.

### Fixed

- The launcher logs in again on the 2026-11-16 build, whose login window moved its focus and
  tab order out from under the typed login. `launch_account` and `launch_accounts` send it
  through the client's memory with wizwalker's `memory_login` (the command Deimos' wizlaunch
  calls), and type it only when that cannot be sent, logging why.
- Auto-select takes Play again on the same build, where Enter no longer does: the drive and
  `press_character_select_play`, which BartleBot's relog calls, press Space. Clicking
  `btnPlay` is the sturdier fix, set aside for now; that function's docstring says what it
  would take.

### Changed

- `ClientManager` does not hook a window a launch is logging in until its login is sent, since
  the login borrows the root window hook's site: every new window while a launch spawns, and
  the launch's own until each login is out (`launcher/login_gate.py`). A window the user opens
  during a launch's spawn is hooked a few seconds later than it was.

## 0.4.0

Following shelved, and wizwalker that resizes a running client on Ahmarra's live build again.
A minor release for both: a product drops any `Follower` and moves its own wizwalker pin.

### Breaking

- **Following is shelved.** `bartleby.runtime.movement` is gone, `Follower`, `spacing_for`,
  `Trail` and its geometry with it, and so are the timing constants only it read:
  `FOLLOW_TICK`, `FOLLOW_STALL_WINDOW`, `FOLLOW_BACK_OFF`, `LEADER_SETTLE`,
  `FOLLOW_PROMPT_WAIT`, `FOLLOW_LINK_WAIT`, `FOLLOW_GATE_WAIT` and `FOLLOW_PORT_RETRY`. Long
  written teleports hold again on Ahmarra's live build, so walking is no longer the only way
  to move a wizard. All of it, with `docs/WALKING.md`, is on the `walking-shelf` branch, which
  is `master` as it stood before; bringing it back is a revert of the merge that removed it.
  A product that ran a `Follower` drops it. `SpeedLedger` stays, as `SpeedBoost`'s, and
  `SpeedBoost(skip=)` too.
- **wizwalker is pinned at `78929a9`** (`bartlebot-pin-20261116b`), where it was `6a64fcf`. A
  product that pins wizwalker itself pins the same commit, as for 0.3.0.

### Fixed

- Resizing a running client works on r813697, 0.3.0's *Known*: wizwalker's video manager finds
  its fields in `GamebryoClient::VidRestart`, which r813697 no longer inlines into the frame
  function where the old pattern matched (BartleBuddy #30). Nothing in core changed for it.

### Changed

- `docs/WIZARD101.md` has a *Moving* section in `WALKING.md`'s place: what the server allows,
  with long teleports holding again, walking pace, the 2.0x speed rule, and the game's own
  Teleport to Leader and Follow Leader.

## 0.3.0

Wizwalker for Ahmarra's live build, r813697, on which the hooks of the old pin do not
install. A minor release only because a product has to move its own wizwalker pin with it.

### Breaking

- **wizwalker is pinned at `6a64fcf`** (`bartlebot-pin-20261116`), where it was `20c7ba4`. uv
  reads a git dependency's sources too, so a product that pins wizwalker itself has to pin
  the same commit, or `uv lock` refuses the two. On r813697 the old pin's autobot, client,
  teleport and drops hooks match nothing, so no client could be hooked with it anyway.

### Changed

- The `comma`/`period` comment in `key_bindings.KEY_CODES`, `docs/KEY_BINDINGS.md` and
  `docs/GAME_UPDATES.md` say the swap is still there on the live build, checked in game;
  the codes themselves are unchanged. `docs/GAME_UPDATES.md` has a record of r813697.
- The three game data snapshots were regenerated from the live install. Only their headers
  changed: the date, and the module each names, which had still said `bartlebot/`.

### Known

- Resizing a running client fails on r813697: the video manager's `_FRAME_PATTERN` matches
  nothing, so `ClientResizer` gets the error before anything is written and the client keeps
  its size.

## 0.2.1

The four fixes of core's 0.2.1 milestone, and the `ElidedLabel(color=)` BartleBot's bot
header needs, which makes this BartleBot's first release pin. Nothing breaks.

### Added

- `ElidedLabel(color=)`, a `THEME` token the text is painted in and re-read on every theme
  switch, as `ThemedTextLabel`'s is; and `ElidedLabel.shown_text()` and `is_elided()`, as
  on `WrappingElidedLabel` (#3). `text()` is still the full text.

### Changed

- `ElidedLabel`'s tooltip is the full text only while the text is elided, as
  `WrappingElidedLabel`'s is, where it was the full text on every label (#3). A label that
  fits shows no tooltip unless the caller set one. A tooltip the caller set is kept beside
  the full text, and through the next `setText`, which used to overwrite it; and on either
  label, one that is the label's own text is not said twice.
- An `ElidedLabel`'s minimum width is 24px or its text's width, whichever is less, and an
  empty one asks for no width (#3), where every label's minimum was 24px and an empty one's
  hint about a character. It elides inside `margin()` as well as the contents margins,
  which its size hint already counted.

### Fixed

- The render sheet frees what a surface `deleteLater`'d before it grabs, as the running
  app's event loop does, so Quick Launch draws each card once instead of twice (#11). A
  product's sheet changes only where a surface was drawing widgets it had replaced.
- A disabled control in the light theme reads as off, not as a shade of enabled (#2):
  `surface_disabled` moved from `#e4e7ec` to a grayer `#dadfe8`, 1.19:1 off `surface`, and
  `text_disabled` from `#a8adb8` to `#9096a3`, 2.2:1 on it. Everything both products draw
  disabled changes with it, fields, combo boxes, buttons and cards, and so do the two that borrow
  `surface_disabled` as a hover: `ActionButton`'s `TERTIARY` and `IconButton`. The dark
  theme is unchanged.
- `wizard_names.capture_from_combat` reads the local player's nameplate again, where it never
  returned a name (#6): it read `client.combat_handler`, which no client has, and logged the
  `AttributeError` at DEBUG. It builds wizwalker's `CombatHandler(client)` instead. A failed
  read mid-fight, a wizwalker error or a `ValueError`, is still logged at DEBUG; anything
  else is a warning with its traceback. It still never raises. The test client lost its
  `combat_handler`, which is what hid this, so a product test that set one has nothing to set.

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

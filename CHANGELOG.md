# Changelog

What each release of bartleby-core changes for a product that takes it. A product moving
its pin reads the **Breaking** list of every release it moves past: that list is what it
has to fix, whichever product the change was made for. `docs/UPSTREAM.md`, *Releasing
core*, says how a release is cut.

Versions are SemVer, with Cargo's rule while core is 0.x: a minor bump (0.2.0 to 0.3.0)
can break a product, and a patch bump (0.2.0 to 0.2.1) breaks nothing.

## Unreleased

### Added

- **Following is back**, as it was before 0.4.0 shelved it: `bartleby.runtime.movement`
  (`Follower`, `spacing_for`, `Trail` and its geometry) and the timing constants only it
  reads, `FOLLOW_TICK`, `FOLLOW_STALL_WINDOW`, `FOLLOW_BACK_OFF`, `LEADER_SETTLE`,
  `FOLLOW_PROMPT_WAIT`, `FOLLOW_LINK_WAIT`, `FOLLOW_GATE_WAIT` and `FOLLOW_PORT_RETRY`.
  Gather alone did not keep a team together: a solo zone refuses the port, a short port costs a
  loading screen, and a ported wizard was not seen to join the leader's fight. Walking routes
  and `docs/WALKING.md` stay on `walking-shelf`.

## 0.6.0

Hooking the 2026-11-19 build, r814353, which KingsIsle built with the old compiler again. A
minor release for its wizwalker pin, which a product that pins wizwalker moves too.

### Breaking

- **wizwalker is pinned at `991c257`** (`bartlebot-pin-20261119`), where it was `a1559cf`. A
  product that pins wizwalker itself pins the same commit. wizwalker's `HookHandler.AUTOBOT_PATTERN`
  is now `AUTOBOT_PATTERNS`, one per compiler, and `Duel.duel`/`write_duel` are gone, replaced
  by `max_archmastery_team0`/`_team1`; nothing in core used any of them.

### Fixed

- Every client hooks again on r814353, so the launcher's auto-select reads the screen and skips
  the loading screens, and clicks sent through the mouseless cursor land. With no hook, a
  launched client never reached `ClientManager.clients` and auto-select fell back to blind
  keys, or with auto-select off left the loading screens to the user.

### Docs

- `docs/GAME_UPDATES.md`: the two compilers and keeping both's patterns, `typedump.py misnamed`
  for an offset `stale` missed once, how `GameClient`'s fields are checked, and *Record: r814353*.

## 0.5.1

Groups read off the HUD, and Teleport to Leader: membership from the group menu button, who
leads from the menu opened, and a group record that says who leads which of a product's
clients, used by Gather and by a product's own ports. A patch release: Breaking is empty.

### Added

- `group_manager.hud_grouped(client)` and `hud_leads(client)`, each `-> bool | None`: whether
  a client is in a group, and whether it leads one, read off the HUD's group menu button
  and its `LeaderText` with no window opened. None in battle, where the menu hides, while
  loading, when the path is not in the window tree, or on a failed read; neither raises.
  `hud_leads` is None too whenever the client's group menu is closed, since `LeaderText`
  shows only inside the open menu. Their paths are `paths.GROUP_HUD_BUTTON_PATH` and
  `GROUP_LEADER_TEXT_PATH`.
- `group_manager.read_leads(client) -> bool | None`, whether a client leads, opening its
  group menu to read it where it is closed and closing it again: two clicks for a closed
  menu, none for an open one. None in battle, while loading, when the menu will not open or
  on a failure; never raises. It takes no lease, so a caller holds one.
- The group record in `group_manager`, the one place that says who leads a group and which
  of a product's clients are in it, with each one's character id, held weakly by client.
  `group_record(client) -> GroupRecord | None` reads it unchecked (`leader`, `members` with
  the leader first, `names`). `members_led_by(target, clients) -> list | None` checks it with
  the cheap reads (the HUD's group button, each character id) and answers the clients it
  proves in a group `target` leads, or None for no record of `target` leading.
  `learn_group(target, clients) -> list` learns a group with no record, as one the player
  formed by hand: one `read_leads` on `target` and, if it leads, one `read_group_state`.
  `forget_group(client)` throws a record away, and `forget_all_groups()` every one.
  `form_group` writes it on every way through that leaves a known leader, already grouped
  included, learning the leader by `read_leads` where nothing else says. A cheap read that
  contradicts it throws it away: the leader reading ungrouped or on another character, or an
  open menu disagreeing about who leads. A member reading ungrouped, on another character,
  or leaving by `leave_group` is dropped from it; a leader leaving takes it along.
- `GroupState.leads`, the HUD's answer for that client, read on the same survey and left
  out of comparisons, so `GroupState(grouped, roster)` builds and compares as before.
- `node_helpers.visible_if_present(client, path)`, `is_visible_by_path` with None for a
  path that resolves to no window.
- `group_manager.port_to_leader(clients_to_move, **kwargs) -> PortToFriendResult`, a port
  to the group's leader by the HUD's own Teleport to Leader, driven by `port_clients` as
  `port_to_group_member` is, with the same arguments and result. It closes the group menu
  on every client once the loading screens clear. Its starter is
  `teleport_to_leader(client)`, which opens the menu on a client the HUD shows grouped,
  reads who leads with it open, and raises ValueError, closing the menu it opened, unless
  the client reads not leading, since on the leader the same button summons the whole
  group; a leader passed in is left out as unstarted. `close_group_menu(client)` closes the menu
  where it is open and never raises. The button's path is `paths.GROUP_TELEPORT_BUTTON_PATH`.
- `group_manager.leader_roster(clients) -> LeaderRoster | None`: when the HUD shows every
  client grouped and the leader is known, by its open group menu or by the group record
  proving every other client in its group, that leader's index and its `read_group_state`,
  from one social-window visit; None, with no window opened, otherwise. `LeaderRoster.lists(name)`
  says whether a wizard is in the leader's group by its roster, case-insensitively, and is
  False for no name or a leader the visit found ungrouped. `is_grouped`'s one-leader path
  is built on it.
- `group_manager.missing_from_record(target, clients, proven) -> list`: the clients a valid
  record of `target` leading leaves out while the HUD shows them grouped, with no window and
  no click. `learn_group` now learns the group again, once per call, when one is missing,
  since the player may have added that wizard by hand; a client the learning finds in
  another group is remembered as outside it, by character, until the HUD shows it leave, so
  it does not make the next call learn again. `helpers.gather` learns again the same way,
  under its lease on the source.

### Changed

- `helpers.gather` ports each mover by Teleport to Leader (`port_to_leader`) when the group
  record proves it in a group the source leads (`members_led_by`), since a mover in a
  stranger's group reads grouped too. With no record of the source leading, and a grouped
  mover, it learns the source's group (`learn_group`) under a lease on the source: one group
  menu read, put back, and a roster read only on a leader. A valid record clicks nothing on
  the source, and a leader-route mover that does not go forgets it. Every other mover goes
  through the friends list as before, and both batches port at once. A source whose name is
  not known still sends its proven movers by the leader route. Each slot's route is logged.
- Group membership comes from the HUD, and the social window is opened only to tell which
  group. `read_group_states` skips every client the HUD shows ungrouped, so a survey at bot
  start opens nothing. `is_grouped` is False with no window when any client reads
  ungrouped, and reads the one leader's roster alone when exactly one of a product's clients
  leads and every cached wizard name is on it; anything less certain is the full survey,
  as before. `leave_group` and an invite's join confirmation watch the HUD instead of the
  Leave Group button, and a client in battle or loading falls back to that button.
- `form_group` sends the invitations from the client that actually leads the group it
  keeps, when one of the product's clients does, ahead of `leader_index`: the survey's or
  the record's leader, or else the first of that group whose group menu reads leading.
- `read_group_state` returns the HUD's answer, with no roster, when the social window does
  not open, where it returned a blank state; and it logs a warning when the HUD and the
  Leave Group button disagree on a loaded Group page.

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

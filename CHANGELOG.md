# Changelog

What each release of bartleby-core changes for a product that takes it. A product moving
its pin reads the **Breaking** list of every release it moves past: that list is what it
has to fix, whichever product the change was made for. `docs/UPSTREAM.md`, *Releasing
core*, says how a release is cut.

Versions are SemVer, with Cargo's rule while core is 0.x: a minor bump (0.2.0 to 0.3.0)
can break a product, and a patch bump (0.2.0 to 0.2.1) breaks nothing.

## Unreleased

Nothing breaks.

### Fixed

- **Walk maps for the 48 Challenge Mode copies** of mapped zones, which had none, so no walk
  could be planned in a dungeon entered in Challenge Mode. `tools/build_zone_maps.py` now builds
  and verifies every copy of a listed zone the install has a wad for.

## 0.15.2

Nothing breaks.

### Added

- **A walk map for Ahmarra's Ruined Hermitage**, which BartleBot's Captain Nemo bot walks in
  (BartleBot #339).

## 0.15.1

Nothing breaks.

### Added

- **Walk maps for Ahmarra's main zones**, the eleven beyond Bale Flotilla (BartleBot #339).
  BartleBot's zone map declares them, and needs them to walk in them.
- **Ahmarra's ways about its zones are links**: its teleporting NPCs, leaf rides and wing
  harnesses, safe currents, whalebirds and Bale Flotilla's two teleporters. Primal Archipelago,
  Dark Water Trench and Eastern Sea, which were in pieces no walk joined, are whole. A carpet
  ride's template names its landing (`InteractTemplate.ride`); "Swim" is a travel verb; and two
  travel objects named `X` and `X-A` or sharing a zone tag, and a teleporter's `_TOP` and
  `_BOTTOM`, are two-way pairs, linked only between pieces of floor no walk joins.
- **A quest's title is no longer read as an object's prompt** when its template holds another.
  With the pair rules, this also changes links in Tyrian Gorge (two ladders added), Ruined
  Alcazar (isle teleports), the Hive (its Palace of Fangs ladder now lands at the other end, not
  the middle) and Stone of Heaven (two ropes added). Every walk the zone map and shipped bots
  ask for in them still plans; six in the Hive take fewer ladders. None of these is checked in
  game.

### Fixed

- **A render sheet's pixels no longer depend on where the mouse pointer is.** Qt opened each
  surface on the pointer's screen and grabbed it at that screen's scale, so with two screens
  scaled differently, moving the pointer between them made two runs of one tree differ.
  `render_sheet` now puts every surface on the primary screen.

## 0.15.0

### Breaking

- **The fallback DSL uses Aegis and Indemnity, and needs wizsprinter `5d5bd4c` (`bartlebot-pin-20261127`) or later.**
  `LAST_RESORT_DSL` gives a blade `[sharp | "Aegis"]`, a trap `[potent | "Indemnity"]` and a
  shield `["Aegis"]`: an enchant's backups, which an older wizsprinter does not parse. A
  product moves its own wizsprinter pin with this one, and a shipped Default that must equal
  the fallback (BartleBot's) changes with it.

### Fixed

- **A `|` inside an enchant's bracket is a backup, not the next priority.** The DSL editor's
  scanner (`dsl_syntax.scan_line`) kept the bracket open past it, and completion after it offers
  what the bracket does. The tooltip shows the form.
- **A step's lip is floor.** `collision.walkable_triangles` keeps a face too steep for floor
  when it rises no more than `LIP_RISE` (20, the step a walk takes). Ironworks' raised walkway
  ends in lips rising 12 to 18 that were read as cliffs, which left no route onto it. The maps
  are rebuilt: four floors' pieces join (Ironworks, Celestia's Science Center, Zafaria's Drum
  Jungle, Marleybone's Roof) and 100 others fill small holes.
- **A teleporting volume is a link before it is an exit.** `zone_links.zone_links` takes
  `teleports` (`route.links_and_exits` passes the zone's), and a volume a teleporting trigger
  fires on, whose name pairs it with no landing, takes its landing from `zone.nav` as a volume
  whose name says it moves the wizard does. Nine volumes once taken for exits are links now,
  among them Crimson Fields' passage north to `FromBattlefieldAMain`.

## 0.14.4

Nothing breaks.

### Added

- **`walk.plan_in_thread(plan, *args)`** runs a plan in a worker thread and, when its caller is
  cancelled, sets the plan's `stop` event, so the search ends instead of running on. `astar`,
  `ZonePlanner.route` and `ZonePlanner.no_route` take `stop`, and raise `route.PlanStopped`
  once it is set. A product that plans in a thread of its own should plan through it.

### Fixed

- **Stopping a run mid-plan stops the plan.** A cancelled walk left its search running in its
  thread, holding the GIL, and with it the event loop, for as long as the search took: in
  Dragonspyre's Arena, half a minute past each of two stopped runs.
- **`no_route` plans once when no closed gate could help.** It planned once for every closed
  gate, each searching all the floor it could reach, and eight listed stones made the Arena's
  half a minute. It now plans with every closed gate open first, and stops there when even
  that finds no route.
- **Dragonspyre's fast-travel stones are links.** Their names (`DS_TeleportStoneA_Main`,
  `ArenaTeleportStoneSecondaryA`) put a letter and a role where Triton's put `TeleporterA` and
  `_ToMain`, so no rule saw them, and the Arena's floor, in pieces the stones join, had no
  route from the Necropolis to the Academy. The shipped maps are rebuilt with them.

## 0.14.3

Nothing breaks.

### Fixed

- **A walk waiting for mobs eases even when they leave it a little room.** Room at the full
  margin short of the slack a waiting walk goes on with counted as the mobs having left it room,
  so after `WALK_MOB_CLEAR` the spell of waits started over while the walk still stood: it
  never came down to `walk.NARROW_MARGIN`, and could have waited for good. A waiting walk now
  needs the room it would go on with for the spell to start over.

## 0.14.2

Nothing breaks: no product's code needs to change, though a walk held up by mobs now ends
differently.

### Changed

- **A walk held up by mobs steps down instead of ending blocked.** After `WALK_MOB_EASE` (8 s)
  of waiting it keeps only `walk.NARROW_MARGIN` (40) beyond each trigger radius, and after
  `WALK_MOB_WAIT` (30 s) it walks on past them unwatched, a fight met ending the walk `FIGHT` as
  any does. Mobs leaving it room for `WALK_MOB_CLEAR` (5 s) start the steps over.
  `WalkEnd.BLOCKED` now comes only from stalls. `WalkResult.eased` and `ran_through` count the
  steps, and `walk.Margin` (`FULL`, `NARROW`) is the room asked of `room`, `tightest`,
  `mob_discs` and `choose`. `docs/WALKING.md`, *Walking a route*, has the evidence (the Hive's 8
  spiders on one loop).

### Added

- **A walk held up by mobs says which.** `RouteWalker`'s log names the mob leaving the least
  room when it starts to wait, and again when another takes over, and says why no detour was
  taken; a walk ending blocked by mobs names the last in its detail and in the new
  `WalkResult.held_by`. `walk.tightest` is `walk.room` with that mob, and `Mob.mapped` says
  whether its trigger radius is a patrol's. `docs/WALKING.md`, *Walking a route*, has the rest.

## 0.14.1

Nine more zone maps (Stage 6 of BartleBot #322). Nothing breaks.

### Added

- **The zones of every Scales of Knowledge** that no shipped bot farms: Keep of Ganelon, North
  Sands of Time, the Cavity and the two zones on the way to it, Xibalba, Horizon Hold, Storm
  Titan and Manticore's Madness. 473 zones in all.
- **`tools/build_zone_maps.py --names`** also takes one file. `docs/WALKING.md`, *Which zones*,
  has the command.

### Fixed

- **`zone_grid.Grid` on a zone with no floor** raises `ValueError` saying so, where it failed in
  `_stack` with a `TypeError`. The Dragonspyre dragon's flight path is one.

## 0.14.0

A walk into a fight, and the floor outside a zone's exits (Stage 5 of BartleBot #322). Nothing
a product calls today changes; the new arguments default to what it did.

### Added

- **`RouteWalker.walk(lease, goal, fight=True, spare=…)`**: a walk into a fight. Its watch leaves
  out the mobs in *spare* (by global id) and any whose trigger radius comes within
  `route.FIGHT_REACH` (120) of the goal, which it would otherwise wait on forever, and its plan
  pays nothing for their patrols. Every other mob is watched and every other patrol costs as on
  any walk. `walk.Mob` gains `key`, the mob's global id.
- **`ZonePlanner.route(..., fight=True)`** and `no_route(..., fight=True)`, planning on
  **`ZonePlanner.fight_cost(at)`**: the patrols' cost less every patrol whose trigger radius comes
  within `FIGHT_REACH` of *at*.
- **`ZonePlanner.outside_exits(x, y, z)`**: the point when no exit's no-go area holds it, else the
  nearest floor that none holds, or None. A pickup inside an exit walks there (BartleBot #35).

## 0.13.0

The walker reads its zone's gates from the client (Stage 3.2 of BartleBot #322).

### Breaking

- **`walk.client_mobs` is gone**, and `RouteWalker`'s `mobs=` is now `watch_mobs: bool`: the
  walker reads its client's entity list itself (`ClientRead`, whose `mobs` is the old
  `MobRead`), for its mobs and its gates on one pass. A product that passed
  `mobs=client_mobs(client)` passes `watch_mobs=True`.

### Added

- **`RouteWalker` closes the gates its client lists** before reaching them (`GateRead`),
  whether or not it watches mobs: read before the first plan and every `WALK_MOB_REFRESH` after.
  A listed gate on the way left is planned around, or the walk ends `NO_ROUTE` naming it, with
  no stall. One not listed within `GATE_LISTED_RANGE` (3,500) is gone: a stall near it closes
  the floor ahead instead, and a gate a pass closed opens again after `GATE_GONE_PASSES` (2)
  passes show it gone. `WalkResult.gates_seen` and `gates_opened` list them. No map rebuild.
- **A goal on a closed gate's floor has no route**: `ZonePlanner.route` returns None, and
  `no_route` names the gate, where it used to snap the goal to the floor beside the gate.
- `ZonePlanner.open(gate)`, the inverse of `close`, and `crosses(gate, path)`, whether a line of
  waypoints steps on a gate's floor. `ZonePlanner.blocks` records each `block`.
- `testing.sim_client.SimEntity`, and `SimClient.entities`, its entity list (None for one that
  cannot be read).

## 0.12.1

Gates in the zone maps (Stage 3.1 of BartleBot #322), wizwalker's pin for live r815378, and a
duel's id. Nothing breaks.

### Added

- **Each zone's map holds its gates** (`movement/gates.py`): every placed object whose template
  names a solid file, its solids where it stands. 3,152 of them in the 464 zones, and the maps
  come to 20.5 MB, from 18.5; the floors are unchanged, and the map format is 2. **A gate is open
  until a walk finds it closed**, since the game's files cannot say which a player's quests have
  removed, so every route planned with no gate closed is the route it was. A link object's
  solid, such as a teleporter stone's, is always a wall (78 of them), and its link is still
  taken from the floor around it. `CACHE_VERSION` is 2, so every saved copy is rebuilt once.
- `ZonePlanner.close(gate)`, `gates_on(route)`, `gates_near(x, y, z, reach)`, and
  **`no_route(start, goal)`**, which explains a missing route as a `NoRoute`: the zone, both
  points, and the closed gate in the way with its `Crossing`, the length a hop past it would
  need. `route()` still returns None.
- `RouteWalker` closes a gate it stalls within `GATE_REACH` (150) of, on its own planner, and
  replans; with no way around, the walk ends `NO_ROUTE` naming the gate.
  `WalkResult.gates_closed` and `WalkResult.no_route` say what it found.
- `collision.parse_solid_xml`, `place_solids`, `placed_footprints` and `object_rotation`, for a
  placed object's solid file.
- **Core writes no position**: `tests/test_no_position_writes.py` fails on any call under
  `bartleby/` to `teleport`, `tp_to`, `write_position` or `write_xyz`.
- `tools/build_zone_maps.py --verify` sees a change to a template or solid file a zone's gates
  were read from (`index.json`'s `objects` hash).
- **`combat.duel_members.duel_id(client)`**, the game's id for a client's duel (`Duel.m_duelID`),
  or None. The same on every client in the duel, as a wizard's list of its members is not while
  that wizard joins a duel already planning; BartleBuddy places its fighters by it.

### Changed

- **wizwalker is pinned at `ed97af1`** (`bartlebot-pin-20261125`), whose card and combatant
  window reads follow live r815378's +8 move; on the old pin every wizard passes every round.
  A product pinning wizwalker itself moves its own pin with it.
- `spell_names.json`: six Beastmoon sigil spells renamed on r815378.

## 0.12.0

Fixes to walking a route found by reviewing Stages 1.1 to 2 of BartleBot #322 and by walking the
walker in game. A minor release: `astar` and `runs` change.

### Breaking

- **`route.astar` returns `(nodes, jumps)`**, `jumps` being each k whose step from node k to node
  k + 1 is a link's, and **`route.runs(path, jumps)`** cuts at those steps. `Route` gains a
  required `jumps`, which `ZonePlanner.route` fills in. A caller of `ZonePlanner.route` and `legs`
  changes nothing; one building a `Route` or calling `astar` or `runs` itself passes or takes
  `jumps`.

### Added

- **`WALK_CEILING_FACTOR`** (3) in `timing.py`: a walk ends `TIMED_OUT` once it has taken that many
  times its first plan's time, planning included and only its waits for mobs left out, however
  often it replanned or detoured.

### Fixed

- **A link whose landing is the next cell across from where it is taken**, such as a short ladder,
  ends its leg instead of being walked past without pressing Interact. No shipped map has one
  today.
- `RouteWalker`: lets go of backward while it plans a detour; asks whether a fight started or the
  zone changed before replanning after a jump, so a zone exit ends the walk `ZONE_CHANGED`, not
  `NO_ROUTE`; always stops its mob watch, a second cancel during its cleanup included; gives a
  walk time again for the route left after each replan or detour; and no longer raises on a reset
  onto a leg of one waypoint.
- `RouteWalker` makes its first fight check and key-binding read while it plans its first route,
  so a fresh process no longer stands about half a second before walking.
- **A saved zone map damaged inside its zip** (a corrupt deflate stream, an unknown zip version, a
  garbled `.npy` header) is rebuilt instead of failing every walk in the zone and `warm()`.

## 0.11.2

Walking a route (BartleBot #322, Stage 2): core walks a planned route on a movement lease. A patch
release: it breaks nothing.

### Added

- **`movement.walk`**: `RouteWalker(client, planner, avoid=…, mobs=…).walk(lease, goal)` walks a
  wizard to a point in its zone through the lease's input, from where it stands: the yaw written
  each `WALK_TICK`, resets walked again, stalls backed off from three times, volume links stood in
  and objects pressed once their prompt shows, live mobs (`client_mobs(client)`, watched by
  `MobWatch`) detoured around before they are waited for. It ends in a `WalkResult`: `ARRIVED`,
  `NO_ROUTE`, `BLOCKED`, `FIGHT`, `ZONE_CHANGED` or `TIMED_OUT`. Every plan runs in a worker
  thread. It never writes `speed_multiplier` or a position. Its timing is `WALK_*` in `timing.py`
  (`docs/WALKING.md`, *Walking a route*).
- **A held event loop lets go of held keys**: `ClientInput`'s watchdog thread sends key-up for
  every held key once the loop is `HELD_KEY_LOOP_LATE` (0.2 s) behind their repeat, and forgets
  them, so a holder finds them no longer `held` and holds them again. This covers BartleBuddy's
  followers too. `KeySender` gains `up_now`, a key-up from any thread; a sender without it has its
  keys left to the loop as before.
- `SimClient.on_step`: where the game puts a wizard that walked, for a test's wall, teleport
  volume or reset.

### Changed

- **A route never walks through a volume link it does not take** (`ZonePlanner.route`), since
  stepping in moves the wizard. A volume link's floor, its reach plus `EXIT_MARGIN`, is left only
  by its link (`astar`'s `taken_only`); where that leaves no route, the plan is made as before.
  `route.link_reach` and `ZonePlanner.volume_floor` are new.

## 0.11.1

The shipped zone maps (BartleBot #322, Stage 1.2): every zone BartleBot walks has a map, so a run
plans a route with no wad and no type dump. A patch release: it breaks nothing.

### Added

- **The shipped zone maps** (`docs/WALKING.md`, *The shipped zone maps*), under
  `bartleby/runtime/game/data/zone_maps/`: 464 zones on 442 floors, 18.5 MB. Each zone's floor is
  its `collision.bcd`'s triangles and footprints, shared by zones whose `collision.bcd` is the same
  file; its patrols, arrival points, links and exits are a small JSON file of its own.
- **`movement.zone_maps`**: `has(zone)` answers from the index alone; `await load(zone)` gives the
  zone's `ZonePlanner` off the event loop, planning as one built from the wad does, cell for cell.
  What a load builds (the grid, its passable floor, the patrols' clearance) is saved on the user's
  machine, under `%LocalAppData%/Bartleby/cache/zone_maps/`, so each zone is built once a machine:
  2 to 16 s the first time, 50 to 250 ms after. `ZoneMaps.warm(zones)` builds them ahead of need,
  on a thread of the product's. A session keeps the 8 planners used last.
- **`tools/build_zone_maps.py`** builds the maps from an install's wads, deterministically, for
  the zones a product names (BartleBot's `zone_nav.json`, `bots/` and `subgraphs/`); `--verify`
  checks them against an install and `--compare` plans on a map and on its wad and diffs.
  `tools/zone_files.py` is the wad reader it uses, moved from BartleBot's probes, which now take it
  from here. It reads a template in a world's own data wad (`|Darkmoor|WorldData|...`), which 13
  zones' mobs and chests need, and works on katsuba 0.4 and 0.6 alike.
- `movement.route`: `assemble`, `grid_of`, `floor_grid` and `links_and_exits`, the steps of
  `build_planner`, so a planner from a map is put together as one from a wad is; and
  `zone_grid.Grid.from_arrays`.

## 0.11.0

Deck sets (BartleBuddy #31, Stage 9), followers that find the trail again once something
moved them, and route planning (BartleBot #322, Stage 1.1). A minor release: a product that
called `Trail.nearest` fixes the Breaking item below, and the `runtime` extra now brings numpy.

### Breaking

- **`Trail.nearest` is gone**, replaced by `Trail.rejoin`, which takes the leader's newest pass
  over the ground rather than the nearest point (under Fixed). No product called it.

### Fixed

- **A follower that something other than its own walk moved finds the trail again from where it
  stands** (`movement.follow`): Gather, the player, or a zone change. It moved more than
  `DISPLACED`, 300 units, or changed zone between two plans. It kept its old place on the trail
  before, so after Stop following, a teleport and Gather it walked back to where the leader had
  left: to the teleport's spot, where it pressed Interact, or out through a door taken long before.
  Each such move is logged at INFO.
- **A follower made afresh, or one finding the trail again, rejoins the leader's newest pass**
  (`Trail.rejoin`): the newest point within `REJOIN_SLACK` of the nearest. The nearest alone could
  be on an older pass over the same ground, which walked the follower the whole way round again.
- **A follower re-reads both clients before a port** (`StateReader.read_now`), and ports only
  if the port is still the plan. A zone is read about once a second, and Gather lets go of a
  wizard as its loading screen starts, so a follower that saw itself still in the zone it left
  ported again, through a friends list the loading screen hid, and then stood for
  `FOLLOW_PORT_RETRY`.
- **A follower going into a dungeon by its own press logs no move** (`SigilState.went_in`, which
  the follower takes): "went in" is enough.

### Added

- **`Follower(paused=)`**: while it answers True the follower sends nothing at all, a fight's
  join and a sigil's cancel included, but goes on reading its sigil and the X presses on it.
  Resumed, it finds the trail again from where it stands, and ports if that is beyond
  `PORT_FAR`. BartleBuddy pauses a follower the player is on with it, where it used to stop the
  follower and make a new one: that one knew nothing of an X pressed meanwhile, and walked the
  wizard off its seat in its countdown (2026-11-23).
- **`bartleby.shared.deck_sets`**: a `DeckSet` names one deck for everyone and exceptions by character
  id, in the vendor's `deck_sets.json`, and `DeckSetStore` reads it and writes a set at a time, refusing
  one another product has changed since it was read (`DeckSetChanged`).
- **`bartleby.qt.combat.deck_sets`**: `DeckSetList`, a product's deck sets in its settings, the editor
  `DeckSetDialog`, and `DeckWizards`, which carries the team's wizards and the decks each owns to it
  (`runtime.views.DeckWizard`).
- **`bartleby.qt.widgets.pickers.deck_name_picker.DeckNamePicker`**, a deck's name on the game's three
  wheels, or one of the decks the user's wizards own.
- **`runtime.decks.worn_deck(client)`**, the deck a wizard has on, read without its inventory.
- `bartleby.qt.dialogs.confirm.confirm_delete`, `AddCard(gutter=)`, the `boss_decks` glyph.
- **`DeckSetList(card_factory=, add_card=)`**: a product draws each set on a `DeckSetCard` of its own,
  with what it adds, and leaves the add card out where its surface has an add button of its own,
  calling `DeckSetList.add`. BartleBuddy's sidebar Combat tab does both.
- **Route planning** (`docs/WALKING.md`), moved from BartleBot's `tools/probe_walk_route.py`:
  `movement.route.build_planner(ZoneFiles)` gives a zone's `ZonePlanner`, whose `route(start,
  goal, avoid=)` keeps around patrols, through the zone's links, and out of its exits, and whose
  `legs(route)` are the stretches to walk. The files come in parsed: `movement.collision`,
  `movement.patrols.mob_patrols`, and `movement.zone_links`' `parse_volumes`,
  `teleport_volumes`, `parse_zone_nav` and `parse_interact_template`, each from bytes a tool read.
  Nothing calls it at run time yet. The `runtime` extra adds numpy for it.

## 0.10.1

Mob and boss duels for BartleBuddy, and the card both apps' Settings › Combat choose a duel on
(BartleBuddy #31, Stage 7); and the keyboard under a dialog (#23). A patch release: it breaks
nothing.

### Added

- **`bartleby.qt.combat.duel_card.DuelCard`**, BartleBot's card, for a duel a product's settings
  hold: the product hands it its words (`DuelCardText`), its library, what answers with no choice
  (`Unset`), what is wrong with a choice, and its Combat Strategies window.
- **`combat_defaults.FightKind`** (`MOB`, `BOSS`) and **`runtime.combat.fight_kind.fight_kind(client)`**,
  which reads it from the duel's participants: boss when any living enemy is one, None while it
  can't be told.
- **`runtime.combat.duel_members.duel_members(client)`**, the global ids of everyone in a client's
  duel, or None when it can't be read, and **`own_id(client)`**: two clients share a fight when each
  one's duel lists the other.
- **`combat_defaults.choice_notes`**, what is wrong with a duel chosen for a team size, and
  `SIZE_RANGE`; `shortfall_text` takes `where=`.
- **`JoinOrder.wait()`** holds every follower at its gate until the order is set, or the fight ends.

### Fixed

- **`GameView` gives no game the keyboard while a dialog or a popup menu is open**, and gives the
  keyboard target its own once the last dialog, or the menu, has closed (#23). It used to drop what
  it owed and name the game as the player's. A product that called `give_keyboard_back()` after a
  dialog of its own can drop the call.

## 0.10.0

The Combat Strategies window, from BartleBot (BartleBuddy #31, Stage 6), with the widgets it is
drawn from and the read side of the game art; and a wizard's decks, read and equipped by name
(#22, Stage 8 of BartleBuddy #31). It needs wizwalker at the pin in `docs/UPSTREAM.md`. A minor
release: a product with its own copies fixes the Breaking item below.

### Breaking

- **A product's own copy of a module or helper listed under Added fails its `core_copies`
  guard** until it drops it: BartleBot's Combat Strategies window and the widgets under it, its
  art read side, its deck contents, and its spellbook helpers. A product with no copy changes
  nothing.

### Added

- **`bartleby.qt.combat`**: `dialog.CombatStrategyDialog(ws, settings)`, the shared strategy
  library's window in `DialogMode.LIBRARY` and `DialogMode.PICK`, with its editors
  (`duel_editor`, `player_editor`, `assignment_card`, `deck_editor`, `combat_order_widget`),
  `filter_bar`, `dsl_checks` and `deck_link`. A product hands it a working set and a
  **`strategy_settings.StrategySettings`**, three methods that keep the favorites; a
  `deck_link` and an `archmastery_default` are optional, and without the first the Deck tab has
  no Import, Apply or New. A product's own additions are a subclass, through the hooks `dialog.py`
  lists (`KindPage`, `AboutSection`, `StrategyCard.owner_pill`).
- **Widgets**: in `bartleby.qt.widgets`, `cards.strategy_card`, `card_art_tile`,
  `deck_card_list`, `archmastery_badge`, `archmastery_editor` and `duel_slot_gem`;
  `pickers.tag_picker`, `school_picker` and `player_picker`; `pills.tag_pill`, `source_pill` and
  `player_pills`; `inputs.dsl_edit` and `growing_text_edit`; `labels.step_heading`. A tag
  picker, pill, chip or section takes its `TagVocabulary`, with no default.
- **`runtime/game/card_art.py`**: the extracted game art's read side. It reads the running
  product's own `art/` folder, and where there is none, another Bartleby product's, so a
  product that extracts nothing draws what another did. `own_art_dir()` is where an extractor
  writes.
- **`runtime/combat/deck_content.py`**, what a saved deck holds, with
  `resolve_apply_archmastery(deck, app_default)`; **`runtime/combat/dsl_check_worker.py`**;
  `spell_schools.PIP_SCHOOLS`; `store_files.pill_text` and `OFFICIAL_UPDATED`.
- **For tests and render sheets**: `qt/testing/sample_library.py`, a small strategy library;
  `extracted_art.py`, a made-up art cache; `picker_helpers.py`.
- **`bartleby.shared.deck_names`**: `DeckName`, a deck's name by its three lang keys, so it is
  the same in every language and a wheel left empty is left out of a file
  (`model_dump(exclude_defaults=True)`); `text(locale)`; `matches` and `same_text`, for reading
  a backpack row; `Wheel`, `choices(wheel, locale)` and `locales()`, for a picker. Its snapshot
  is `bartleby/shared/data/deck_names.json`, which a packaged build that names decks bundles,
  written by `tools/dump_deck_names.py`.
- **`bartleby.runtime.decks`**: `owned_decks(client)`, each `OwnedDeck` by name, id and cards,
  with the equipped one marked; `equip_deck(client, name)`, under the caller's lease, with an
  `EquipResult` (`EquipOutcome`: equipped, already on, not owned, owned twice, empty, in a
  fight, the backpack can't open, failed).
- **`node_helpers.is_spellbook_open` and `close_spellbook`**, BartleBot's, which drops its
  copies when it takes this release.
- **`dialog_captions.EQUIP_EMPTY_DECK`**, the question equipping an empty deck raises, and
  `paths`' `BACKPACK_*`, `backpack_row_path`, `backpack_row_equipped_path` and
  `WORLD_MESSAGE_BOX_*`.
- **`runtime.testing.sim_backpack.SimBackpack`**, a simulated client's decks and backpack.
- **`bartleby.shared.locales.LOCALES`**, which `realms.LOCALES` now is.
- **`tools/equip_deck.py`**, which lists each client's decks and equips by name on live clients.

## 0.9.0

The strategy layer under the Combat Strategies window, from BartleBot (BartleBuddy #31,
Stage 5): the working set, the summaries, the tags, the DSL's vocabulary, slot targets, card
kinds and the spell-school snapshot. A minor release: a product fixes the Breaking item below.

### Breaking

- **`strategy_library.LibraryKind` is `StrategyKind`**, the one enum for the library's three
  kinds in every product. A product renames its uses; BartleBot's own `StrategyKind`, which
  had a fourth member for its Bot Combat Strategy, gives way to it.

### Added

- **`bartleby.runtime.combat.strategy_workset`**: `StrategyWorkingSet`, the Save/Cancel
  boundary over the library's `(shipped, user)` roots, with `Changes`, `Conflict` and
  `SharedUse`. Beside the library it may hold a **`StrategyOwner`**: what keeps duel and
  player strategies of its own and names the library's, as BartleBot's Bot Combat Strategy
  does. One owner may be open, edited inside the boundary (`owner`, `own_items`,
  `create(..., own=True)`, `move_to_library`, `owner_dirty`); `others` are counted for what
  uses an entry; `make_owner` lets a window make one (`create_owner`). With none, the working
  set is the library alone.
- **`strategy_summary`**: every display string a strategy card draws (`summarize_duel`,
  `summarize_player`, `summarize_deck`, `format_players`, `find_references`, `UsageCount`),
  with owners in place of Bot Combat Strategies (`UsageCount.owners`, `nouns`,
  `StrategySummary.owner_name`).
- **`strategy_tags`**: the tag vocabulary and its filter rule; `tag_groups(own_source)` puts an
  owner's Source word first in Source, and `facets_match` and `strategy_tag_group` take the
  groups.
- **`dsl_syntax`, `slot_order`, `card_kinds`** in `bartleby.runtime.combat`, and
  **`runtime/game/spell_schools.py`** with its snapshot, unchanged but for their imports.
- **`store_files.SOURCE_LABELS`, `LISTED_ORIGINS` and `unique_name`**, and
  **`strategy_library.shipped_items` and `origin`**.

## 0.8.1

The last-resort DSL hits a mob once no boss is left. A patch release: nothing breaks.

### Changed

- **`LAST_RESORT_DSL` hits a mob once no boss is left.** After its single-target hit at the
  boss it carries `?(all(enemies).is_boss == 0) any<damage>[any<mod_damage>] @ enemy`, as
  BartleBot's shipped Default does from its Official set 10. Before it, a wizard short of pips
  for an AoE, or holding none, only buffed and passed at a minion left after its boss died. A
  product shipping Default keeps its text equal to this one.

## 0.8.0

Who is cast for at each planning phase (BartleBuddy #31, Stage 3), and the team's order: the
duel's combat order handed to wizsprinter, and a leader's followers joining a fight in it
(Stage 4). A minor release: a product fixes the Breaking items below, and pins wizsprinter at
`bcd5758`.

### Breaking

- **`default_duel` takes no `ambient`, and `SHIPPED_AMBIENT_DUEL_IDS` is gone** (BartleBuddy
  #31, Stage 4). A fight no node started gets Default Team Duel as its shipped Team default,
  as every other fight does, not Everyone Attacks: the runner hands wizsprinter the duel's
  combat order, so its supports' `ally(last)` is P1 however the team sat. A product drops the
  argument; one that listed the ambient ids, as BartleBot's `--self-check` did, lists
  `SHIPPED_EVERYONE_ATTACKS_DUEL_ID` itself if it still ships it.
- **wizsprinter moves to `bcd5758`** (`bartlebot-pin-20261121`), the commit with
  `SprintyCombat.team_order`, which the runner now sets every round. A product pins the same
  commit.

### Added

- **The team's order, for `ally(-N)`** (BartleBuddy #31, Stage 4). `run_fight(..., order=)`,
  `CombatRun(..., order=)` and its settable `order` take the duel's combat order as player
  numbers, and each round the watch hands the players' global ids in that order to their
  combat as wizsprinter's `team_order` (`FightWatch.team_order`). Without one, the seats
  decide, as before.
- **`movement.join_order.JoinOrder`** and **`Follower(..., join_gate=)`**: a leader's followers
  walk into its fight one after another, each once the one before has joined. While that one is
  on its way (`on_its_way=`, a `Follower`'s `walking`) the next waits however long it takes, and
  one waiting its own turn holds up everyone behind it; one that stopped trying, or never asks,
  holds up the next by `timing.JOIN_ORDER_HOLD` from its turn. A follower waiting at its gate stands, says
  `WAITING_TO_JOIN`, and counts as `joining_fight`. The gate is asked before any port, since a
  port that lands beside a fight is pulled into it at once, out of turn.

- **Who is cast for, at each planning phase** (BartleBuddy #31, Stage 3). `CombatRun` and
  `FightWatch` take `casts`, asked of each player at the start of every planning phase, and
  `hold`, the async context a cast or a flee is made under. A player played by hand is not cast
  for that round, never votes on a flee, and keeps its fight from being fled, the time limit's
  included: `FightWatch.would_flee` names the players of such a fight instead. Without either,
  nothing changes, so BartleBot's fights run as before.
- **`Follower.joining_fight`**: whether a follower is walking into the leader's fight, what a
  fighter's first cast is held for.
- **`official_set.shipped_root(..., others=)`**: more roots to weigh by the set version rule, each
  with its product, as a checkout beside a product that ships no set.
- **`combat_defaults.slot_dsls(duel, player, players)`**, the DSL each player slot runs in a duel,
  the last resort where it runs nothing; and **`DefaultDuel.label`**, a default's phrase for the
  log. BartleBot's `resolve_combat` still builds its own list; Stage 5 (BartleBot #332) moves it
  onto `slot_dsls`.

### Changed

- **A follower made with `port_when_far` ports beyond `follow.PORT_FAR`, 3,000 units**, not
  `RESYNC`'s 1,500, when it starts or is let go. Nearer, it walks, which joins a fight in its
  turn where a port lands in it at once, at the cost of up to about 5 s at a wizard's own speed. `RESYNC` still says when a follower has
  lost the trail, and how near the leader it reads a dungeon sigil's prompt.

## 0.7.0

The fight runner, the DSL checks, the first cast's hold and the spell-name snapshot, moved
from BartleBot so both products fight with one runner (BartleBuddy #31, Stage 2), with what
BartleBuddy's startup, follow and resize work changed since 0.6.0. A minor release: a product
fixes the Breaking items below, and pins wizwalker at `d68082c`.

### Breaking

- **`ClientManager._refresh_account_identity` is gone**, split in two so publishing a client
  waits only on what places it (under Changed): `_learn_identities`, which learns a titled
  client's fingerprint in a task of its own, and `_adopt_by_identity`, which places an untitled
  one. Private, but a test harness that binds it fails at collection: BartleBot's
  `tests/clients/test_account_identity_storage.py` binds `_learn_identity`, `_learn_fingerprint`
  and `_read_user_id` instead, and awaits `_learn_identity(client, username)` where it awaited
  the pass.
- **`SimMemory` answers `heap_regions()` instead of `pattern_scan`**, since every heap scan now
  goes through `clients/heap_scan.py` (under Changed). `scans` counts its passes, and a resizer
  finding its nodes makes one where it made two, so a product test asserting a count of two per
  client asserts one. BartleBuddy's `test_session.py` asserts only none or some, and passes as it
  is. A double of a reader of its own answers `heap_regions()` with `(address, bytes)` pairs.
- **`SimMemory`'s live config nodes are a group's children**, inline in its vector as the game
  lays them out, under a config object and its map of sections (under Added): `USER_SETTINGS` is
  gone, and `place_node`'s default parent is `SimMemory.group`. Neither product imports either.
- **The client manager calls `game_keys.key_map` with `priority=`** (under Added), so a double of
  `key_map` patched in under a `ClientManager` takes it, as core's `test_client_loop.py` now
  does. Neither product has one: BartleBot's `test_run_probe.py` fakes it for the pre-check,
  which passes no priority.
- **The resizer no longer waits out a loading screen** (under Changed), so a resize asked during
  one applies then, and `client_resizer.POLL_INTERVAL`, which only that wait read, is gone: a
  test that patches it fails at setup, as BartleBuddy's `resizing` fixture in `test_session.py`
  did. A test that held a resize off with `SimClient(loading=True)` now holds a lease above
  `Owner.DISPLAY` instead. A `give_back` reaches a client that is loading, where it was left as
  it was with a warning. BartleBot doesn't use the resizer.
- **`Follower` takes no `spacing`**, and `movement.follow.Spacing` and `spacing_for` are gone,
  since every follower walks onto the leader's own spot (under Changed). So are `ARRIVE_PLACE`
  (now `ON_LEADER`), `PLACE_DIRECT`, `timing.LEADER_SETTLE` and `geometry.offset_from`, which
  only they used. A follower's stop says `ON_LEADERS_SPOT` where it said "in place" or "close
  enough", and nothing says "to its place". BartleBuddy's session and its follow-jitter probe
  passed a spacing and no longer do; BartleBot doesn't follow.

### Added

- **`bartleby.runtime.combat`, the fight runner both products fight with** (BartleBuddy #31,
  Stage 2), moved from BartleBot with its tests. `fight` holds `run_fight`, `CombatRun`,
  `FightWatch` and its flee vote, `FightEnd`, `FightResult`, `RoundReport`, `slots_in_battle`
  and the flee itself; each takes a `log(message, level)` callable, never a machine, and takes
  no lease. `dsl` holds `parse_strategies`, `dsl_check`, `dsl_error`, `dsl_warnings`,
  `validate_dsl`, `known_dsl_check`, `store_dsl_check`, `check_dsl_uncached` and
  `dsl_warning_text`; its disk cache, `dsl_check_cache`, keeps
  `%APPDATA%/Bartleby/<product>/cache/dsl_checks.json` for whichever product `product.current()`
  is. `hold.FirstCastHold` holds a fighter's first cast for the players joining it, until they
  are in or AMBIENT_HELP_HOLD has passed since the later of its first planning phase and the
  latest join.
- **`bartleby.runtime.game.spell_names`**, with its snapshot `runtime/game/data/spell_names.json`:
  a card's in-game name (`display_name`) and whether a name in a DSL finds a card
  (`name_problem`), moved from BartleBot for `dsl_warnings`. BartleBot's
  `tools/dump_spell_names.py` still writes the snapshot, into this checkout, since it reads
  BartleBot's type dumps.
- **`on_play`, called once Play is taken**, on `auto_select.auto_select_character`,
  `auto_select.drive_client_in` and `ClientManager.drive_client_in`, for a product that acts
  during the load-in, before the hooks: BartleBuddy resizes the game its large tile is held for.
  It is called with the client the drive borrowed (`pending_client_titled`) on the first read
  after a press that finds character select gone, never while the screen is up, and never
  waited on: an awaitable it returns runs as a task. What it raises is logged and never fails
  the drive. With auto-select off, and on the blind drive, it isn't called. `auto_select.OnPlay`
  is its type. Existing callers are unchanged.
- **`ClientResizer.failed(client)` and `clear_failure(client)`.** A resize asked before the hook
  that failed turns resizing off for the client as any failure does; `clear_failure`, as the
  client is hooked, turns it back on, keeping the nodes and the home found, and ends nothing
  under way. It says whether there was a failure to clear.

- **`clients/static_routes.py`, a chain of reads from a slot in the game's image**, for the
  resizer's config nodes and the key map's bindings (under Changed). `Route` is a slot's
  `patterns`, a tuple tried in turn as wizwalker's `AUTOBOT_PATTERNS` is, each a regex whose one
  group is a RIP-relative disp32; `CONFIG` and `BINDINGS` are the two. `static_routes.slot(reader,
  route)` is the slot, found by `pattern_scan(..., module="WizardGraphicalClient.exe")` once per
  client and kept; `follow(reader, route, chain)` is what *chain* reaches from it, or None, its
  reason traced and logged once per client; `config_groups` and `held_binding_list` are the two
  chains, raising `Missed` with its `reason` where they stop. `client_resizer.config_nodes_at(reader,
  slot)` and `game_keys.binding_list_at(reader, slot)` follow and check them. Traced as a
  `config.route` or `key_map.route` span, with its `outcome`, `route` or `heap`, the `reason` for a
  miss, and `module_scan`, the seconds of the slot's pattern scan, on the attempt that made it.
  `SimMemory` has the config object's slot, map and group, a module answering `pattern_scan` and
  counting `module_scans`, `place_slot_code()` to open the route, `put` and `put_u64`;
  `testing.sim_client.rip_operand` and `CONFIG_STORE`, `CONFIG_CLEAR` and `BINDINGS_STORE` are
  code each pattern matches, for a double of a reader of its own.
- **`clients/heap_scan.py`, one scanner for every scan of a client's whole memory.**
  `heap_scan.scan(reader, *patterns, priority=Priority.URGENT | Priority.BACKGROUND)` returns
  each pattern's hits, a pattern being a literal or a set of literals, from one pass shared by
  every request waiting for that client; `heap_scan.forget(reader)` ends a client's waiting
  requests with `ScanDropped`. `HEAP_SCAN_PASSES` (1) run at once across every client (above
  one, background passes take all but one place), and a client's background requests wait
  `HEAP_SCAN_BACKGROUND_GRACE` (3 s) from its first request unless an urgent pass over it comes
  first. Traced as a `heap_scan.pass` span per pass, with the regions it `skipped`, and a
  `heap_scan.request` span per request, with its `wait` in the queue, each with the `hwnd` an
  enclosing span names. `run_trace.current_slot()` gives the innermost span's player, and
  `run_trace.enclosing(name)` the innermost open span's attribute *name*.
- **`heap_scan.promote(reader) -> int`** raises every background request waiting on a client to
  urgent, in place, queued as if asked for now, and ends the client's grace; it returns how many
  it raised, and a pass already under way runs on as it began. Traced as a `heap_scan.promoted`
  mark, with the `requests` raised and how long the first had `waited`, and `promoted` on each
  raised request's span. `game_keys.key_map(..., priority=Priority.URGENT)` says how soon a scan
  for the list is wanted, and `find_binding_list(reader, priority=...)` takes a callable asked
  before each of its three scans, background unless given.
- **`heap_scan.scan` returns `Hits`**, a `list` subclass, so every caller that unpacks or
  compares its result works as it did: `hits.skipped` is how many regions the pass skipped as
  the game changed them under the read, and `hits.incomplete` whether it skipped any.
  **`heap_scan.lookup(reader, *patterns, priority=..., usable=..., caller=...)`** is a scan and
  what an async `usable` makes of its hits; when that is nothing (falsy) and the pass was
  incomplete, it waits `HEAP_SCAN_RETRY_DELAY` (1 s, new in `timing.py`), holding no place in
  the queue, and scans once more, whose answer stands. *priority* may be a callable, asked
  before each scan. `forget` ends a lookup waiting to retry with `ScanDropped`, and
  `HeapScanner.retrying(reader)` counts those waiting. Traced as a `heap_scan.retry` mark with
  the `caller`, slot, `hwnd`, `skipped` and `delay`. `SimMemory.unsettled` is how many passes
  to come skip its heap and find nothing, and `testing.sim_client.SkippedWalk` is what a
  double's `heap_regions()` returns for a pass that skipped regions.

- **The run trace places a startup**, for BartleBuddy's slow first resize and late Team tab.
  `run_trace.mark(name, *, client=None, slot=None, **attrs)` writes a moment with no length, and
  is the one call safe from any thread, so a product's GUI can mark its side of a crossing. Each
  job wizwalker runs on asyncio's default executor, every pattern scan among them, is an
  `executor` record with its wait for a thread and its run apart, and each of wizwalker's waits
  for a hook's address is a `hook.wait` span named by that address (`player_struct`). The client
  manager traces each client's hooking (`client.hook`), its key-map scan (`client.key_map`), the
  loop's identity and wizard-name passes, and marks each client as it is published
  (`client.published`); the resizer traces its worker (`resize.worker`), the scan for the nodes and each part of it (`resize.find`), the lease (`resize.leased`)
  and the apply (`resize.apply`). Nothing changes while no trace runs.
- **Following is back**, as it was before 0.4.0 shelved it: `bartleby.runtime.movement`
  (`Follower`, `spacing_for`, `Trail` and its geometry) and the timing constants only it
  reads, `FOLLOW_TICK`, `FOLLOW_STALL_WINDOW`, `FOLLOW_BACK_OFF`, `LEADER_SETTLE`,
  `FOLLOW_PROMPT_WAIT`, `FOLLOW_LINK_WAIT`, `FOLLOW_GATE_WAIT` and `FOLLOW_PORT_RETRY`.
  Gather alone did not keep a team together: a solo zone refuses the port, a short port costs a
  loading screen, and a ported wizard was not seen to join the leader's fight. Walking routes
  and `docs/WALKING.md` stay on `walking-shelf`.
- `helpers.port_to(source, mover, *, what) -> bool`: one mover ported by Gather's routes, for a
  follower's fallback. Teleport to Leader where the group record proves the mover in a group
  the source leads, and the friends list otherwise; unlike `gather`, it never learns a group it
  has no record of, so it clicks nothing on the source. The caller holds the mover's lease.
- A `Follower` logs each walk into the leader's fight, as it starts and as it ends: joined
  after how long, or not, and why (`follow.JOINING_FIGHT` is the step's reason).
- `Follower(writes_speed=False)` never writes its speed field, 2.0x catch-up included, and
  gives nothing back; `Follower.holds_speed` says whether the field is the follower's just now,
  which is `walking` only for one that writes it.
- **Followers at a dungeon sigil**, with `Follower(sigil_prompts=PromptReads())`, shared by a
  leader's followers (`bartleby.runtime.movement.sigil`). With the leader standing at a sigil
  with its prompt, or from its press of X through its walk to its seat and its countdown, a
  follower with its own prompt stands, and one without walks at the leader until it has one. A
  press is the arbiter's Interact lease (detail `Helper.INTERACT`) on the follower or the
  leader, or for the leader its prompt going as it stood at the sigil. From the follower's own
  press it sends nothing at all until it loads, its prompt comes back, or `SIGIL_COUNTDOWN_MAX`
  passes; a prompt not seen gone within `SIGIL_PRESS_CONFIRM` of a press means the press did not
  take. A leader that cancels (its prompt back, off the seat it stood still on for
  `SIGIL_SEAT_SETTLE` after its press, its press not taken, or never seated) has it cancel too,
  by a `SIGIL_CANCEL_TAP` of Move Left. From `SIGIL_COUNTDOWN` after a press, a prompt back or a
  leader off its seat may be the countdown ending, so cancels only after `SIGIL_END_GRACE` with
  no zone change. New: `StepKind.CANCEL`, `Follower.at_sigil` and `.entering`, the `SIGIL_*`
  timing constants. Without `sigil_prompts` nothing changes and no prompt is read.
- **A launch can open at the size its tiles will want, and its games still go back to the
  user's.** For BartleBuddy, whose traced four-client launches spent their last 10 s resizing
  each game from the user's resolution to its tile, one 2.5 s scan at a time. All additive, so
  BartleBot is unchanged:
  - `GameView.planned_targets(layout, played, launches, *, cap=None)` is the target each slot
    would get, by slot, with the tiles in *played*'s slots placed by rung in *layout*: the
    slots held now and the ones a launch will take, each new one at its size in *launches*. It
    reads the view's own geometry and `tile_targets`, and changes nothing on screen; empty while
    the view has no size or no tile has a ladder. `GameView.synced_tiles` is what the last
    `sync_tiles` was handed, for the slots, windows and accounts held.
  - `GameView.set_home_sizes({username: size or None})` is the size an account's next window
    counts as launched at, so a lone client goes back to the user's size, not the small one it
    opened at.
  - `ClientResizer.give_back_to(client, size)` has every give back return the client to *size*,
    with `ui_size_for` the user's own UI scale, whether it was ever resized or not; a client
    never resized had nothing to give back. It finds the client's nodes at once, in the
    background, so a quit doesn't wait on a scan, and a resize or give back that comes first
    promotes it. Traced as `background` on its `resize.find` span. `find_config_nodes(reader,
    priority=...)` takes the priority, urgent by default, or a callable asked before each pass.
  - `launch_profile.launch_profile(..., resolution=None)` launches at the size it is handed, over
    the profile's own and the global setting, with the UI scale for it, as `launch_accounts`
    already did; `launch_profile.profile_resolution(profile)` is a profile's own, or None.
- **`GameView.set_layout(..., played_slots=())`** names the slots played whatever their tile
  holds, beside *played*'s players: for a wizard still launching, a pending tile or a window not
  in game, so the Main tile waits in its slot for it. For BartleBuddy, whose traced four-client
  launch gave the large tile to each wizard in game in turn, three resizes where one was planned.
  Keyword-only and empty by default, so BartleBot is unchanged.

### Fixed

- **A reorder or a swap during a launch moves the wizards in game, and no slot still coming.**
  `ClientManager.reorder_clients` and `swap_clients` moved `_client_order` by position, as if
  position i in the hooked list were slot i, so with two of four reserved slots in game a drag
  of those two traded the other two's pending tiles and left the dragged ones where they were.
  They now hand round the slots the hooked clients hold, which keeps a slot held for a recovery
  where it is too. A client with no slot, one this app did not launch, moves the ordering by
  position as before.
- **wizwalker is pinned at `d68082c`** (`bartlebot-pin-20261121`), where it was `0857de9`. A
  product that pins wizwalker itself pins the same commit. A module pattern scan is now done
  once per build and shared by every client of it, each hit checked in the client at hand, so a
  four-client startup made 16 module scans where it made 68.
- **wizwalker was pinned at `0857de9`** (`bartlebot-pin-20261120`), where it was `991c257`. A
  click cut short while the
  mouseless hook was being written could leave its jump at `GetCursorPos`, and every click by
  hand on that client missed until it was relaunched; wizwalker now writes nothing until its
  lookups are done and takes user32's originals from a clean copy, so a leftover from before is
  put right on the next click.

### Changed

- **Followers walk onto the leader's spot**, where each stopped at its own gap behind a moving
  leader (180 to 420 units) and, once the leader had stood still for a second, took its own
  place around it: behind-left, behind-right and behind, from the way the leader faced, 170
  units or more out and walked to in a straight line. In game that left a follower out of range
  of an NPC the leader was just in range of, sent it into a wall behind the leader, and out
  through the door of a room just entered, since a wizard spawns facing in with its back to the
  door; and the player waited on the second and the gap at every stop. Now a follower walks the
  trail right up to the leader and stops on its spot, within `ON_LEADER` (40 units), as soon as
  the leader stops; wizards never collide, so they stack. It walks again once the leader is
  `RESUME` (120 units) away, so it doesn't stop and start at the leader's heels. A stall within
  `NEAR_STALL` (300 units) of the leader stands a follower where it is until the leader moves
  (`AS_NEAR_AS_IT_GETS`), where three stalls walking to its place ported it.
- **A resize no longer waits out a loading screen** (under Breaking). The worker waited on
  `client.is_loading()` from its first commit, with no reason recorded. It was the one step that
  needed the root window hook, and it can't see the load-in from character select, where a tile
  swap applied in game with the HUD at the right scale. So a product may resize a game during
  its load-in, before the hook, on the client its drive borrowed. A resize during a zone's
  loading screen is not yet proven in game; a byte the game holds back there is taken back after
  `RESIZE_APPLY_TIMEOUT`, as before. The `resize.worker` span no longer notes `loading_s`.
  `docs/WINDOW_SIZING.md`, "Resizing a running client", has the rest.
- **The config nodes and the key bindings are found by a static route first, in a millisecond or
  two, and the heap only when it misses.** Each hangs off an object whose constructor stores it
  in a static slot of the image, which a code pattern finds once per process (84 to 93 ms and 63
  to 68 ms on four live clients of r814353): `find_config_nodes` follows the config object's map
  of sections to the user's `VideoSettings` group and checks its two children as the heap's are,
  their parent that group, in 1.6 to 2.0 ms against 2.4 to 4.0 s; `find_binding_list` takes the
  holder's `+0xB0` with its class and two bindings or more, in 0.1 to 0.2 ms against 6.1 to 6.5 s
  for three passes. On every client the route gave what the heap gave. So the resizer's
  background lookup at hook and the key map's warm-up no longer wait in the heap scanner's queue.
  Any miss, from no single pattern match to a check that fails, falls back to the heap lookup as
  it was, retry included, and is logged once per client at INFO, naming its reason: a game patch
  that moves a slot or an offset costs seconds, never the feature. Both functions keep their
  signatures. The patterns are the old compiler's only; a build of the new one misses both routes
  until a second form is added (`docs/GAME_UPDATES.md`). Not taken: GameClient, which holds both
  objects too (`+0x21390`, `+0x21358`), as a second try before the heap, since its fields move
  with patches (r814353 moved its camera fields by 8), the finders take a reader and not a client,
  and a stale offset there would only add a miss before the same heap pass.
- **`ClientManager` publishes a client its title placed without waiting for its user id.** That
  id only teaches the account its fingerprint, for a later session, so it is read in a task of
  its own, one per window at a time, which `stop()` cancels, and traced as
  `client.identity_learn`. Each pass read every client's one after another before publishing
  any, and a first read is a pattern scan: in a four-client launch the last two wizards reached
  the list 15 s after their hooks. A window the title could not place is still identified
  before its slot is assigned, its read now beside the others' rather than after them. An
  account removed from the settings while its window is open is no longer written every pass.
  The split this took is under Breaking.
- **The resizer's and the key map's scans share passes, the resizer's first.** Each scanned
  through wizwalker's `pattern_scan` on its own, so a four-client startup made twenty passes of
  1.1 GB nearly all at once, sharing one GIL: the first resize landed 17 s after it was asked
  for, the key maps took 20 to 24 s, and the event loop stalled for up to 1.6 s. Now
  `find_config_nodes` asks for both names in one urgent request, each of the key map's three
  steps is a background request, and a client's first pass takes everything it has waiting.
  The search is `bytes.find`, one literal at a time, with the same hits as wizwalker's regex:
  its search for an alternation, which the key map's pointer step is, gives up and retakes the
  GIL constantly, and beside one busy thread ran 240 times slower. One pass runs at a time: in
  a traced four-client launch two at once took 4.2 to 6.4 s each against 2.2 to 2.5 s alone, so
  each requester waited twice as long. A region the game frees or changes between asking about
  it and reading it (`ERROR_PARTIAL_COPY`, `ERROR_NOACCESS`, `ERROR_INVALID_ADDRESS`) is read
  once more, then skipped, where wizwalker's walk failed the whole scan: in game one such region,
  as a client settled after its load, cost it its key map for the session. An object in a
  skipped region is not found. An exited process or a bad handle still fails the scan. A client
  unhooked or closed has its waiting scans dropped, which the key map takes as unreadable, as
  before, until its next attach.
- **A key map someone needs now is no longer background work.** The warm-up at hook still asks
  in the background, so a resize goes first, and in a traced four-client launch the key maps
  finished about 25 s after the hooks. A caller that needed one before then, a follower starting
  to walk, a helper's press or a run's pre-check, waited on that queue for as long. Every
  `key_map` caller but the warm-up now asks at urgent, the default: one arriving while the
  lookup is under way promotes it, its waiting scan raised with its grace ended and its later
  scans asked at urgent, and it is still one lookup, scanned once. Its failure is as before: the
  defaults with one warning, until a refresh on attach.
- **A lookup whose pass skipped a region tries once more before it fails.** An object in a
  region the game changed under the read is not found, and both lookups took that for an
  object not there: the resizer turned itself off for the client, and the key map pressed the
  defaults, each until the next hook, for a condition that mostly lasts only while a client
  settles after its hook. Now `find_config_nodes`, and each of `find_binding_list`'s three
  stages on its own, scans once more after `HEAP_SCAN_RETRY_DELAY` when an incomplete pass gave
  it nothing usable, at the priority asked for then, so a background warm-up's retry stays
  background unless promoted meanwhile. A whole pass that finds nothing fails at once, and a
  retry that is incomplete too fails as before. Both functions keep their signatures, so
  BartleBot's `tools/probe_key_bindings.py` and BartleBuddy's `tools/probe_resize_scan.py`
  call them unchanged, and get the retry. A product double of `heap_scan.scan` would need to
  return `Hits`; neither product has one.
- **`pymem` is a declared dependency of the `runtime` extra** (`>=1.13`), since
  `clients/heap_scan.py` imports it. It came only through wizwalker before.

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

# Walking

Moving a wizard by walking: how a route is planned from a zone's own files, how a live walk
follows it, and every measurement taken so far.

Every BartleBot node that moves a wizard writes its position to memory (34 writes across 18
files: `client.teleport`, and one wizsprinter `tp_to`). From late October to mid-November 2026
the server reset any long jump it could not have seen walked; since Ahmarra's live build
(r813697) long jumps hold again (`WIZARD101.md`, *Moving, and what the server allows*, which
has the server's rules of movement). BartleBot's Walking Routes epic (BartleBot #322) goes
ahead anyway, by choice, to take every long teleport out of the app: everything walks, takes
the game's own links, or crosses one gate an author placed by a hop no longer than its limit.

Route planning is core's, in `bartleby.runtime.movement` (Stage 1 of that epic), and so are
the maps it plans on, one for every zone BartleBot walks, shipped with core. Walking a route is
still a probe in BartleBot's `tools/` until Stage 2 brings it here, and nothing in `bartlebot/`
walks yet. Each probe's docstring has its own OUTCOME section with the run-by-run
numbers.

| where | what it does | needs a client |
|---|---|---|
| core's `movement/collision.py`, `zone_grid.py`, `patrols.py`, `zone_links.py`, `route.py` | plans a route in a zone from what its files hold | no |
| core's `movement/zone_maps.py` | a zone's planner from its shipped map, saved on the user's machine once built | no |
| core's `tools/zone_files.py`, `tools/build_zone_maps.py` | reads a zone's wad into `ZoneFiles`; builds, verifies and compares the shipped maps | no |
| BartleBot's `tools/probe_walk_route.py` | plans on a zone's wad with core's reader, draws the routes | no |
| BartleBot's `tools/probe_walk_input.py` | walks a planned route on a live client, around mobs, through links | yes |
| BartleBot's `tools/probe_walk_zones.py` | checks every zone's arrival points can reach each other | no |
| BartleBot's `tools/probe_mob_paths.py` | watches a zone's mobs and says whether they walk their paths | yes |

The probes run as `uv run python tools/<probe>.py`. With core taken by path, editable, add
`--no-sync`, or uv syncs first and silently swaps the editable core for the pinned one. Their
output goes to `logs/walk_route/`.

**Nothing boosts a wizard's speed while it walks**, until the user's go-ahead (2026-11-24): how
a boost affects the server's snapbacks is not understood yet. BartleBuddy's followers write
none (`FOLLOWERS_WRITE_SPEED`), both apps' Speed toggles are off (`SPEED_TOGGLE_ENABLED`), and
no probe writes it. The 2.0x rule in `WIZARD101.md` stands for when the go-ahead comes.

**A short hop holds**, such as a step past a gate that won't open, and has in play since late
October. Nothing in core writes a position. The epic's Stage 7 gives BartleBot its one hop, an
author's Teleport to XYZ, with a limit measured from where the wizard stands.

## Planning a route from a zone's files

`route.build_planner(files)` builds a zone's `ZonePlanner` from its `ZoneFiles`: the parsed
solids of `collision.bcd`, the patrols, the named arrival points of `gamedata.bin`, the volumes
of `volumes.xml`, the volumes `triggers.xml` teleports from, the placed objects that can be
interacted with, and `zone.nav`. **No planner module opens a file or imports katsuba**: each
parser takes bytes, and a tool reads them out of the zone's wad (`GameData/<zone with / as
->.wad`): core's `tools/zone_files.py`, with katsuba, whose `ZoneReader.zone_files(zone)` both
the map build and BartleBot's probes use; a test holds it. No client is needed; a zone builds in
0.1 to 2 s, Unicorn Way in about 4 s with everything. A run never reads a wad: it plans on the
zone's shipped map (below).

`planner.route(start, goal, avoid=True)` gives a `Route`, or None, and `planner.legs(route)` the
stretches to walk.

### The floor: `collision.bcd`

`collision.py` and `zone_grid.py`. The parser follows Deimos' `src/collision.py`
(PeechezNCreem, ISC); the rule for when a solid blocks a foot follows Deimos'
`src/collision_math.py` (GPL-3, as core is).

- **Floor** is every mesh with the WALKABLE flag (bit 1), rotated and placed by its matrix
  (applied transposed, as Deimos does). Faces steeper than a normal z of 0.5 are cliff, not floor.
- **Walls** are the boxes, cylinders and spheres, unless flagged client-object, trigger,
  hitscan or fog. A solid blocks a cell only where its height range meets the floor there
  (Deimos' `_collider_blocks_foot`), which is what lets a ramp pass under a tall cylinder. A
  solid based at or below the floor blocks once it rises 20 above it. Deimos lets a round one
  rise 75, as a big cylinder is often the coarse bounds of a whole structure whose floor lies
  inside it (Celestia's chamber, radius about 834); **only a round one of radius 400 or more
  is given that.** (The probe's notes said "400 across", but its code compared the radius, and
  every route since was planned that way, so core does too.) Mirage's hub has a rock, a cylinder of radius 143 rising 56 above the street,
  that a route went straight through: walked into, the wizard slid around it holding 182 to 183
  units from its center, which fits radius 142.6 (and puts the wizard's own radius nearer 40
  than 49). Counting small rounds as firm closed 0.7% of Mirage's hub and 0.1% of the other
  zones checked, and changed no route walked before.
- **A cylinder stores (length, radius), the reverse of how Deimos reads it.** Found by walking
  into a tree in Unicorn Way stored as (264.9, 85.7): the wizard slid around it holding exactly
  135 units from its center, which is radius 85.7 plus the wizard's own 49. Read Deimos' way
  it is a 265-wide disc the wizard stood inside of, too short to count as a wall, and every
  tree was missing from the grid. Deimos' collision teleport presumably has the same bug.
- **Non-walkable meshes are ignored.** 103 surveyed zones have 250 of them. Triton's three are
  water (flag bit 6). Whether any are real walls is unchecked.
- **Placed objects' collision is not in `collision.bcd`.** An object's template can carry a
  `CollisionBehavior` with its own `m_solidCollisionFilename` (Triton's teleporter stones use
  `MU_TeleportStoneCollision.xml`), and the grid does not read those yet. Every walk up to a
  stone stalled on it once; the walk recovers (below), but the planner should know.

The grid is 40-unit cells, with the player's clearance taken as 50 (49 measured). **Each cell
keeps every floor it has** (floors more than 150 apart are separate; a 3-storey house has
cells with three), and a step goes only to the neighbor's floor at about the same height
(rise up to 1 unit per unit walked, plus 20 for a tread or kerb). That is what lets stairs,
and nothing else, join storeys. Floor is passable only if every cell within the clearance has
floor at about its height, which keeps a route off both walls and drop edges.

### Mobs: `spawnData.xml`, `pathData.xml`, `pathNodeData.bin`

`patrols.py`. Every mob spawner names a path; the path is a list of node ids, and the nodes
carry positions. A spawned template is a mob if it has `DuelistBehavior`, and its `m_npcProximity`
is the trigger radius (200 for most Wizard City mobs, 275 for dark sprites). Both start node
types walk their paths:

| `m_kStartNodeType` | starts at | checked | spawns surveyed |
|---|---|---|---|
| 0 | the path's first node, then loops it | Unicorn Way: 561 samples a median 23 units off the line, max 370 | 2,759 in 66 zones |
| 1 | one of the path's nodes, then walks it | Triton Avenue: 6 of 6 ghosts walked their path, p90 within 35 units | 3,978 in 219 zones |

Paths loop: samples sit on the segment from the last node back to the first.

**The client lists only the mobs near the wizard** (12 of Triton's 96 spawns). Live positions
cover what is close; the paths cover the rest, which is why the planner avoids every path.

The danger cost is per cell: its **clearance**, how far outside the nearest path's mob's own
trigger radius it is. Beyond 1300 the cost is nil; it rises with the square toward 250; under
250 a step costs 50 times its length. Every route the cost picked in Unicorn Way kept at least
250 units outside every trigger radius by hugging the street edges, for 4 to 20% more
walking, and those routes were checked in game.

Measured trigger distances (`tools/probe_aggro_radius.py`, September 2026): 270 to 390 units
from a still or slow mob, 650 to 886 from one closing at 150 to 400 units a second (client lag).

### In-zone links

Some zones are pieces of floor no walk joins. **A link is anything the game moves a wizard
through**, and the server accepts its move because the move is its own. There are two ways to
take one: **step into a volume**, or **press the interact key at a placed object** (a
fast-travel stone, a zipline, a ladder, a rope). Every one taken so far, of either kind, set
the wizard down within 5 units of one of the zone's named arrival points (`gamedata.bin`'s
`m_locationList`), so a link is planned as *where it is taken* plus *which arrival point it
lands on*. **No client file says where any link lands**: a volume's trigger has a
`ResTeleport` result with no properties left (below, *Exits*), and every world object's template has `DeletedBehavior` where the behavior
that moves the wizard would be (`TeleportProximityBehaviorTemplate`, whose `m_locationName`
is exactly that landing, survives only in housing and guild templates). So the landing is put
together from what the files do keep, in this order (`zone_links.py`'s `zone_links`):

| source | gives | where it holds |
|---|---|---|
| a volume's name | the arrival point sharing the most words, "to"/"from" breaking a tie, no link where two fit as well | Triton's six volumes (four walked, 1 to 5 off) |
| `zone.nav` | for a volume the name leaves on its own piece, if its name says it moves the wizard (teleport, to), the far end of the quest arrow's edge starting at it | Lemuria's tower lifts, each named for the end it stands at; Krokotopia's Forgotten Tombs |
| a ride's template | a zipline's `InteractableBehavior` holds its two ends as LOCATION records; it lands on the arrival point nearest the second (800 to 920 past it) | all four Floating Mountains ziplines; two pressed, 3 off each |
| a stone's partner | a `TeleporterX` stone lands at the arrival point nearest `TeleporterX_ToMain`, and that one at the arrival point nearest the hub's stones | Triton's four (pressed, 1 to 5 off); Colossus, Cyclops, Firecat, nine Krokotopia zones and Hametsu give names like "StreetB", "Target_Arena", "BackToHub", "HamPorter2" |
| an X / X_B pair | each at the arrival point nearest the other | Krokotopia's Forgotten Tombs teleporters |
| `zone.nav` | for any other object whose prompt is a travel verb, the far end of the edge starting nearest it (within 700) | Mirage's caravan ladders (four climbed, 0 to 4 off; Ladder 04 has no edge), Tyrian Gorge's six ("Ladder 01 top", ...), Novus's ropes, Krokotopia's hub teleporter |

What the sources are:

- **`zone.nav`**, in every zone's wad, is the graph the quest arrow routes through
  (`zone_links.parse_zone_nav`; BartleBot's `tools/zone_graph/zone_nav.py` prints one): `zonemap.nav`'s layout without the name table, the 12 bytes
  that are zero there holding each node's x, y, z. Most nodes sit on a 480-unit grid; **the
  links are its edges no walk takes**, longer than 1,500 across the floor or rising more than
  500 more steeply than 1:1 (a ladder). Edges are stored both ways and say nothing of what they
  are, and an end can sit a few hundred units off, so an edge is only trusted next to a volume
  or a travel object. One landing on another piece of floor wins, as an edge whose ends share a
  floor is a walk (Arcanum's lift foot starts one to the other lift's foot); of the rest, where
  two start about as near one, the shorter wins (the top of Mirage's
  Ladder 06 starts its climb down and a 7,004-unit edge across the zone). It lacks some links:
  Triton's stones are not in it, nor Mirage's Ladder 04.
- **A travel object** is a placed object whose template's prompt, from `GUI.lang` or its own
  words in `WizardGameObjects.lang`, says Teleport, Ride, Climb, Descend, Ascend, Travel or
  Exit (613 templates), or, with no prompt, whose name says teleport, rope, ladder, zipline,
  boat, elevator, lift, cart, dinghy or portal.
- **A volume link must join two pieces of floor**: that alone throws out zone exits, whose
  names match an arrival point on their own piece ("Street4 to Cavern"). **An object link is
  kept if it joins two pieces or moves the wizard 1,000 units or more**, as a stone is worth
  pressing even where the far end can be walked to; the route's cost decides. An object within
  300 of a volume is the pad drawn there (Triton's `Telepapad`s), not a link. Cinematic points
  are left out.

None of this is the server's word, so a link that moves nobody is dropped by the walk and the
route replanned. That has already paid off: Floating Mountains has `AZ-ZipLine-3` and a
`-3-Quest` copy at the same spot; the first moved nobody, and the walk took the second.

**A zipline is a teleport, not a ride**, and so is a ladder. Pressed, a zipline plays its ride,
and 2.2s later the server moves the wizard in one tick (5,148 units) onto the arrival point; a
ladder moves it in one tick 0.06 to 0.12s after the press. No reset on any.

**A press counts only once the game shows the prompt.** Mirage's Ladder 02, pressed one tick
after the wizard stopped beside it, did nothing; pressed once the prompt was up, it climbed.
The prompt took 0.2 to 0.4s to appear.

`probe_walk_input.py --interact` still learns an unknown one by pressing it: it walks to a
placed object, presses, and reports how soon, how far, one jump or many steps, and the arrival
point it settled nearest.

`probe_walk_zones.py` across every zone on the zone map or named by a bot (455). Of the 387
with two or more arrival points:

| links used | all on one piece | joined by links | still apart (points) |
|---|---|---|---|
| volumes by name | 295 | 2 | 63 (299) |
| all of the above | 295 | 21 | 44 (135) |

295 links in 56 zones: 189 volumes, 101 objects pressed, 5 rides. 38 zones have a point off
the grid, nearly all cinematic or marker spots. The worst left: Empyrea's Husk (14 points),
Big Ben's counterweights and tower (levers and lifts), MooShu's Waterfall Cave, the Hive,
Forlorn Taig, Dragonspire's Arena. Each has a map in `logs/walk_route/zones/`.

### Exits

**A route keeps out of every exit of its zone but the one it is sent to**, so a walk never leaves
the zone by an exit it was not sent to, as a teleport into a cave once did (BartleBot #35). An
exit is a volume that teleports a wizard and is not one of the zone's links
(`zone_links.zone_exits`). Both halves are read from the files, not guessed from names:

- **Which volumes teleport** comes from `triggers.xml` (`zone_links.teleport_volumes`). katsuba
  cannot read it, but its records can be: each trigger starts with its name, the events it fires
  on are strings `Enter_<volume>` and `Exit_<volume>`, and a trigger that teleports holds a
  `ResTeleport` result, found by its class hash from the type dump. `ResTeleport` keeps no
  properties in the client's copy, so where it goes is not known. A volume a teleporting trigger
  fires on when entered is a teleport volume. Names would not do: 52 of the 334 exits below have
  names no rule catches (`HauntedCaveZoneEntry`, `Volume-ToLibrary`, `Activator Volume (2)`,
  whose trigger is "Teleport To Lady Blackhope's Tower").
- **How big each is** comes from `volumes.xml` (`zone_links.parse_volumes`). After a volume's
  position come its shape and size records: `m_radius`, and for a box `m_length`, `m_width` and a
  third side, the hashes being the type dump's for properties of those names. A volume with no
  shape record is a sphere (1,563 of 1,643 surveyed in Wizard City and Krokotopia; 80 are boxes).
  A box's turn is not in the file, so it reaches as far as the circle holding it however it is
  turned, its sides read as full lengths, as `collision.bcd`'s are.

Checked against `zone_nav.json`'s 334 verified gate points (2026-11-24), each a spot a bot has
stepped onto to change zone: **every one lies inside a teleport volume**, and none outside its
decoded radius, while 83 lie beyond half of it, so the value is a radius and not a diameter. Of
the 2,296 volumes in those zones, 815 teleport; the rest are points of interest, music and quest
triggers, which a route crosses freely.

The floor within an exit's reach plus `EXIT_MARGIN` (50, the wizard's own clearance), at about
its height, is closed to a route, unless the route starts in that exit (a wizard just arrived
through it walks out) or ends in it (it is sent there). `ZonePlanner.floor_for` builds that
floor, and a `Route` carries it, so straightening never cuts through an exit the route went
around. A volume that is one of the zone's links is not closed: a route through it is planned
to step in. Unicorn Way has nine exits; of the six routes in the probe's OUTCOME, one moved, by
2.8 cells, to go around one.

### Routes

`route.py`. A* over the cells (8 neighbors, no cutting a wall's corner), with each link an extra edge from
the floor near where it is taken (100 units of a volume's center, 150 of an object's) to the
landing, costing 4 seconds of walking for a volume, 6 for a press, 20 for a ride. The
heuristic does not know about links, so a route through one is found but not always the
shortest. The route is then straightened: from each kept point, the furthest point still
reachable in a straight line without coming nearer a patrol than the route itself did. That
cuts 100 to 300 grid turns to 20 to 40. A route with links comes back as **legs**, each
straightened alone and each but the last ending at the link's volume, or on the floor beside
the object to press.

**Checked against the probe it came from** (2026-11-24, on r814353): BartleBot's shelved
`probe_walk_route.py` and core's planner, with exits left out, built the same grid, floor and
costs and planned every route identically, cell for cell, with the same turns: Unicorn Way's
three in its OUTCOME, shortest and avoiding, and four each in Triton Avenue (10 links) and
Floating Mountains (5 rides), whose links matched by name and landing. The Unicorn Way times
are the OUTCOME's still: 75.5, 53.3 and 34.4 s straightened, against about 75, 55 and 36.

### The shipped zone maps

`movement/zone_maps.py`, and the files under `bartleby/runtime/game/data/zone_maps/`, which
`tools/build_zone_maps.py` writes. A run plans on these, so it needs neither the zone's wad nor
the type dump, and `has(zone)` says from the index alone, without opening a map, whether a
zone can be planned at all (BartleBot's Stage 4 checks a bot's zones with it).

**Which zones:** every zone a product names, given to the build as arguments, since core reads
no product's files: BartleBot's `zone_nav.json` (each world's zones) and every `zone` or `*_zone`
param in its `bots/` and `subgraphs/`, as its `restricted_zones` reads them. On r814353 that is
464 zones, every one with a wad and a map. BartleBot's
`test_every_zone_on_the_map_has_a_walking_map_core_ships` fails on a `zone_nav.json` zone
without one. A later product's zones (BartleBuddy's quests) join by a further `--names` or
`--zone-nav`.

**What a map holds:**

| file | holds |
|---|---|
| `floors/<key>.npz` | the floor: `collision.bcd`'s walkable triangles, and each solid's footprint (its hull or circle, the heights it spans, whether it is firm), as `collision.py` finds them. Keyed by the start of `collision.bcd`'s SHA-256, so zones with the same file share it |
| `zones/<zone with / as ->.json` | what differs between zones on one floor: the patrols, the named arrival points, the links and the exits, already found, in the order found |
| `index.json` | each zone's two files, and the SHA-256 of the wad entries it was built from (`zone_files.MAP_ENTRIES`, not the models and textures) |

Only derived geometry, names and positions: no game file and nothing pickled (`np.load(...,
allow_pickle=False)`); the arrays are plain floats and ints. 464 zones on 442 floors, so only
22 zones share one; the same room placed at another spot does not share, and sharing those too
would save under a tenth. 18.5 MB in all, the largest floor 0.2 MB.

**Deterministic:** the same zone writes the same bytes. Every zip entry is dated 1980 and written
in name order (`np.savez_compressed` stamps the time, so the zip is built by hand), and every
float is written as Python writes it, which reads back exactly. A rebuild after a game update
changes only the zones it changed (`GAME_UPDATES.md`, step 2). The maps built under katsuba 0.4
(BartleBot's) and 0.6 (core's) are byte for byte the same.

**Why geometry and not the grid:** shipped as grids, the maps came to 147 MB, and the patrols'
clearance, a float per cell and 4 MB a street, would have added about 700 MB more, all in git and
growing with every update. So a map ships what the grid is built from, and **a load builds the
rest once and saves it on the user's machine**: the grid, its passable floor and the clearance,
built by the same `route.grid_of` and `assemble` as from a wad, under
`%LocalAppData%/Bartleby/cache/zone_maps/` (`zone_maps.cache_dir`), shared by every product.

| zone | first load, built | after, from the saved copy | saved copy |
|---|---|---|---|
| Unicorn Way | 2.1 s | 0.05 s | 4.9 MB |
| Triton Avenue | 4.4 s | 0.06 s | 4.5 MB |
| Wallaru's Outback, the largest | 16.5 s | 0.25 s | 39 MB |
| all 464 zones (`warm()`, one thread) | 456 s | | 854 MB |

`ZoneMaps.warm(zones)` builds the copies ahead of need, one zone at a time on the caller's
thread, until a stop event is set, and finds every copy already saved in 0.4 s; when a product
calls it is Stage 4's to decide. A saved copy
is used only when its key matches: the SHA-256 of the zone's two map files and
`zone_maps.CACHE_VERSION`. A changed map, a raised version and a damaged file (logged) are
rebuilt; a copy that cannot be saved is logged, and the planner is still given.
`CACHE_VERSION` is raised by hand whenever the code that builds a copy changes, and
`test_cache_version_follows_the_planner` fails until it is.

`await zone_maps.load(zone)` builds or reads off the event loop. A session keeps the 8 planners
used last (`KEPT`; a big street holds some 20 MB), and each caller gets its own copy of what a
walk changes: the floor it blocks and the links it drops.

**Checked against the wad** (2026-11-24, r814353): `build_zone_maps.py --compare` built eight
zones' planners from their wads and from their maps, both built and read back from the saved
copy: Unicorn Way, Triton Avenue, the Outback, Floating Mountains, Mirage's hub, Lemuria's hub,
Graveholm and Karamelle's hub. The grids, floors, clearance, costs, points, links and exits were
the same, and so were all 470 routes between their arrival points, shortest and avoiding, cell
for cell, legs included. BartleBot's `probe_walk_route.py`, on core's reader, plans Unicorn Way's
three routes as its OUTCOME has them: 75.5, 53.3 and 34.4 s straightened.

## Walking a route

BartleBot's `probe_walk_input.py --to <arrival point, placed object or x,y[,z]>` walks from
wherever the wizard stands, on core's plan. The epic's Stage 2 brings it into core as the walker.

- **Keys are the account's own**, read from the client's memory (`KEY_BINDINGS.md`):
  MoveForward, MoveBackward, MoveLeft and MoveRight (A and D *turn* a wizard; these are not
  strafes) and NPCInteract, each a `GameAction`. The probe predates that reader and still falls
  back to `Bin/config.xml`'s `MoveForwardStart` and the rest, then W S A D and X.
- **Steering writes the yaw.** wizwalker's yaw is 0 facing -y, growing counterclockwise
  (`atan2(-dx, -dy)`). Hundreds of writes, none undone. `--turn-keys` steers with the keys
  instead (A turns the yaw down at about 152 degrees a second), and tracks the route less closely.
- **Speed.** Walking is horizontal only: a stair's height is free. On foot it measured 600 on
  flat ground (wizwalker's constant says 580); a mount multiplies it, most by 1.4 and the
  fastest by 1.55. The client's `speed_multiplier` (a percentage) is read to plan; once
  walking, the measured speed takes over. On the +40% mount: 840 expected, 838 measured. No
  probe writes it, and nothing will until the user's go-ahead (above).
- **Following** a leg: hold forward, re-aim at the next waypoint each 50ms tick, pass a
  waypoint within 80 units or once past it along its leg, stop within 50 of the goal.
- **Stalls** (forward held 1.5s, under 60 units moved): back off 150 units with the backward
  key, close the floor 80 units ahead within 110, replan from there; up to 3 times. A wizard
  grazing a round trunk slides around it unaided; only one walking straight at an obstacle stalls.
- **Mobs.** A watcher reads every listed mob's position five times a second and estimates its
  velocity. Each tick the walk projects itself 2.5s along its route and every mob along its
  velocity, and wants each mob's trigger radius plus 120, plus 40 for every second looked
  ahead. **A mob walks at about 40 to 180 units a second and a wizard at 600 to 840**, so next
  to a wizard a mob is nearly a standing obstacle: short of room, the walk first plans a
  detour closing each mob out to that same room at three moments over the 2.5s, and only
  without one waits or backs off, for 30s at most. Detours plan with the patrol cost even on
  a shortest-path walk, so after one the rest of the leg follows the patrol-avoiding route.
- **Links.** At the end of a leg the walk stands in the volume, or faces the object and
  presses the account's interact key once the game shows its prompt (wizwalker's
  `is_in_npc_range`, waited on up to 2s), again every 2s, until moved more than 300 units
  (rising counts, for a ladder); after a press it waits for the wizard to be set down (under 20 units in 1s), then
  replans from wherever it landed. A jump near the pending link's landing is that link, taken
  early. Not moved within 6s, the link is dropped and the walk replans: a wrong pairing and a
  quest gate look the same.
- It stops on a battle, a zone change, the goal, or running out of time, and releases every
  key however it stops.

Results, all with no battle unless said:

| walk | result |
|---|---|
| Unicorn Way, gate to Tower 1, keys only | 46 units off, 35.2s against 35.5s planned, median 9 off the route |
| Unicorn Way, Hedge Maze to the gate, patrol-avoiding route | 14 off, 55.2s for 44,880 units, never nearer than 163 beyond a trigger radius and margin, 3 resets absorbed |
| Unicorn Way, gate, Tower 1, Tower 2, Hedge Maze in turn, shortest | all reached; one detour each; waits of 11.7s, 0, 0 |
| Unicorn Way, through the Ghosts 02 loop, waiting only | stopped after 30s held up: a loop of 4 ghosts never clears |
| the same with detours | crossed in 11.2s, 3 detours, 2.1s waiting, least room 11 |
| Housing_FarmHouse, main floor to basement and back | 18 and 16 units off; two flights, z 3100 to 2300 |
| Triton Avenue, two volume links in one route | both taken, landings 5 and 4 units off |
| Triton Avenue, four fast-travel stones | all landed 1 to 5 units off their arrival point |
| Floating Mountains, From3Points to Target-FromTurquoiseCave over ZipLine-3 | ZipLine-3 moved nobody and was dropped; -3-Quest landed 3 off "Target-ToMajorIsland"; 33 off the goal in 29.9s, two detours |
| Floating Mountains, back over ZipLine-4 | landed 3 off "Target-FromMajorIsland", 2.2s after the press; 13 off the goal in 23.7s, one stall recovered |
| Mirage's hub, up Ladder 02, pressing as it stopped | moved nobody: pressed before the prompt showed |
| Mirage's hub, Ladder 02 down, Ladder 05 up, waiting for the prompt | each landed 1 off its arrival point |
| Mirage's hub, Ladder 05's top to Ladder 01's | no ladder: the caravan roofs join, 5,695 units walked |
| Mirage's hub, down Ladder 01 and up Ladder 07 in one route | 4 and 3 off; 0 off the goal in 23.3s, one stall recovered (on a rock the grid let through) |
| Mirage's hub, the same stretch with small rounds firm | no stall; the route kept 333 from the rock's center |

## What is not done

In rough order of what a bot needs first. Most is a stage of BartleBot's Walking Routes epic
(#322); `docs/EPICS.md` there has the stage-to-issue table.

1. **Walking a route in core** (Stage 2): `probe_walk_input.py`'s walk, on a movement lease, its
   timing constants in `timing.py`. Several clients walking together belongs there too.
2. **Saving the zone maps' copies ahead of need** (Stage 4): when a product calls
   `ZoneMaps.warm`, so a walk's first load in a zone is never the 2 to 16 s build.
3. **Placed objects' collision** (Stage 3). Read each placed object's `CollisionBehavior` solid
   file into the grid, so a walk does not have to stall on a teleporter stone to find it.
4. **A volume link a route does not take is not closed to it.** A route planned past one could
   step in and be moved. The walk notices any jump and replans, but the planner could keep out
   of it as it does an exit; for Stage 2.
5. **Walk the link kinds not yet tried**: a rope (Novus's Stone of Heaven), a Lemuria tower
   lift, a Krokotopia stone pair, an X / X_B pair. How near an object a press must be is
   assumed (150; every Mirage ladder took a press from where its leg ended); whether facing it
   matters is not known (the walk faces it anyway).
6. **The 44 zones still apart**: their maps, starting with the worst. Levers and lifts that
   change state (Big Ben) may need the quest's state, not a link.
7. **A checked-in table of verified links** (Stage 8), as `zone_nav.json` keeps verified gates,
   so a pairing once walked is not re-derived, and one that moved nobody is not tried again.
8. **Zone exits as a route's end.** A walk's goal across zones is the zone map's route
   (BartleBot's `docs/ZONE_NAVIGATION.md`), with each crossing an exit to walk into; today every
   crossing is reached by `client.teleport` (Stage 4).
9. **Smaller:** whether any of the 250 non-walkable meshes are walls; a box volume's turn, and
   the name of its third side's property; whether a quest-gated link can be told from the
   client before stepping into it (a trigger's `ReqHasQuest` may say).

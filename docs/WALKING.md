# Walking

Moving a wizard by walking: how a route is planned from a zone's own files, how a live walk
follows it, and every measurement taken so far.

BartleBot's nodes used to move a wizard by writing its position to memory (34 writes across 18
files: `client.teleport`, and one wizsprinter `tp_to`). From late October to mid-November 2026
the server reset any long jump it could not have seen walked; since Ahmarra's live build
(r813697) long jumps hold again (`WIZARD101.md`, *Moving, and what the server allows*, which
has the server's rules of movement). BartleBot's Walking Routes epic (BartleBot #322) goes
ahead anyway, by choice, to take every long teleport out of the app: everything walks, takes
the game's own links, or crosses one gate an author placed by a hop no longer than its limit.

Route planning is core's, in `bartleby.runtime.movement` (Stage 1 of that epic), and so are
the maps it plans on, one for every zone BartleBot walks, shipped with core, and the walker that
follows a route on a live client (Stage 2). BartleBot's run walks on it since Stage 4 (*A bot
run*), and every node since Stage 7; its `probe_walk_input.py` walks on it too. Each probe's docstring has its own OUTCOME section with the run-by-run
numbers.

| where | what it does | needs a client |
|---|---|---|
| core's `movement/collision.py`, `zone_grid.py`, `patrols.py`, `zone_links.py`, `gates.py`, `route.py` | plans a route in a zone from what its files hold | no |
| core's `movement/zone_maps.py` | a zone's planner from its shipped map, saved on the user's machine once built | no |
| core's `movement/walk.py` | walks a planned route on a movement lease, around mobs, through links | yes |
| core's `tools/zone_files.py`, `tools/build_zone_maps.py` | reads a zone's wad into `ZoneFiles`; builds, verifies and compares the shipped maps | no |
| BartleBot's `tools/probe_walk_route.py` | plans on a zone's wad with core's reader, draws the routes | no |
| BartleBot's `tools/probe_walk_input.py` | plans on a zone's wad and walks it with core's walker, drawing the trail | yes |
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
October. Nothing in core writes a position. BartleBot's one hop is an author's Teleport to XYZ
(Stage 7), at most `HOP_MAX` from where the wizard stands (*A bot run*).

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
  (applied transposed, as Deimos does). Faces steeper than a normal z of 0.5 are cliff, not floor,
  **unless they rise no more than 20** (`LIP_RISE`, the step a walk takes, `STEP_TOL`): those
  are a step's lip, and a wizard walks over them. Ironworks' raised walkway ends in lips 3 to 5
  units across rising 12 to 18 (61 to 73 degrees); taken for cliffs, they left a hole a cell
  wide between the street and the walkway, and a walk failed there in game (2026-11-26). Keeping
  them joined four floors' pieces (Ironworks, Celestia's Science Center, Zafaria's Drum Jungle,
  Marleybone's Roof) and filled small holes in 100 others.
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
  `MU_TeleportStoneCollision.xml`). Those are the zone's gates (below): every walk up to a stone
  stalled on it once before they were read.

The grid is 40-unit cells, with the player's clearance taken as 50 (49 measured). **Each cell
keeps every floor it has** (floors more than 150 apart are separate; a 3-storey house has
cells with three), and a step goes only to the neighbor's floor at about the same height
(rise up to 1 unit per unit walked, plus 20 for a tread or kerb). That is what lets stairs,
and nothing else, join storeys. Floor is passable only if every cell within the clearance has
floor at about its height, which keeps a route off both walls and drop edges.

**Room from walls and drops** (`Grid.wall_room`, since 0.15.5, Stage 1.2 of BartleBot's #340):
each floor spot's room is the widest of `ROOM_STEPS` (50, 80, 120) it has floor all round
within, measured as passable is. On the cells, a spot at 80 stands 100 to 120 units from a
straight wall's face, and one at 120 stands 140 to 160. A route pays to keep the most room
there is (`route.WALL_WEIGHT`, below), and narrow floor is never refused, only priced, so it
squeezes down to the clearance only where the floor narrows: a doorway, a bridge, a gap. **Why:**
walks were seen clipping into corners (the user, 2026-11-27). The clearance is a 50-unit radius,
but its one-cell disc has no diagonal, so a route on a test zone's inside corner passed 34 units
from it, and the walker turning toward its next waypoint 80 units early (*Following*, below)
cuts every inside corner by some 25 more. With the room priced, that route keeps 105. The
middle step alone kept 57 there, since a two-cell disc's diagonal reach is short too, so 120
is what buys about 100 at a corner.

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
lands on*. **For all but a carpet ride, no client file says where a link lands**: a volume's
trigger has a `ResTeleport` result with no properties left (below, *Exits*), and every world object's template has `DeletedBehavior` where the behavior
that moves the wizard would be (`TeleportProximityBehaviorTemplate`, whose `m_locationName`
is exactly that landing, survives only in housing and guild templates). So the landing is put
together from what the files do keep, in this order (`zone_links.py`'s `zone_links`):

| source | gives | where it holds |
|---|---|---|
| a volume's name | the arrival point sharing the most words, "to"/"from" breaking a tie, no link where two fit as well | Triton's six volumes (four walked, 1 to 5 off) |
| `zone.nav` | for a volume the name leaves on its own piece, if its name says it moves the wizard (teleport, to) or a teleporting trigger fires on it (*Exits*), the far end of the quest arrow's edge starting at it | Lemuria's tower lifts, each named for the end it stands at; Krokotopia's Forgotten Tombs; Crimson Fields' passage north, an `Activator Volume` that carries the wizard 20,000 units to `FromBattlefieldAMain` and walks like a passage in game |
| a carpet ride's template | its `InteractCarpetRide` names its zone and **the arrival point it lands on**. One naming no arrival point of its zone leaves it, and is no link by any rule here | Ahmarra's leaves and wing harnesses (12 in Primal Archipelago; `Leaf-ToBird` and the harness land where a player saw them land, `Leaf-ToBird` on a point farther from its return leaf than another); the Oasis's boat names a point on the Sphinx's island |
| another ride's template | a zipline's `InteractableBehavior` holds its two ends as LOCATION records; it lands on the arrival point nearest the second (800 to 920 past it) | all four Floating Mountains ziplines; two pressed, 3 off each |
| a stone's partner | a `TeleporterX` stone lands at the arrival point nearest `TeleporterX_ToMain`, and that one at the arrival point nearest the hub's stones; Dragonspyre names its own by letter and role in either order (`DS_TeleportStoneA_Main`, `ArenaTeleportStoneSecondaryA`), Main at the hub and Secondary at the far end | Triton's four (pressed, 1 to 5 off); Colossus, Cyclops, Firecat, nine Krokotopia zones and Hametsu give names like "StreetB", "Target_Arena", "BackToHub", "HamPorter2"; Dragonspyre's Arena, whose `FromMainStoneA` and `FromOuterStones` match (not yet pressed) |
| a two-way pair: X and X_B or X and X-A among travel objects; the only two travel objects sharing a zone tag; a teleporter's _TOP and _BOTTOM, whatever their prompt says | each at the arrival point nearest the other, in all three axes, if one is within 2,000 | Krokotopia's Forgotten Tombs teleporters; Dark Water Trench's two safe currents and its whalebirds (all six ends land where a player saw them land); Eastern Sea's `NPC12` and `NPC12-A`; Bale Flotilla's `Teleport_2_TOP` and `_BOTTOM` |
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
  words in `WizardGameObjects.lang`, says Teleport, Ride, Climb, Descend, Ascend, Travel, Exit or
  Swim, or, with no prompt, whose name says teleport, rope, ladder, zipline,
  boat, elevator, lift, cart, dinghy or portal. **A template can name its quests before its
  prompt**, so a quest's title is passed over when any other text resolves. Reading the first one
  gave Eastern Sea's `NPC12` the prompt "Jelly School" instead of "Press X to Teleport"
  (2026-11-27). With no other text the title stays, since an object left with no prompt would be
  taken for travel by its name.
- **A volume link must join two pieces of floor**: that alone throws out zone exits, whose
  names match an arrival point on their own piece ("Street4 to Cavern"). **An object link is
  kept if it joins two pieces or moves the wizard 1,000 units or more**, as a stone is worth
  pressing even where the far end can be walked to; the route's cost decides. **A pair other
  than X and X_B is kept only where it joins two pieces**: none has been pressed where its ends
  are a walk apart, and as shortcuts they would have changed walks that work (one a shipped bot
  takes in Katzenstein's Lab, to save 2 s). An object within 300 of a teleport or activator
  volume is the pad drawn there (Triton's `Telepapad`s), not a link. A carpet ride or a pair's
  end is hidden only by a volume that teleports or is named for it: Eastern Sea's `NPC12-A`
  stands in a quest goal's activator. The wider rule stays for the rest. Cinematic points are
  left out.

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
  whose trigger is "Teleport To Lady Blackhope's Tower"). The same read says which unpaired
  volumes to try against `zone.nav` (*In-zone links*): a teleport volume whose quest-arrow edge
  lands on another piece of the zone is a link, not an exit. Taking that rule from the trigger
  rather than the name (2026-11-26) made links of nine volumes once taken for exits, in Crimson
  Fields, Nana's HQ, Krokotopia's hub and the Temple of Storms.
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
to step in. `ZonePlanner.outside_exits(x, y, z)` gives the point itself when no exit holds it,
and otherwise the nearest floor no exit's area holds (since 0.14.0): a pickup whose spot is
inside an exit walks there instead, so it stays in the zone. Unicorn Way has nine exits; of the six routes in the probe's OUTCOME, one moved, by
2.8 cells, to go around one.

### Gates

`gates.py`, and `ZonePlanner`'s gate methods in `route.py`. **A gate is a placed object's solids
where it stands**, and **it is open until a walk finds it closed** (Stage 3 of BartleBot's epic
#322; its plan page has the decision).

**Why open:** a survey of the 464 mapped zones on r814353 (2026-11-25) found 3,163 placed objects
whose template names a solid file, in 413 zones. Nearly all of them are things a player's quests
decide: 789 are removable by a quest (`DeletedBehavior`), 233 spawn only on a requirement, and
845 have a start state. Among them are the dungeon doors that open once a room's fight is won
(43 `AZ_TempleDoor_col` in the Gauntlet's rooms), blockers gone once a quest is done
(`BigGenericBlocker`, 1,360 by 2,117, `GenericWallBlocker`, 500 deep, `AV-IceBlocker`,
`AZ-FlamingBridgeBlocker`), and the quest lights that show only while their quest is on. And the
files cannot tell them apart from real walls: the quest lights, and 57 objects named as doors,
gates or blockers (`MS_Barricade`, `KT_IguanaBlocker`, Novus's ley line doors), carry none of
those markers. So a farming player, past most of them, keeps the routes the floor gives, and an
auto-quester, who meets many, learns which are there as it walks.

**A link object's solid is always a wall**: a gate standing where a pressed link is taken, named
for the same object (`gates.mark_walls`), such as a teleporter stone or a zipline's post, since
the link proves it is there. Its footprints are walled after the links are found, so its own
solid never hides its link: the link is taken from the floor around it.

**Reading one:** `tools/zone_files.py`'s `ZoneReader.gates` reads each placed object
(`gamedata.bin`'s `m_objectList`) whose template's `CollisionBehavior` names a solid file and
does not set `m_bDisableCollision`. The solid file is plain XML: a `<world>` of `<primitive
type="box|cylinder|sphere|mesh">`, each with its `<location>` and `<rotation matrix>`; a box's
`<dimensions l w d>` are its full sides along x, y and z, a cylinder's `<cylinder radius length>`
stand along z, a sphere has `<radius value>`, and a mesh lists its vertices and faces
(`collision.parse_solid_xml`). Of the files the 3,163 objects name, 329 hold cylinders, 324
boxes, 91 meshes and 10 spheres; four are missing from their wads and two unreadable, and a mesh
naming another file (`<mesh source>`) is skipped. The solids are put where the object stands
(`collision.place_solids`): turned by its `m_orientation`, scaled by `m_fScale` and moved to its
`m_location`. Each wall's footprint is found as `collision.bcd`'s are, a mesh's as the hull of its
vertices (`collision.placed_footprints`), and **whatever its category**: the blockers are tagged
`CT_ClientObject`, which in `collision.bcd` marks a solid that walls nobody.

**Which way a yaw turns an object** was settled on the gates (2026-11-25): an object's yaw turns
it the way a wizard's yaw turns a wizard (`geometry.facing`). Of the door-, gate- and
blocker-named objects in ten zones turned off the axes, walled one way 10 of 12 split their
floor in two and each closed 12 to 135 cells, a gate filling its doorway; walled the other way 7
of 11 did, and several closed 160 to 931, lying across open floor. Pitch and roll, which 61 of
the objects have, are applied about x and then y before the yaw, by a convention nothing has
checked.

**What the planner does with them:**

- `ZonePlanner.close(gate)` walls a gate for every route the planner plans after: the floor its
  footprints take, and the wizard's clearance around them (`gate_floor`, built once a session on a
  window around the gate and shared by every copy of the planner). Each caller of
  `zone_maps.load` gets its own list of closed gates, as it gets its own floor.
- `gates_on(route)` names the gates a route crosses, in order: BartleBot's `check_bot_routes.py`
  (its Stage 4) lists them. `gates_near(x, y, z, reach)` names the open ones near a point.
- `route()` still returns None when there is no route, and `no_route(start, goal)` explains it as
  a `gates.NoRoute`: the zone and both points, and, when opening one closed gate alone gives a
  route, that gate's name and a `Crossing`, the last floor before it and the first after it on
  that route and the distance between them. That is about the gate's depth plus the clearance
  on each side: a gate 120 deep comes out at 220, to within a cell. It is the length a hop
  past the gate would need (BartleBot's `HOP_MAX`). Each gate it tries is a plan of its own,
  and a plan with no route searches all the floor it can reach, seconds in a big zone; so it
  plans once with every closed gate open first, and when even that finds nothing, it names no
  gate without trying them one by one (Dragonspyre's Arena, eight stones listed closed: half a
  minute became one plan).
- **A plan can be stopped.** `astar`, `route()` and `no_route()` take a `stop` event, looked at
  every `STOP_EVERY` nodes, and raise `PlanStopped` once it is set. The walker plans through
  `walk.plan_in_thread`, which sets it when the walk is cancelled: a thread cannot be, and a
  run stopped mid-plan once left its search holding the GIL, and the event loop with it, for
  half a minute after.
- **A goal on a closed gate's floor has no route**: `route()` returns None rather than snap the
  goal to the floor left beside the gate, which would end a walk short of it as if arrived (the
  Shopping District's Colossus blocker covers half of the exit behind it), and `no_route` names
  the gate.
- `open(gate)` undoes `close` for a gate the client shows gone after all: it gives back only the
  floor closing it took, and re-walls every other closed gate and every `block`, which may share
  it.
- `crosses(gate, path)` says whether a line of waypoints steps on a gate's floor: the walker's
  question of a gate it learns of mid-walk, when it has the waypoints left and not a `Route`.
- **The walker closes the gates its client lists, and a gate it stalls at** (*Walking a
  route*, below).

**What the client lists** was read in game on 2026-11-25 (r815378), with BartleBot's
`tools/probe_gates.py`, on two wizards, one past Colossus Boulevard and one not. Nothing was
written but wizwalker's hooks.

- **A gate a quest removed is missing from the entity list, and one that is there is listed
  and blocks.** Shopping District, `BigGenericBlocker_CollossusGate`: for the wizard who had not
  opened Colossus it was listed, 0 units from where `gamedata.bin` places it, under the same
  template id, with `CollisionBehaviorClient`, `RenderBehavior`, `ObjectStateBehavior` and
  `SelectBehavior`, and pushed into it stopped that wizard 40 units short of its footprint every
  time. For the wizard past it, it was in no pass of 24, from 2,722 units to 1,629, and that
  wizard walked through where it stands. Unicorn Way's 89 Rattlebones debris gates, which a
  quest removes, were in no pass for a wizard past that quest, as near as 68 units, and none was
  on the street.
- **An event's objects are not listed while it is off**: the Shopping District's Halloween
  apple tub and Unicorn Way's 22 pumpkins, as near as 146 units (`m_spawnRequirements` names
  the event). Many gates are these: most of Unicorn Way's 116, and the Commons' 46.
- **A live object is a map gate by its template id and its location**: every one listed stood
  0 units from its placed `m_location`, and its template's `m_objectName` is the gate's
  `template`. The live zone tag is an id, not the name the map keeps.
- **Placed objects are listed out to about 4,400 units, and those exempt from AOI anywhere in
  the zone.** Unicorn Way's four fairy cages and Archmastery pedestal went in and out of the
  list as the wizard walked across the zone, listed out to 3,877 to 4,388 units and dropped from
  4,555 to 5,142. Triton Avenue's `WC_Teleporter_Hub`, exempt, the decorative center of the
  zone's own teleporter network, was listed in all 61 passes, out to 17,445 units. Entities of
  other kinds were listed past 10,000.
- **States read**: an object's `ObjectStateBehavior` holds `m_stateList` (a list of `uint32`
  at 136), one per category of its template's state set, each the `string_id` or `djb2` of a
  state name in `Root.wad`'s `StateData/*.xml`. The Colossus blocker's read
  `Idle;Alive;NotInteracting;Stationary`, Triton Avenue's mill lever `NotInteracting;Off`, and
  Unicorn Way's fairy cages `FairyEscaped`, with `Interacting` turning to `NotInteracting` as the
  wizard walked off. **Whether a listed object's collision follows its state is not known**: no
  door was seen to open.
- **The read is cheap**: every entity's template id and location took a median 3 ms, 6 at
  worst, for 66 to 97 entities; wizsprinter's `get_mobs()` took a median 7 ms in Triton Avenue.

### Routes

`route.py`. A* over the cells (8 neighbors, no cutting a wall's corner), with each link an extra edge from
the floor near where it is taken (100 units of a volume's center, 150 of an object's) to the
landing, costing 4 seconds of walking for a volume, 6 for a press, 20 for a ride. The
heuristic does not know about links, so a route through one is found but not always the
shortest. The route is then straightened: from each kept point, the furthest point still
reachable in a straight line without coming nearer a patrol than the route itself did, and
without paying more for its room from walls than the stretch of route it replaces, give or
take a cell at the middle step (`smooth`'s *walls*). That cuts 100 to 300 grid turns to 20 to
40.

**Room from walls costs a step** `WALL_WEIGHT` (4) more per unit walked on floor with under 80
of room, and a quarter of that under 120, added to the patrols' cost per floor spot
(`ZonePlanner.step_cost`), whether or not a walk avoids mobs. So 50 units of the tightest floor
cost what 250 of roomy floor do: a route takes a wider way up to that much further round. On a
test hall cut by a wall with a gap 120 wide, a gap 600 wide some 380 units further round is
taken, and one 600 further is not. Straightening is held to the same price rather than to the
least room along the stretch, since a route starting beside a wall has the least there is
there, and its line would then hug the wall all the way. On the shipped maps (2026-11-27, 12
random routes over 2,000 units in Unicorn Way and 8 in the Hive): the same routes found,
planning 4 to 5% slower, routes 1 to 1.4% longer; 16 of the 20 straightened lines had crossed a
cell with no room at all, and with the room priced none came under 50, and half kept 80. A route with links comes back as **legs**, each
straightened alone and each but the last ending at the link's volume, or on the floor beside
the object to press.

**A route never walks through a volume link it does not take**, since stepping in moves the
wizard. A volume link's floor, within its reach plus `EXIT_MARGIN` (150 units of its center),
is left only by its link (astar's `taken_only`), unless the route starts there; one holding the
start or the goal is open. Where that leaves no route, a volume across the only way whose landing
leads nowhere useful, the plan is made once more as if the volumes were floor, and the walker
replans after whatever moves it. The route's floor drops the volumes it did not step into, so
straightening never cuts across one either. On the shipped maps, 14 pairs each in Triton Avenue
(six volume links) and Unicorn Way planned the same routes through the same links as before,
within 12 units (2026-11-25).

**Seconds to several goals** (`ZonePlanner.seconds_to(start, goals, avoid, speed)`, since
0.15.5, for BartleBot's #340): one search out from the start, on the floor and costs a route
uses, that spreads by cost instead of heading for one goal (Dijkstra, where a route is A*),
and stops once every goal is settled. Each goal gets the walking seconds of the route `route`
would plan there, straightened and measured as legs (`walking_seconds`: each stretch's flat
length at *speed*, which defaults to 600, and each link's own seconds), or None where it would
find none: off the floor, behind a closed gate, or out of reach. So a goal a wall puts a long
way round costs what walking round does, which straight-line distance never shows: it is how
Collect Wisps picks a wisp. A goal in an exit, or on a volume link's floor, opens that floor to
its own route only, so it is planned alone, with `route`. `astar` and `seconds_to` share the one
search loop (`_search`), so they cannot drift apart; a straightened route's length agrees with
`seconds_to`'s within a cell. Like a route with none, a goal that has no route makes the search
reach every cell it can, which takes seconds in a big zone.

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
no product's files: BartleBot's `zone_nav.json` (each world's zones), every `zone` or `*_zone`
param in its `bots/` and `subgraphs/`, as its `restricted_zones` reads them, and every zone its
Scales of Knowledge table names (`--names scales_of_knowledge.json`: a `--names` is a folder or
one file). The table joined on 2026-11-25 (BartleBot's Stage 6 of #322): a Scales stood in a
mapped zone only when some shipped bot farmed its dungeon, so seven stations, six of them world
defaults, had no map. On r815378 that is 473 zones, every one with a wad and a map. A ride's
cutscene zone (a link's `ride_zone`) is left out, since nothing walks there: the ride carries the
player, and one found there finishes it by pressing X. The Dragonspyre dragon's has no floor at
all, and `Grid` raises "nothing to walk on" for it. BartleBot's
`test_every_zone_on_the_map_has_a_walking_map_core_ships` fails on a zone either file names
without one. A later product's zones (BartleBuddy's quests) join by a further `--names` or
`--zone-nav`.

**Challenge Mode copies** are built beside the zones named. Challenge Mode loads a dungeon as a
second zone with its own wad (`_Hard`, `_Challenge`, `_Adv` or `Adv` on the id), a bot names only
the ordinary one, and a walk plans on the zone the client is in. So the build adds every copy of a
listed zone that the install has a wad for (`with_challenge_copies`). They were left out until
2026-11-27, when the party at Captain Nemo, in Challenge Mode, had no map to walk to him on. The
check at Run reads only the zones a bot names, so it could not say so.

**What a map holds:**

| file | holds |
|---|---|
| `floors/<key>.npz` | the floor: `collision.bcd`'s walkable triangles, and each solid's footprint (its hull or circle, the heights it spans, whether it is firm), as `collision.py` finds them. Keyed by the start of `collision.bcd`'s SHA-256, so zones with the same file share it |
| `zones/<zone with / as ->.json` | what differs between zones on one floor: the patrols, the named arrival points, the links and the exits, already found, in the order found, and the gates, each with its footprints and whether it is a link object's wall. Zones that share a floor place different objects, so the gates go here |
| `index.json` | each zone's two files, the SHA-256 of the wad entries it was built from (`zone_files.MAP_ENTRIES`, not the models and textures), and `objects`, the SHA-256 of every template and solid file its gates were read from (`ZoneReader.gate_hashes`), so `--verify` sees a change to one |

Only derived geometry, names and positions: no game file and nothing pickled (`np.load(...,
allow_pickle=False)`); the arrays are plain floats and ints. 473 zones on 451 floors, so only
22 zones share one; the same room placed at another spot does not share, and sharing those too
would save under a tenth. 20.8 MB in all with the gates (18.5 before them, 2026-11-25), the largest floor 0.2 MB, and 3,167 gates, 162 of them a link object's wall (2026-11-26).

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

**Checked again with the gates** (2026-11-25, r814353): rebuilt with them, every one of the 464
zones kept the links, exits, patrols and arrival points it had, and every link one of the 78
link-object walls stands at still has floor to be taken from, in the 17 zones that have one.
`--compare` on the same eight zones found map and wad the same again, gates included, and all
866 routes between their arrival points the same cell for cell; `--verify` found all 464 maps as
the install has them, gates' templates and solid files included.

## Walking a route

Core's `movement/walk.py`: `RouteWalker(client, planner, avoid=…, watch_mobs=…).walk(lease, goal)`
walks one wizard to a point in its zone, from wherever it stands, through the lease's input and
nothing else. BartleBot's `probe_walk_input.py --to <arrival point, placed object or x,y[,z]>`
walks on it, on a planner built from the wad.

- **Keys are the account's own**, read from the client's memory (`KEY_BINDINGS.md`):
  MoveForward, MoveBackward, MoveLeft and MoveRight (A and D *turn* a wizard; these are not
  strafes) and NPCInteract, each a `GameAction`, held and pressed through `ClientInput`.
- **Steering writes the yaw.** wizwalker's yaw is 0 facing -y, growing counterclockwise
  (`atan2(-dx, -dy)`). Hundreds of writes, none undone. Before the walker, the probe could steer
  with the turn keys (A turns the yaw down at about 152 degrees a second), which tracked the route
  less closely; the walker writes the yaw only.
- **Speed.** Walking is horizontal only: a stair's height is free. On foot it measured 600 on
  flat ground (wizwalker's constant says 580); a mount multiplies it, most by 1.4 and the
  fastest by 1.55. The walker reads `speed_multiplier` (a percentage) to give a walk its time,
  through `walk.own_speed(client)` (since 0.15.7, for a caller costing a route before a walk),
  and never writes it, nor a position; a guard test reads the module for either. On the +40%
  mount: 840 expected, 838 measured.
- **Planning never runs on the event loop.** A long route takes about 5 s to plan in the Outback,
  1 to 1.5 s in Triton Avenue and 0.2 s in Olde Town (on the shipped maps, 2026-11-25), so every
  plan, replan and detour runs in a worker thread, with forward and backward let go meanwhile: a
  wizard backing off from a mob would otherwise back on for the seconds a detour takes to plan.
- **Following** a leg: hold forward, re-aim at the next waypoint each 50 ms tick, pass a
  waypoint within 80 units or once past it along its leg, stop within 50 of the goal.
- **Resets and jumps.** A move faster than 1,600 units a second (or twice the speed expected)
  between two reads is a jump. One that leaves the wizard within 150 of the leg it walks is the
  server putting it back, and the stretch is walked again; one that leaves it off the leg, a link
  it did not plan for, is replanned from where it landed. A zone's exit and being pulled into a
  duel are jumps too, so on any jump but the pending link's, the walk checks for a fight and a
  zone change first, and ends on either rather than planning on in the zone it left.
- **Stalls** (forward held 1.5 s, under 60 units moved): back off 150 units with the backward
  key, close the floor 80 units ahead within 110, replan from there, three times; the fourth
  stall ends the walk blocked. A wizard grazing a round trunk slides around it unaided; only one
  walking straight at an obstacle stalls. **A stall within 150 units of an open gate's solid**
  (`GATE_REACH`) closes the nearest such gate on the walk's own planner, instead of the floor
  ahead, and replans: around it, or the walk ends `NO_ROUTE` with its `NoRoute` on
  `WalkResult.no_route`, naming the gate and the crossing past it. `WalkResult.gates_closed` lists
  each gate a stall closed. **A gate the client shows gone is left out**: a stall near it is
  something else (a mob, a solid the map lacks), so the floor ahead is closed and the gate stays
  open. Only a client whose list could not be read leaves a stall to decide alone.
- **Gates the client lists** (`GateRead`), whether or not mobs are watched. One pass over the
  entity list (`ClientRead`) reads every entity's template name and location and picks out the
  mobs as wizsprinter's `get_mobs` does (`NPCBehavior`'s flag at 288): before the first plan,
  and every 2 s after (`WALK_MOB_REFRESH`), by the `MobWatch` when mobs are watched and by a
  refresher of the walk's own when not. On each pass:
  - **A listed gate is there** and is closed: an entity of the gate's template, by name, within
    25 units of where the map places it (`GATE_MATCH`; every one read in game stood 0 off). When
    it is on the way left (`crosses`), the walk replans around it, or ends `NO_ROUTE` naming it,
    before it is reached, with no stall. `WalkResult.gates_seen` lists each.
  - **A gate not listed within 3,500 units is gone** (`GATE_LISTED_RANGE`, under the least
    distance an object was seen listed at, 3,877, so out of view is never taken for gone). A gate
    a pass closed opens again (`open`) once two passes in a row show it gone
    (`GATE_GONE_PASSES`: one pass that skipped its entity would otherwise flap it), and the walk
    replans; `WalkResult.gates_opened` lists each. A gate a stall closed never reopens.
  - **A gate beyond range and not listed is not known**, and stays as it is: open, unless a
    stall or an earlier pass closed it. An object exempt from AOI is listed anywhere in the zone,
    so a listed one far off is closed like any other; whether it is exempt is never needed,
    since gone matters only near the wizard, at a stall or for a reopen, well inside the range.
  - **A listed gate is taken as closed whatever its state** (`ObjectStateBehavior`): no door was
    seen to open, so whether a door's collision follows its state is not known, and by the
    user's choice (2026-11-25) the walker assumes that a gate there blocks.
  - **Walked in game** (2026-11-25, r815378, `probe_walk_input.py`, the two wizards of the
    session), to the Shopping District's Colossus exit: the wizard past Colossus Boulevard,
    its blocker unlisted, walked through where the blocker stands into the exit, with and
    without `--no-mobs`; the other, its blocker listed, ended `NO_ROUTE` naming it in 0.7 s,
    no key pressed. A listed `WC_RateMyStitch_Pedestal_Tome` off the route was closed with no
    replan.
- **Mobs**, when the walk is told to watch them (`watch_mobs`): a `MobWatch` reads every listed
  mob's position five times a second and estimates its velocity, listed afresh on each pass.
  Each tick the walk projects itself 2.5 s along its route and every mob along its velocity, and
  wants each mob's trigger radius plus 120, plus 40 for every second looked ahead. **A mob walks
  at about 40 to 180 units a second and a wizard at 600 to 840**, so next to a wizard a mob is
  nearly a standing obstacle: short of room, the walk first plans a detour closing each mob out
  to that same room at three moments over the 2.5 s, and only without one waits or backs off.
  A detour plans only to a waypoint of the route at least 1,500 units along it, beyond every mob
  near it and outside each, and the rest of the route follows unchanged: a detour planned to the
  goal took seconds in a big zone, with the mobs moving meanwhile. Detours plan with the patrol
  cost even on a shortest-path walk.
- **A spell of waits for mobs steps down** (settled with the user 2026-11-26), counting the
  seconds stood or backed off since the mobs last left it room:
  1. **The full room** above, for `WALK_MOB_EASE` (8 s).
  2. **Then `NARROW_MARGIN` (40) beyond each trigger radius**, none more for the seconds ahead,
     and no slack to go on with, for detours too.
  3. **Then past `WALK_MOB_WAIT` (30 s), it walks on past them unwatched**, logged as a warning,
     rather than ending blocked as it did before: waiting longer for a loop of mobs that never
     clears gets nowhere, while a fight it meets ends the walk `FIGHT`, as any does, for the
     caller's ambient combat.

  Once the mobs leave it room at the full margin for `WALK_MOB_CLEAR` (5 s), it keeps the full
  room again and the next spell starts over. For a walk standing or backing off, that room is
  what it would go on with, the 60 of slack included (since 0.14.3): room short of it, taken
  for clear, started the spell over while the walk still stood, so it never eased, and since
  waits for mobs are left out of a walk's time, it could have waited for good. `WalkResult.eased` and `ran_through` count the
  steps taken, and `describe()` says when it walked on unwatched, naming the mob that last held
  it up. **Why:** in the Hive, on 2026-11-26, all 8 spiders of one patrol loop (trigger 350, 117
  to 203 units a second) held a walk up 30 s at its entrance, 2,200 to 3,000 units from it, each
  in turn; the floor keeps the route 390 to 650 units from their path. A simulation on the zone's
  map put a straight run, started at a random moment, clear of all 8 at 40 beyond the trigger
  from 93% of moments, and at 120 from none (evenly spaced spiders) to 32% (randomly spaced). The
  numbers are to be tuned in game: lag pulls some mobs from further than their trigger radius
  (*Mobs*, above), so a pull is likelier at the narrow margin. Predicting each mob along its
  patrol path, not its velocity, and timing the crossing, is the larger fix, left until this
  proves not enough.
- **The log says what held it up**: the wait names the mob leaving the least room (its name, its
  trigger radius and whether that is a patrol's or `DEFAULT_TRIGGER`, where it stands from the
  wizard, how fast it moves, how many seconds ahead the room is least, and how many mobs are
  watched), again whenever another mob takes over, and why no detour was taken (no way around, a
  way only by a link, or too little room on it, naming that mob), and each step of a spell above.
  `WalkResult.held_by` keeps the last mob that held it up.
- **A walk into a fight** (`walk(lease, goal, fight=True, spare=…)`, since 0.14.0) aims at the
  fight's mob, or at the duel circle of a fight already on. Watching every mob, it would wait
  forever for the one it means to meet, so the watch leaves out the mobs in *spare* (their
  global ids, wherever they stand) and every mob whose trigger radius comes within
  `route.FIGHT_REACH` (120) of the goal. Its plan pays nothing for those mobs' patrols either
  (`ZonePlanner.fight_cost`): every patrol whose trigger radius comes within that reach of the
  goal is left out of the cost, and every other costs what it would. On a street with a ghost's
  path 300 units beside the straight line, a fight walk up it is that line, where any other walk
  swings out half as far again. The walk ends `FIGHT` once `in_battle` reads true, as any walk
  does; the run to the circle after the trigger is the server's.
- **Links.** At the end of a leg the walk stands in the volume, or faces the object and
  presses the account's interact key once the game shows its prompt (wizwalker's
  `is_in_npc_range`, waited on up to 2 s), again every 2 s, until moved more than 300 units
  (rising counts, for a ladder); after a press it waits for the wizard to be set down (under 20
  units in 1 s), then replans from wherever it landed. A jump near the pending link's landing is
  that link, taken early. Not moved within 6 s, the link is dropped and the walk replans: a wrong
  pairing and a quest gate look the same.
- **A held event loop** leaves forward held with nothing steering: in 21.8 hours of BartleBot and
  BartleBuddy run traces (2026-11-25) the loop was held past 0.5 s 1.7 times an hour, past 1 s
  0.5 times an hour, and 3.1 s at the longest, up to about 1,900 units on foot. So
  `ClientInput`'s watchdog thread lets go of every held key once the loop is 0.2 s behind
  (`HELD_KEY_LOOP_LATE`), and the walk, finding forward no longer held, holds it again, aimed
  afresh: a held loop walks a wizard some 120 to 190 units. Settled with the user on 2026-11-25,
  over a walk tick on a thread of its own, which would have had to cross the arbiter, the leases
  and the key repeat, all on the loop.
- **A plan too long** (`walk(..., longest=…)`, since 0.15.5, for BartleBot's #340): every walk
  puts its first plan's walking seconds on `WalkResult.planned` (`route.walking_seconds` of its
  legs, so no wait for a mob, a prompt or the planner), at the wizard's own speed, read from its
  speed field before it plans, which goes on `WalkResult.speed`. The field holds the whole bonus:
  a mount's, up to 1.55x, or the Speed hotkey's write, up to 2.0x. Given *longest*, in seconds, a
  plan over it ends the walk `TOO_LONG` before any key is pressed, logged at INFO: a caller asking
  whether to walk at all (a port to one of ours, in BartleBot) has its answer at the price of the
  one plan the walk would make anyway.
- **The plan kept.** A walk that ended `TOO_LONG` leaves its plan on the walker, and the next walk
  takes it instead of planning, once, when it starts within `ARRIVE_GOAL` (50) of the same spot,
  goes to the same goal and the same fight, and no walk has changed the planner's closed gates,
  blocked spots or links since; the gates are read before it, as before any first plan, so a gate
  listed meanwhile plans afresh. Any other walk drops it. Only a `TOO_LONG` keeps one: a walk that
  ended `BLOCKED` or `TIMED_OUT` and is asked for again must not rerun the plan that failed.
- **A goal that moves** (`walk(lease, goal_read, reach=…)`, since 0.15.6, for BartleBot's #340):
  the goal is a `GoalRead`, a coroutine function giving where the goal is now, or None once it is
  gone, as a wisp is once taken. The walk reads it every tick and plans to where it first read.
  It is arrived once it stands within *reach* of the goal's position now, or once the goal reads
  None, and a walk that starts within reach presses nothing and plans nothing. *reach* is
  `ARRIVE_GOAL` by default; a fixed point given a larger one ends within it too, and a fixed point
  at the default is walked exactly as before, with no reach measured. Collect Wisps passes 300,
  100 under the 401 to 418 units from which a wizard took a health wisp (`WIZARD101.md`, *How
  wisps move*).
  - **It re-aims** once the goal has strayed more than `REAIM` (150) from where the route ends,
    at most every `WALK_REAIM_EVERY` (1.5 s; a world wisp moves about 130 units in that time). If
    the line from the wizard to the goal is clear (`ZonePlanner.straight`: the floor a route may
    use, no volume link stepped into, and `HARD` kept outside every patrol's trigger radius, or as
    much as the nearer end keeps), it steers straight at the goal, and follows it as it moves
    while it stays within `REAIM` of where the line was found clear. Otherwise it plans a route to
    the goal in a worker thread **while it walks on** along the old one, since that still leads
    toward the goal and a plan can take seconds; when the plan comes back, it walks straight to
    the furthest waypoint of the new route's first leg it has a clear line to, and on from there,
    or plans afresh, standing, if none is clear. Reaching the end of its route with the goal moved
    on, it stands and turns toward it, still no oftener than `WALK_REAIM_EVERY`; with the goal
    within `REAIM` of the route's end it is arrived, as for a fixed point. `WalkResult.reaims`
    counts the turns, and `WalkResult.goal` is where the goal last read.
  - **It never aims ahead.** A wizard walks at least 6.7 times a world wisp's 88 units a second,
    and the walker does not know the loop's shape, so a straight-line guess at where the wisp will
    be would be wrong on its curves.
  - **A plan kept** from a `TOO_LONG` walk fits a moving goal that has strayed less than `REAIM`
    since, so the walk after a refused port does not plan again. A walk into a fight takes only a
    fixed point.
- **It ends** on the goal, no route, being blocked (by stalls; mobs no longer end it), a fight, a zone change,
  running out of time or a plan too long, with a `WalkResult` saying which and `describe()` naming the zone and both
  points; and it lets go of every key however it ends, and stops its mob watch even if a second
  cancel lands in the letting go. A cancel, or the lease being lost, goes on up to the caller.
- **Its time** is three times the walking planned, its links' waits, its waits for mobs and its
  planning, plus 20 s. It is given again from each replan and each detour taken, for the route
  left then, since a dropped link or a stall can leave a much longer one. Over it all is a
  ceiling no replan renews, `WALK_CEILING_FACTOR` (3) times the first plan's time, about nine
  times the walk first planned, counted from the start with the planning in and only the waits
  for mobs left out (`WALK_MOB_WAIT` bounds each spell of those), so a server resetting a wizard off its route
  over and over cannot keep a walk going for good. The walk ends at whichever comes first, and
  the detail says which.

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

## A bot run

BartleBot walks its run's clients on the walker above (Stage 4 of its #322; its
`bartlebot/nodes/walking.py`, `nodes/CLAUDE.md` *Moving a client*):

- **Every client of a step at once**, each from where it stands, on a planner of its own from
  `zone_maps.load`, under an `Owner.MOVEMENT` lease on the run's one `Arbiter`. The FSM is on no
  arbiter yet (#301), so only the walks hold one.
- **At the wizard's own speed.** Nothing writes `speed_multiplier` while the speed paths are off
  (Stage 0), so the walk plans from the speed field as it reads.
- **Avoid Mobs**, a bot option on by default, sets both `avoid` and `watch_mobs`. The gates are
  read either way.
- **A goal in an exit** has done its part standing in it or once the zone has changed:
  `ARRIVED` and `ZONE_CHANGED` are both success there, since the server's move follows the walk
  by a moment (item 1 below, as was).
- **An interact point inside its own object** (a pedestal, a chest, a brazier), which the client
  lists and the walk so closes, is walked to the gate's `Crossing.before` instead, the last open
  floor in front of it, and only for the walk to a prompt. Six shipped interactions need it.
- **A failed walk** is logged under the node with `describe()`, and fails the step there; a walk
  that ends `FIGHT` waits for ambient combat to preempt the node instead.
- **A walk into a fight** (Stage 5), the fight join, walks with `fight=True` and the mob's id as
  *spare*; its `FIGHT` is the node's success. Wisps are walked to by the clients that need them,
  and a pickup inside an exit by `outside_exits`.
- **Collect Wisps costs its wisps before it walks** (Stage 2 of its #340): its
  `walking.seconds_to` asks `ZonePlanner.seconds_to` for every wisp in sight, one search from
  each walker at the walker's own speed (its speed field, as the walker reads it) and by Avoid
  Mobs. Each round, the health walkers and the mana walkers each take the wisp of their kind
  with the least total seconds, and both groups walk at once. A wisp some walker has no route
  to is dropped and left out of later scans, so its search of every reachable cell is paid
  once; a zone with no map is picked by straight line. Near the node's timeout, a group whose
  planned walk runs past the time left goes only for a player below a fail threshold.
- **Collect Wisps walks to the wisp, not to where it was** (Stage 5 of its #340): its goal is
  the wisp's position read afresh, None once the entity reads no more, is no longer the wisp it
  was, or is gone from the client's list, with a reach of 300. The port question reads the goal
  once. A walker that arrived within reach, whose ratio did not rise and whose wisp is still
  listed a moment later, walks on to `ARRIVE_GOAL` of it once, so neither a smaller reach than
  the health wisps' nor a still wizard beside a wisp that barely moves passes for the game's word
  that the player is full.
- **A long walk to one of ours becomes a port** (Stage 3 of its #340), whichever node walks,
  once the run has a port map. Each mover walks with `longest` at `PORT_LONGEST`, which is
  `PORT_COST` (3 s, the user's estimate of a port within a zone) plus `PORT_MARGIN` (2 s), so a
  shorter walk never asks. A plan over it ends `TOO_LONG`, and the mover costs each player of
  ours it could port to: one in the zone, not fighting, holding no movement lease, with a port
  route from the mover. A trip is `PORT_COST` plus the walk on from that player, a
  `seconds_to` search from where it stands at the mover's own speed (`WalkResult.speed`), since
  a search out from the goal would cost the way back, which a one-way link makes differ. A
  player whose straight line to the goal already misses the margin is never searched. The
  quickest trip wins if it beats `planned` by `PORT_MARGIN`; then the mover ports through
  BartleBot's `port_map.port_waves`, under its movement lease, and walks the rest on the same
  walker with no `longest`. When nothing wins, or the port goes nowhere, it walks the kept plan.
  - **The movers of one call join as they arrive.** A mover still on its first walk is a target
    whose trip is the wait for it, at most what is left of `PORT_LONGEST` since the call began.
    The long walker waits only while a port to it would still save the margin, and costs every
    target again whenever a walk of the call changes, so it ports to the first to arrive.
  - **A zone that refuses a port** is walked for the rest of the run: the refusal goes in the
    run's `no_port_zones`, logged once, cleared at the next run's start. A walk into a fight or
    an exit always walks, and so does a walk with no port map (the authoring tools' go-to).
  - **Each port is a span** in the run trace, with its zone, route, seconds from the first click
    to the landing check and whether it landed or was refused, so `PORT_COST` can be set from
    real runs. Not yet run in game.
- **Ahead of need**: a thread builds and saves every zone's copy from startup, the open bot's
  zones first, then those Sync in Zone's network crosses, then the rest. A build holds the
  event loop at most about 33 ms (Triton Avenue, the largest map, 6.1 s to build). Each client's
  first `in_battle` and key-binding read are made when it hooks, so the first walk's warm-up
  (above) is a no-op.
- **A hop past a closed gate** (Stage 7) is the one position BartleBot writes, and only where an
  author placed a Teleport to XYZ: its `walking.hop`, after a walk to the node's start. It writes
  nothing unless, right before the write and under the client's movement lease, the wizard stands
  within `HOP_START_TOLERANCE` (50, the walk's `ARRIVE_GOAL`) of the start and at most `HOP_MAX`
  from the end, in three dimensions, in the zone, not loading and not fighting. It writes once,
  reads back after `HOP_SETTLE`, and a hop the server undid fails the node, never written again.
  - **`HOP_MAX` is 350 units**, the user's call of 2026-11-26: a sixth of the 2,000 units Deimos's
    wizwalker fork has sent in one jump since 2026-11-12 as unlikely to be undone, and enough for
    every gate crossing the shipped bots need (the longest, 283, by `check_bot_routes.py` that
    day). No session log held a teleport at a listed gate, and long jumps hold on live, so no log
    could show a short radius: the number is a judgment, not a measurement, and nothing tested it
    on an account the user plays. It is a constant with no setting or override, and raising it is
    a decision logged on the epic's page. A node's own span is at most `HOP_MAX − HOP_START_TOLERANCE`
    (300), so a node that validates never fails the hop's own check for where its walk stopped.
  - **`HOP_SETTLE` is 1.0 s**: above the 0.3 to 0.5 s Deimos waits for the server's snapback, and
    the 0.7 s after which a mount's rubber-banding threw a wizard back (`WIZARD101.md`). Right
    after a write the position reads as the end whether or not the server keeps it. A chain of
    hops so moves at most 350 units a second, under walking's 600.
- **What the shipped bots ask for**: BartleBot's `tools/check_bot_routes.py` plans every walk
  between authored points. Of 859 on 2026-11-27, on routes that keep their room from walls
  (0.15.5), 777 were clear, 41 crossed a gate with a way around, 28 had a gate as the only way,
  11 took over 60 s and 2 had no route (placed walkable floor, item 8, most likely). On
  2026-11-25, before the room, 4 of 850 had none and 9 took over 60 s.

## What is not done

In rough order of what a bot needs first. Most is a stage of BartleBot's Walking Routes epic
(#322); `docs/EPICS.md` there has the stage-to-issue table.

1. **Several clients walking together**: done in BartleBot's Stage 4 (*A bot run*). A walk
   whose goal is in an exit can end `ARRIVED` before the server moves the wizard (the
   Colossus exit fired 9 to 26 units after), which `walk_clients` counts as success.
2. **Saving the zone maps' copies ahead of need**: done in Stage 4, by BartleBot's
   `walking_maps.WARMER`.
3. **Whether a door's collision follows its state.** The walker reads the gates its client
   lists (*Walking a route*) and takes every listed one as closed. If a door stays listed once
   open (a dungeon door after its room's fight, say), a walk ends `NO_ROUTE` at a door it could
   walk through, and its state (`ObjectStateBehavior`) would be the way to tell. No door was
   seen to open in the session, and the read has not walked a live client yet.
4. **Walk the link kinds not yet tried**: a rope (Novus's Stone of Heaven), a Lemuria tower
   lift, a Krokotopia stone pair, an X / X_B pair, and the `TeleportStone` stones paired by name
   in Dragonspyre, Celestia, Marleybone and Zafaria (*In-zone links*). How near an object a press must be is
   assumed (150; every Mirage ladder took a press from where its leg ended); whether facing it
   matters is not known (the walk faces it anyway).
5. **The 44 zones still apart**: their maps, starting with the worst. Levers and lifts that
   change state (Big Ben) may need the quest's state, not a link.
6. **A checked-in table of verified links** (Stage 8), as `zone_nav.json` keeps verified gates,
   so a pairing once walked is not re-derived, and one that moved nobody is not tried again.
7. **Zone exits as a route's end.** A walk's goal across zones is the zone map's route
   (BartleBot's `docs/ZONE_NAVIGATION.md`), with each crossing an exit to walk into. Since
   Stage 4 a step walks into its exit from its Reset XYZ; the zone route itself is still the
   steps' own.
8. **Placed objects that add floor**: 63 objects name a walkable collision file
   (`m_walkableCollisionFilename`), most of them teleporter pads (Krokotopia's, Celestia's,
   Empyrea's, Triton's), and the maps do not read them. Moved out of Stage 8 on 2026-11-26:
   they stand in 30 zones, none on a shipped bot's walk.
9. **Smaller:** whether `collision.bcd`'s own client-object solids are walls after all, since the
   placed blockers carry the same tag and are; whether any of the 250 non-walkable meshes are walls; a box volume's turn, and
   the name of its third side's property; whether a quest-gated link can be told from the
   client before stepping into it (a trigger's `ReqHasQuest` may say).
10. **Tilted boxes, left as they are on purpose** (decided 2026-11-26). The grid takes floor
    only from walkable meshes, and walls a box by its corners' outline from its lowest point to
    its highest (*The floor*). Both hold for a box that stands upright, and both fail for one
    that is turned: a ramp made of a box is wall, not floor, and the floor under a leaning
    slab's raised side is walled. Dragonspyre's Arena joins its two halves by exactly one of
    each, walked by hand and recorded with BartleBot's `tools/probe_walk_track.py`. Treating
    every up-facing box face as floor and walling each box by its own span joined them, but
    made every crate and wall top floor: passable floor went from 550k cells to 900k and the
    Arena's grid from two layers to four, mostly floor no walk should use, to fix geometry that
    is rare. The Arena is joined by its stones instead. A zone split this way is found by
    recording a walk across the split with that tool, whose analysis names the solid in the
    way.

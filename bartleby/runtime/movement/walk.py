"""Walking a planned route with the account's own keys, on a movement lease.

:class:`RouteWalker` walks one wizard to a point in its zone, as BartleBot's
``tools/probe_walk_input.py`` first did, whose walk this is (core's ``docs/WALKING.md``,
*Walking a route*, has what each number was measured against):

* **It plans from where the wizard stands**, on the :class:`~bartleby.runtime.movement.route.ZonePlanner`
  it is given, and follows each leg: forward held, the yaw written toward the next waypoint every
  :data:`~bartleby.runtime.timing.WALK_TICK`, a waypoint passed within :data:`ARRIVE_TURN` or once
  past it along its leg, the goal reached within :data:`ARRIVE_GOAL`.
* **Planning never runs on the event loop.** A long route takes about 5 s to plan in the Outback
  and 1 to 1.5 s in Triton Avenue, so every plan, replan and detour runs in a worker thread, with
  forward and backward let go meanwhile.
* **Resets are re-walked.** The position is read back every tick, so a wizard the server put back
  on the stretch it walked walks it again. A jump that leaves it off the leg (a link it did not
  plan to take) is replanned from where it landed, once it is known to be no fight and no other
  zone, which a jump also looks like.
* **Stalls** on something the map doesn't know: back off :data:`BACK_OFF`, close the floor just
  ahead, and replan, up to :data:`MAX_RECOVERIES` times; the next stall ends the walk
  :attr:`WalkEnd.BLOCKED`. A stall within :data:`GATE_REACH` of a gate (``gates.py``) closes
  that gate instead, on this walk's planner, unless the client shows the gate gone: a gate is
  open until a walk finds it there. When the replan then finds no way, the walk ends
  :attr:`WalkEnd.NO_ROUTE` with a :class:`~bartleby.runtime.movement.gates.NoRoute` naming the
  gate and the crossing past it.
* **Gates the client lists** (:class:`GateRead`) are closed before the walk reaches them: the
  entity list is read before the first plan and every
  :data:`~bartleby.runtime.timing.WALK_MOB_REFRESH` after, on the one pass the mobs are read on,
  whether or not mobs are watched. A listed gate on the way left is planned around, or the walk
  ends :attr:`WalkEnd.NO_ROUTE` naming it, with no stall. A gate a pass closed opens again
  once the client shows it gone in range.
* **Links**: at a leg's end it stands in the volume, or faces the object and presses Interact
  once the prompt shows, and again every :data:`~bartleby.runtime.timing.WALK_PRESS_EVERY`, until
  moved :data:`LINK_MOVED`. A link that moves nobody in :data:`~bartleby.runtime.timing.WALK_LINK_WAIT`
  is dropped and the route replanned.
* **Mobs**, when it is told to watch them (:class:`ClientRead`'s ``mobs``): each tick it looks
  :data:`~bartleby.runtime.timing.WALK_LOOK_AHEAD` along its route and every mob along its
  velocity, and short of room, detours first, planning only to a point a short way along the
  route, then waits or backs off. Held up for :data:`~bartleby.runtime.timing.WALK_MOB_EASE` it
  keeps only :data:`NARROW_MARGIN` beyond each trigger radius, and for
  :data:`~bartleby.runtime.timing.WALK_MOB_WAIT` it walks on past them unwatched, a fight it meets
  ending the walk as any does; once they leave it room for
  :data:`~bartleby.runtime.timing.WALK_MOB_CLEAR` it keeps the full room again, and its waits
  start over. Its log names the mob that holds it up (:func:`held_by`), whenever another takes
  over, and says why no detour was taken.
* **A walk into a fight** (``walk(..., fight=True)``) aims at the fight's mob or its duel circle.
  Its plan pays nothing for the patrols of the mobs in that fight
  (:meth:`~bartleby.runtime.movement.route.ZonePlanner.fight_cost`), and the live watch leaves
  out the mobs it was told to meet (*spare*, by key) and any mob whose trigger radius comes within
  :data:`~bartleby.runtime.movement.route.FIGHT_REACH` of the goal; it would wait for them
  forever. Every other mob is avoided as on any walk. It ends :attr:`WalkEnd.FIGHT` once
  ``in_battle`` reads true, which for this walk is what it was for.
* **No speed, and no position.** It never writes ``speed_multiplier``, so a wizard walks at
  whatever its field holds, until the user's go-ahead for a faster walk; and it never writes a
  position, so it has no hop to hand over.
* **A held event loop** lets go of its keys by :class:`~bartleby.runtime.input.ClientInput`'s
  watchdog; the walk finds forward no longer held and holds it again, aimed afresh.

It ends on the goal, no route, being blocked, a fight, a zone change or running out of time
(the time given again at each replan and detour, for the route left, and never past
:data:`~bartleby.runtime.timing.WALK_CEILING_FACTOR` times the first plan's), with a
:class:`WalkResult` saying which, and lets go of every key however it ends. A cancel, or the lease
being lost, goes on up to the caller.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from wizwalker.constants import Primitive

from bartleby.runtime.arbiter import Lease
from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.movement.gates import Gate, NoRoute
from bartleby.runtime.movement.geometry import Point, facing, flat_distance, heading_to, wrap
from bartleby.runtime.movement.route import FIGHT_REACH, WIZARD_SPEED, Leg, Route, ZonePlanner
from bartleby.runtime.movement.zone_links import Link
from bartleby.runtime.timing import (
    WALK_BACK_OFF_LONGEST,
    WALK_CEILING_FACTOR,
    WALK_DETOUR_EVERY,
    WALK_LANDED_FOR,
    WALK_LANDING_WAIT,
    WALK_LINK_WAIT,
    WALK_LOOK_AHEAD,
    WALK_LOOK_STEP,
    WALK_MOB_CLEAR,
    WALK_MOB_EASE,
    WALK_MOB_POLL,
    WALK_MOB_REFRESH,
    WALK_MOB_WAIT,
    WALK_PRESS_EVERY,
    WALK_PROMPT_WAIT,
    WALK_SETTLE,
    WALK_STALL_WINDOW,
    WALK_STATE_POLL,
    WALK_TICK,
    WALK_TIME_ALLOWANCE,
)

logger = logging.getLogger(__name__)

#: Units from a waypoint at which it is passed.
ARRIVE_TURN = 80.0
#: Units from the goal at which a walk has arrived.
ARRIVE_GOAL = 50.0
#: Radians off the heading under which the yaw is left alone.
DEADBAND = math.radians(4)
#: Units a second faster than which a move between two reads is a jump, not walking, or twice
#: the speed expected, if that is more. A wizard walked Unicorn Way at a median 600 units a second
#: and stretches ran past 800; the reads themselves can stall, so it is speed over the real gap.
JUMP_SPEED = 1600.0
#: Units from the leg being walked within which a jump is a reset, re-walked, not a replan: the
#: server puts a wizard back where it stood some 0.7 s before, on the stretch it walked.
REWALK = 150.0
#: Units of progress under which forward held for the stall window is a stall.
STALL_MIN_MOVE = 60.0
#: Stalls backed off from and replanned; the next ends the walk blocked.
MAX_RECOVERIES = 3
#: Units a stalled walk backs off by.
BACK_OFF = 150.0
#: How far ahead of a stall the floor is closed, and within what radius.
BLOCK_AHEAD = 80.0
BLOCK_RADIUS = 110.0
#: Units from a gate's solid within which a stall is taken as the gate's: a wizard stops its own
#: radius, some 50, from what it walks into, the cells are 40 across, and a gate's edge may be
#: grazed rather than met square on.
GATE_REACH = 150.0
#: Units from the wizard within which the client lists every placed object, so a gate it does not
#: list this near is gone. Read in game (core's ``docs/WALKING.md``, *Gates*): objects were listed
#: out to 3,877 to 4,388 units and dropped from 4,555; this is under the least, so a gate is never
#: taken for gone when it is only out of view.
GATE_LISTED_RANGE = 3500.0
#: Units from a gate's placed spot within which a listed object of its template is that gate:
#: every one seen stood 0 units off.
GATE_MATCH = 25.0
#: Passes over the entity list in a row that a gate the read closed must be missing from, within
#: range, to be opened again: one pass that failed to read its entity would otherwise open it
#: and close it again, replanning each time.
GATE_GONE_PASSES = 2
#: Units a link must move the wizard to have taken it; rising counts, for a ladder.
LINK_MOVED = 300.0
#: Units from where a pending link lands within which a jump is that link, taken early at the
#: volume's edge.
LINK_LANDED = 600.0
#: Units moved over :data:`~bartleby.runtime.timing.WALK_LANDED_FOR` under which a pressed link
#: has set the wizard down.
SETTLED_MOVE = 20.0

# Mobs.
#: How much a mob's velocity is smoothed toward each new reading; and the most it may be, since a
#: mob yanked across the map (a respawn) is a new mob, not a fast one.
MOB_SMOOTHING = 0.5
MOB_MAX_SPEED = 600.0
#: Mobs further than this, or this far above or below, are not watched.
MOB_RANGE = 4000.0
MOB_LEVEL = 300.0
#: The trigger radius of a mob with no patrol in the zone's map, such as a boss: the largest seen.
DEFAULT_TRIGGER = 275.0
#: The room wanted beyond a trigger radius: a fixed margin for lag and body radii, and more for
#: every second ahead a guess reaches.
MARGIN = 120.0
MARGIN_PER_SECOND = 40.0
#: The room wanted beyond a trigger radius once held up for WALK_MOB_EASE, with none more for the
#: seconds ahead: eight spiders on one Hive loop left a straight run clear from 93% of moments at
#: 40, against none at 120 (a simulation on the zone's map, 2026-11-26). Lag pulls some mobs from
#: further (*Mobs* in core's ``docs/WALKING.md``), so a pull is likelier here.
NARROW_MARGIN = 40.0
#: Once stopped for a mob, go on only with this much room to spare, so the walk does not flap.
RESUME_SLACK = 60.0
#: Backing off, as a fraction of forward speed (allowing for key latency).
BACK_SPEED_FACTOR = 0.7
#: How much wider than the room asked for a detour closes each mob out.
DETOUR_SLACK = 40.0
#: Units along the route at least that a detour plans to, before rejoining it: about the look
#: ahead at a wizard's own speed. A detour planned to the goal costs seconds in a big zone.
DETOUR_REACH = 1500.0

#: The moments, in seconds from now, at which :func:`room` compares the wizard with each mob.
LOOK_AHEAD = tuple(k * WALK_LOOK_STEP for k in range(int(round(WALK_LOOK_AHEAD / WALK_LOOK_STEP)) + 1))


class WalkEnd(StrEnum):
    """How a walk ended."""

    ARRIVED = "arrived"
    NO_ROUTE = "no route"
    BLOCKED = "blocked"
    FIGHT = "a fight started"
    ZONE_CHANGED = "the zone changed"
    TIMED_OUT = "out of time"


@dataclass
class WalkResult:
    """What a walk did: how it ended, where, and what it met on the way."""

    end: WalkEnd
    zone: str | None
    start: Point
    goal: Point
    position: Point | None = None
    detail: str = ""
    seconds: float = 0.0
    planning: float = 0.0  # of the seconds, those spent waiting on the planner
    replans: int = 0
    recoveries: int = 0
    detours: int = 0
    links_taken: int = 0
    links_dropped: int = 0
    waited: float = 0.0  # standing or backing off for mobs
    least_room: float = math.inf  # beyond a trigger radius and the margin kept then, while watching
    held_by: str = ""  # the mob that last held it up, as held_by() gives it
    eased: int = 0  # times a spell of waits for mobs came down to keeping only NARROW_MARGIN
    ran_through: int = 0  # times one ended with the walk going on past them unwatched
    gates_closed: list[str] = field(default_factory=list)  # each gate a stall found there, in order
    gates_seen: list[str] = field(default_factory=list)  # each gate the client listed, closed, in order
    gates_opened: list[str] = field(default_factory=list)  # each the read closed and then saw gone
    no_route: NoRoute | None = None  # why, when it ended NO_ROUTE
    trail: list[Point] = field(default_factory=list, repr=False)
    routes: list[list[Point]] = field(default_factory=list, repr=False)  # each plan's waypoints

    @property
    def arrived(self) -> bool:
        return self.end is WalkEnd.ARRIVED

    @property
    def miss(self) -> float | None:
        """Units from the goal where it stopped."""
        return None if self.position is None else flat_distance(self.position, self.goal)

    def describe(self) -> str:
        """One line for a log: how it ended, and where, in the zone and both points."""
        where = "?" if self.position is None else f"({self.position[0]:.0f}, {self.position[1]:.0f})"
        text = (
            f"walk in {self.zone} from ({self.start[0]:.0f}, {self.start[1]:.0f}) to "
            f"({self.goal[0]:.0f}, {self.goal[1]:.0f}): {self.end.value} at {where} after {self.seconds:.1f} s"
        )
        if self.detail:
            text += f", {self.detail}"
        if self.ran_through:
            last_by = f", last held by {self.held_by}" if self.held_by else ""
            text += f"; walked on past mobs unwatched {_times(self.ran_through)}{last_by}"
        if self.gates_seen:
            text += f"; gates listed, so closed: {', '.join(self.gates_seen)}"
        if self.gates_opened:
            text += f"; opened again, gone: {', '.join(self.gates_opened)}"
        return text


@dataclass
class WalkTick:
    """One tick of a walk, for whoever wants a log of it (the probe's CSV)."""

    t: float
    position: Point
    yaw: float
    waypoint: int
    held: frozenset[GameAction]
    mode: str
    room: float


# --- the client's entity list ----------------------------------------------------------------

#: Every listed mob's key, template name and position, read afresh.
MobRead = Callable[[], Awaitable[list[tuple[int, str, Point]]]]

#: Where in an ``NPCBehavior`` the flag that makes an entity a mob is, as wizsprinter's
#: ``get_mobs`` reads it.
NPC_MOB_FLAG = 288


@dataclass(frozen=True)
class Listing:
    """One pass over a client's entity list: where the wizard stood, every entity's template
    name and location, and the mobs among them, by key, with their names."""

    at: Point
    objects: list[tuple[str, Point]]
    mobs: dict[int, tuple[object, str]]


async def _is_mob(entity) -> bool:
    """Whether *entity* is a mob, as wizsprinter's ``get_mobs`` decides."""
    try:
        for behavior in await entity.inactive_behaviors():
            if await behavior.read_type_name() == "NPCBehavior":
                return bool(await behavior.read_value_from_offset(NPC_MOB_FLAG, Primitive.bool))
    except Exception:  # noqa: BLE001 - freed while read, or not an object with behaviors
        return False
    return False


class ClientRead:
    """A client's entity list, read in one pass for its mobs and for the placed objects a walk's
    gates are matched against (core's ``docs/WALKING.md``, *Gates*).

    :meth:`refresh` makes a pass, and :meth:`mobs`, a :data:`MobRead`, makes one whenever the
    last is :data:`~bartleby.runtime.timing.WALK_MOB_REFRESH` old, and reads each listed mob's
    position on every call. *passes* counts the passes made, for a walk to see a new one."""

    def __init__(self, client, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.clock = clock
        self.latest: Listing | None = None
        self.passes = 0
        self._refreshed = -math.inf

    async def refresh(self) -> Listing:
        """Read the list once: every entity's template name and location, and which are mobs."""
        pos = await self.client.body.position()
        objects: list[tuple[str, Point]] = []
        mobs: dict[int, tuple[object, str]] = {}
        for entity in await self.client.get_base_entity_list():
            try:
                name = await entity.object_name()
                loc = await entity.location()
            except Exception:  # noqa: BLE001 - an entity freed while listed is gone
                continue
            if name is None:
                continue
            objects.append((name, (loc.x, loc.y, loc.z)))
            if await _is_mob(entity):
                try:
                    mobs[await entity.global_id_full()] = (entity, name)
                except Exception:  # noqa: BLE001 - freed since
                    continue
        self.latest = Listing((pos.x, pos.y, pos.z), objects, mobs)
        self.passes += 1
        self._refreshed = self.clock()
        return self.latest

    async def mobs(self) -> list[tuple[int, str, Point]]:
        """Every listed mob's key, name and position now."""
        if self.latest is None or self.clock() - self._refreshed >= WALK_MOB_REFRESH:
            await self.refresh()
        out = []
        mobs = self.latest.mobs
        for key, (entity, name) in list(mobs.items()):
            try:
                loc = await entity.location()
            except Exception:  # noqa: BLE001 - freed since it was listed
                mobs.pop(key, None)
                continue
            out.append((key, name, (loc.x, loc.y, loc.z)))
        return out


class GateRead:
    """What a walk's passes over the entity list say of its zone's gates. A listed gate is there,
    and is closed. One not listed within :data:`GATE_LISTED_RANGE` of the wizard is gone, and if
    a pass closed it, it is opened again once :data:`GATE_GONE_PASSES` passes in a row agree. One
    beyond range and not listed is not known, and stays as it is. A gate a stall closed is never
    opened."""

    def __init__(self, planner: ZonePlanner) -> None:
        self.planner = planner
        self._by_template: dict[str, list[Gate]] = {}
        for gate in planner.gates:
            if not gate.wall:
                self._by_template.setdefault(gate.template, []).append(gate)
        self.gone: set[Gate] = set()  # by the latest pass
        self.closed: list[Gate] = []  # closed by a pass and not opened since
        self._missing: dict[Gate, int] = {}

    def listed(self, listing: Listing) -> set[Gate]:
        """The gates *listing* shows there."""
        there = set()
        for name, at in listing.objects:
            for gate in self._by_template.get(name, ()):
                if math.dist(gate.at, at) <= GATE_MATCH:
                    there.add(gate)
        return there

    def update(self, listing: Listing) -> tuple[list[Gate], list[Gate]]:
        """Close every gate *listing* shows there, and open those a pass closed that are gone:
        the gates closed and those opened."""
        there = self.listed(listing)
        shut = set(self.planner.closed)
        closed = [g for g in self.planner.gates if g in there and g not in shut]
        for gate in closed:
            self.planner.close(gate)
        self.closed += closed
        self.gone = {
            g
            for gates in self._by_template.values()
            for g in gates
            if g not in there and math.dist(g.at, listing.at) <= GATE_LISTED_RANGE
        }
        opened = []
        for gate in list(self.closed):
            self._missing[gate] = self._missing.get(gate, 0) + 1 if gate in self.gone else 0
            if self._missing[gate] >= GATE_GONE_PASSES:
                self.planner.open(gate)
                self.closed.remove(gate)
                del self._missing[gate]
                opened.append(gate)
        return closed, opened


@dataclass
class Mob:
    name: str
    trigger: float
    x: float
    y: float
    z: float
    t: float
    vx: float = 0.0
    vy: float = 0.0
    key: int | None = None  # the entity's global id, as the client lists it
    mapped: bool = True  # whether its trigger radius is a patrol's in the zone's map, not DEFAULT_TRIGGER


class MobWatch:
    """Every mob's last position and velocity, kept current in the background."""

    def __init__(self, read: MobRead, triggers: dict[str, float], clock: Callable[[], float] = time.monotonic) -> None:
        self._read = read
        self.triggers = triggers
        self.clock = clock
        self.mobs: dict[int, Mob] = {}
        self.errors = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.wait([task])

    async def _run(self) -> None:
        while True:
            try:
                seen = await self._read()
            except Exception:  # noqa: BLE001 - one failed read; the next may not
                self.errors += 1
            else:
                now = self.clock()
                for key, name, (x, y, z) in seen:
                    self._update(key, name, x, y, z, now)
                keys = {key for key, _, _ in seen}
                for key in [k for k in self.mobs if k not in keys]:
                    del self.mobs[key]
            await asyncio.sleep(WALK_MOB_POLL)

    def _update(self, key: int, name: str, x: float, y: float, z: float, t: float) -> None:
        mob = self.mobs.get(key)
        if mob is None:
            trigger = self.triggers.get(name)
            mapped = trigger is not None
            self.mobs[key] = Mob(name, trigger if mapped else DEFAULT_TRIGGER, x, y, z, t, key=key, mapped=mapped)
            return
        gap = t - mob.t
        if gap <= 0:
            return
        vx, vy = (x - mob.x) / gap, (y - mob.y) / gap
        if math.hypot(vx, vy) > MOB_MAX_SPEED:
            vx = vy = 0.0  # moved too far to have walked: a respawn
        mob.vx += (vx - mob.vx) * MOB_SMOOTHING
        mob.vy += (vy - mob.vy) * MOB_SMOOTHING
        mob.x, mob.y, mob.z, mob.t = x, y, z, t

    def near(self, position: Point) -> list[Mob]:
        x, y, z = position
        return [
            m for m in self.mobs.values() if math.dist((m.x, m.y), (x, y)) <= MOB_RANGE and abs(m.z - z) <= MOB_LEVEL
        ]


def along(route: Sequence[Point], target: int, x: float, y: float, distance: float) -> tuple[float, float]:
    """Where a wizard at (x, y), heading for route[target], is after walking *distance* along it."""
    px, py = x, y
    for k in range(target, len(route)):
        tx, ty = route[k][0], route[k][1]
        leg = math.dist((px, py), (tx, ty))
        if leg >= distance:
            f = distance / leg if leg else 0.0
            return px + (tx - px) * f, py + (ty - py) * f
        distance -= leg
        px, py = tx, ty
    return px, py


@dataclass(frozen=True)
class Margin:
    """The room a walk wants beyond each mob's trigger radius: *fixed*, *per_second* more for every
    second a guess looks ahead, and *slack* more to go on once stopped."""

    fixed: float
    per_second: float
    slack: float


#: The room a walk keeps from mobs, and what it keeps once held up for WALK_MOB_EASE.
FULL = Margin(MARGIN, MARGIN_PER_SECOND, RESUME_SLACK)
NARROW = Margin(NARROW_MARGIN, 0.0, 0.0)

#: How much room a walk keeps from mobs, in the order a spell of waits goes through them: the
#: FULL margin; the NARROW one once held up for WALK_MOB_EASE; and past WALK_MOB_WAIT none, the
#: walk going on unwatched, since waiting longer for a loop of mobs that never clears gets
#: nowhere, while a fight it meets is ambient combat's. Mobs leaving it room at the full margin
#: for WALK_MOB_CLEAR start the spell over.
FULL_ROOM, NARROW_ROOM, RUN_THROUGH = "full", "narrow", "through"
_LEVELS = (FULL_ROOM, NARROW_ROOM, RUN_THROUGH)


def room(path: Sequence[tuple[float, float]], mobs: Sequence[Mob], now: float, margin: Margin = FULL) -> float:
    """The least room between the wizard, at path[k] LOOK_AHEAD[k] seconds from now, and any mob
    carried along its velocity, beyond that mob's trigger radius and the margin."""
    return tightest(path, mobs, now, margin)[0]


def tightest(
    path: Sequence[tuple[float, float]], mobs: Sequence[Mob], now: float, margin: Margin = FULL
) -> tuple[float, Mob | None, float]:
    """:func:`room`, with the mob that leaves it and how many seconds ahead it does; no mob
    when there are none."""
    least, by, when = math.inf, None, 0.0
    for tau, (wx, wy) in zip(LOOK_AHEAD, path):
        for m in mobs:
            ahead = tau + (now - m.t)
            mx, my = m.x + m.vx * ahead, m.y + m.vy * ahead
            left = math.dist((wx, wy), (mx, my)) - m.trigger - margin.fixed - margin.per_second * tau
            if left < least:
                least, by, when = left, m, tau
    return least, by, when


def held_by(mob: Mob, here: Point, when: float, watched: int) -> str:
    """For a log: the mob that leaves a walk the least room, seen from *here*, and how many mobs
    it watches. *when* is how many seconds ahead the room is least."""
    off = math.dist((mob.x, mob.y), here[:2])
    mapped = "" if mob.mapped else ", no patrol in the map"
    return (
        f"{mob.name} (trigger {mob.trigger:.0f}{mapped}) at ({mob.x:.0f}, {mob.y:.0f}), {off:.0f} units off,"
        f" height {mob.z - here[2]:+.0f}, moving {math.hypot(mob.vx, mob.vy):.0f} units a second, least room"
        f" {when:.1f} s ahead; {_mobs(watched)} watched"
    )


def _mobs(n: int) -> str:
    return f"{n} mob" if n == 1 else f"{n} mobs"


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def mob_discs(mobs: Sequence[Mob], now: float, margin: Margin = FULL) -> list[tuple[float, float, float]]:
    """(x, y, radius) to close around each mob: where it will be now, halfway through the look
    ahead and at its end, each as wide as :func:`room` asks for then, plus :data:`DETOUR_SLACK`."""
    discs = []
    for m in mobs:
        for tau in (0.0, WALK_LOOK_AHEAD / 2, WALK_LOOK_AHEAD):
            ahead = now - m.t + tau
            radius = m.trigger + margin.fixed + margin.per_second * tau + DETOUR_SLACK
            discs.append((m.x + m.vx * ahead, m.y + m.vy * ahead, radius))
    return discs


def choose(go: float, wait: float, back: float, waiting: bool, margin: Margin = FULL) -> str:
    """Go on, wait, or back off, from the room each leaves (:func:`room`)."""
    if go >= (margin.slack if waiting else 0.0):
        return "go"
    return "wait" if wait >= back or wait >= 0 else "back"


def rejoin(route: Sequence[Point], target: int, here: Point, discs: Sequence[tuple[float, float, float]]) -> int:
    """The waypoint of *route* a detour plans to: the first from *target* on that is at least
    :data:`DETOUR_REACH` along the route from *here*, beyond every disc the route passes through,
    and outside every disc; else its last."""
    path = [(here[0], here[1])] + [(p[0], p[1]) for p in route[target:]]
    walked = [0.0]
    for a, b in zip(path, path[1:]):
        walked.append(walked[-1] + math.dist(a, b))
    clear = DETOUR_REACH
    for dx, dy, r in discs:
        for k, (a, b) in enumerate(zip(path, path[1:])):
            off, t = _segment_distance((dx, dy, 0.0), (*a, 0.0), (*b, 0.0))
            if off <= r:
                clear = max(clear, walked[k] + t * (walked[k + 1] - walked[k]) + r)
    for k in range(1, len(path)):
        x, y = path[k]
        if walked[k] >= clear and all(math.dist((x, y), (dx, dy)) > r for dx, dy, r in discs):
            return target + k - 1
    return len(route) - 1


# --- the walk ------------------------------------------------------------------------------


class _Ended(Exception):  # noqa: N818 - a way out of the tick loop, not an error
    def __init__(self, end: WalkEnd, detail: str = "") -> None:
        super().__init__(end.value)
        self.end, self.detail = end, detail


class _Refresh:
    """Passes over a client's entity list every :data:`~bartleby.runtime.timing.WALK_MOB_REFRESH`,
    for a walk's gates when it watches no mobs (a :class:`MobWatch` makes them when it does)."""

    def __init__(self, read: ClientRead) -> None:
        self._read = read
        self.errors = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.wait([task])

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(WALK_MOB_REFRESH)
            try:
                await self._read.refresh()
            except Exception:  # noqa: BLE001 - one failed pass; the next may not
                self.errors += 1


class RouteWalker:
    """Walks one client to points in its zone, on the planner it is given.

    *avoid* plans around the patrols (the Avoid Mobs option); *watch_mobs* watches the live ones,
    which a walk detours around or waits for, and False walks past them unwatched. Either way it
    reads the client's entity list for the zone's gates (:class:`GateRead`), on the one pass the
    mobs are read on; *read* is that list's reader, the client's own unless one is given. The
    planner is the walker's own: a walk closes the gates it finds there and spots it stalls on,
    and drops links that move nobody, on it.
    """

    def __init__(
        self,
        client,
        planner: ZonePlanner,
        *,
        avoid: bool = True,
        watch_mobs: bool = False,
        read: ClientRead | None = None,
        label: str = "",
        on_tick: Callable[[WalkTick], None] | None = None,
    ) -> None:
        self.client = client
        self.planner = planner
        self.avoid = avoid
        self.watch_mobs = watch_mobs
        self.entities = read or ClientRead(client)
        self.gates = GateRead(planner)
        self.label = label or "a walk"
        self.on_tick = on_tick
        self._seen = 0  # the passes over the entity list acted on
        self._fight: Point | None = None  # the goal of a walk into a fight, while one is walked
        self._spare: frozenset[int] = frozenset()  # the mobs that walk means to meet, by key
        self._detour_failed: str | None = None  # the kind of reason no detour was taken, last logged

    async def _read(self) -> tuple[Point, float]:
        pos = await self.client.body.position()
        return (pos.x, pos.y, pos.z), await self.client.body.yaw()

    async def _expected_speed(self) -> float:
        """Units a second the wizard walks at: its own, by its speed field, which a walk never writes."""
        try:
            field_value = await self.client.client_object.speed_multiplier()
        except Exception:  # noqa: BLE001 - unreadable just now: plan on foot
            return WIZARD_SPEED
        # A logged-out character's field is freed memory; nothing real is past 2.0x.
        return WIZARD_SPEED * (1 + field_value / 100) if 0 <= field_value <= 100 else WIZARD_SPEED

    async def walk(self, lease: Lease, goal: Point, *, fight: bool = False, spare: Sequence[int] = ()) -> WalkResult:
        """Walk to *goal* through *lease*'s input, from where the wizard stands.

        *fight* makes it a walk into a fight at *goal*: the patrols of the mobs there cost its plan
        nothing, and the watch leaves out the mobs in *spare* (their keys, the entities' global ids)
        and any whose trigger radius comes within FIGHT_REACH of *goal*. It ends FIGHT once the
        wizard is in battle, as any walk does."""
        self._fight = tuple(goal) if fight else None
        self._spare = frozenset(spare) if fight else frozenset()
        self._detour_failed = None
        try:
            return await self._walk_to(lease, goal)
        finally:
            self._fight, self._spare = None, frozenset()

    async def _walk_to(self, lease: Lease, goal: Point) -> WalkResult:
        started = time.monotonic()
        position, _ = await self._read()
        zone = await self.client.zone_name()
        result = WalkResult(WalkEnd.NO_ROUTE, zone, position, tuple(goal))
        await self._first_read(result)
        watch = None
        if self.watch_mobs:
            watch = MobWatch(self.entities.mobs, {p.template: p.proximity for p in self.planner.patrols})
        else:
            watch = _Refresh(self.entities)
        watch.start()
        try:
            await self._walk(lease, result, watch if self.watch_mobs else None, started)
        except _Ended as ended:
            result.end, result.detail = ended.end, ended.detail
        finally:
            # Each in a finally of its own: a second cancel (a preempt, then Stop all) landing in
            # the release would otherwise leave the watch reading mobs for good.
            try:
                await lease.input.release_all()
            finally:
                try:
                    await watch.stop()
                finally:
                    result.seconds = time.monotonic() - started
        try:
            result.position, _ = await self._read()
        except Exception:  # noqa: BLE001 - a closed client: where it stopped is not known
            result.position = result.trail[-1] if result.trail else None
        log = logger.info if result.arrived else logger.warning
        log("%s: %s", self.label, result.describe())
        return result

    async def _first_read(self, result: WalkResult) -> None:
        """Read the gates before the first plan, so a gate in view never costs a replan. A list
        that cannot be read leaves every gate as it is, and the walk goes on."""
        try:
            listing = await self.entities.refresh()
        except Exception:  # noqa: BLE001 - the next pass may read it; until then gates stay open
            logger.debug("%s: the entity list could not be read", self.label, exc_info=True)
            return
        self._seen = self.entities.passes
        self._gate_pass(result, listing)

    def _gate_pass(self, result: WalkResult, listing: Listing) -> tuple[list[Gate], list[Gate]]:
        """Act on one pass's gates, and note them on *result*: the gates closed and opened."""
        closed, opened = self.gates.update(listing)
        for gate in closed:
            result.gates_seen.append(gate.name)
            logger.info("%s: the client lists the gate %s: it is closed", self.label, gate.name)
        for gate in opened:
            result.gates_opened.append(gate.name)
            logger.info("%s: the gate %s the client listed is gone: it is open again", self.label, gate.name)
        return closed, opened

    async def _plan(
        self, result: WalkResult, start: Point, goal: Point, planner: ZonePlanner | None = None
    ) -> Route | None:
        """A route, planned in a worker thread: it takes seconds in a big zone."""
        planner = planner or self.planner
        fight = self._fight is not None and tuple(goal) == self._fight
        began = time.monotonic()
        try:
            return await asyncio.to_thread(planner.route, start, goal, self.avoid, fight)
        finally:
            result.planning += time.monotonic() - began

    async def _why_not(self, result: WalkResult, start: Point, goal: Point) -> NoRoute:
        """Why there is no route, kept on *result*: planned in a worker thread, since it plans
        again once for each gate closed."""
        began = time.monotonic()
        try:
            fight = self._fight is not None and tuple(goal) == self._fight
            result.no_route = await asyncio.to_thread(self.planner.no_route, start, goal, self.avoid, fight)
        finally:
            result.planning += time.monotonic() - began
        return result.no_route

    async def _warm_up(self) -> None:
        """Make the walk's first lookups while its first route is planned, not standing after it.
        In a fresh process the first ``in_battle`` runs wizwalker's scan for the duel manager (about
        0.5 s) and the first key held reads the account's bindings (about 70 ms); each is kept for
        the process's life. A failure is left to the walk's own call to meet."""
        try:
            await self.client.in_battle()  # for the lookup only: the game lets no fight start a walk
        except Exception:  # noqa: BLE001 - the walk's own state poll raises it, as it would have
            logger.debug("%s: the first fight check failed", self.label, exc_info=True)
        try:
            await game_keys.key_map(self.client)
        except Exception:  # noqa: BLE001 - the first key held raises it, as it would have
            logger.debug("%s: the first read of the key bindings failed", self.label, exc_info=True)

    async def _legs(self, result: WalkResult, route: Route, here: Point) -> list[Leg]:
        """*route*'s legs, the first starting from *here*."""
        legs = await asyncio.to_thread(self.planner.legs, route)
        result.routes.append([p for leg in legs for p in leg.waypoints])
        first = list(legs[0].waypoints)
        first[0] = (here[0], here[1], first[0][2])
        return [Leg(first, legs[0].link), *legs[1:]]

    async def _walk(self, lease: Lease, result: WalkResult, watch: MobWatch | None, started: float) -> None:  # noqa: C901, PLR0912, PLR0915 - the probe's one loop, kept whole
        keys = lease.input
        goal = result.goal
        expected = await self._expected_speed()
        jump_speed = max(JUMP_SPEED, 2.0 * expected)

        here = result.start
        route, _ = await asyncio.gather(self._plan(result, here, goal), self._warm_up())
        if route is None:
            why = await self._why_not(result, here, goal)
            raise _Ended(WalkEnd.NO_ROUTE, why.describe())
        legs = await self._legs(result, route, here)
        # The leg being walked, its link, and the legs after it, which a replan after the link plans afresh.
        waypoints, link, later = legs[0].waypoints, legs[0].link, legs[1:]
        target = 1

        history: list[tuple[float, float, float]] = []  # (t, x, y) since forward was last held
        forward_since = math.inf
        last, last_t = here, 0.0
        last_poll = -math.inf
        mode, mode_since = "go", 0.0
        held_key: int | None = None  # the mob a wait was last logged as held by
        # How much room it keeps from mobs (_LEVELS), from what held_up() was when this spell of
        # waits began, and since when the mobs have left it room at the full margin, which ends it.
        level, spell_from, clear_since = FULL_ROOM, 0.0, None
        last_detour = -math.inf
        speeds: list[float] = []
        budget = deadline = 0.0

        def clock() -> float:
            return time.monotonic() - started

        def held_up(now: float) -> float:
            """Seconds stood or backed off for mobs, the one under way included."""
            return result.waited + (now - mode_since if mode != "go" else 0.0)

        def give_time(planned: Sequence[Leg]) -> None:
            """Give the walk *planned*'s time from now, and beyond it whatever it waits for mobs
            and for the planner from now on: a route replanned may be much longer than the first."""
            nonlocal budget, deadline
            now = clock()
            budget = _time_budget(planned, expected)
            deadline = now + budget - held_up(now) - result.planning

        give_time(legs)
        # What no replan or detour renews: a multiple of the first plan's time, from the start.
        ceiling = WALK_CEILING_FACTOR * budget

        async def stand() -> None:
            nonlocal forward_since
            await keys.release(GameAction.MOVE_FORWARD)
            await keys.release(GameAction.MOVE_BACKWARD)
            forward_since = math.inf

        async def check_state(now: float) -> None:
            """End the walk if a fight has started or the zone has changed."""
            nonlocal last_poll
            last_poll = now
            if await self.client.in_battle():
                raise _Ended(WalkEnd.FIGHT)
            if await self.client.zone_name() != result.zone:
                raise _Ended(WalkEnd.ZONE_CHANGED)

        async def replan(at: Point, why: str) -> None:
            """Plan again from *at*, standing meanwhile."""
            nonlocal waypoints, link, later, target, last, last_t
            await stand()
            again = await self._plan(result, at, goal)
            if again is None:
                reason = await self._why_not(result, at, goal)
                raise _Ended(
                    WalkEnd.NO_ROUTE, f"no route on from ({at[0]:.0f}, {at[1]:.0f}) {why}: {reason.describe()}"
                )
            result.replans += 1
            position, _ = await self._read()
            planned = await self._legs(result, again, position)
            waypoints, link, later = planned[0].waypoints, planned[0].link, planned[1:]
            target = 1
            history.clear()
            last, last_t = position, clock()
            give_time(planned)

        try:
            while True:
                now = clock()
                here, yaw = await self._read()
                x, y, z = here
                result.trail.append(here)
                gap = max(now - last_t, WALK_TICK)
                step = flat_distance(last, here)

                # A jump: a link taken at a volume's edge, a reset, or something unplanned.
                if step > jump_speed * gap:
                    if link is not None and flat_distance(here, link.target) <= LINK_LANDED:
                        result.links_taken += 1
                        logger.info("%s: %s moved it on the way in, %.0f units", self.label, link.name, step)
                        await replan(here, f"where {link.name} landed")
                        continue
                    # A zone's exit and a fight's start move a wizard too, and this zone's planner
                    # knows nothing of where either puts it: ask before planning on from there.
                    await check_state(now)
                    if _off_leg(waypoints, here) > REWALK:
                        logger.info(
                            "%s: moved %.0f units off its route, to (%.0f, %.0f): replanning", self.label, step, x, y
                        )
                        await replan(here, "where it was moved to")
                        continue
                    target = _next_ahead(waypoints, here)
                    logger.debug("%s: put back %.0f units on its route: walking it again", self.label, step)
                elif forward_since <= last_t:
                    speeds.append(step / gap)
                last, last_t = here, now
                history.append((now, x, y))

                if now - last_poll >= WALK_STATE_POLL:
                    await check_state(now)
                if now - held_up(now) > ceiling:  # planning counts: a replan loop is bounded too
                    raise _Ended(
                        WalkEnd.TIMED_OUT,
                        f"past its ceiling of {ceiling:.0f} s, {WALK_CEILING_FACTOR:g} times its first plan's",
                    )
                if now > deadline + held_up(now) + result.planning:
                    raise _Ended(WalkEnd.TIMED_OUT, f"{budget:.0f} s planned for")
                # Held up long by mobs: keep less room from them, and then walk on past them.
                held = held_up(now) - spell_from
                wanted = RUN_THROUGH if held > WALK_MOB_WAIT else NARROW_ROOM if held > WALK_MOB_EASE else FULL_ROOM
                if _LEVELS.index(wanted) > _LEVELS.index(level):
                    level = wanted
                    if level == NARROW_ROOM:
                        result.eased += 1
                        logger.info(
                            "%s: held up by mobs for %.1f s: keeping only %.0f beyond each trigger radius",
                            self.label,
                            held,
                            NARROW_MARGIN,
                        )
                    else:
                        result.ran_through += 1
                        last_by = f", last held by {result.held_by}" if result.held_by else ""
                        logger.warning(
                            "%s: held up by mobs for %.1f s: walking on past them unwatched until they leave it room%s",
                            self.label,
                            held,
                            last_by,
                        )

                # A new pass over the entity list: close the gates it lists, open those it shows gone,
                # and replan when one closed is on the way left, or one opened.
                if self.entities.passes != self._seen and self.entities.latest is not None:
                    self._seen = self.entities.passes
                    closed, opened = self._gate_pass(result, self.entities.latest)
                    ahead = [[here, *waypoints[target:]], *([*leg.waypoints] for leg in later)]
                    on_way = [g for g in closed if any(self.planner.crosses(g, path) for path in ahead)]
                    if on_way or opened:
                        why = f"around the gate {on_way[0].name}" if on_way else f"past the gate {opened[0].name}"
                        await replan(here, why)
                        continue

                target = _passed(waypoints, target, here)
                if target >= len(waypoints) and link is not None:
                    await stand()
                    if await self._take(keys, link, here):
                        result.links_taken += 1
                        landed, _ = await self._read()
                        await replan(landed, f"where {link.name} landed")
                    else:
                        result.links_dropped += 1
                        logger.info("%s: %s moved nobody in %.0f s: dropping it", self.label, link.name, WALK_LINK_WAIT)
                        self.planner.drop(link)
                        await replan(here, f"without {link.name}")
                    continue
                if target >= len(waypoints):
                    await stand()
                    await asyncio.sleep(WALK_SETTLE)
                    raise _Ended(WalkEnd.ARRIVED)

                want = heading_to(here, waypoints[target])
                error = wrap(want - yaw)

                # Mobs: go on, detour, wait, or back off, by the room each leaves.
                go_room = math.inf
                choice = "go"
                holder: Mob | None = None  # the mob that leaves the least room ahead
                mobs = self._watched(watch, here) if watch is not None else []
                full_room, when = math.inf, 0.0
                if mobs:
                    speed = statistics.median(speeds[-20:]) if len(speeds) >= 10 else expected
                    ahead = [along(waypoints, target, x, y, speed * tau) for tau in LOOK_AHEAD]
                    full_room, holder, when = tightest(ahead, mobs, watch.clock())
                # Room at the full margin for WALK_MOB_CLEAR ends a spell of waits: they start over.
                # Waiting, room is what it would go on with, the slack too: less, counted as clear,
                # would start the spell over while it stood, and it would never ease.
                if full_room < (FULL.slack if mode != "go" else 0.0):
                    clear_since = None
                elif clear_since is None:
                    clear_since = now
                elif now - clear_since >= WALK_MOB_CLEAR:
                    spell_from = held_up(now)
                    if level != FULL_ROOM:
                        logger.info(
                            "%s: the mobs have left it room for %.1f s: keeping the full room again",
                            self.label,
                            now - clear_since,
                        )
                        level = FULL_ROOM
                if mobs and level != RUN_THROUGH:
                    margin = FULL if level == FULL_ROOM else NARROW
                    go_room = full_room
                    if margin is not FULL:
                        go_room, holder, when = tightest(ahead, mobs, watch.clock(), margin)
                    enough = margin.slack if mode != "go" else 0.0
                    if go_room < enough and now - last_detour >= WALK_DETOUR_EVERY:
                        await stand()
                        sought = now
                        detour = await self._detour(result, waypoints, target, mobs, watch, speed, enough, margin)
                        # It stood while it planned, for as long as that took: the clock, the last
                        # read and the stall window go on from now, the next detour is sought a
                        # while after this one ended, and the planning, which result.planning has,
                        # is not counted as a wait for mobs as well.
                        now = last_detour = clock()
                        if mode != "go":
                            mode_since += now - sought
                        here, yaw = await self._read()
                        x, y, z = here
                        last, last_t = here, now
                        history[:] = [(now, x, y)]
                        if detour is not None:
                            waypoints, go_room = detour
                            target = 1
                            give_time([Leg(waypoints, link), *later])
                        want = heading_to(here, waypoints[target])
                        error = wrap(want - yaw)
                    if go_room < enough:
                        t_mob = watch.clock()
                        fx, fy = facing(want)
                        back_speed = speed * BACK_SPEED_FACTOR
                        backing = [(x - fx * back_speed * tau, y - fy * back_speed * tau) for tau in LOOK_AHEAD]
                        standing = [(x, y)] * len(LOOK_AHEAD)
                        stood = room(standing, mobs, t_mob, margin)
                        choice = choose(go_room, stood, room(backing, mobs, t_mob, margin), mode != "go", margin)
                        if choice != "go":
                            result.least_room = min(result.least_room, stood)
                    if choice == "go":
                        result.least_room = min(result.least_room, go_room)
                elif mobs:
                    go_room = full_room  # walking on unwatched: whatever the room, it goes
                holding = ""
                if choice != "go" and holder is not None:
                    holding = result.held_by = held_by(holder, here, when, len(mobs))
                if choice != mode:
                    if mode != "go":
                        result.waited += now - mode_since
                    if choice != "go" or mode != "go":
                        logger.info(
                            "%s: %s -> %s at (%.0f, %.0f), room ahead %.0f%s",
                            self.label,
                            mode,
                            choice,
                            x,
                            y,
                            go_room,
                            f", held by {holding}" if holding else "",
                        )
                    mode, mode_since = choice, now
                    if mode == "go":
                        self._detour_failed = None
                elif holding and holder.key != held_key:
                    logger.info("%s: still %s at (%.0f, %.0f), now held by %s", self.label, mode, x, y, holding)
                held_key = holder.key if holding else None

                if abs(error) > DEADBAND:
                    await keys.write_yaw(want)
                if mode == "go":
                    await keys.release(GameAction.MOVE_BACKWARD)
                    if await keys.hold(GameAction.MOVE_FORWARD):
                        # Held afresh: by this walk, or again after the watchdog let it go.
                        forward_since = now
                        history[:] = [(now, x, y)]
                else:
                    await keys.release(GameAction.MOVE_FORWARD)
                    forward_since = math.inf
                    if mode == "back":
                        await keys.hold(GameAction.MOVE_BACKWARD)
                    else:
                        await keys.release(GameAction.MOVE_BACKWARD)
                if GameAction.MOVE_FORWARD not in keys.held:
                    forward_since = math.inf

                if self.on_tick is not None:
                    self.on_tick(WalkTick(now, here, yaw, target, keys.held, mode, go_room))

                # Stalled: forward held across the whole window, and the wizard barely moved.
                while len(history) > 1 and history[1][0] <= now - WALK_STALL_WINDOW:
                    history.pop(0)
                window_start, sx, sy = history[0]
                if (
                    forward_since <= window_start <= now - WALK_STALL_WINDOW
                    and math.dist((sx, sy), (x, y)) < STALL_MIN_MOVE
                ):
                    result.recoveries += 1
                    if result.recoveries > MAX_RECOVERIES:
                        raise _Ended(
                            WalkEnd.BLOCKED, f"stalled at ({x:.0f}, {y:.0f}) after {MAX_RECOVERIES} recoveries"
                        )
                    logger.info(
                        "%s: stalled at (%.0f, %.0f): backing off (%d of %d)",
                        self.label,
                        x,
                        y,
                        result.recoveries,
                        MAX_RECOVERIES,
                    )
                    await self._back_off(keys)
                    fx, fy = facing(yaw)
                    # A gate the client shows gone is not what stopped it: the floor ahead is blocked.
                    gates = [g for g in self.planner.gates_near(x, y, z, GATE_REACH) if g not in self.gates.gone]
                    if gates:
                        self.planner.close(gates[0])
                        result.gates_closed.append(gates[0].name)
                        logger.info("%s: stalled at the gate %s: it is closed", self.label, gates[0].name)
                    else:
                        self.planner.block(x + fx * BLOCK_AHEAD, y + fy * BLOCK_AHEAD, z, BLOCK_RADIUS)
                    backed, _ = await self._read()
                    await replan(backed, f"past the gate {gates[0].name}" if gates else "after a stall")
                    continue

                await asyncio.sleep(WALK_TICK)
        finally:
            if mode != "go":  # standing or backing off for mobs when it ended
                result.waited += clock() - mode_since

    def _watched(self, watch: MobWatch, here: Point) -> list[Mob]:
        """The mobs near *here* that this walk keeps clear of: all of them, but on a walk into a
        fight, the ones it means to meet."""
        mobs = watch.near(here)
        if self._fight is None:
            return mobs
        fx, fy = self._fight[0], self._fight[1]
        return [
            m for m in mobs if m.key not in self._spare and math.dist((m.x, m.y), (fx, fy)) - m.trigger > FIGHT_REACH
        ]

    async def _back_off(self, keys) -> None:
        """Back off by distance, not time: the backward key walks near forward speed."""
        await keys.release(GameAction.MOVE_FORWARD)
        start, _ = await self._read()
        await keys.hold(GameAction.MOVE_BACKWARD)
        began = time.monotonic()
        try:
            while time.monotonic() - began < WALK_BACK_OFF_LONGEST:
                await asyncio.sleep(WALK_TICK)
                now, _ = await self._read()
                if flat_distance(start, now) >= BACK_OFF:
                    break
        finally:
            await keys.release(GameAction.MOVE_BACKWARD)

    async def _detour(self, result, waypoints, target, mobs, watch, speed, enough, margin: Margin = FULL):
        """A way around *mobs* to a point a short way along the route, and the room it leaves at
        *margin*, or None. The rest of the route follows it unchanged. Sought standing: the walk
        lets go of forward and backward first, so a wizard backing off is not still backing
        meanwhile."""
        discs = mob_discs(mobs, watch.clock(), margin)
        here, _ = await self._read()
        k = rejoin(waypoints, target, here, discs)
        around = self.planner.around(discs)
        route = await self._plan(result, here, waypoints[k], around)
        to = waypoints[k]
        if route is None:
            inside = sum(1 for m in mobs if math.dist((m.x, m.y), here[:2]) <= m.trigger + margin.fixed + DETOUR_SLACK)
            standing = f"; it stands inside the room closed around {inside} of them" if inside else ""
            self._no_detour(
                "no route",
                f"no way around {_mobs(len(mobs))} to ({to[0]:.0f}, {to[1]:.0f}), {flat_distance(here, to):.0f} units"
                f" off{standing}",
            )
            return None
        legs = await asyncio.to_thread(around.legs, route)
        if len(legs) != 1:
            self._no_detour(
                "link", f"the only way around {_mobs(len(mobs))} to ({to[0]:.0f}, {to[1]:.0f}) takes a link"
            )
            return None  # a detour by a link is no detour
        new = list(legs[0].waypoints)
        new[0] = (here[0], here[1], new[0][2])
        new += list(waypoints[k + 1 :])
        new_room, holder, when = tightest(
            [along(new, 1, here[0], here[1], speed * tau) for tau in LOOK_AHEAD], mobs, watch.clock(), margin
        )
        if new_room < enough:
            self._no_detour(
                "room",
                f"the way around leaves room {new_room:.0f}, under the {enough:.0f} wanted, held by"
                f" {held_by(holder, here, when, len(mobs))}",
            )
            return None
        self._detour_failed = None
        result.detours += 1
        result.routes.append(new)
        logger.info(
            "%s: detour around %d mobs at (%.0f, %.0f), room ahead %.0f",
            self.label,
            len(mobs),
            here[0],
            here[1],
            new_room,
        )
        return new, new_room

    def _no_detour(self, kind: str, why: str) -> None:
        """Log why no detour was taken: at INFO when the reason is of another *kind* than the one
        before it, since the walk last went on or took a detour, else at DEBUG, since a detour is
        sought every :data:`~bartleby.runtime.timing.WALK_DETOUR_EVERY` while the walk waits."""
        log = logger.info if kind != self._detour_failed else logger.debug
        self._detour_failed = kind
        log("%s: no detour: %s", self.label, why)

    async def _take(self, keys, link: Link, here: Point) -> bool:
        """Stand in a volume, or press an object's link, until moved; whether it moved the wizard."""
        pressed = link.kind != "volume"
        began = time.monotonic()
        last_press = math.inf
        if pressed:
            await keys.write_yaw(heading_to(here, link.source))
            shown = time.monotonic()
            while time.monotonic() - shown < WALK_PROMPT_WAIT:
                if await self.client.is_in_npc_range():
                    break
                await asyncio.sleep(WALK_TICK)
            await keys.press(GameAction.INTERACT)
            began = last_press = time.monotonic()
        while time.monotonic() - began < WALK_LINK_WAIT:
            await asyncio.sleep(WALK_TICK)
            if pressed and time.monotonic() - last_press >= WALK_PRESS_EVERY:
                await keys.press(GameAction.INTERACT)
                last_press = time.monotonic()
            now, _ = await self._read()
            if math.dist(here, now) > LINK_MOVED:
                if pressed:
                    await self._settle()
                logger.info("%s: %s moved it %.0f units", self.label, link.name, math.dist(here, now))
                return True
        return False

    async def _settle(self) -> None:
        """Wait for a pressed link to set the wizard down: at once for a teleport, after a ride."""
        began = time.monotonic()
        recent: list[tuple[float, Point]] = []
        while time.monotonic() - began < WALK_LANDING_WAIT:
            here, _ = await self._read()
            t = time.monotonic() - began
            recent = [(s, p) for s, p in recent if s >= t - WALK_LANDED_FOR - WALK_TICK] + [(t, here)]
            if t >= WALK_LANDED_FOR and math.dist(recent[0][1], here) < SETTLED_MOVE:
                return
            await asyncio.sleep(WALK_TICK)


def _passed(route: Sequence[Point], target: int, here: Point) -> int:
    """The waypoint to head for next: each passed within ARRIVE_TURN, or once past it along its
    leg; the last only within ARRIVE_GOAL."""
    x, y, _ = here
    while target < len(route):
        tx, ty, _ = route[target]
        last = target == len(route) - 1
        if math.dist((x, y), (tx, ty)) < (ARRIVE_GOAL if last else ARRIVE_TURN):
            target += 1
            continue
        px, py, _ = route[target - 1]
        dx, dy = tx - px, ty - py
        if not last and ((x - px) * dx + (y - py) * dy) / ((dx * dx + dy * dy) or 1.0) >= 1.0:
            target += 1
            continue
        break
    return target


def _segment_distance(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """(distance from p to the segment a-b, how far along it the nearest point is, 0 to 1)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if not length2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy)), t


def _off_leg(route: Sequence[Point], here: Point) -> float:
    """Units from *here* to the nearest point of the leg being walked."""
    if len(route) < 2:
        return flat_distance(route[0], here) if route else math.inf
    return min(_segment_distance(here, a, b)[0] for a, b in zip(route, route[1:]))


def _next_ahead(route: Sequence[Point], here: Point) -> int:
    """After a reset onto the leg: the waypoint ending the stretch nearest *here*. A leg of one
    waypoint has no stretch, and is walked once at it."""
    if len(route) < 2:
        return 1
    return min(range(1, len(route)), key=lambda k: _segment_distance(here, route[k - 1], route[k])[0])


def _time_budget(legs: Sequence[Leg], speed: float) -> float:
    """Seconds a walk is given for *legs* at *speed* units a second: three times their walking,
    each link's wait and ride, and :data:`~bartleby.runtime.timing.WALK_TIME_ALLOWANCE`."""
    walked = sum(flat_distance(a, b) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
    links = sum(WALK_LINK_WAIT + leg.link.seconds for leg in legs if leg.link)
    return walked / speed * 3 + WALK_TIME_ALLOWANCE + links

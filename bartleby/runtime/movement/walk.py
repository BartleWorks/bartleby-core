"""Walking a planned route with the account's own keys, on a movement lease.

:class:`RouteWalker` walks one wizard to a point in its zone, as BartleBot's
``tools/probe_walk_input.py`` first did, whose walk this is (core's ``docs/WALKING.md``,
*Walking a route*, has what each number was measured against):

* **It plans from where the wizard stands**, on the :class:`~bartleby.runtime.movement.route.ZonePlanner`
  it is given, and follows each leg: forward held, the yaw written toward the next waypoint every
  :data:`~bartleby.runtime.timing.WALK_TICK`, a waypoint passed within :data:`ARRIVE_TURN` or once
  past it along its leg, the goal reached within :data:`ARRIVE_GOAL`.
* **Planning never runs on the event loop.** A long route takes about 5 s to plan in the Outback
  and 1 to 1.5 s in Triton Avenue, so every plan and replan runs in a worker thread, with forward
  let go meanwhile.
* **Resets are re-walked.** The position is read back every tick, so a wizard the server put back
  on the stretch it walked walks it again. A jump that leaves it off the leg (a link it did not
  plan to take) is replanned from where it landed.
* **Stalls** on something the map doesn't know: back off :data:`BACK_OFF`, close the floor just
  ahead, and replan, up to :data:`MAX_RECOVERIES` times; the next stall ends the walk
  :attr:`WalkEnd.BLOCKED`.
* **Links**: at a leg's end it stands in the volume, or faces the object and presses Interact
  once the prompt shows, and again every :data:`~bartleby.runtime.timing.WALK_PRESS_EVERY`, until
  moved :data:`LINK_MOVED`. A link that moves nobody in :data:`~bartleby.runtime.timing.WALK_LINK_WAIT`
  is dropped and the route replanned.
* **Mobs**, when it is given a way to read them (:func:`client_mobs`): each tick it looks
  :data:`~bartleby.runtime.timing.WALK_LOOK_AHEAD` along its route and every mob along its
  velocity, and short of room, detours first, planning only to a point a short way along the
  route, then waits or backs off, :data:`~bartleby.runtime.timing.WALK_MOB_WAIT` at most in all.
* **No speed, and no position.** It never writes ``speed_multiplier``, so a wizard walks at
  whatever its field holds, until the user's go-ahead for a faster walk; and it never writes a
  position, so it has no hop to hand over.
* **A held event loop** lets go of its keys by :class:`~bartleby.runtime.input.ClientInput`'s
  watchdog; the walk finds forward no longer held and holds it again, aimed afresh.

It ends on the goal, no route, being blocked, a fight, a zone change or running out of time,
with a :class:`WalkResult` saying which, and lets go of every key however it ends. A cancel, or
the lease being lost, goes on up to the caller.
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

from bartleby.runtime.arbiter import Lease
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.movement.geometry import Point, facing, flat_distance, heading_to, wrap
from bartleby.runtime.movement.route import WIZARD_SPEED, Route, ZonePlanner
from bartleby.runtime.movement.zone_links import Link
from bartleby.runtime.timing import (
    WALK_BACK_OFF_LONGEST,
    WALK_DETOUR_EVERY,
    WALK_LANDED_FOR,
    WALK_LANDING_WAIT,
    WALK_LINK_WAIT,
    WALK_LOOK_AHEAD,
    WALK_LOOK_STEP,
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
    least_room: float = math.inf  # beyond a trigger radius and margin
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
        return f"{text}, {self.detail}" if self.detail else text


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


# --- mobs ----------------------------------------------------------------------------------

#: Every listed mob's key, template name and position, read afresh.
MobRead = Callable[[], Awaitable[list[tuple[int, str, Point]]]]


def client_mobs(client) -> MobRead:
    """A :data:`MobRead` for a game client: its mobs listed every
    :data:`~bartleby.runtime.timing.WALK_MOB_REFRESH` (the list reads every entity's behaviors),
    each one's position read on every call."""
    entities: dict[int, tuple[object, str]] = {}
    refreshed = -math.inf

    async def read() -> list[tuple[int, str, Point]]:
        nonlocal entities, refreshed
        if time.monotonic() - refreshed >= WALK_MOB_REFRESH:
            fresh = {}
            for entity in await client.get_mobs():
                try:
                    fresh[await entity.global_id_full()] = (entity, await entity.object_name())
                except Exception:  # noqa: BLE001 - an entity freed while listed is gone
                    continue
            entities, refreshed = fresh, time.monotonic()
        out = []
        for key, (entity, name) in list(entities.items()):
            try:
                loc = await entity.location()
            except Exception:  # noqa: BLE001 - freed since it was listed
                entities.pop(key, None)
                continue
            out.append((key, name, (loc.x, loc.y, loc.z)))
        return out

    return read


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
            self.mobs[key] = Mob(name, self.triggers.get(name, DEFAULT_TRIGGER), x, y, z, t)
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


def room(path: Sequence[tuple[float, float]], mobs: Sequence[Mob], now: float) -> float:
    """The least room between the wizard, at path[k] LOOK_AHEAD[k] seconds from now, and any mob
    carried along its velocity, beyond that mob's trigger radius and the margin."""
    least = math.inf
    for tau, (wx, wy) in zip(LOOK_AHEAD, path):
        for m in mobs:
            ahead = tau + (now - m.t)
            mx, my = m.x + m.vx * ahead, m.y + m.vy * ahead
            least = min(least, math.dist((wx, wy), (mx, my)) - m.trigger - MARGIN - MARGIN_PER_SECOND * tau)
    return least


def mob_discs(mobs: Sequence[Mob], now: float) -> list[tuple[float, float, float]]:
    """(x, y, radius) to close around each mob: where it will be now, halfway through the look
    ahead and at its end, each as wide as :func:`room` asks for then, plus :data:`DETOUR_SLACK`."""
    discs = []
    for m in mobs:
        for tau in (0.0, WALK_LOOK_AHEAD / 2, WALK_LOOK_AHEAD):
            ahead = now - m.t + tau
            radius = m.trigger + MARGIN + MARGIN_PER_SECOND * tau + DETOUR_SLACK
            discs.append((m.x + m.vx * ahead, m.y + m.vy * ahead, radius))
    return discs


def choose(go: float, wait: float, back: float, waiting: bool) -> str:
    """Go on, wait, or back off, from the room each leaves (:func:`room`)."""
    if go >= (RESUME_SLACK if waiting else 0.0):
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


class RouteWalker:
    """Walks one client to points in its zone, on the planner it is given.

    *avoid* plans around the patrols (the Avoid Mobs option); *mobs* reads the live ones, which
    a walk detours around or waits for, and None walks past them unwatched. The planner is the
    walker's own: a walk closes spots it stalls on and drops links that move nobody, on it.
    """

    def __init__(
        self,
        client,
        planner: ZonePlanner,
        *,
        avoid: bool = True,
        mobs: MobRead | None = None,
        label: str = "",
        on_tick: Callable[[WalkTick], None] | None = None,
    ) -> None:
        self.client = client
        self.planner = planner
        self.avoid = avoid
        self._read_mobs = mobs
        self.label = label or "a walk"
        self.on_tick = on_tick

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

    async def walk(self, lease: Lease, goal: Point) -> WalkResult:
        """Walk to *goal* through *lease*'s input, from where the wizard stands."""
        started = time.monotonic()
        position, _ = await self._read()
        zone = await self.client.zone_name()
        result = WalkResult(WalkEnd.NO_ROUTE, zone, position, tuple(goal))
        watch = None
        if self._read_mobs is not None:
            watch = MobWatch(self._read_mobs, {p.template: p.proximity for p in self.planner.patrols})
            watch.start()
        try:
            await self._walk(lease, result, watch, started)
        except _Ended as ended:
            result.end, result.detail = ended.end, ended.detail
        finally:
            await lease.input.release_all()
            if watch is not None:
                await watch.stop()
            result.seconds = time.monotonic() - started
        try:
            result.position, _ = await self._read()
        except Exception:  # noqa: BLE001 - a closed client: where it stopped is not known
            result.position = result.trail[-1] if result.trail else None
        log = logger.info if result.arrived else logger.warning
        log("%s: %s", self.label, result.describe())
        return result

    async def _plan(
        self, result: WalkResult, start: Point, goal: Point, planner: ZonePlanner | None = None
    ) -> Route | None:
        """A route, planned in a worker thread: it takes seconds in a big zone."""
        planner = planner or self.planner
        began = time.monotonic()
        try:
            return await asyncio.to_thread(planner.route, start, goal, self.avoid)
        finally:
            result.planning += time.monotonic() - began

    async def _first_leg(self, result: WalkResult, route: Route, here: Point) -> tuple[list[Point], Link | None]:
        legs = await asyncio.to_thread(self.planner.legs, route)
        result.routes.append([p for leg in legs for p in leg.waypoints])
        first = legs[0]
        waypoints = list(first.waypoints)
        waypoints[0] = (here[0], here[1], waypoints[0][2])
        return waypoints, first.link

    async def _walk(self, lease: Lease, result: WalkResult, watch: MobWatch | None, started: float) -> None:  # noqa: C901, PLR0912, PLR0915 - the probe's one loop, kept whole
        keys = lease.input
        goal = result.goal
        expected = await self._expected_speed()
        jump_speed = max(JUMP_SPEED, 2.0 * expected)

        here = result.start
        route = await self._plan(result, here, goal)
        if route is None:
            raise _Ended(WalkEnd.NO_ROUTE, "nowhere on the floor joins the two")
        legs = await asyncio.to_thread(self.planner.legs, route)
        result.routes.append([p for leg in legs for p in leg.waypoints])
        walked = sum(flat_distance(a, b) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
        budget = (
            walked / expected * 3
            + WALK_TIME_ALLOWANCE
            + sum(WALK_LINK_WAIT + leg.link.seconds for leg in legs if leg.link)
        )
        waypoints, link = list(legs[0].waypoints), legs[0].link
        waypoints[0] = (here[0], here[1], waypoints[0][2])
        target = 1

        history: list[tuple[float, float, float]] = []  # (t, x, y) since forward was last held
        forward_since = math.inf
        last, last_t = here, 0.0
        last_poll = -math.inf
        mode, mode_since = "go", 0.0
        last_detour = -math.inf
        speeds: list[float] = []

        def clock() -> float:
            return time.monotonic() - started

        async def stand() -> None:
            nonlocal forward_since
            await keys.release(GameAction.MOVE_FORWARD)
            await keys.release(GameAction.MOVE_BACKWARD)
            forward_since = math.inf

        async def replan(at: Point, why: str) -> None:
            """Plan again from *at*, standing meanwhile."""
            nonlocal waypoints, link, target, last, last_t
            await stand()
            again = await self._plan(result, at, goal)
            if again is None:
                raise _Ended(WalkEnd.NO_ROUTE, f"no route on from ({at[0]:.0f}, {at[1]:.0f}) {why}")
            result.replans += 1
            position, _ = await self._read()
            waypoints, link = await self._first_leg(result, again, position)
            target = 1
            history.clear()
            last, last_t = position, clock()

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
                    last_poll = now
                    if await self.client.in_battle():
                        raise _Ended(WalkEnd.FIGHT)
                    if await self.client.zone_name() != result.zone:
                        raise _Ended(WalkEnd.ZONE_CHANGED)
                held_up = result.waited + (now - mode_since if mode != "go" else 0.0)
                if now > budget + held_up + result.planning:
                    raise _Ended(WalkEnd.TIMED_OUT, f"{budget:.0f} s planned for")
                if held_up > WALK_MOB_WAIT:
                    raise _Ended(WalkEnd.BLOCKED, f"held up by mobs for over {WALK_MOB_WAIT:.0f} s")

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
                if watch is not None and (mobs := watch.near(here)):
                    speed = statistics.median(speeds[-20:]) if len(speeds) >= 10 else expected
                    go_room = room(
                        [along(waypoints, target, x, y, speed * tau) for tau in LOOK_AHEAD], mobs, watch.clock()
                    )
                    enough = RESUME_SLACK if mode != "go" else 0.0
                    if go_room < enough and now - last_detour >= WALK_DETOUR_EVERY:
                        last_detour = now
                        detour = await self._detour(result, keys, waypoints, target, mobs, watch, speed, enough)
                        if detour is not None:
                            waypoints, go_room = detour
                            target = 1
                            here, yaw = await self._read()
                            want = heading_to(here, waypoints[target])
                            error = wrap(want - yaw)
                            x, y, z = here
                    if go_room < enough:
                        t_mob = watch.clock()
                        fx, fy = facing(want)
                        back_speed = speed * BACK_SPEED_FACTOR
                        backing = [(x - fx * back_speed * tau, y - fy * back_speed * tau) for tau in LOOK_AHEAD]
                        standing = [(x, y)] * len(LOOK_AHEAD)
                        choice = choose(go_room, room(standing, mobs, t_mob), room(backing, mobs, t_mob), mode != "go")
                        if choice != "go":
                            result.least_room = min(result.least_room, room(standing, mobs, t_mob))
                    if choice == "go":
                        result.least_room = min(result.least_room, go_room)
                if choice != mode:
                    if mode != "go":
                        result.waited += now - mode_since
                    if choice != "go" or mode != "go":
                        logger.info(
                            "%s: %s -> %s at (%.0f, %.0f), room ahead %.0f", self.label, mode, choice, x, y, go_room
                        )
                    mode, mode_since = choice, now

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
                    self.planner.block(x + fx * BLOCK_AHEAD, y + fy * BLOCK_AHEAD, z, BLOCK_RADIUS)
                    backed, _ = await self._read()
                    await replan(backed, "after a stall")
                    continue

                await asyncio.sleep(WALK_TICK)
        finally:
            if mode != "go":  # standing or backing off for mobs when it ended
                result.waited += clock() - mode_since

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

    async def _detour(self, result, keys, waypoints, target, mobs, watch, speed, enough):
        """A way around *mobs* to a point a short way along the route, and the room it leaves,
        or None. The rest of the route follows it unchanged."""
        await keys.release(GameAction.MOVE_FORWARD)
        discs = mob_discs(mobs, watch.clock())
        here, _ = await self._read()
        k = rejoin(waypoints, target, here, discs)
        around = self.planner.around(discs)
        route = await self._plan(result, here, waypoints[k], around)
        if route is None:
            return None
        legs = await asyncio.to_thread(around.legs, route)
        if len(legs) != 1:
            return None  # a detour by a link is no detour
        new = list(legs[0].waypoints)
        new[0] = (here[0], here[1], new[0][2])
        new += list(waypoints[k + 1 :])
        new_room = room([along(new, 1, here[0], here[1], speed * tau) for tau in LOOK_AHEAD], mobs, watch.clock())
        if new_room < enough:
            return None
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
    """After a reset onto the leg: the waypoint ending the stretch nearest *here*."""
    best = min(range(1, len(route)), key=lambda k: _segment_distance(here, route[k - 1], route[k])[0])
    return best

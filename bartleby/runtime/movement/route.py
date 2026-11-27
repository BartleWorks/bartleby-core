"""Planning a route across one zone, from its files: the floor, its patrols, its links, its exits.

A* over the grid's cells (8 neighbors, no cutting a wall's corner), each link an extra edge from
the floor near where it is taken to its landing, costing seconds of walking. The route is then
straightened to the points where a walker has to turn, never coming nearer a patrol than the
route itself did, and comes back as legs, each but the last ending at a link. It keeps out of
every exit of the zone but the one it is sent to, and never walks through a volume link it
does not take, since stepping in moves the wizard. Moved from BartleBot's
``tools/probe_walk_route.py``, whose ``Planner`` this is; core's ``docs/WALKING.md``,
*Planning a route*, has what each number was measured against.

A zone's gates (``gates.py``) are open until a planner is told one is closed
(:meth:`ZonePlanner.close`); a closed one is walled for every route after, until the client shows
it gone (:meth:`ZonePlanner.open`), and when one is what keeps a route from its goal,
:meth:`ZonePlanner.no_route` names it.

Planning needs no client and opens no file: :class:`ZoneFiles` is what a tool reads out of the
zone's wad.
"""

from __future__ import annotations

import dataclasses
import heapq
import itertools
import math
import threading
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field

import numpy as np

from bartleby.runtime.movement.collision import Footprint, Solid, blocking_footprints, walkable_triangles
from bartleby.runtime.movement.gates import Crossing, Gate, NoRoute, mark_walls
from bartleby.runtime.movement.patrols import (
    HARD,
    HARD_WEIGHT,
    SOFT,
    WEIGHT,
    Patrol,
    clearance,
    danger_cost,
    distance_to_segments,
    segments,
)
from bartleby.runtime.movement.zone_grid import (
    CELL,
    CLEARANCE,
    LEVEL_GAP,
    MAX_RISE,
    ROOM_STEPS,
    STEP_TOL,
    Grid,
    Node,
    nearest_node,
    pieces,
)
from bartleby.runtime.movement.zone_links import (
    XYZ,
    Link,
    PlacedObject,
    Volume,
    ZoneNav,
    zone_exits,
    zone_links,
)

#: Units a second on foot, unmounted, counting only horizontal movement (a stair's height is
#: free): what a link's seconds cost a route in distance. Measured at 600 on flat ground in
#: Unicorn Way and 626 in a house; wizwalker says 580. A mount multiplies it, most by 1.4.
WIZARD_SPEED = 600.0
#: How much further than its own size an exit is kept clear of: the wizard's own body.
EXIT_MARGIN = CLEARANCE
#: A patrol whose trigger radius comes this near a fight's goal is that fight's: a walk into the
#: fight pays nothing to pass it. A mob stands on its path, and a duel circle opens where a mob's
#: trigger met the wizard who started it, so the mob walked to, or the ones in the circle, are
#: well inside this (:meth:`ZonePlanner.fight_cost`).
FIGHT_REACH = 120.0
#: How many nodes A* expands between looks at its stop event: about 10 ms of searching.
STOP_EVERY = 4096
#: Extra cost per unit walked on floor with under ROOM_STEPS[1] of room from walls and drops
#: (:meth:`~bartleby.runtime.movement.zone_grid.Grid.wall_room`), and a quarter of it on floor
#: with under ROOM_STEPS[-1]: a route keeps the most room there is, and squeezes down to
#: CLEARANCE only where the floor narrows, a doorway or a bridge, never refused there, only
#: priced. 50 units of the tightest floor cost what 250 of roomy floor do, so a route goes up
#: to that much further round to keep its room.
WALL_WEIGHT = 4.0


class PlanStopped(Exception):
    """A plan's *stop* event was set before it finished: whoever wanted the route has gone."""


def _check(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise PlanStopped


def astar(
    grid: Grid,
    passable: np.ndarray,
    cost: np.ndarray,
    start: Node,
    goal: Node,
    max_rise: float = MAX_RISE,
    portals: Mapping[int, list[tuple[int, float]]] | None = None,
    taken_only: Set[int] | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[Node], frozenset[int]] | None:
    """A cell-by-cell route, and each k whose step from node k to node k + 1 is a link, or None.
    *cost* is what a unit walked costs, per cell (j, i) or per floor spot (layer, j, i). *portals* maps a node's flat index to the nodes a link puts it on, and what taking it costs.
    A link's step is recorded as one, since its landing may be the next cell across from where it
    is taken, on another storey: a short ladder. The distance heuristic does not know about
    links, so a route using one is found but not always the shortest. A node in *taken_only* (a
    volume link's floor, which moves whoever steps in) is left only by a link, never walked on
    from, unless the route starts there.

    A search with no route expands every node it can reach, which takes seconds in a big zone,
    and it runs in a worker thread, where a cancel cannot reach it. So it looks at *stop* every
    :data:`STOP_EVERY` nodes, and raises :class:`PlanStopped` once it is set."""
    came, by_link, reached = _search(grid, passable, cost, start, [goal], max_rise, portals, taken_only, stop, aim=True)
    if not reached:
        return None
    _, ny, nx = grid.levels.shape
    return _path(came, by_link, next(iter(reached)), ny * nx, nx)


def _search(
    grid: Grid,
    passable: np.ndarray,
    cost: np.ndarray,
    start: Node,
    goals: Sequence[Node],
    max_rise: float,
    portals: Mapping[int, list[tuple[int, float]]] | None,
    taken_only: Set[int] | None,
    stop: threading.Event | None,
    aim: bool,
) -> tuple[dict[int, int], dict[int, bool], set[int]]:
    """The search :func:`astar` and :meth:`ZonePlanner.seconds_to` share, out from *start* until
    every one of *goals* is settled or nothing is left to reach: the node each settled node was
    best reached from, whether by a link's step, and the flat index of every goal settled. With
    *aim*, it heads for the first goal by straight-line distance (A*); without, it spreads
    evenly by cost (Dijkstra), so every goal it settles has its cheapest way there."""
    layers, ny, nx = grid.levels.shape
    plane = ny * nx
    ok = passable.ravel().tolist()
    z = np.nan_to_num(grid.levels).ravel().tolist()
    mult = np.broadcast_to(cost, grid.levels.shape).ravel().tolist()
    s = start[0] * plane + start[1] * nx + start[2]
    remaining = {g[0] * plane + g[1] * nx + g[2] for g in goals}
    reached: set[int] = set()
    _, gj, gi = goals[0]
    c = grid.cell if aim else 0.0  # what a cell of straight line to the first goal adds to a node's priority
    steps = [(dj, di, math.hypot(dj, di) * grid.cell) for dj in (-1, 0, 1) for di in (-1, 0, 1) if dj or di]

    def standing(j: int, i: int, height: float, tol: float) -> bool:
        return any(ok[n] and abs(z[n] - height) <= tol for n in range(j * nx + i, layers * plane, plane))

    best = {s: 0.0}
    came = {s: -1}
    by_link = {s: False}  # whether the best way yet onto a node is a link's step
    heap = [(0.0, 0.0, s)]
    done = set()
    while heap:
        _, g_cost, cur = heapq.heappop(heap)
        if cur in done:
            continue
        if len(done) % STOP_EVERY == 0:
            _check(stop)
        if cur in remaining:
            remaining.discard(cur)
            reached.add(cur)
            if not remaining:
                break
        done.add(cur)
        rest = cur % plane
        cj, ci = divmod(rest, nx)
        height = z[cur]
        walks_on = not (taken_only and cur in taken_only and cur != s)
        for dj, di, length in steps if walks_on else ():
            nj, ni = cj + dj, ci + di
            if not (0 <= nj < ny and 0 <= ni < nx):
                continue
            tol = max_rise * length + STEP_TOL
            if dj and di and not (standing(cj, ni, height, tol) and standing(nj, ci, height, tol)):
                continue  # no cutting a wall's corner
            for nb in range(nj * nx + ni, layers * plane, plane):
                if nb in done or not ok[nb] or abs(z[nb] - height) > tol:
                    continue
                ng = g_cost + length * (mult[cur] + mult[nb]) / 2
                if ng < best.get(nb, math.inf):
                    best[nb] = ng
                    came[nb] = cur
                    by_link[nb] = False
                    heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
        for nb, extra in portals.get(cur, ()) if portals else ():
            if nb in done:
                continue
            ng = g_cost + extra
            if ng < best.get(nb, math.inf):
                best[nb] = ng
                came[nb] = cur
                by_link[nb] = True
                nj, ni = divmod(nb % plane, nx)
                heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
    return came, by_link, reached


def _path(
    came: Mapping[int, int], by_link: Mapping[int, bool], g: int, plane: int, nx: int
) -> tuple[list[Node], frozenset[int]]:
    """The route :func:`_search` found to the node at flat index *g*, start first, and each k
    whose step from node k to node k + 1 is a link."""
    path, linked = [], []
    cur = g
    while cur != -1:
        layer, rest = divmod(cur, plane)
        path.append((layer, *divmod(rest, nx)))
        linked.append(by_link[cur])
        cur = came[cur]
    # Gathered goal first: the k-th node gathered is reached from node len - k - 2 of the route.
    return path[::-1], frozenset(len(path) - k - 2 for k, step in enumerate(linked) if step)


def _route(floor: np.ndarray, volumes: np.ndarray, nodes: list[Node], jumps: frozenset[int]) -> Route:
    """A found path as a :class:`Route`, on *floor* less the *volumes* it stepped round:
    straightening must not cut across a volume the route did not step into either."""
    walked = floor & ~volumes
    for node in nodes:
        walked[node] = floor[node]
    return Route(nodes, walked, jumps)


def wall_cost(wall_room: np.ndarray) -> np.ndarray:
    """The extra cost per unit walked on each floor spot of *wall_room* (:meth:`Grid.wall_room`):
    nothing with ROOM_STEPS[-1] of room, a quarter of :data:`WALL_WEIGHT` with less, and all of
    it under ROOM_STEPS[1]."""
    extra = np.zeros(wall_room.shape)
    extra[wall_room < ROOM_STEPS[-1]] = WALL_WEIGHT / 4
    extra[wall_room < ROOM_STEPS[1]] = WALL_WEIGHT
    return extra


def walking_seconds(legs: Sequence[Leg], speed: float = WIZARD_SPEED) -> float:
    """Seconds walking *legs* takes at *speed* units a second: each stretch's flat length, and
    each link's own seconds. No wait for a mob, a prompt or the planner is in it."""
    walked = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for leg in legs for a, b in itertools.pairwise(leg.waypoints))
    return walked / speed + sum(leg.link.seconds for leg in legs if leg.link)


def link_reach(link: Link) -> float:
    """How near its source a link's floor reaches: as near as a wizard stands to take it, and
    for a volume, which moves whoever steps in, :data:`EXIT_MARGIN` more, as an exit has, so
    a route walking past keeps the wizard's body out of it."""
    return link.reach + (EXIT_MARGIN if link.kind == "volume" else 0.0)


def _flat(spots: np.ndarray) -> list[int]:
    """The flat index astar uses of every True spot of a (layer, j, i) array."""
    _, ny, nx = spots.shape
    return [int(layer * ny * nx + j * nx + i) for layer, j, i in zip(*np.nonzero(spots))]


def runs(path: Sequence[Node], jumps: Set[int]) -> list[list[Node]]:
    """*path* cut at each link: after each k of *jumps*, whose step to k + 1 is a link's. The
    cut is never guessed from the nodes, since a link's landing may be the next cell across."""
    out = [[path[0]]]
    for k, node in enumerate(path[1:]):
        if k in jumps:
            out.append([])
        out[-1].append(node)
    return out


def straight_ok(
    grid: Grid,
    passable: np.ndarray,
    room: np.ndarray,
    a: Node,
    b: Node,
    least: float,
    max_rise: float = MAX_RISE,
) -> bool:
    """Whether a wizard can walk from a to b in a straight line, never passing where the
    :func:`~bartleby.runtime.movement.patrols.clearance` is under *least*, nor stepping up or
    down more than a walk allows."""
    n = int(max(abs(b[1] - a[1]), abs(b[2] - a[2])) * 2) + 1
    js = np.rint(np.linspace(a[1], b[1], n)).astype(int)
    is_ = np.rint(np.linspace(a[2], b[2], n)).astype(int)
    if (room[js, is_] < least).any():
        return False
    step = math.hypot(b[1] - a[1], b[2] - a[2]) * grid.cell / max(n - 1, 1)
    tol = max_rise * max(step, grid.cell) + STEP_TOL
    if len(grid.levels) == 1:
        z = grid.levels[0, js, is_]
        return bool(passable[0, js, is_].all() and (np.abs(np.diff(z)) <= tol).all())
    # Several floors: follow the floor at about the height walked so far, cell by cell.
    heights = np.where(passable[:, js, is_], grid.levels[:, js, is_], np.nan).T.tolist()
    z = float(grid.levels[a])
    for column in heights:
        nearest = min((abs(h - z), h) for h in column if h == h) if any(h == h for h in column) else None
        if nearest is None or nearest[0] > tol:
            return False
        z = nearest[1]
    return abs(z - float(grid.levels[b])) <= tol


def _line_cost(grid: Grid, walls: np.ndarray, a: Node, b: Node) -> float:
    """What walking straight from a to b costs for its room from walls (:func:`smooth`'s
    *walls*), sampled every half cell, as :func:`straight_ok` looks along it."""
    n = int(max(abs(b[1] - a[1]), abs(b[2] - a[2])) * 2) + 1
    js = np.rint(np.linspace(a[1], b[1], n)).astype(int)
    is_ = np.rint(np.linspace(a[2], b[2], n)).astype(int)
    along = walls[js, is_]
    step = math.hypot(b[1] - a[1], b[2] - a[2]) * grid.cell / max(n - 1, 1)
    return float((along.sum() - (along[0] + along[-1]) / 2) * step)


def smooth(
    grid: Grid,
    passable: np.ndarray,
    room: np.ndarray,
    path: Sequence[Node],
    soft: float = SOFT,
    max_rise: float = MAX_RISE,
    walls: np.ndarray | None = None,
) -> list[Node]:
    """Cut a cell-by-cell path down to the points where a walker has to turn.

    From each kept point, the next is the furthest point along the path still reachable in a
    straight line. The line may not pass closer to a patrol than the path itself did between
    the two, so straightening never trades safety for distance; beyond *soft* it does not
    matter, as the cost there is nil. With *walls*, what each cell's room from walls and drops
    costs a unit walked (:func:`wall_cost` of its roomiest floor), nor may the line cost more
    for its room than the stretch of path it replaces did, give or take a cell of the middle
    step: a path that kept its room round a corner, or off a wall, is not straightened back
    into it. (The least room along the stretch would not do: a path starting beside a wall
    has the least there is, and its line could then hug the wall all the way.)"""
    if len(path) <= 2:
        return list(path)
    along = [float(room[j, i]) for _, j, i in path]
    spent = [0.0]  # what the path's room from walls cost from its start to each node
    if walls is not None:
        for p, q in itertools.pairwise(path):
            across = math.hypot(q[1] - p[1], q[2] - p[2]) * grid.cell
            spent.append(spent[-1] + across * (float(walls[p[1:]]) + float(walls[q[1:]])) / 2)
    slack = grid.cell * WALL_WEIGHT / 4

    out = [path[0]]
    a = 0
    while a < len(path) - 1:
        b = a + 1
        least = along[a]
        for k in range(a + 1, len(path)):
            least = min(least, along[k])
            if k > a + 1 and (
                not straight_ok(grid, passable, room, path[a], path[k], min(least, soft) - grid.cell, max_rise)
                or (walls is not None and _line_cost(grid, walls, path[a], path[k]) > spent[k] - spent[a] + slack)
            ):
                break
            b = k
        out.append(path[b])
        a = b
    return out


@dataclass
class Leg:
    """A stretch walked: its waypoints, and the link stepped into at its end (None for the last)."""

    waypoints: list[XYZ]
    link: Link | None = None


@dataclass(frozen=True)
class Route:
    """A planned route: its cells, the floor it was planned on, and where it takes a link. The
    floor is the zone's less the exits it keeps out of; straightening a route uses the same
    floor, so a straight line never cuts through an exit the route went around. *jumps* holds
    each k whose step from node k to node k + 1 is a link's, as astar took it."""

    nodes: list[Node]
    passable: np.ndarray = field(repr=False)
    jumps: frozenset[int]


@dataclass(frozen=True)
class ZoneFiles:
    """What a zone's planner is built from, already read out of its wad by a tool."""

    zone: str
    solids: list[Solid]  # collision.bcd
    patrols: list[Patrol]  # spawnData.xml, pathData.xml, pathNodeData.bin
    named: dict[str, XYZ]  # gamedata.bin's arrival points
    volumes: list[Volume] = field(default_factory=list)  # volumes.xml
    teleports: frozenset[str] = frozenset()  # triggers.xml
    placed: list[PlacedObject] = field(default_factory=list)  # gamedata.bin's objects, interactable
    nav: ZoneNav | None = None  # zone.nav
    gates: list[Gate] = field(default_factory=list)  # gamedata.bin's objects with a solid file


@dataclass
class ZonePlanner:
    """One zone's grid, walls, patrols, links and exits, built once and asked for as many
    routes as needed."""

    zone: str
    grid: Grid
    passable: np.ndarray  # per floor: (layer, j, i)
    clearance: np.ndarray  # per cell: distance beyond the nearest patrol's trigger radius
    cost: np.ndarray  # per cell
    patrols: list[Patrol]
    named: dict[str, XYZ]
    soft: float = SOFT
    max_rise: float = MAX_RISE
    links: list[Link] = field(default_factory=list)
    exits: list[Volume] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)  # a link object's, marked wall, is in the grid's walls
    closed: list[Gate] = field(default_factory=list)  # the gates this planner was told are closed
    blocks: list[tuple[float, float, float, float]] = field(default_factory=list)  # block's (x, y, z, radius)
    # Per floor spot: its room from walls and drops (Grid.wall_room), which a route pays to keep
    # (WALL_WEIGHT) and straightening never cuts into; None prices no room.
    wall_room: np.ndarray | None = field(default=None, repr=False)
    # The floor spots each closed gate took when it was closed, for open to give back.
    _took: dict[Gate, np.ndarray] = field(default_factory=dict, repr=False)
    # Each gate's floor (gate_floor), shared by every copy of a planner, since the grid is.
    _gate_floors: dict[tuple, tuple[int, int, np.ndarray]] = field(default_factory=dict, repr=False)
    _exit_floors: dict[int, np.ndarray] = field(default_factory=dict, init=False, repr=False)
    # The step costs of a walk into a fight, by the patrols it spares (fight_cost).
    _fight_costs: dict[frozenset[int], np.ndarray] = field(default_factory=dict, init=False, repr=False)
    _wall_cost: np.ndarray | None = field(default=None, init=False, repr=False)  # wall_cost(wall_room)
    _wall_line: np.ndarray | None = field(default=None, init=False, repr=False)  # wall_line()

    def step_cost(self, base: np.ndarray) -> np.ndarray:
        """What a unit walked costs on each floor spot: *base*, per cell (the patrols' cost, or
        none), and what the spot's room from walls costs (:func:`wall_cost`)."""
        if self.wall_room is None:
            return base
        if self._wall_cost is None:
            self._wall_cost = wall_cost(self.wall_room)
        return base[None] + self._wall_cost

    def wall_line(self) -> np.ndarray | None:
        """What each cell's room from walls costs a straightened line (:func:`smooth`): the
        :func:`wall_cost` of its roomiest floor, since a line is not told which storey it is on.
        None when no room is priced."""
        if self.wall_room is None:
            return None
        if self._wall_line is None:
            self._wall_line = wall_cost(self.wall_room.max(axis=0))
        return self._wall_line

    def node(self, x: float, y: float, z: float | None = None, passable: np.ndarray | None = None) -> Node | None:
        return nearest_node(self.grid, self.passable if passable is None else passable, x, y, z)

    def exit_floor(self, exit: Volume) -> np.ndarray:
        """The floor spots within *exit*, and EXIT_MARGIN of it, at about its height."""
        if id(exit) not in self._exit_floors:
            px, py = self.grid.all_centers()
            cx, cy, cz = exit.center
            across = (np.hypot(px - cx, py - cy) <= exit.reach + EXIT_MARGIN)[None]
            with np.errstate(invalid="ignore"):
                self._exit_floors[id(exit)] = across & (np.abs(self.grid.levels - cz) <= exit.rise + EXIT_MARGIN)
        return self._exit_floors[id(exit)]

    def exits_at(self, x: float, y: float, z: float | None = None) -> list[Volume]:
        """The exits whose floor holds the spot a point snaps to."""
        spot = self.node(x, y, z)
        return [] if spot is None else [e for e in self.exits if self.exit_floor(e)[spot]]

    def outside_exits(self, x: float, y: float, z: float | None = None) -> tuple[float, float, float] | None:
        """The point itself when no exit's no-go area (:meth:`exit_floor`) holds the spot it snaps
        to; else the floor nearest it that none holds, weighing height as :meth:`node` does; None
        when the zone has no floor, or none outside its exits. A walk to a point inside an exit
        would leave the zone, as a pickup inside a cave's mouth did (BartleBot #35): one that
        must not walks here instead."""
        spot = self.node(x, y, z)
        if spot is None:
            return None
        if not any(self.exit_floor(e)[spot] for e in self.exits):
            return (float(x), float(y), float(self.grid.levels[spot]) if z is None else float(z))
        clear = self.passable.copy()
        for exit in self.exits:
            clear &= ~self.exit_floor(exit)
        nearest = self.node(x, y, z, passable=clear)
        return None if nearest is None else self.grid.world(*nearest)

    def fight_cost(self, at: Sequence[float]) -> np.ndarray:
        """What a step costs on a walk into a fight at *at*: the patrols' cost (:attr:`cost`),
        less every patrol whose trigger radius comes within :data:`FIGHT_REACH` of *at*. The walk
        means to meet that mob, so its patrol is no danger to it; every other patrol still costs
        what it would. Built once for each set of patrols spared, off the event loop, as every
        plan is."""
        x, y = np.asarray([float(at[0])]), np.asarray([float(at[1])])
        spared = frozenset(
            k
            for k, p in enumerate(self.patrols)
            if float(distance_to_segments(x, y, segments(p, closed=True))[0]) - p.proximity <= FIGHT_REACH
        )
        if not spared:
            return self.cost
        if spared not in self._fight_costs:
            kept = [p for k, p in enumerate(self.patrols) if k not in spared]
            self._fight_costs[spared] = danger_cost(clearance(self.grid, kept), soft=self.soft)
        return self._fight_costs[spared]

    def floor_for(self, start: Sequence[float], *goals: Sequence[float]) -> np.ndarray:
        """The floor a route from *start* to a goal may use: every exit closed but one the goal
        is in, which the route ends at, and one the start is in, which it walks out of. With no
        goal, every exit but the start's is closed."""
        open_ = {id(e) for point in (start, *goals) for e in self.exits_at(*point)}
        out = self.passable.copy()
        for exit in self.exits:
            if id(exit) not in open_:
                out &= ~self.exit_floor(exit)
        return out

    def route(
        self,
        start: Sequence[float],
        goal: Sequence[float],
        avoid: bool = True,
        fight: bool = False,
        stop: threading.Event | None = None,
    ) -> Route | None:
        """A cell-by-cell route between two points, each (x, y) or (x, y, z), or None. With
        *avoid*, it pays the patrols' cost; without, it takes the shortest. With *fight* too, the
        goal is a fight to join, and the patrols of the mobs in it cost nothing (:meth:`fight_cost`).

        It steps onto a volume link's floor only to take that link, since stepping in moves the
        wizard, unless the start or the goal is in it. Where that leaves no route (a volume
        across the only way, whose landing is off the floor), it plans once more as if the
        volumes were floor, and the walker deals with whatever moves it.

        A goal on a closed gate's floor has no route: snapped to the nearest floor left, it
        would end short of the gate as if arrived, when the gate is what is in the way
        (:meth:`no_route` names it).

        *stop*, set from another thread, ends the search with :class:`PlanStopped`."""
        floor = self.floor_for(start, goal)
        s, g = self.node(*start, passable=floor), self.node(*goal, passable=floor)
        if s is None or g is None or self._goal_behind_gate(floor, goal, g):
            return None
        _check(stop)
        cost = self.step_cost((self.fight_cost(goal) if fight else self.cost) if avoid else np.ones_like(self.cost))
        portals = self.portals(floor)
        _check(stop)
        volumes = self.volume_floor(floor, s, g)
        taken_only = set(_flat(volumes))
        found = astar(self.grid, floor, cost, s, g, self.max_rise, portals, taken_only, stop)
        if found is None and taken_only:
            volumes = np.zeros_like(floor)
            found = astar(self.grid, floor, cost, s, g, self.max_rise, portals, stop=stop)
        if found is None:
            return None
        return _route(floor, volumes, *found)

    def seconds_to(
        self,
        start: Sequence[float],
        goals: Sequence[Sequence[float]],
        avoid: bool = True,
        speed: float = WIZARD_SPEED,
        stop: threading.Event | None = None,
    ) -> list[float | None]:
        """Seconds walking from *start* to each of *goals* takes at *speed*, along the route
        :meth:`route` would plan there (:func:`walking_seconds` of its legs), or None for a goal
        it would find none to. One search serves every goal: out from *start* on the same floor
        and costs, spreading by cost rather than heading for any one goal, and stopping once
        every goal is settled. So a goal a wall puts a long way round costs what walking round
        does, not its straight line; and one goal with no route makes the search reach every
        cell it can, which takes seconds in a big zone, as a route with none does.

        A goal in one of the zone's exits, or on a volume link's floor, opens that floor to the
        route there and to no other, so it is planned on its own (:meth:`route`). *stop* is
        :meth:`route`'s."""
        out: list[float | None] = [None] * len(goals)
        floor = self.floor_for(start)
        s = self.node(*start, passable=floor)
        if s is None:
            return out
        _check(stop)
        volumes = self.volume_floor(floor, s)
        shared: dict[int, Node] = {}
        for k, goal in enumerate(goals):
            g = self.node(*goal, passable=floor)
            if self.exits_at(*goal) or (g is not None and volumes[g]):
                found = self.route(start, goal, avoid, stop=stop)
                out[k] = None if found is None else walking_seconds(self.legs(found), speed)
            elif g is not None and not self._goal_behind_gate(floor, goal, g):
                shared[k] = g
        if not shared:
            return out
        cost = self.step_cost(self.cost if avoid else np.ones_like(self.cost))
        portals = self.portals(floor)
        _check(stop)
        taken_only = set(_flat(volumes))
        _, ny, nx = floor.shape
        for passes, (left, taken) in enumerate(((volumes, taken_only), (np.zeros_like(floor), None))):
            if passes and not taken_only:
                break  # route plans again with the volumes as floor only when it stepped round some
            came, by_link, reached = _search(
                self.grid, floor, cost, s, list(shared.values()), self.max_rise, portals, taken, stop, aim=False
            )
            for k, g in list(shared.items()):
                flat = g[0] * ny * nx + g[1] * nx + g[2]
                if flat in reached:
                    route = _route(floor, left, *_path(came, by_link, flat, ny * nx, nx))
                    out[k] = walking_seconds(self.legs(route), speed)
                    del shared[k]
            if not shared:
                break
        return out

    def _link_floor(self, link: Link, floor: np.ndarray) -> np.ndarray:
        """The spots of *floor* within :func:`link_reach` of *link*'s source, at about its height."""
        px, py = self.grid.all_centers()
        near = (np.hypot(px - link.source[0], py - link.source[1]) <= link_reach(link))[None] & floor
        with np.errstate(invalid="ignore"):
            near &= np.abs(self.grid.levels - link.source[2]) <= LEVEL_GAP
        return near

    def volume_floor(self, floor: np.ndarray, *keep: Node) -> np.ndarray:
        """The spots of *floor* in a volume link, where stepping in moves the wizard: every
        volume's but those holding a node of *keep* (a route's start or goal)."""
        out = np.zeros_like(floor)
        for link in self.links:
            if link.kind != "volume":
                continue
            spots = self._link_floor(link, floor)
            if not any(spots[node] for node in keep):
                out |= spots
        return out

    def portals(self, passable: np.ndarray | None = None) -> dict[int, list[tuple[int, float]]]:
        """For astar: every passable spot within a link's reach of its source, at about its
        height, leads to the spot nearest where the link lands."""
        floor = self.passable if passable is None else passable
        _, ny, nx = floor.shape
        plane = ny * nx
        out: dict[int, list[tuple[int, float]]] = {}
        for link in self.links:
            landing = self.node(*link.target, passable=floor)
            if landing is None:
                continue
            to = landing[0] * plane + landing[1] * nx + landing[2]
            for spot in _flat(self._link_floor(link, floor)):
                out.setdefault(spot, []).append((to, link.seconds * WIZARD_SPEED))
        return out

    def turns(self, route: Route) -> list[Node]:
        """The route cut to its turns, each stretch between links straightened on its own."""
        return self.turns_and_jumps(route)[0]

    def turns_and_jumps(self, route: Route) -> tuple[list[Node], set[int]]:
        """turns(), and each k whose step from turn k to turn k + 1 is a link rather than walked."""
        out: list[Node] = []
        jumps: set[int] = set()
        for run in runs(route.nodes, route.jumps):
            if out:
                jumps.add(len(out) - 1)
            out += smooth(self.grid, route.passable, self.clearance, run, self.soft, self.max_rise, self.wall_line())
        return out, jumps

    def legs(self, route: Route) -> list[Leg]:
        """The route as stretches to walk, each but the last ending in the link that joins it to
        the next: at a volume's center, or, for an object pressed, on the floor near it."""
        parts = runs(route.nodes, route.jumps)
        out = []
        for k, run in enumerate(parts):
            straight = smooth(
                self.grid, route.passable, self.clearance, run, self.soft, self.max_rise, self.wall_line()
            )
            points = [self.grid.world(*n) for n in straight]
            link = None
            if k + 1 < len(parts):
                lands_on = parts[k + 1][0]
                ends_at = self.grid.world(*run[-1])
                for candidate in self.links:
                    near = math.dist(candidate.source[:2], ends_at[:2]) <= link_reach(candidate) + self.grid.cell
                    if near and self.node(*candidate.target, passable=route.passable) == lands_on:
                        link = candidate
                        break
                if link is not None and link.kind == "volume":
                    points.append((link.source[0], link.source[1], points[-1][2]))
            out.append(Leg(points, link))
        return out

    def waypoints(self, route: Route) -> list[XYZ]:
        """Every leg's waypoints in a row, start first."""
        return [p for leg in self.legs(route) for p in leg.waypoints]

    def drop(self, link: Link) -> None:
        """Stop routing through *link*: stepping into it moved nobody."""
        self.links = [other for other in self.links if other != link]

    def around(self, discs: Sequence[tuple[float, float, float]]) -> ZonePlanner:
        """A copy whose floor is closed within each (x, y, radius) of *discs*, on every storey:
        the live mobs, to route around where they are now. The original is untouched."""
        px, py = self.grid.all_centers()
        closed = np.zeros(px.shape, dtype=bool)
        for x, y, radius in discs:
            closed |= np.hypot(px - x, py - y) <= radius
        return dataclasses.replace(self, passable=self.passable & ~closed[None])

    # --- gates ---------------------------------------------------------------------------------

    def gate_floor(self, gate: Gate) -> tuple[int, int, np.ndarray]:
        """The floor spots *gate* takes when closed, as (j, i, spots): *spots* a (layer, rows,
        columns) window of the grid whose corner is cell (j, i). They are the spots passable on
        the grid as built and not once the gate's footprints are walls too, which takes the
        wizard's clearance off each side, as :meth:`Grid.passable` does for every other wall.
        Built on a window around the gate, wide enough that nothing beyond it could change."""
        name = (gate.name, gate.at)
        if name not in self._gate_floors:
            self._gate_floors[name] = _gate_floor(self.grid, gate, self.max_rise)
        return self._gate_floors[name]

    def _gate_spots(self, gate: Gate) -> tuple[slice, slice, slice, np.ndarray]:
        j, i, spots = self.gate_floor(gate)
        return slice(None), slice(j, j + spots.shape[1]), slice(i, i + spots.shape[2]), spots

    def close(self, gate: Gate) -> int:
        """Wall *gate* for every route this planner plans from now on: a walk found it there.
        Returns how many floor spots it closed. A link object's gate is a wall already."""
        if gate.wall or any(g is gate for g in self.closed):
            return 0
        layer, rows, cols, spots = self._gate_spots(gate)
        taken = spots & self.passable[layer, rows, cols]
        self.passable[layer, rows, cols] &= ~spots
        self.closed.append(gate)
        self._took[gate] = taken
        return int(taken.sum())

    def open(self, gate: Gate) -> int:
        """Undo :meth:`close`: the client shows *gate* gone after all. Gives back only the floor
        closing it took, and none that another closed gate or a :meth:`block` still takes.
        Returns how many floor spots it opened; 0 for a gate that is not closed."""
        taken = self._took.pop(gate, None)
        if taken is None:
            return 0
        self.closed[:] = [g for g in self.closed if g is not gate]
        layer, rows, cols, _ = self._gate_spots(gate)
        self.passable[layer, rows, cols] |= taken
        for other in self.closed:
            o_layer, o_rows, o_cols, o_spots = self._gate_spots(other)
            self.passable[o_layer, o_rows, o_cols] &= ~o_spots
        for x, y, z, radius in self.blocks:
            self._block(x, y, z, radius)
        return int(taken.sum())

    def gates_on(self, route: Route) -> list[Gate]:
        """The gates *route* crosses, in the order it reaches them: those whose floor it steps on."""
        found: list[tuple[int, int, Gate]] = []
        nodes = np.asarray(route.nodes, dtype=np.intp).reshape(-1, 3)
        for k, gate in enumerate(self.gates):
            if gate.wall:
                continue
            first = _first_on(nodes, self.gate_floor(gate))
            if first is not None:
                found.append((first, k, gate))
        return [gate for _, _, gate in sorted(found, key=lambda t: (t[0], t[1]))]

    def crosses(self, gate: Gate, path: Sequence[Sequence[float]]) -> bool:
        """Whether walking *path*, points joined by straight lines, steps on *gate*'s floor at
        about the path's height: a walker's question of a gate it learns of mid-walk, which has
        the waypoints left to walk, not a :class:`Route`."""
        j, i, spots = self.gate_floor(gate)
        _, rows, cols = spots.shape
        step = self.grid.cell / 2
        for a, b in zip(path, path[1:], strict=False):
            n = max(1, math.ceil(math.dist(a[:2], b[:2]) / step))
            for k in range(n + 1):
                t = k / n
                x, y, z = (a[q] + (b[q] - a[q]) * t for q in range(3))
                row, col = self.grid.index(x, y)
                if not (j <= row < j + rows and i <= col < i + cols):
                    continue
                layers = np.flatnonzero(spots[:, row - j, col - i])
                with np.errstate(invalid="ignore"):
                    if (np.abs(self.grid.levels[layers, row, col] - z) <= LEVEL_GAP / 2).any():
                        return True
        return False

    def gates_near(self, x: float, y: float, z: float, reach: float) -> list[Gate]:
        """The open gates within *reach* of (x, y) at about height z, nearest first."""
        near = []
        for k, gate in enumerate(self.gates):
            if gate.wall or any(g is gate for g in self.closed):
                continue
            d = gate.distance(x, y, z)
            if d <= reach:
                near.append((d, k, gate))
        return [gate for _, _, gate in sorted(near, key=lambda t: (t[0], t[1]))]

    def no_route(
        self,
        start: Sequence[float],
        goal: Sequence[float],
        avoid: bool = True,
        fight: bool = False,
        stop: threading.Event | None = None,
    ) -> NoRoute:
        """Why :meth:`route` found nothing: the zone and both points, and the closed gate in the
        way, with its :class:`Crossing`, when opening that one gate alone gives a route. The
        gates closed last are tried first.

        Each gate tried is a plan of its own, and a plan with no route searches all the floor it
        can reach: seconds each, in a big zone. So it first plans once with every closed gate
        open, and when even that finds nothing, no one gate would, and it says so at once
        (Dragonspyre's Arena: eight stones listed closed, and floor no gate joins). More floor
        never takes a route away; what it can change is the spot a point snaps to, so this is
        trusted only when both points snap where they did. *stop* is :meth:`route`'s."""
        start3, goal3 = self._with_height(start), self._with_height(goal)
        if not self.closed:
            return NoRoute(self.zone, start3, goal3)
        if not self._any_gate_could_help(start, goal, avoid, fight, stop):
            return NoRoute(self.zone, start3, goal3)
        for gate in reversed(self.closed):
            _check(stop)
            others = [g for g in self.closed if g is not gate]
            trial = self.passable.copy()
            layer, rows, cols, spots = self._gate_spots(gate)
            trial[layer, rows, cols] |= spots
            for other in others:
                o_layer, o_rows, o_cols, o_spots = self._gate_spots(other)
                trial[o_layer, o_rows, o_cols] &= ~o_spots
            found = dataclasses.replace(self, passable=trial, closed=others).route(start, goal, avoid, fight, stop)
            if found is None:
                continue
            nodes = np.asarray(found.nodes, dtype=np.intp).reshape(-1, 3)
            first = _first_on(nodes, self.gate_floor(gate))
            last = _first_on(nodes[::-1], self.gate_floor(gate))
            if first is None or last is None:
                continue
            last = len(found.nodes) - 1 - last
            before = self.grid.world(*found.nodes[first - 1]) if first > 0 else start3
            after = self.grid.world(*found.nodes[last + 1]) if last + 1 < len(found.nodes) else goal3
            return NoRoute(self.zone, start3, goal3, gate.name, Crossing(before, after, math.dist(before, after)))
        return NoRoute(self.zone, start3, goal3)

    def _any_gate_could_help(
        self, start: Sequence[float], goal: Sequence[float], avoid: bool, fight: bool, stop: threading.Event | None
    ) -> bool:
        """Whether opening closed gates might give a route: False only when every one open still
        gives none, and both points snap to the spots they snap to with the gates closed."""
        opened = self.passable.copy()
        for gate in self.closed:
            layer, rows, cols, spots = self._gate_spots(gate)
            opened[layer, rows, cols] |= spots
        every_open = dataclasses.replace(self, passable=opened, closed=[])
        floor, open_floor = self.floor_for(start, goal), every_open.floor_for(start, goal)
        for point in (start, goal):
            if self.node(*point, passable=floor) != every_open.node(*point, passable=open_floor):
                return True
        return every_open.route(start, goal, avoid, fight, stop) is not None

    def _goal_behind_gate(self, floor: np.ndarray, goal: Sequence[float], snapped: Node) -> bool:
        """Whether a closed gate is what moved *goal* off its own floor: with every closed gate's
        floor given back, the goal snaps elsewhere, onto one of theirs."""
        if not self.closed:
            return False
        given_back = floor.copy()
        for gate in self.closed:
            layer, rows, cols, spots = self._gate_spots(gate)
            given_back[layer, rows, cols] |= spots
        own = self.node(*goal, passable=given_back)
        if own is None or own == snapped:
            return False
        return any(
            _first_on(np.asarray([own], dtype=np.intp), self.gate_floor(gate)) is not None for gate in self.closed
        )

    def _with_height(self, point: Sequence[float]) -> XYZ:
        """*point* with a height: its own, or the floor's where it snaps to."""
        if len(point) >= 3:
            return (float(point[0]), float(point[1]), float(point[2]))
        spot = self.node(point[0], point[1])
        return (float(point[0]), float(point[1]), self.grid.world(*spot)[2] if spot is not None else 0.0)

    def block(self, x: float, y: float, z: float, radius: float) -> int:
        """Mark the floor within *radius* of (x, y), at about height z, impassable: something a
        walk ran into that the collision file does not have. Returns how many spots it closed."""
        self.blocks.append((x, y, z, radius))
        return self._block(x, y, z, radius)

    def _block(self, x: float, y: float, z: float, radius: float) -> int:
        grid = self.grid
        px, py = grid.all_centers()
        near = np.hypot(px - x, py - y) <= radius
        with np.errstate(invalid="ignore"):
            hit = near[None] & (np.abs(grid.levels - z) <= LEVEL_GAP / 2) & self.passable
        self.passable &= ~hit
        return int(hit.sum())


def build_planner(
    files: ZoneFiles,
    *,
    cell: float = CELL,
    radius: float = CLEARANCE,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
    max_rise: float = MAX_RISE,
) -> ZonePlanner:
    """A zone's planner, from its files. The keywords are the probes' to vary; a run uses the defaults."""
    grid = floor_grid(files.solids, cell)
    passable = grid.passable(radius, max_rise)
    links, exits = links_and_exits(files, grid, passable, pieces(grid, passable, max_rise))
    gates = mark_walls(files.gates, links)
    if wall_gates(grid, gates):
        passable = grid.passable(radius, max_rise)
    return assemble(
        files.zone,
        grid,
        files.patrols,
        files.named,
        links,
        exits,
        gates=gates,
        passable=passable,
        radius=radius,
        hard=hard,
        soft=soft,
        weight=weight,
        hard_weight=hard_weight,
        max_rise=max_rise,
    )


def wall_gates(grid: Grid, gates: Sequence[Gate]) -> bool:
    """Wall *grid* where each gate marked a wall stands (a link object's); whether any was.
    Done after the links are found, on the floor as ``collision.bcd`` has it, so a link object's
    own solid never hides its link: the planner takes it from the floor around."""
    walls = [f for gate in gates if gate.wall for f in gate.footprints]
    if walls:
        grid.add_walls(walls)
    return bool(walls)


def links_and_exits(
    files: ZoneFiles, grid: Grid, passable: np.ndarray, label: np.ndarray
) -> tuple[list[Link], list[Volume]]:
    """The zone's links, found on its floor, split into *label*'s :func:`pieces`, and its exits."""
    links = zone_links(
        files.zone, grid, passable, label, files.volumes, files.placed, files.named, files.nav, files.teleports
    )
    return links, zone_exits(files.volumes, files.teleports, links)


def floor_grid(solids: Sequence[Solid], cell: float = CELL) -> Grid:
    """The zone's floor from its ``collision.bcd``: rastered, and walled by its solids."""
    return grid_of(walkable_triangles(list(solids)), blocking_footprints(list(solids)), cell)


def grid_of(triangles: np.ndarray, footprints: Sequence[Footprint], cell: float = CELL) -> Grid:
    """The floor triangles rastered and walled by the footprints: what a shipped zone map's
    floor (``zone_maps``) is loaded into, so it is built as one from the wad is."""
    grid = Grid(triangles, cell)
    grid.add_walls(list(footprints))
    return grid


def assemble(
    zone: str,
    grid: Grid,
    patrols: Sequence[Patrol],
    named: Mapping[str, XYZ],
    links: Sequence[Link],
    exits: Sequence[Volume],
    *,
    gates: Sequence[Gate] = (),
    passable: np.ndarray | None = None,
    room: np.ndarray | None = None,
    wall_room: np.ndarray | None = None,
    radius: float = CLEARANCE,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
    max_rise: float = MAX_RISE,
) -> ZonePlanner:
    """A planner from a grid already built and the links and exits already found: what
    :func:`build_planner` ends with, and what a shipped zone map (``zone_maps``) is loaded into,
    so both plan alike. *passable* is the grid's own, and *room* the patrols' :func:`clearance`
    on it, when not given, and *wall_room* the grid's :meth:`~Grid.wall_room` from *radius* up:
    ``zone_maps`` gives all three from a copy saved on the user's machine."""
    passable = grid.passable(radius, max_rise) if passable is None else passable
    if wall_room is None:
        wall_room = grid.wall_room((radius, *ROOM_STEPS[1:]), max_rise)
    room = clearance(grid, patrols) if room is None else room
    cost = danger_cost(room, hard, soft, weight, hard_weight)
    return ZonePlanner(
        zone,
        grid,
        passable,
        room,
        cost,
        list(patrols),
        dict(named),
        soft,
        max_rise,
        list(links),
        list(exits),
        list(gates),
        wall_room=wall_room,
    )


def _extent(f: Footprint) -> tuple[float, float, float, float]:
    """A footprint's bounds on the floor plan: (x low, x high, y low, y high)."""
    if f.hull is None:
        (x, y), r = f.center, f.radius
        return x - r, x + r, y - r, y + r
    hull = np.asarray(f.hull, dtype=float)
    return float(hull[:, 0].min()), float(hull[:, 0].max()), float(hull[:, 1].min()), float(hull[:, 1].max())


def _gate_floor(grid: Grid, gate: Gate, max_rise: float) -> tuple[int, int, np.ndarray]:
    """:meth:`ZonePlanner.gate_floor`, built. The window reaches the clearance and two cells
    beyond the gate's footprints, since a wall changes only the floor within the clearance of it."""
    empty = (0, 0, np.zeros((len(grid.levels), 0, 0), dtype=bool))
    if not gate.footprints:
        return empty
    bounds = [_extent(f) for f in gate.footprints]
    reach = max(1, round(CLEARANCE / grid.cell)) + 2
    j0, i0 = grid.index(min(b[0] for b in bounds), min(b[2] for b in bounds))
    j1, i1 = grid.index(max(b[1] for b in bounds), max(b[3] for b in bounds))
    i0, j0 = max(0, i0 - reach), max(0, j0 - reach)
    i1, j1 = min(grid.nx - 1, i1 + reach), min(grid.ny - 1, j1 + reach)
    if i1 < i0 or j1 < j0:  # off the grid altogether
        return empty
    window = Grid.from_arrays(
        grid.levels[:, j0 : j1 + 1, i0 : i1 + 1],
        grid.walls[:, j0 : j1 + 1, i0 : i1 + 1].copy(),
        grid.x0 + i0 * grid.cell,
        grid.y0 + j0 * grid.cell,
        grid.cell,
    )
    before = window.passable(CLEARANCE, max_rise)
    window.add_walls(list(gate.footprints))
    return j0, i0, before & ~window.passable(CLEARANCE, max_rise)


def _first_on(nodes: np.ndarray, floor: tuple[int, int, np.ndarray]) -> int | None:
    """The index of the first of *nodes*, (layer, j, i) rows, on a gate's floor, or None."""
    j, i, spots = floor
    _, rows, cols = spots.shape
    inside = (nodes[:, 1] >= j) & (nodes[:, 1] < j + rows) & (nodes[:, 2] >= i) & (nodes[:, 2] < i + cols)
    at = np.flatnonzero(inside)
    if not len(at):
        return None
    hit = spots[nodes[at, 0], nodes[at, 1] - j, nodes[at, 2] - i]
    return int(at[np.argmax(hit)]) if hit.any() else None

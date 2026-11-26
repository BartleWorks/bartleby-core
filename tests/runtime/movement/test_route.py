"""Routes across one zone: by its stairs, around its patrols, out of its exits, through its links."""

import ast
import math
import threading
from pathlib import Path

import numpy as np
import pytest

from bartleby.runtime.movement.collision import Footprint
from bartleby.runtime.movement.gates import Gate
from bartleby.runtime.movement.patrols import HARD, Patrol
from bartleby.runtime.movement.route import (
    EXIT_MARGIN,
    PlanStopped,
    ZoneFiles,
    ZonePlanner,
    build_planner,
    link_reach,
)
from bartleby.runtime.movement.zone_grid import CELL, CLEARANCE, MAX_RISE, STEP_TOL
from bartleby.runtime.movement.zone_links import LINK_REACH, InteractTemplate, Link, PlacedObject, Volume
from tests.runtime.movement.test_zone_grid import two_storeys
from tests.runtime.movement.zone_fixtures import box, floor, mesh

ZONE = "Test/T_Street"


def _planner(*solids, patrols=(), named=None, **files) -> ZonePlanner:
    return build_planner(ZoneFiles(ZONE, list(solids), list(patrols), dict(named or {}), **files))


def _length(planner: ZonePlanner, nodes) -> float:
    pts = np.array([planner.grid.world(*n)[:2] for n in nodes])
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


# --- stairs --------------------------------------------------------------------------------


def test_a_route_joins_two_storeys_by_the_stairs_and_by_nothing_else():
    planner = _planner(two_storeys())

    route = planner.route((500, 500, 400), (500, 500, 0))

    assert route is not None
    heights = [planner.grid.world(*n)[2] for n in route.nodes]
    assert heights[0] == 400 and heights[-1] == 0
    for a, b in zip(route.nodes, route.nodes[1:]):
        across = math.hypot(b[1] - a[1], b[2] - a[2]) * planner.grid.cell
        assert abs(planner.grid.world(*b)[2] - planner.grid.world(*a)[2]) <= MAX_RISE * across + STEP_TOL
    on_stairs = [
        n for n in route.nodes if 1000 < planner.grid.world(*n)[0] < 1500 and 0 < planner.grid.world(*n)[2] < 400
    ]
    assert on_stairs


def test_with_the_stairs_walled_off_there_is_no_route_between_the_storeys():
    planner = _planner(two_storeys(), box((1250, 150, 200), 100, 400, 600))
    assert planner.route((500, 500, 400), (500, 500, 0)) is None


# --- patrols -------------------------------------------------------------------------------


def _street() -> ZonePlanner:
    """A street 4000 by 2000 with a ghost walking across its lower half, at x 2000 from y 0 to 900."""
    ghost = Patrol("Ghosts 01", "Ghost", 200, 1, np.array([(2000, 0, 0), (2000, 900, 0)], dtype=float))
    return _planner(mesh(floor(0, 0, 4000, 2000, 0)), patrols=[ghost])


START, GOAL = (200, 500), (3800, 500)


def test_avoiding_patrols_keeps_250_outside_the_trigger_radius_where_there_is_room():
    planner = _street()

    route = planner.route(START, GOAL, avoid=True)

    assert min(planner.clearance[j, i] for _, j, i in route.nodes) >= HARD


def test_not_avoiding_takes_the_shortest_route_straight_past_the_patrol():
    planner = _street()

    shortest = planner.route(START, GOAL, avoid=False)
    avoiding = planner.route(START, GOAL, avoid=True)

    assert min(planner.clearance[j, i] for _, j, i in shortest.nodes) < 0
    assert _length(planner, shortest.nodes) < _length(planner, avoiding.nodes)
    assert _length(planner, shortest.nodes) == pytest.approx(3600, abs=2 * planner.grid.cell)


def test_straightening_never_brings_a_route_nearer_a_patrol_than_the_grid_route():
    planner = _street()
    route = planner.route(START, GOAL, avoid=True)
    least = min(planner.clearance[j, i] for _, j, i in route.nodes)

    turns = planner.turns(route)

    assert len(turns) < len(route.nodes) / 4
    for a, b in zip(turns, turns[1:]):
        n = int(max(abs(b[1] - a[1]), abs(b[2] - a[2])) * 2) + 1
        js = np.rint(np.linspace(a[1], b[1], n)).astype(int)
        is_ = np.rint(np.linspace(a[2], b[2], n)).astype(int)
        assert planner.clearance[js, is_].min() >= min(least, planner.soft) - planner.grid.cell


# --- exits ---------------------------------------------------------------------------------


def _hall_with_an_exit() -> ZonePlanner:
    """A hall 4000 by 2000 with an exit of radius 300 standing in its middle."""
    gate = Volume("TeleportVol_Gate", (2000, 1000, 0), radius=300)
    return _planner(mesh(floor(0, 0, 4000, 2000, 0)), volumes=[gate], teleports=frozenset({gate.name}))


def _nearest_exit_distance(planner: ZonePlanner, nodes, center=(2000, 1000)) -> float:
    return min(math.dist(planner.grid.world(*n)[:2], center) for n in nodes)


def test_a_route_never_crosses_an_exit_it_is_not_sent_to():
    planner = _hall_with_an_exit()

    route = planner.route((200, 1000), (3800, 1000))

    assert _nearest_exit_distance(planner, route.nodes) > 300 + EXIT_MARGIN
    for leg in planner.legs(route):
        for a, b in zip(leg.waypoints, leg.waypoints[1:]):
            for t in np.linspace(0, 1, 50):
                x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
                assert math.dist((x, y), (2000, 1000)) > 300


def test_a_route_sent_to_an_exit_ends_in_it():
    planner = _hall_with_an_exit()

    route = planner.route((200, 1000), (2000, 1000))

    assert math.dist(planner.grid.world(*route.nodes[-1])[:2], (2000, 1000)) <= planner.grid.cell


def test_a_route_from_inside_an_exit_walks_out_of_it():
    planner = _hall_with_an_exit()

    route = planner.route((2100, 1000), (3800, 1000))

    assert route is not None
    assert planner.exits_at(2100, 1000) == planner.exits


def test_an_exit_closing_the_only_way_through_leaves_no_route():
    gate = Volume("Gate", (2000, 500, 0), radius=600)
    planner = _planner(mesh(floor(0, 0, 4000, 1000, 0)), volumes=[gate], teleports=frozenset({"Gate"}))

    assert planner.route((200, 500), (3800, 500)) is None


def test_an_exit_on_another_storey_does_not_close_the_floor_under_it():
    upstairs = Volume("Gate", (500, 500, 400), radius=200)
    planner = _planner(two_storeys(), volumes=[upstairs], teleports=frozenset({"Gate"}))

    route = planner.route((200, 500, 0), (800, 500, 0))

    assert route is not None
    assert all(planner.grid.world(*n)[2] == 0 for n in route.nodes)
    assert _nearest_exit_distance(planner, route.nodes, (500, 500)) < 200


# --- links ---------------------------------------------------------------------------------


def _islands() -> ZonePlanner:
    """Two islands 2000 apart, the west one's volume landing on the east one."""
    volume = Volume("TeleportVol To Isle", (800, 500, 0), radius=90)
    named = {"Target To Isle": (3200, 500, 0)}
    return _planner(
        mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0)),
        named=named,
        volumes=[volume],
        teleports=frozenset({volume.name}),
    )


def test_a_route_with_a_link_comes_back_as_legs_each_ending_at_its_link():
    planner = _islands()

    route = planner.route((100, 500), (3900, 500))
    legs = planner.legs(route)

    assert [leg.link.name if leg.link else None for leg in legs] == ["TeleportVol To Isle", None]
    assert legs[0].waypoints[-1][:2] == (800, 500)
    assert math.dist(legs[1].waypoints[-1][:2], (3900, 500)) <= planner.grid.cell
    assert len(planner.waypoints(route)) == sum(len(leg.waypoints) for leg in legs)
    turns, jumps = planner.turns_and_jumps(route)
    [jump] = jumps
    assert planner.grid.world(*turns[jump])[0] < 1000 < 3000 < planner.grid.world(*turns[jump + 1])[0]


def _short_ladder() -> ZonePlanner:
    """Two storeys with no stairs, joined by a ladder at x 1100 on the ground whose top lands on
    the floor above at x 940: 160 across, as Tyrian Gorge's are 200 to 460, beside the 150 a
    press is taken from. The ground runs on under the floor above, as it does along a cliff's
    foot, so the floor the ladder is taken from reaches the cell next to its landing."""
    planner = _planner(two_storeys(stairs=False))
    planner.links.append(Link("Ladder 01", (1100, 500, 0), "Ladder 01 top", (940, 500, 400), kind="interact"))
    return planner


def test_a_link_landing_the_next_cell_across_from_where_it_is_taken_still_ends_a_leg():
    planner = _short_ladder()

    route = planner.route((200, 500, 0), (500, 500, 400))
    legs = planner.legs(route)

    taken, landed = next((a, b) for a, b in zip(route.nodes, route.nodes[1:]) if a[0] != b[0])
    assert abs(taken[1] - landed[1]) <= 1 and abs(taken[2] - landed[2]) <= 1
    assert [leg.link.name if leg.link else None for leg in legs] == ["Ladder 01", None]
    assert {p[2] for p in legs[0].waypoints} == {0}
    assert {p[2] for p in legs[1].waypoints} == {400}
    turns, jumps = planner.turns_and_jumps(route)
    [jump] = jumps
    assert planner.grid.world(*turns[jump])[2] == 0 and planner.grid.world(*turns[jump + 1])[2] == 400


def _hall_with_a_volume_link(width: float = 2000) -> ZonePlanner:
    """A hall 4000 long with a volume link in its middle, landing on an island far off."""
    volume = Volume("TeleportVol To Isle", (2000, width / 2, 0), radius=90)
    return _planner(
        mesh(floor(0, 0, 4000, width, 0), floor(6000, 0, 7000, 1000, 0)),
        named={"Target To Isle": (6500, 500, 0)},
        volumes=[volume],
        teleports=frozenset({volume.name}),
    )


def _crosses(planner: ZonePlanner, route, center, radius) -> bool:
    """Whether any straight stretch walked comes within *radius* of *center*."""
    for leg in planner.legs(route):
        for a, b in zip(leg.waypoints, leg.waypoints[1:]):
            for t in np.linspace(0, 1, 100):
                if math.dist((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), center) <= radius:
                    return True
    return False


def test_a_route_walks_around_a_volume_link_it_does_not_take():
    planner = _hall_with_a_volume_link()

    route = planner.route((200, 1000), (3800, 1000))

    assert [leg.link for leg in planner.legs(route)] == [None]
    assert not _crosses(planner, route, (2000, 1000), LINK_REACH)
    assert not _crosses(planner, route, (2000, 1000), link_reach(planner.links[0]) - planner.grid.cell)


def test_a_route_starting_in_a_volume_link_walks_out_of_it():
    planner = _hall_with_a_volume_link()

    route = planner.route((2000, 1000), (3800, 1000))

    assert route is not None
    assert [leg.link for leg in planner.legs(route)] == [None]


def test_a_volume_link_across_the_only_way_is_walked_through_rather_than_no_route():
    planner = _hall_with_a_volume_link(width=150)

    route = planner.route((200, 75), (3800, 75))

    assert route is not None
    assert [leg.link for leg in planner.legs(route)] == [None]


def test_a_zones_volume_link_is_not_one_of_its_exits():
    planner = _islands()
    assert [link.name for link in planner.links] == ["TeleportVol To Isle"]
    assert planner.exits == []


def test_a_dropped_link_is_routed_through_no_more():
    planner = _islands()
    planner.drop(planner.links[0])
    assert planner.route((100, 500), (3900, 500)) is None


def test_a_planner_around_live_mobs_routes_around_them_and_leaves_the_original_alone():
    planner = _planner(mesh(floor(0, 0, 4000, 2000, 0)))

    around = planner.around([(2000, 1000, 400)])
    route = around.route((200, 1000), (3800, 1000))

    assert min(math.dist(around.grid.world(*n)[:2], (2000, 1000)) for n in route.nodes) > 400
    assert planner.route((200, 1000), (3800, 1000)) is not None
    assert planner.passable.sum() > around.passable.sum()


def test_blocking_a_spot_a_walk_ran_into_closes_the_floor_there():
    planner = _planner(mesh(floor(0, 0, 4000, 2000, 0)))
    closed = planner.block(2000, 1000, 0, 200)

    assert closed > 0
    assert not planner.passable[(0, *planner.grid.index(2000, 1000))]


def test_a_point_off_every_floor_has_no_route():
    planner = _planner(mesh(floor(0, 0, 400, 400, 0)))
    planner.passable[:] = False
    assert planner.route((100, 100), (300, 300)) is None


# --- what planning reads -------------------------------------------------------------------

PLANNER = ("collision", "zone_grid", "patrols", "zone_links", "gates", "route")
MOVEMENT = Path(__file__).resolve().parents[3] / "bartleby" / "runtime" / "movement"


@pytest.mark.parametrize("module", PLANNER)
def test_no_planner_module_opens_a_file_or_imports_katsuba(module):
    tree = ast.parse((MOVEMENT / f"{module}.py").read_text(encoding="utf-8"))
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    calls = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }

    assert not {name for name in imports if name.split(".")[0] in ("katsuba", "wizwalker", "pathlib", "io")}
    assert not calls & {"open", "read_bytes", "read_text", "mmap", "load", "fromfile"}


# --- gates -------------------------------------------------------------------------------------

#: A gate 120 deep, filling a doorway 200 wide in a wall at x 1500.
GATE_DEPTH = 120.0


def _gate(name="Door", y0=400.0, y1=600.0, x=1500.0, depth=GATE_DEPTH) -> Gate:
    hull = np.array([(x - depth / 2, y0), (x + depth / 2, y0), (x + depth / 2, y1), (x - depth / 2, y1)])
    return Gate(name, "Door", (x, (y0 + y1) / 2, 0.0), (Footprint(hull, (0.0, 0.0), 0.0, 0.0, 300.0, True),))


def _walled(*gaps: tuple[float, float], gates=()) -> ZonePlanner:
    """A hall 3000 by 1000 cut in two by a wall at x 1500, 120 deep, open at each (y0, y1) of *gaps*."""
    edges = [0.0] + [v for gap in gaps for v in gap] + [1000.0]
    walls = [
        box((1500, (lo + hi) / 2, 150), GATE_DEPTH, hi - lo, 300)
        for lo, hi in zip(edges[::2], edges[1::2], strict=True)
        if hi > lo
    ]
    return _planner(mesh(floor(0, 0, 3000, 1000, 0)), *walls, gates=list(gates))


def test_a_gate_is_open_until_closed_so_a_route_crosses_it_and_names_it():
    planner = _walled((400, 600), gates=[_gate()])

    route = planner.route((300, 500), (2700, 500))

    assert route is not None
    assert [g.name for g in planner.gates_on(route)] == ["Door"]


def test_a_placed_solid_closed_takes_its_floor_and_the_clearance_around_it():
    planner = _walled((400, 600), gates=[_gate()])
    spot = (0, *planner.grid.index(1500, 500))
    beside = (0, *planner.grid.index(1500 - GATE_DEPTH / 2 - 20, 500))
    assert planner.passable[spot] and planner.passable[beside]

    taken = planner.close(planner.gates[0])

    assert taken > 0
    assert not planner.passable[spot] and not planner.passable[beside]
    assert planner.closed == [planner.gates[0]]
    assert planner.close(planner.gates[0]) == 0  # closed already


def test_a_route_that_can_walk_around_a_closed_gate_walks_around_it():
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    planner.close(planner.gates[0])

    route = planner.route((300, 500), (2700, 500))

    assert route is not None
    assert planner.gates_on(route) == []
    assert min(planner.grid.world(*n)[1] for n in route.nodes if abs(planner.grid.world(*n)[0] - 1500) < 60) > 750


def test_a_closed_gate_that_separates_two_floors_is_named_with_its_crossing():
    planner = _walled((400, 600), gates=[_gate()])
    planner.close(planner.gates[0])

    assert planner.route((300, 500), (2700, 500)) is None
    why = planner.no_route((300, 500), (2700, 500))

    assert why.gate == "Door"
    assert (why.zone, why.start[:2], why.goal[:2]) == (ZONE, (300.0, 500.0), (2700.0, 500.0))
    # The gate's depth, the clearance each side, to within a cell.
    assert why.crossing.length == pytest.approx(GATE_DEPTH + 2 * CLEARANCE, abs=CELL)
    assert why.crossing.before[0] < 1500 < why.crossing.after[0]


def test_a_goal_on_a_closed_gates_floor_has_no_route_and_the_gate_is_named():
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    planner.close(planner.gates[0])

    # Not snapped to the floor beside the gate, short of it as if arrived.
    assert planner.route((300, 500), (1500, 500)) is None
    assert planner.no_route((300, 500), (1500, 500)).gate == "Door"
    assert planner.route((300, 500), (1300, 500)) is not None  # short of the gate: a route
    assert planner.route((300, 500), (2700, 500)) is not None  # beyond it, by the other way


def test_no_route_names_no_gate_when_none_is_closed_or_opening_one_would_not_help():
    planner = _planner(mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0)), gates=[_gate(x=2000)])
    assert planner.no_route((500, 500), (3500, 500)).gate is None
    planner.close(planner.gates[0])
    assert planner.no_route((500, 500), (3500, 500)).gate is None


def test_no_route_plans_once_when_even_every_gate_open_gives_none(monkeypatch):
    """Each gate tried is a plan of its own, seconds each in a big zone. Floor no gate joins
    needs one plan, with them all open, to say no one gate would help."""
    planner = _planner(
        mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0)),
        gates=[_gate(x=300, name="A"), _gate(x=600, name="B"), _gate(x=3500, name="C")],
    )
    for gate in planner.gates:
        planner.close(gate)
    plans = []
    route = ZonePlanner.route
    monkeypatch.setattr(ZonePlanner, "route", lambda self, *a, **k: plans.append(1) or route(self, *a, **k))

    assert planner.no_route((800, 500), (3800, 500)).gate is None
    assert len(plans) == 1


def test_no_route_still_names_the_one_gate_in_the_way_among_several_closed():
    planner = _walled((400, 600), gates=[_gate(), _gate(x=600, y0=100.0, y1=200.0, name="Elsewhere")])
    for gate in planner.gates:
        planner.close(gate)

    assert planner.no_route((300, 500), (2700, 500)).gate == "Door"


def test_a_search_whose_stop_is_set_ends_with_plan_stopped():
    planner = _walled((400, 600), gates=[_gate()])
    planner.close(planner.gates[0])
    stop = threading.Event()
    stop.set()

    with pytest.raises(PlanStopped):
        planner.route((300, 500), (2700, 500), stop=stop)
    with pytest.raises(PlanStopped):
        planner.no_route((300, 500), (2700, 500), stop=stop)


def test_a_gate_opened_again_gives_back_the_floor_its_closing_took():
    planner = _walled((400, 600), gates=[_gate()])
    before = planner.passable.copy()
    taken = planner.close(planner.gates[0])

    assert planner.open(planner.gates[0]) == taken
    assert planner.closed == []
    assert (planner.passable == before).all()
    assert planner.route((300, 500), (2700, 500)) is not None
    assert planner.open(planner.gates[0]) == 0  # open already


def test_opening_a_gate_leaves_closed_what_another_closed_gate_or_a_block_still_takes():
    one, other = _gate("One", 400, 600), _gate("Other", 400, 600, x=1580)  # overlapping
    planner = _walled((400, 600), gates=[one, other])
    planner.close(one)
    planner.close(other)
    planner.block(1420, 500, 0, 30)  # on One's floor alone, closed already, so it takes nothing new
    shared = (0, *planner.grid.index(1540, 500))
    blocked = (0, *planner.grid.index(1420, 500))

    planner.open(one)

    assert planner.closed == [other]
    assert not planner.passable[shared]
    assert not planner.passable[blocked]
    assert planner.route((300, 500), (2700, 500)) is None


def test_a_path_of_waypoints_crosses_a_gate_when_it_steps_on_its_floor_at_its_height():
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    gate = planner.gates[0]

    assert planner.crosses(gate, [(300, 500, 0), (2700, 500, 0)])
    assert not planner.crosses(gate, [(300, 500, 0), (1300, 500, 0)])  # stops short of it
    assert not planner.crosses(gate, [(300, 850, 0), (2700, 850, 0)])  # through the other gap
    assert not planner.crosses(gate, [(300, 500, 900), (2700, 500, 900)])  # a storey above


def test_gates_near_a_point_are_the_open_ones_within_reach_nearest_first():
    near, far = _gate("Near", 400, 600), _gate("Far", 400, 600, x=2400)
    planner = _walled((400, 600), gates=[far, near])

    assert [g.name for g in planner.gates_near(1350, 500, 0, 150)] == ["Near"]
    planner.close(near)
    assert planner.gates_near(1350, 500, 0, 150) == []


def test_a_link_objects_solid_is_a_wall_and_its_link_is_still_taken_from_the_floor_around_it():
    teleport = InteractTemplate("Stone", "Press X to Teleport")
    placed = [PlacedObject("Stone_A", (500, 500, 0), teleport), PlacedObject("Stone_A_B", (3500, 500, 0), teleport)]
    stone = Gate("Stone_A", "Stone", (500.0, 500.0, 0.0), (Footprint(None, (500.0, 500.0), 43.0, 0.0, 210.0, True),))
    planner = _planner(
        mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0)),
        named={"Near": (600, 500, 0), "Far": (3400, 500, 0)},
        placed=placed,
        gates=[stone],
    )

    assert planner.gates[0].wall
    assert (
        not planner.passable[(0, *planner.grid.index(500, 500))]
        and planner.grid.walls[(0, *planner.grid.index(500, 500))]
    )
    assert planner.gates_near(500, 500, 0, 150) == []  # never closed: it is a wall
    assert any(link.name == "Stone_A" for link in planner.links)
    assert planner.route((200, 200), (3800, 800)) is not None


# --- a walk into a fight -------------------------------------------------------------------


def _two_patrols() -> ZonePlanner:
    """The street, with a second ghost walking its far corner, at x 3500 from y 1500 to 2000."""
    ghost = Patrol("Ghosts 01", "Ghost", 200, 1, np.array([(2000, 0, 0), (2000, 900, 0)], dtype=float))
    other = Patrol("Ghosts 02", "Ghost", 200, 1, np.array([(3500, 1500, 0), (3500, 2000, 0)], dtype=float))
    return _planner(mesh(floor(0, 0, 4000, 2000, 0)), patrols=[ghost, other])


def _cost_at(planner: ZonePlanner, cost, x: float, y: float) -> float:
    j, i = planner.grid.index(x, y)
    return float(cost[j, i])


def test_a_fight_spares_the_patrol_of_the_mob_it_meets_and_costs_every_other():
    planner = _two_patrols()

    cost = planner.fight_cost((2000, 450, 0))

    assert _cost_at(planner, cost, 2000, 450) == 1.0
    assert _cost_at(planner, cost, 3500, 1700) == _cost_at(planner, planner.cost, 3500, 1700) > 1.0


def test_a_fight_nowhere_near_a_patrol_costs_what_any_walk_does():
    planner = _two_patrols()

    assert planner.fight_cost((200, 1900, 0)) is planner.cost


def test_a_route_into_a_fight_walks_beside_its_mobs_patrol_where_any_other_walk_swings_wide():
    planner = _two_patrols()
    start, goal = (2300, 50), (2000, 850)  # up the ghost's own path, 300 units off it

    fight = planner.route(start, goal, avoid=True, fight=True)
    wary = planner.route(start, goal, avoid=True)

    straight = math.dist(start, goal)
    assert _length(planner, fight.nodes) < straight + 2 * planner.grid.cell
    assert _length(planner, wary.nodes) > straight * 1.5


def test_a_route_into_a_fight_still_keeps_off_another_patrol_in_its_way():
    planner = _two_patrols()

    route = planner.route((3800, 1000), (2000, 450), avoid=True, fight=True)

    room = planner.clearance  # the other ghost is the nearer patrol all along its side of the street
    assert min(room[j, i] for _, j, i in route.nodes if planner.grid.world(j=j, i=i, layer=0)[0] > 3000) >= HARD


def test_without_avoiding_a_fight_takes_the_shortest_route_as_any_walk_does():
    planner = _two_patrols()

    route = planner.route((200, 450), (2000, 450), avoid=False, fight=True)

    assert _length(planner, route.nodes) == pytest.approx(1800, abs=2 * planner.grid.cell)


# --- a point outside the exits ---------------------------------------------------------------


def test_a_point_no_exit_holds_is_its_own_spot_outside_the_exits():
    planner = _hall_with_an_exit()

    assert planner.outside_exits(3500, 1000, 0) == (3500, 1000, 0)


def test_a_point_inside_an_exit_gives_the_nearest_floor_outside_its_no_go_area():
    planner = _hall_with_an_exit()

    spot = planner.outside_exits(2200, 1000, 0)

    assert spot is not None
    assert planner.exits_at(*spot) == []
    assert math.dist(spot[:2], (2000, 1000)) == pytest.approx(300 + EXIT_MARGIN, abs=2 * planner.grid.cell)
    assert spot[0] > 2000  # on the side the point was, not across the exit
    route = planner.route((3800, 1000), spot)
    assert _nearest_exit_distance(planner, route.nodes) > 300 + EXIT_MARGIN - planner.grid.cell


def test_a_zone_with_no_floor_outside_its_exits_gives_none():
    gate = Volume("TeleportVol_Gate", (500, 500, 0), radius=2000)
    planner = _planner(mesh(floor(0, 0, 1000, 1000, 0)), volumes=[gate], teleports=frozenset({gate.name}))

    assert planner.outside_exits(500, 500, 0) is None

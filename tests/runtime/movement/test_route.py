"""Routes across one zone: by its stairs, around its patrols, out of its exits, through its links."""

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from bartleby.runtime.movement.patrols import HARD, Patrol
from bartleby.runtime.movement.route import EXIT_MARGIN, ZoneFiles, ZonePlanner, build_planner
from bartleby.runtime.movement.zone_grid import MAX_RISE, STEP_TOL
from bartleby.runtime.movement.zone_links import Volume
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

PLANNER = ("collision", "zone_grid", "patrols", "zone_links", "route")
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

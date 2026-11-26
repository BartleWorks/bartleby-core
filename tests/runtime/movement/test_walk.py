"""Walking a route on a simulated client: arriving, resets, stalls, links, mobs, and ending."""

import ast
import asyncio
import dataclasses
import itertools
import math
import threading
import time
from pathlib import Path

import pytest

from bartleby.runtime.arbiter import Arbiter, Owner
from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import ClientInput
from bartleby.runtime.movement import walk as walk_module
from bartleby.runtime.movement.route import ZoneFiles, ZonePlanner, build_planner
from bartleby.runtime.movement.walk import ClientRead, GateRead, Listing, RouteWalker, WalkEnd, rejoin
from bartleby.runtime.movement.zone_links import Link, Volume
from bartleby.runtime.testing.sim_client import SimClient, SimEntity
from tests.runtime.movement.test_route import _gate, _walled
from tests.runtime.movement.zone_fixtures import floor, mesh

ZONE = "Test/T_Street"


def _planner(*floors, **files) -> ZonePlanner:
    return build_planner(ZoneFiles(ZONE, [mesh(*floors)], [], dict(files.pop("named", {})), **files))


def _hall() -> ZonePlanner:
    return _planner(floor(0, 0, 4000, 2000, 0))


def _client(at=(200.0, 1000.0, 0.0), **kw) -> SimClient:
    # A mount's +100%, so the tests walk at 1,200 units a second; a walk never writes it.
    return SimClient(position=at, zone=ZONE, speed=100, **kw)


async def _walk(client, planner, goal, **kw):
    arbiter = Arbiter()
    async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
        return await RouteWalker(client, planner, **kw).walk(lease, goal)


# --- arriving --------------------------------------------------------------------------------


class _Recorded(ClientInput):
    """A lease's input that notes every key and yaw it is asked for."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.asked: list[str] = []

    async def hold(self, action):
        held = await super().hold(action)
        if held:
            self.asked.append("down")
        return held

    async def write_yaw(self, yaw):
        self.asked.append("yaw")
        await super().write_yaw(yaw)


async def test_a_walk_reaches_its_goal_within_50_units_pressing_only_through_its_lease():
    client = _client()
    inputs = []

    def make(c):
        inputs.append(_Recorded(c))
        return inputs[-1]

    async with Arbiter(make_input=make).acquire(client, Owner.MOVEMENT) as lease:
        result = await RouteWalker(client, _hall()).walk(lease, (2600, 1400, 0))

    assert result.end is WalkEnd.ARRIVED
    assert result.miss <= 50
    assert client.held == set()
    [asked] = inputs
    sent = [r.kind for r in client.inputs if r.kind in ("down", "yaw")]
    # Every first key-down and every yaw the client saw was one the lease's input was asked for.
    assert sent.count("yaw") == asked.asked.count("yaw") > 0
    assert len({r.detail for r in client.inputs if r.kind == "down"}) <= asked.asked.count("down")


async def test_a_goal_with_no_floor_between_is_no_route_and_presses_nothing():
    client = _client()
    planner = _planner(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0))
    client.position = (200.0, 500.0, 0.0)

    result = await _walk(client, planner, (3500, 500, 0))

    assert result.end is WalkEnd.NO_ROUTE
    assert [r for r in client.inputs if r.kind == "down"] == []


async def test_every_plan_runs_off_the_event_loop():
    client = _client()
    planner = _hall()
    threads = []
    route, legs = planner.route, planner.legs

    def noting(fn):
        def run(*a, **k):
            threads.append(threading.current_thread())
            return fn(*a, **k)

        return run

    planner.route, planner.legs = noting(route), noting(legs)
    result = await _walk(client, planner, (1400, 1000, 0))

    assert result.arrived
    assert threads and threading.main_thread() not in threads


# --- resets and jumps --------------------------------------------------------------------------


async def _when(client, test, then, timeout=5.0):
    began = time.monotonic()
    while not test(client.position) and time.monotonic() - began < timeout:
        await asyncio.sleep(0.01)
    then()


async def test_a_position_thrown_back_mid_walk_is_walked_again_not_replanned():
    client = _client()
    thrown = asyncio.create_task(_when(client, lambda p: p[0] > 1500, lambda: client.place((900.0, 1000.0, 0.0))))

    result = await _walk(client, _hall(), (2600, 1000, 0))
    await thrown

    assert result.arrived
    assert result.replans == 0
    assert any(p[0] > 1500 for p in result.trail) and any(p[0] < 1000 for p in result.trail[10:])


async def test_a_jump_off_the_route_is_replanned_from_where_it_landed():
    client = _client()
    moved = asyncio.create_task(_when(client, lambda p: p[0] > 1000, lambda: client.place((1200.0, 200.0, 0.0))))

    result = await _walk(client, _hall(), (2600, 1000, 0))
    await moved

    assert result.arrived
    assert result.replans == 1


async def test_a_jump_into_another_zone_ends_the_walk_unplanned():
    # The other zone's spot is on a floor of this one that joins nothing: planned on, no route.
    client = _client(at=(200.0, 500.0, 0.0))
    planner = _planner(floor(0, 0, 2000, 1000, 0), floor(3000, 0, 4000, 1000, 0))
    door = asyncio.create_task(
        _when(client, lambda p: p[0] > 800, lambda: client.place((3500.0, 500.0, 0.0), zone="Test/T_Other"))
    )

    result = await _walk(client, planner, (1800, 500, 0))
    await door

    assert result.end is WalkEnd.ZONE_CHANGED
    assert result.replans == 0


async def test_a_jump_into_a_fight_ends_the_walk_unplanned():
    client = _client()

    def pulled_in():
        client.place((1200.0, 200.0, 0.0))
        client.battle = True

    fight = asyncio.create_task(_when(client, lambda p: p[0] > 800, pulled_in))

    result = await _walk(client, _hall(), (3000, 1000, 0))
    await fight

    assert result.end is WalkEnd.FIGHT
    assert result.replans == 0


def test_a_reset_onto_a_leg_of_one_waypoint_has_walked_it():
    assert walk_module._next_ahead([(500.0, 500.0, 0.0)], (520.0, 500.0, 0.0)) == 1


# --- stalls ----------------------------------------------------------------------------------


@pytest.fixture
def quick_stalls(monkeypatch):
    # Eight ticks still fit the window. A stall is the branch the tests name: the wall never gives.
    monkeypatch.setattr(walk_module, "WALK_STALL_WINDOW", 0.4)


async def test_a_stall_on_something_the_map_lacks_is_backed_off_from_and_walked_around(quick_stalls):
    post = (1000.0, 1000.0)

    def solid(old, new):
        return old if math.dist(new[:2], post) < 60 else new

    client = _client(on_step=solid)
    result = await _walk(client, _hall(), (1800, 1000, 0))

    assert result.arrived
    assert 1 <= result.recoveries <= walk_module.MAX_RECOVERIES


async def test_three_stalls_are_recovered_and_the_fourth_ends_the_walk_blocked(quick_stalls):
    client = _client(on_step=lambda old, new: old if new[0] > 1000 else new)  # a wall the map lacks
    result = await _walk(client, _hall(), (1800, 1000, 0))

    assert result.end is WalkEnd.BLOCKED
    assert result.recoveries == 4
    assert "stalled" in result.detail
    assert client.held == set()


def _shut_door(old, new):
    """The gate test_route's _gate stands for, there in the game: a wizard, some 45 across, stops
    short of its 120-deep solid in the doorway from y 400 to 600."""
    return old if abs(new[0] - 1500) < 60 + 45 and 400 - 45 < new[1] < 600 + 45 else new


async def test_a_stall_at_a_gate_closes_it_and_the_walk_goes_around_by_another_way(quick_stalls):
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    # An entity list that cannot be read: the stall is all the walk has to go on.
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=None)

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.arrived
    assert result.gates_closed == ["Door"]
    assert result.recoveries == 1


async def test_a_stall_at_a_gate_with_no_way_around_ends_no_route_naming_it_and_the_crossing(quick_stalls):
    planner = _walled((400, 600), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=None)

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.end is WalkEnd.NO_ROUTE
    assert result.gates_closed == ["Door"]
    assert result.no_route.gate == "Door"
    assert result.no_route.crossing.before[0] < 1500 < result.no_route.crossing.after[0]
    assert "the closed gate Door is in the way" in result.describe()
    assert client.held == set()


async def test_a_stall_nowhere_near_a_gate_is_recovered_as_before_and_closes_none(quick_stalls):
    post = (1000.0, 500.0)
    planner = _walled((400, 600), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=lambda old, new: old if math.dist(new[:2], post) < 60 else new)

    result = await _walk(client, planner, (1200, 500, 0))

    assert result.arrived
    assert result.gates_closed == [] and planner.closed == []


# --- gates the client lists ----------------------------------------------------------------


@pytest.fixture
def quick_passes(monkeypatch):
    # A pass over the entity list every 0.2 s, so a walk of a second or two sees several.
    monkeypatch.setattr(walk_module, "WALK_MOB_REFRESH", 0.2)


def _door() -> SimEntity:
    """The client's entity for test_route's _gate: its template's name, where it is placed."""
    return SimEntity("Door", (1500.0, 500.0, 0.0))


async def test_a_listed_gate_on_the_route_is_planned_around_before_it_is_reached_with_no_stall(quick_stalls):
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=[_door()])

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.arrived
    assert result.gates_seen == ["Door"]
    assert result.recoveries == 0 and result.replans == 0  # closed before the first plan
    assert result.gates_closed == []
    assert "gates listed, so closed: Door" in result.describe()


async def test_a_listed_gate_with_no_way_around_ends_no_route_naming_it_and_presses_nothing():
    planner = _walled((400, 600), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=[_door()])

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.end is WalkEnd.NO_ROUTE
    assert result.no_route.gate == "Door"
    assert result.recoveries == 0
    assert [r for r in client.inputs if r.kind == "down"] == []
    # Why first, then the gates the read closed.
    assert result.describe().index("is in the way") < result.describe().index("gates listed")


async def test_a_goal_behind_a_listed_gate_ends_no_route_naming_it_not_arrived_short_of_it():
    # The Shopping District's Colossus blocker: the goal, a zone's exit, is where the gate stands.
    planner = _walled((400, 600), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=[_door()])

    result = await _walk(client, planner, (1500, 500, 0))

    assert result.end is WalkEnd.NO_ROUTE
    assert result.no_route.gate == "Door"
    assert [r for r in client.inputs if r.kind == "down"] == []


async def test_a_gate_listed_mid_walk_on_the_way_left_is_replanned_around_with_no_stall(quick_stalls, quick_passes):
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door)
    # Spawned once the wizard is on its way: unlisted at the first pass, listed from x 600.
    client.entities = [SimEntity("Door", lambda: (1500.0, 500.0, 0.0) if client.position[0] > 600 else None)]

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.arrived
    assert result.gates_seen == ["Door"]
    assert result.recoveries == 0 and result.replans >= 1


async def test_a_gate_the_client_does_not_list_stays_open_and_the_route_crosses_it():
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0), entities=[])

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.arrived
    assert result.gates_seen == [] and planner.closed == []
    assert max(p[1] for p in result.trail if abs(p[0] - 1500) < 60) < 600  # through the doorway


async def test_a_stall_near_a_gate_the_client_shows_gone_blocks_the_floor_ahead_and_leaves_the_gate_open(
    quick_stalls,
):
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    blocks = []
    block = planner.block

    def noting(*a):
        blocks.append(a)
        return block(*a)

    planner.block = noting
    # Something the map lacks stands in the doorway, where the client lists no gate.
    client = _client(at=(300.0, 500.0, 0.0), on_step=_shut_door, entities=[])

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.recoveries >= 1 and blocks
    assert result.gates_closed == [] and planner.closed == []


async def test_a_gate_the_read_closed_opens_again_once_the_client_shows_it_gone(quick_passes):
    planner = _walled((400, 600), (750, 1000), gates=[_gate()])
    client = _client(at=(300.0, 500.0, 0.0))
    door = SimEntity("Door", lambda: (1500.0, 500.0, 0.0) if door.reads <= 1 else None)  # gone after the first pass
    client.entities = [door]

    result = await _walk(client, planner, (2700, 500, 0))

    assert result.arrived
    assert result.gates_seen == ["Door"] and result.gates_opened == ["Door"]
    assert planner.closed == []
    assert "opened again, gone: Door" in result.describe()


def _pass(at, *objects) -> Listing:
    return Listing(at, list(objects), {})


def test_a_gate_read_closes_what_is_listed_and_takes_for_gone_only_what_is_unlisted_in_range():
    near, far = _gate("Near"), _gate("Far", x=1500 + walk_module.GATE_LISTED_RANGE + 1000)
    planner = _walled((400, 600), gates=[near, far])
    read = GateRead(planner)

    closed, opened = read.update(_pass((1000.0, 500.0, 0.0)))
    assert (closed, opened) == ([], [])
    assert read.gone == {near}  # Far is beyond range: not known, so left open
    assert planner.closed == []

    closed, _ = read.update(_pass((1000.0, 500.0, 0.0), ("Door", far.at)))  # listed far off: exempt from AOI
    assert closed == [far] and planner.closed == [far]
    assert read.update(_pass((1000.0, 500.0, 0.0), ("Door", far.at))) == ([], [])  # closed already


def test_a_listed_object_is_a_gate_only_by_its_template_and_its_placed_spot():
    planner = _walled((400, 600), gates=[_gate()])
    read = GateRead(planner)
    x, y, z = planner.gates[0].at

    assert read.listed(_pass((0.0, 0.0, 0.0), ("Door", (x + walk_module.GATE_MATCH + 5, y, z)))) == set()
    assert read.listed(_pass((0.0, 0.0, 0.0), ("Chest", (x, y, z)))) == set()
    assert read.listed(_pass((0.0, 0.0, 0.0), ("Door", (x + 3, y, z)))) == {planner.gates[0]}


def test_a_gate_the_read_closed_opens_after_passes_in_a_row_agree_it_is_gone():
    planner = _walled((400, 600), gates=[_gate()])
    gate = planner.gates[0]
    read = GateRead(planner)
    here, listed = (1000.0, 500.0, 0.0), ("Door", gate.at)
    read.update(_pass(here, listed))

    assert read.update(_pass(here)) == ([], [])  # once missing: perhaps a read that skipped it
    read.update(_pass(here, listed))  # back: the count starts again
    for _ in range(walk_module.GATE_GONE_PASSES - 1):
        assert read.update(_pass(here)) == ([], [])
    assert read.update(_pass(here)) == ([], [gate])
    assert planner.closed == []


def test_a_gate_a_stall_closed_is_never_opened_by_a_read():
    planner = _walled((400, 600), gates=[_gate()])
    gate = planner.gates[0]
    read = GateRead(planner)
    planner.close(gate)  # as the walk does at a stall

    for _ in range(walk_module.GATE_GONE_PASSES + 1):
        assert read.update(_pass((1000.0, 500.0, 0.0))) == ([], [])
    assert planner.closed == [gate]


def test_a_link_objects_wall_is_never_read_as_a_gate():
    planner = _walled((400, 600), gates=[dataclasses.replace(_gate(), wall=True)])
    read = GateRead(planner)

    assert read.update(_pass((1000.0, 500.0, 0.0), ("Door", planner.gates[0].at))) == ([], [])
    assert read.gone == set()


# --- links -----------------------------------------------------------------------------------


def _islands() -> ZonePlanner:
    """Two islands 2000 apart, the west one's volume landing on the east."""
    floors = [floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0)]
    volume = Volume("TeleportVol To Isle", (800, 600, 0), radius=90)
    return _planner(
        *floors,
        named={"Target To Isle": (3200, 600, 0)},
        volumes=[volume],
        teleports=frozenset({volume.name}),
    )


def _volume(center, landing, radius=90.0):
    def step(old, new):
        return landing if math.dist(new[:2], center[:2]) < radius else new

    return step


async def test_a_volume_link_moves_the_walk_on_and_it_walks_on_from_where_it_landed():
    client = _client(at=(100.0, 600.0, 0.0), on_step=_volume((800, 600), (3200.0, 600.0, 0.0)))

    result = await _walk(client, _islands(), (3900, 600, 0))

    assert result.arrived
    assert result.links_taken == 1


async def test_a_link_that_moves_nobody_is_dropped_and_the_route_replanned(monkeypatch):
    # A ceiling, cut since running out is the branch tested: nothing ever moves the wizard here.
    monkeypatch.setattr(walk_module, "WALK_LINK_WAIT", 0.3)
    client = _client(at=(100.0, 600.0, 0.0))
    planner = _islands()

    result = await _walk(client, planner, (3900, 600, 0))

    assert result.links_dropped == 1
    assert planner.links == []
    # Replanned without it: and with no other way across, the walk ends there.
    assert result.end is WalkEnd.NO_ROUTE
    assert "without TeleportVol To Isle" in result.detail


async def test_a_pressed_link_is_pressed_only_once_its_prompt_shows():
    planner = _planner(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0))
    planner.links = [Link("Lever", (900.0, 500.0, 0.0), "Target Lever", (3200.0, 500.0, 0.0), kind="interact")]
    client = _client(at=(100.0, 500.0, 0.0))
    shown_at = []

    def press(action):
        if action is GameAction.INTERACT and client.npc_range:
            client.place((3200.0, 500.0, 0.0))

    client.on_press = press

    async def prompt():
        await _when(client, lambda p: math.dist(p[:2], (900, 500)) < 200, lambda: None)
        await asyncio.sleep(0.4)  # the game shows the prompt a moment after the wizard stops
        client.npc_range = True
        shown_at.append(time.monotonic())

    showing = asyncio.create_task(prompt())
    result = await _walk(client, planner, (3800, 500, 0))
    await showing

    assert result.arrived
    assert result.links_taken == 1
    assert client.presses(GameAction.INTERACT) == 1


# --- mobs ------------------------------------------------------------------------------------


def _mobs_at(client, *where):
    client.entities = [SimEntity("Ghost", w, mob=True) for w in where]
    return client


async def test_a_mob_in_the_way_is_detoured_around_before_it_is_waited_for():
    mob = (1500.0, 1000.0, 0.0)
    client = _mobs_at(_client(), mob)

    result = await _walk(client, _hall(), (3000, 1000, 0), watch_mobs=True)

    assert result.arrived
    assert result.detours >= 1
    assert result.waited == 0
    assert min(math.dist(p[:2], mob[:2]) for p in result.trail) > walk_module.DEFAULT_TRIGGER


async def test_a_mob_with_no_way_around_is_waited_for_and_then_walked_past_unwatched(monkeypatch, caplog):
    # Cut, since running out is the branch tested: the mob never leaves the corridor, and stands
    # in it, so no margin leaves room.
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 0.6)
    client = _client(at=(200.0, 100.0, 0.0))
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    _mobs_at(client, (2000.0, 100.0, 0.0))
    with caplog.at_level("INFO", logger=walk_module.__name__):
        result = await _walk(client, corridor, (3800, 100, 0), watch_mobs=True)

    assert result.arrived
    assert result.ran_through == 1
    assert result.detours == 0
    assert result.waited > 0.5
    assert client.held == set()
    [through] = [r for r in caplog.records if "walking on past them unwatched" in r.message]
    assert through.levelname == "WARNING"
    assert "walked on past mobs unwatched once, last held by Ghost" in result.describe()


async def test_held_up_long_a_walk_keeps_only_the_narrow_margin_and_goes_on(monkeypatch, caplog):
    """The Hive's case: the mob's trigger radius leaves the corridor 85 units, under the full
    margin of 120 and over the narrow one of 40, so only waiting long enough lets it by."""
    monkeypatch.setattr(walk_module, "WALK_MOB_EASE", 0.3)
    client = _mobs_at(_client(at=(200.0, 100.0, 0.0)), (2000.0, 460.0, 0.0))
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    with caplog.at_level("INFO", logger=walk_module.__name__):
        result = await _walk(client, corridor, (3800, 100, 0), watch_mobs=True)

    assert result.arrived
    assert (result.eased, result.ran_through) == (1, 0)
    assert 0.3 < result.waited < 2.0
    reach = walk_module.DEFAULT_TRIGGER + walk_module.NARROW_MARGIN
    assert min(math.dist(p[:2], (2000, 460)) for p in result.trail) > reach
    assert any(
        m.endswith(f"keeping only {walk_module.NARROW_MARGIN:.0f} beyond each trigger radius") for m in caplog.messages
    )


async def test_room_too_little_to_go_on_with_does_not_start_a_spell_of_waits_over(monkeypatch, caplog):
    """A waiting walk goes on only with the full margin's slack to spare. Room short of that is no
    room left it: counted as clear, it would start the spell over every WALK_MOB_CLEAR, and the
    walk would never ease, waiting for good."""
    monkeypatch.setattr(walk_module, "WALK_MOB_EASE", 0.5)
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 30.0)
    monkeypatch.setattr(walk_module, "WALK_MOB_CLEAR", 0.2)
    client = _client(at=(200.0, 100.0, 0.0))
    # In the corridor first, so the walk waits; then beside it, where the room ahead, some 30 at
    # the full margin, is under its slack of 60 and well over the narrow margin.
    spot = [(2000.0, 100.0, 0.0)]
    client.entities = [SimEntity("Ghost", lambda: spot[0], mob=True)]
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    async def move():
        while not any(" -> wait " in m for m in caplog.messages):
            await asyncio.sleep(0.02)
        spot[0] = (2000.0, 585.0, 0.0)

    with caplog.at_level("INFO", logger=walk_module.__name__):
        moving = asyncio.create_task(move())
        result = await asyncio.wait_for(_walk(client, corridor, (3800, 100, 0), watch_mobs=True), 10.0)
        await moving

    assert result.arrived
    assert (result.eased, result.ran_through) == (1, 0)
    said = [m for m in caplog.messages if "keeping only" in m or "keeping the full room again" in m]
    assert "keeping only" in said[0], "it eased before anything started its waits over"


async def test_once_the_mobs_leave_it_room_a_walks_waits_start_over_at_the_full_margin(monkeypatch, caplog):
    monkeypatch.setattr(walk_module, "WALK_MOB_EASE", 0.3)
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 30.0)  # never reached in this test's spells
    monkeypatch.setattr(walk_module, "WALK_MOB_CLEAR", 0.2)
    client = _client(at=(200.0, 100.0, 0.0))
    # In the corridor, then gone out of range, then back ahead of the wizard: a spell each side.
    spot = [(1500.0, 460.0, 0.0)]
    client.entities = [SimEntity("Ghost", lambda: spot[0], mob=True)]
    corridor = _planner(floor(0, 0, 5000, 200, 0))

    def said(text):
        return sum(text in m for m in caplog.messages)

    async def move():
        while not said("keeping only"):
            await asyncio.sleep(0.02)
        spot[0] = (30000.0, 100.0, 0.0)  # out of the watch's range: room enough
        while not said("keeping the full room again"):
            await asyncio.sleep(0.02)
        spot[0] = (client.position[0] + 2500.0, 460.0, 0.0)

    with caplog.at_level("INFO", logger=walk_module.__name__):
        moving = asyncio.create_task(move())
        result = await _walk(client, corridor, (4800, 100, 0), watch_mobs=True)
        await moving

    assert result.arrived
    assert result.eased == 2, "the second spell eased again from the full margin"
    assert said("keeping the full room again") == 2, "once past each spot"


async def test_a_walk_held_up_by_mobs_names_the_mob_that_held_it_up_and_why_no_detour_was_taken(monkeypatch, caplog):
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 0.6)
    monkeypatch.setattr(walk_module, "WALK_DETOUR_EVERY", 0.1)  # sought several times over the wait
    client = _mobs_at(_client(at=(200.0, 100.0, 0.0)), (2000.0, 100.0, 0.0))
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    with caplog.at_level("DEBUG", logger=walk_module.__name__):
        result = await _walk(client, corridor, (3800, 100, 0), watch_mobs=True)

    # The corridor's map has no patrols, so the ghost's trigger radius is the default, and says so.
    named = f"Ghost (trigger {walk_module.DEFAULT_TRIGGER:.0f}, no patrol in the map) at (2000, 100)"
    assert result.held_by.startswith(named)
    assert result.describe().endswith(f"last held by {result.held_by}")
    [waits] = [r.message for r in caplog.records if " -> wait " in r.message]
    assert f"held by {named}" in waits and waits.endswith("1 mob watched")
    no_detour = [r for r in caplog.records if "no detour: no way around 1 mob to (" in r.message]
    assert [r.levelname for r in no_detour][:2] == ["INFO", "DEBUG"]  # said once, then only at DEBUG
    assert sum(r.levelname == "INFO" for r in no_detour) == 1


async def test_a_wait_says_so_when_another_mob_takes_over_holding_it_up(monkeypatch, caplog):
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 1.5)
    monkeypatch.setattr(walk_module, "WALK_MOB_REFRESH", 0.2)  # the list read again soon after the swap
    client = _mobs_at(_client(at=(200.0, 100.0, 0.0)), (2000.0, 100.0, 0.0))
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    async def swap():
        while not any(" -> wait " in m for m in caplog.messages):
            await asyncio.sleep(0.02)
        client.entities = [SimEntity("Bat", (2000.0, 100.0, 0.0), mob=True)]

    with caplog.at_level("INFO", logger=walk_module.__name__):
        swapping = asyncio.create_task(swap())
        result = await _walk(client, corridor, (3800, 100, 0), watch_mobs=True)
        await swapping

    assert result.ran_through == 1
    assert [m for m in caplog.messages if "still wait at" in m and "now held by Bat (trigger" in m]
    assert result.held_by.startswith("Bat ")


def test_the_tightest_mob_is_the_one_leaving_the_least_room_and_when():
    near = walk_module.Mob("Near", 200.0, 3000.0, 0.0, 0.0, t=0.0)  # never reached in the look ahead
    far = walk_module.Mob("Far", 200.0, 5000.0, 0.0, 0.0, t=0.0)
    path = [(600.0 * tau, 0.0) for tau in walk_module.LOOK_AHEAD]  # straight at both, 600 a second

    least, mob, when = walk_module.tightest(path, [far, near], now=0.0)

    assert mob is near
    assert least == walk_module.room(path, [far, near], now=0.0)
    # The room shrinks while the wizard closes on it, by its walk less the margin growing with time.
    assert when == walk_module.LOOK_AHEAD[-1]
    assert walk_module.tightest(path, [], now=0.0) == (math.inf, None, 0.0)


def test_held_by_says_where_the_mob_is_from_the_wizard_and_how_fast_it_moves():
    mob = walk_module.Mob("KR_Spider_Warrior_B", 350.0, 300.0, 400.0, 60.0, t=0.0, vx=30.0, vy=40.0)

    text = walk_module.held_by(mob, (0.0, 0.0, 10.0), 1.3, watched=3)

    assert text == (
        "KR_Spider_Warrior_B (trigger 350) at (300, 400), 500 units off, height +50, moving 50 units a second,"
        " least room 1.3 s ahead; 3 mobs watched"
    )


async def test_a_watched_mob_has_its_patrols_trigger_radius_or_the_default_and_knows_which():
    async def read():
        return [(1, "Spider", (0.0, 0.0, 0.0)), (2, "Boss", (0.0, 0.0, 0.0))]

    watch = walk_module.MobWatch(read, {"Spider": 350.0})
    watch.start()
    while len(watch.mobs) < 2:
        await asyncio.sleep(0.01)
    await watch.stop()

    assert (watch.mobs[1].trigger, watch.mobs[1].mapped) == (350.0, True)
    assert (watch.mobs[2].trigger, watch.mobs[2].mapped) == (walk_module.DEFAULT_TRIGGER, False)


async def _fight_walk(client, planner, goal, **kw):
    arbiter = Arbiter()
    async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
        return await RouteWalker(client, planner, watch_mobs=True).walk(lease, goal, fight=True, **kw)


def _fights_within(client, mob, reach=200.0):
    """Start a fight once the wizard comes within *reach* of *mob*, as its trigger would."""
    return asyncio.create_task(
        _when(client, lambda p: math.dist(p[:2], mob[:2]) < reach, lambda: setattr(client, "battle", True))
    )


async def test_a_walk_into_a_fight_goes_up_to_its_mob_and_ends_in_the_fight():
    mob = (3000.0, 1000.0, 0.0)
    client = _mobs_at(_client(), mob)
    fight = _fights_within(client, mob)

    result = await _fight_walk(client, _hall(), mob)
    await fight

    assert result.end is WalkEnd.FIGHT
    assert result.detours == 0 and result.waited == 0
    assert client.held == set()


async def test_a_walk_into_a_fight_still_goes_around_a_mob_that_is_not_in_it():
    mob, other = (3000.0, 1000.0, 0.0), (1500.0, 1000.0, 0.0)
    client = _mobs_at(_client(), mob, other)
    fight = _fights_within(client, mob)

    result = await _fight_walk(client, _hall(), mob)
    await fight

    assert result.end is WalkEnd.FIGHT
    assert result.detours >= 1
    assert min(math.dist(p[:2], other[:2]) for p in result.trail) > walk_module.DEFAULT_TRIGGER


async def test_a_walk_into_a_fight_spares_the_mob_it_was_told_to_meet_wherever_it_stands(monkeypatch):
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 0.6)  # the wary walk only has to be held up
    goal = (2200.0, 1000.0, 0.0)
    ghost = SimEntity("Ghost", (2600.0, 1000.0, 0.0), mob=True)  # beyond the goal's reach, but the fight's
    client = _client()
    client.entities = [ghost]

    spared = await _fight_walk(client, _hall(), goal, spare=[ghost.gid])

    assert spared.arrived and spared.detours == 0 and spared.waited == 0
    client.place((200.0, 1000.0, 0.0))
    wary = await _fight_walk(client, _hall(), goal)
    assert wary.detours + wary.waited > 0


async def test_a_detour_is_planned_standing_even_while_backing_off(monkeypatch):
    # Cadences, cut so a detour is sought while backing: in a corridor none is ever found.
    monkeypatch.setattr(walk_module, "WALK_DETOUR_EVERY", 0.1)
    client = _client(at=(200.0, 100.0, 0.0))
    corridor = _planner(floor(-4000, 0, 4000, 200, 0))
    modes, moving_while_planning = set(), []
    around = corridor.around

    def noting(discs):
        detour = around(discs)
        route = detour.route

        def planning(*a, **k):
            moving_while_planning.append(bool(client.held))
            return route(*a, **k)

        detour.route = planning
        return detour

    corridor.around = noting
    began = time.monotonic()

    def coming():  # down the corridor at the wizard, gone after 3 s
        t = time.monotonic() - began
        return None if t > 3.0 else (1200.0 - 150.0 * t, 100.0, 0.0)

    def tick(t):
        modes.add(t.mode)

    client.entities = [SimEntity("Ghost", coming, mob=True)]
    result = await _walk(client, corridor, (3800, 100, 0), watch_mobs=True, on_tick=tick)

    assert result.arrived
    assert "back" in modes
    assert moving_while_planning and not any(moving_while_planning)


def test_a_detour_rejoins_the_route_a_short_way_ahead_outside_every_mob():
    route = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (2000.0, 0.0, 0.0), (3000.0, 0.0, 0.0), (9000.0, 0.0, 0.0)]

    assert rejoin(route, 1, route[0], [(2000.0, 0.0, 500.0)]) == 3
    assert rejoin(route, 1, route[0], [(3000.0, 0.0, 500.0)]) == 4


async def test_one_pass_over_the_entity_list_reads_the_mobs_and_every_object():
    client = _client()
    ghost, door, freed = SimEntity("Ghost", (100.0, 0.0, 0.0), mob=True), _door(), SimEntity("Ghost", None, mob=True)
    client.entities = [ghost, door, freed]
    lists = []
    listing = client.get_base_entity_list

    async def counted():
        lists.append(1)
        return await listing()

    client.get_base_entity_list = counted
    read = ClientRead(client)

    assert await read.mobs() == [(ghost.gid, "Ghost", (100.0, 0.0, 0.0))]
    assert read.latest.objects == [("Ghost", (100.0, 0.0, 0.0)), ("Door", (1500.0, 500.0, 0.0))]
    assert read.latest.at == client.position
    assert await read.mobs() == [(ghost.gid, "Ghost", (100.0, 0.0, 0.0))]  # positions, no new pass
    assert lists == [1] and read.passes == 1


# --- ending ----------------------------------------------------------------------------------


async def test_a_fight_starting_ends_the_walk_and_lets_go_of_the_keys():
    client = _client()
    fight = asyncio.create_task(_when(client, lambda p: p[0] > 800, lambda: setattr(client, "battle", True)))

    result = await _walk(client, _hall(), (3000, 1000, 0))
    await fight

    assert result.end is WalkEnd.FIGHT
    assert client.held == set()


async def test_a_zone_change_ends_the_walk():
    client = _client()
    door = asyncio.create_task(_when(client, lambda p: p[0] > 800, lambda: setattr(client, "zone", "Test/T_Other")))

    result = await _walk(client, _hall(), (3000, 1000, 0))
    await door

    assert result.end is WalkEnd.ZONE_CHANGED


async def test_the_first_fight_check_and_key_lookup_are_made_while_the_first_route_is_planned(monkeypatch):
    # In a fresh process the first in_battle scans for the duel manager (about 0.5 s) and the first
    # key held reads the bindings: both are made during the first plan, not standing after it.
    client = _client()
    planner = _hall()
    events: list[str] = []
    route, battle, key_map = planner.route, client.in_battle, game_keys.key_map

    def planning(*a, **k):
        events.append("plan")
        time.sleep(0.2)
        try:
            return route(*a, **k)
        finally:
            events.append("planned")

    async def first(name, call, *a, **k):
        if name not in events:
            events.append(name)
        return await call(*a, **k)

    planner.route = planning
    monkeypatch.setattr(client, "in_battle", lambda: first("battle", battle))
    monkeypatch.setattr(game_keys, "key_map", lambda c, **k: first("keys", key_map, c, **k))

    result = await _walk(client, planner, (1400, 1000, 0))

    assert result.arrived
    planned = events.index("planned")
    assert events.index("battle") < planned and events.index("keys") < planned


async def test_a_failed_first_fight_check_or_key_lookup_leaves_the_walk_as_it_was(monkeypatch):
    client = _client()
    battle, key_map = client.in_battle, game_keys.key_map
    calls = {"battle": 0, "keys": 0}

    async def failing_once(name, call, *a, **k):
        calls[name] += 1
        if calls[name] == 1:
            raise OSError("the process went away for a moment")
        return await call(*a, **k)

    monkeypatch.setattr(client, "in_battle", lambda: failing_once("battle", battle))
    monkeypatch.setattr(game_keys, "key_map", lambda c, **k: failing_once("keys", key_map, c, **k))

    result = await _walk(client, _hall(), (1400, 1000, 0))

    assert result.end is WalkEnd.ARRIVED
    assert calls["battle"] > 1 and calls["keys"] > 1


async def test_a_replan_gives_the_walk_time_for_the_route_left(monkeypatch):
    # A ceiling, cut since running out is the branch tested: 3 s for the 1.5 s first planned.
    monkeypatch.setattr(walk_module, "WALK_TIME_ALLOWANCE", 0.0)
    client = _client()
    moved = asyncio.create_task(_when(client, lambda p: p[0] > 500, lambda: client.place((3800.0, 1900.0, 0.0))))

    result = await _walk(client, _hall(), (800, 1000, 0))
    await moved

    assert result.replans == 1
    assert result.end is WalkEnd.ARRIVED


async def test_a_walk_replanned_over_and_over_ends_at_its_ceiling(monkeypatch):
    # Ceilings, cut since running out is the branch tested: 3 s for the first plan's 6 s, and 1.5 s
    # in all, which no replan renews.
    monkeypatch.setattr(walk_module, "WALK_TIME_ALLOWANCE", 0.0)
    monkeypatch.setattr(walk_module, "WALK_CEILING_FACTOR", 0.25)
    client = _client()

    async def resetting():  # the server puts it off its route each time it gets past x = 1500
        for y in itertools.cycle((200.0, 1800.0)):  # by turns, each far off the leg from the other
            await _when(client, lambda p: p[0] > 1500, lambda y=y: client.place((1200.0, y, 0.0)))
            await asyncio.sleep(0.05)

    server = asyncio.create_task(resetting())
    try:
        result = await asyncio.wait_for(_walk(client, _hall(), (2600, 1000, 0)), 10)
    finally:
        server.cancel()

    assert result.end is WalkEnd.TIMED_OUT
    assert "ceiling" in result.detail
    assert result.replans >= 2
    assert result.seconds < 3


class _SlowToLetGo(ClientInput):
    """A lease's input whose first release takes a while, for a second cancel to land in."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.slowed = False

    async def release_all(self):
        if not self.slowed:
            self.slowed = True
            await asyncio.sleep(0.5)
        await super().release_all()


async def test_a_second_cancel_while_letting_go_still_stops_the_mob_watch():
    client = _client()
    ghost = SimEntity("Ghost", (3900.0, 1900.0, 0.0), mob=True)
    client.entities = [ghost]

    async def walking():
        async with Arbiter(make_input=_SlowToLetGo).acquire(client, Owner.MOVEMENT) as lease:
            await RouteWalker(client, _hall(), watch_mobs=True).walk(lease, (3800, 1000, 0))

    task = asyncio.create_task(walking())
    await _when(client, lambda p: p[0] > 600, lambda: None)
    task.cancel()
    await asyncio.sleep(0.1)  # in the first release, now
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    seen = ghost.reads
    await asyncio.sleep(3 * walk_module.WALK_MOB_POLL)

    assert ghost.reads == seen
    assert client.held == set()


async def test_a_cancel_mid_walk_lets_go_of_every_key_and_is_not_swallowed():
    client = _client()
    walking = asyncio.create_task(_walk(client, _hall(), (3800, 1000, 0)))
    await _when(client, lambda p: p[0] > 600, lambda: None)

    walking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await walking

    assert client.held == set()


async def test_a_held_event_loop_lets_go_of_forward_and_the_walk_goes_on_after():
    client = _client()
    arbiter = Arbiter()

    async def hold_the_loop():
        await _when(client, lambda p: p[0] > 800, lambda: time.sleep(0.6))

    async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
        holding = asyncio.create_task(hold_the_loop())
        result = await RouteWalker(client, _hall()).walk(lease, (2600, 1000, 0))
        await holding
        dropped = lease.input.dropped

    assert dropped >= 1
    assert result.arrived
    # Walked on for the watchdog's 0.2 s or so at 1,200 a second, not the 0.6 s the loop was held.
    stopped = max(b[0] - a[0] for a, b in zip(result.trail, result.trail[1:]))
    assert stopped < 0.45 * 1200


# --- what a walk never writes --------------------------------------------------------------------


def test_nothing_in_the_walker_writes_speed_or_a_position():
    tree = ast.parse(Path(walk_module.__file__).read_text(encoding="utf-8"))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    writes = {name for name in called if name.lower().startswith(("write", "set_"))}
    forbidden = {name for name in writes if name != "write_yaw"}
    forbidden |= {name for name in called if "teleport" in name.lower()} | (called & {"tp_to", "goto"})
    assert forbidden == set()


async def test_a_walk_leaves_the_speed_field_as_it_found_it():
    client = _client()

    await _walk(client, _hall(), (1400, 1000, 0))

    assert client.speed == 100
    assert [r for r in client.inputs if r.kind == "speed"] == []

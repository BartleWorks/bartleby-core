"""Walking a route on a simulated client: arriving, resets, stalls, links, mobs, and ending."""

import ast
import asyncio
import math
import threading
import time
from pathlib import Path

import pytest

from bartleby.runtime.arbiter import Arbiter, Owner
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import ClientInput
from bartleby.runtime.movement import walk as walk_module
from bartleby.runtime.movement.route import ZoneFiles, ZonePlanner, build_planner
from bartleby.runtime.movement.walk import RouteWalker, WalkEnd, client_mobs, rejoin
from bartleby.runtime.movement.zone_links import Link, Volume
from bartleby.runtime.testing.sim_client import SimClient
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


# --- stalls ----------------------------------------------------------------------------------


@pytest.fixture
def quick_stalls(monkeypatch):
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


def _mob_at(*where):
    async def read():
        return [(k, "Ghost", w) for k, w in enumerate(where)]

    return read


async def test_a_mob_in_the_way_is_detoured_around_before_it_is_waited_for():
    mob = (1500.0, 1000.0, 0.0)
    client = _client()

    result = await _walk(client, _hall(), (3000, 1000, 0), mobs=_mob_at(mob))

    assert result.arrived
    assert result.detours >= 1
    assert result.waited == 0
    assert min(math.dist(p[:2], mob[:2]) for p in result.trail) > walk_module.DEFAULT_TRIGGER


async def test_a_mob_with_no_way_around_is_waited_for_and_the_wait_ends_blocked(monkeypatch):
    monkeypatch.setattr(walk_module, "WALK_MOB_WAIT", 0.6)
    client = _client(at=(200.0, 100.0, 0.0))
    corridor = _planner(floor(0, 0, 4000, 200, 0))

    result = await _walk(client, corridor, (3800, 100, 0), mobs=_mob_at((2000.0, 100.0, 0.0)))

    assert result.end is WalkEnd.BLOCKED
    assert "mobs" in result.detail
    assert result.detours == 0
    assert result.waited > 0.5
    assert client.held == set()


def test_a_detour_rejoins_the_route_a_short_way_ahead_outside_every_mob():
    route = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (2000.0, 0.0, 0.0), (3000.0, 0.0, 0.0), (9000.0, 0.0, 0.0)]

    assert rejoin(route, 1, route[0], [(2000.0, 0.0, 500.0)]) == 3
    assert rejoin(route, 1, route[0], [(3000.0, 0.0, 500.0)]) == 4


async def test_client_mobs_reads_each_listed_mobs_position():
    class Entity:
        def __init__(self, gid, x):
            self.gid, self.x = gid, x

        async def global_id_full(self):
            return self.gid

        async def object_name(self):
            return "Ghost"

        async def location(self):
            if self.x is None:
                raise RuntimeError("freed")
            return type("XYZ", (), {"x": self.x, "y": 0.0, "z": 0.0})()

    class Client:
        async def get_mobs(self):
            return [Entity(1, 100.0), Entity(2, None)]

    assert await client_mobs(Client())() == [(1, "Ghost", (100.0, 0.0, 0.0))]


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

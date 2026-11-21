"""Resizing a running client, on SimClient: the order of its writes, its lease, its one warning, giving back."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time

import pytest
from wizwalker.errors import PatternFailed, VideoManagerError

from bartleby.runtime import run_trace, timing
from bartleby.runtime.arbiter import Arbiter, Owner, Preempted
from bartleby.runtime.clients import client_resizer, heap_scan, static_routes
from bartleby.runtime.clients.client_resizer import ClientResizer, ConfigNodes, find_config_nodes
from bartleby.runtime.game.game_sizing import ui_size_for
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.launcher import launch_profile
from bartleby.runtime.movement import follow
from bartleby.runtime.movement.follow import Follower, spacing_for
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import StateReader
from bartleby.runtime.testing.sim_client import COPY_VTABLE, DEFAULT_SETTINGS, IMAGE_BASE, SimClient

_HUGE, _REGULAR, _MICRO = 4, 2, 5
_SMALL_SIZE, _LARGE_SIZE = (534, 300), (1280, 720)


@pytest.fixture(autouse=True)
def preferred(monkeypatch):
    """The user's own UI scale, with no game files behind it."""
    monkeypatch.setattr(launch_profile, "capture_game_settings", lambda: _HUGE)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """A wait for a client to come free, shortened. Its reads count iterations, not seconds."""
    monkeypatch.setattr(client_resizer, "POLL_INTERVAL", 0.005)
    monkeypatch.setattr(client_resizer, "RESIZE_APPLY_POLL", 0.002)
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_RETRY_DELAY", 0.01)


@pytest.fixture
def arbiter():
    return Arbiter()


@pytest.fixture
def reports():
    return []


@pytest.fixture
def resizer(arbiter, reports):
    return ClientResizer(arbiter, lambda client, size: reports.append((client, size)))


@pytest.fixture
def warnings(caplog):
    caplog.set_level(logging.WARNING, logger=client_resizer.logger.name)
    return lambda: [r for r in caplog.records if r.levelno == logging.WARNING]


def _writes(client: SimClient) -> list[tuple[str, object]]:
    return [(r.kind, r.detail) for r in client.inputs if r.kind in ("ui_size", "resolution", "pending")]


async def _settled(resizer: ClientResizer, client: SimClient) -> None:
    """Wait for the worker on *client* to end."""
    task = resizer._entries[client.window_handle].task
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)


async def _until(condition, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.002)


# -- the writes ------------------------------------------------------------------------------


async def test_the_writes_come_in_order_with_the_ladder_s_ui_size(resizer, reports):
    client = SimClient(game_size=_LARGE_SIZE, ui_size=_HUGE)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)

    assert _writes(client) == [
        ("ui_size", ui_size_for(_HUGE, _SMALL_SIZE)),
        ("resolution", "534x300"),
        ("pending", True),
    ]
    assert ui_size_for(_HUGE, _SMALL_SIZE) == _MICRO
    assert client.game_size == _SMALL_SIZE
    assert not client.video_manager.pending
    assert reports == [(client, _SMALL_SIZE)]


async def test_the_user_s_settings_are_captured_before_the_first_write(resizer, monkeypatch):
    client = SimClient()
    seen = []

    def capture():
        seen.append(list(client.inputs))
        return _HUGE

    monkeypatch.setattr(launch_profile, "capture_game_settings", capture)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert seen == [[]]
    assert _writes(client)


async def test_a_game_with_no_ui_scale_is_left_alone(resizer, monkeypatch, warnings):
    monkeypatch.setattr(launch_profile, "capture_game_settings", lambda: None)
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert _writes(client) == []
    assert client.game_size == (800, 600)
    assert len(warnings()) == 1


async def test_a_size_it_is_already_at_writes_nothing_and_still_reports(resizer, reports, arbiter):
    client = SimClient(game_size=_SMALL_SIZE)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert _writes(client) == []
    assert reports == [(client, _SMALL_SIZE)]


async def test_the_device_ending_at_another_size_is_a_failure_that_reports_what_it_measured(resizer, reports, warnings):
    client = SimClient(game_size=_LARGE_SIZE)
    client.video_manager.lands_at = (640, 360)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert reports == [(client, (640, 360))]
    assert len(warnings()) == 1
    resizer.resize(client, _LARGE_SIZE)
    assert resizer._entries[client.window_handle].target is None


# -- the lease --------------------------------------------------------------------------------


async def test_it_holds_the_display_lease_only_while_it_applies(resizer, arbiter):
    client = SimClient()
    client.video_manager.apply_seconds = 0.05
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: client.video_manager.pending)
    lease = arbiter.holder(client)
    assert lease.owner is Owner.DISPLAY
    assert lease.owner.label == "resizing"
    await _settled(resizer, client)
    assert arbiter.holder(client) is None


def test_display_sits_above_movement_and_below_hotkeys():
    assert Owner.QUESTER < Owner.MOVEMENT < Owner.DISPLAY < Owner.HOTKEY < Owner.COMBAT < Owner.RECOVERY


async def test_it_waits_while_the_client_loads(resizer):
    client = SimClient(loading=True)
    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.03)
    assert _writes(client) == []
    client.loading = False
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


async def test_a_fight_does_not_hold_it_off(resizer):
    """The tile has changed shape already, so waiting would leave the game cut off for the whole fight."""
    client = SimClient(battle=True)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


async def test_a_load_that_starts_while_it_waits_for_the_lease_holds_it_off(resizer, arbiter):
    client = SimClient()
    release = asyncio.Event()

    async def hotkey():
        async with arbiter.acquire(client, Owner.HOTKEY):
            await release.wait()

    holder = asyncio.create_task(hotkey())
    await asyncio.sleep(0)
    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.02)
    client.loading = True
    release.set()
    await holder
    await asyncio.sleep(0.03)
    assert _writes(client) == []
    client.loading = False
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


@pytest.mark.parametrize("owner", [Owner.HOTKEY, Owner.COMBAT, Owner.RECOVERY])
async def test_it_waits_for_an_owner_above_it(resizer, arbiter, owner):
    client = SimClient()
    release = asyncio.Event()

    async def hold():
        async with arbiter.acquire(client, owner):
            await release.wait()

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0)
    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.03)
    assert _writes(client) == []
    assert not holder.done(), "it waits, and preempts nothing above it"
    release.set()
    await holder
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


@pytest.mark.parametrize("owner", [Owner.QUESTER, Owner.MOVEMENT])
async def test_it_preempts_an_owner_below_it(resizer, arbiter, owner):
    client = SimClient()
    held = asyncio.Event()
    outcome = []

    async def hold():
        try:
            async with arbiter.acquire(client, owner):
                held.set()
                await asyncio.sleep(10)
        except Preempted as lost:
            outcome.append(lost.by)

    holder = asyncio.create_task(hold())
    await held.wait()
    resizer.resize(client, _SMALL_SIZE)
    await holder
    await _settled(resizer, client)
    assert outcome == [Owner.DISPLAY]
    assert client.game_size == _SMALL_SIZE


async def test_an_owner_above_it_that_arrives_during_an_apply_waits_for_the_apply(resizer, arbiter, reports):
    client = SimClient()
    client.video_manager.apply_seconds = 0.05
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: client.video_manager.pending)

    order = []

    async def hotkey():
        async with arbiter.acquire(client, Owner.HOTKEY):
            order.append(("hotkey", client.video_manager.pending))

    await hotkey()
    await _settled(resizer, client)
    assert order == [("hotkey", False)]
    assert reports == [(client, _SMALL_SIZE)]


async def test_a_higher_owner_arriving_before_the_apply_leaves_the_target_standing(resizer, arbiter):
    client = SimClient()
    # Held at the check under the lease, so the higher owner lands before the apply.
    gate = asyncio.Event()
    is_loading, calls = client.is_loading, []

    async def slow_check():
        calls.append(1)
        if len(calls) == 2:
            await gate.wait()
        return await is_loading()

    client.is_loading = slow_check
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: len(calls) == 2)

    async def hotkey():
        async with arbiter.acquire(client, Owner.HOTKEY):
            assert _writes(client) == []

    higher = asyncio.create_task(hotkey())
    await higher
    gate.set()
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


async def test_a_follower_moves_again_after_a_resize(resizer, arbiter, monkeypatch):
    monkeypatch.setattr(follow, "FOLLOW_TICK", 0.01)
    monkeypatch.setattr("bartleby.runtime.input.HELD_KEY_REPEAT", 0.01)
    leader, client = SimClient(position=(2000.0, 0.0, 0.0)), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    leader_reader.publish(position=leader.position, yaw=0.0, zone=leader.zone, loading=False, speed=0)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    trail.note(leader_reader.latest)
    follower = Follower(client, me, leader_reader, trail, arbiter, spacing=spacing_for(0))
    task = asyncio.create_task(follower.run())
    try:
        await _until(lambda: client.holding(GameAction.MOVE_FORWARD))
        client.video_manager.apply_seconds = 0.03
        resizer.resize(client, _SMALL_SIZE)
        await _until(lambda: client.video_manager.pending)
        assert not client.holding(GameAction.MOVE_FORWARD), "the lease it lost let go of its keys"
        await _settled(resizer, client)
        assert client.game_size == _SMALL_SIZE
        before = client.position
        await _until(lambda: client.holding(GameAction.MOVE_FORWARD))
        await _until(lambda: client.position[0] > before[0] + 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# -- the latest target ----------------------------------------------------------------------


async def test_three_targets_sent_during_a_wait_run_only_the_last(resizer, reports):
    client = SimClient(loading=True)
    resizer.resize(client, (640, 360))
    resizer.resize(client, (854, 480))
    resizer.resize(client, _SMALL_SIZE)
    client.loading = False
    await _settled(resizer, client)
    assert [d for k, d in _writes(client) if k == "resolution"] == ["534x300"]
    assert reports == [(client, _SMALL_SIZE)]


async def test_a_target_sent_during_an_apply_runs_after_it(resizer, reports):
    client = SimClient()
    client.video_manager.apply_seconds = 0.03
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: client.video_manager.pending)
    resizer.resize(client, _LARGE_SIZE)
    await _settled(resizer, client)
    assert [size for _client, size in reports] == [_SMALL_SIZE, _LARGE_SIZE]
    assert client.game_size == _LARGE_SIZE


async def test_going_back_to_the_launch_size_gives_back_the_launch_ui_size(resizer):
    client = SimClient(game_size=(854, 480), ui_size=3)  # Not what the ladder would say for it
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.ui_size == _MICRO
    resizer.resize(client, (854, 480))
    await _settled(resizer, client)
    assert client.ui_size == 3


# -- Stop all ------------------------------------------------------------------------------------


async def test_stop_all_before_the_byte_ends_the_resize(resizer, reports):
    client = SimClient(loading=True)
    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.01)
    await resizer.stop_all()
    client.loading = False
    await asyncio.sleep(0.02)
    assert _writes(client) == []
    assert reports == []
    assert client.game_size == (800, 600)


async def test_the_next_target_asks_again_after_stop_all(resizer):
    client = SimClient(loading=True)
    resizer.resize(client, _SMALL_SIZE)
    await resizer.stop_all()
    client.loading = False
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


async def test_stop_all_after_the_byte_still_ends_the_apply_and_reports(resizer, arbiter, reports):
    client = SimClient()
    client.video_manager.apply_seconds = 0.05
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: client.video_manager.pending)

    await asyncio.gather(resizer.stop_all(), arbiter.revoke_all())

    assert not client.video_manager.pending
    assert client.game_size == _SMALL_SIZE
    assert reports == [(client, _SMALL_SIZE)]
    await _until(lambda: arbiter.holder(client) is None)


async def test_the_arbiter_s_stop_all_alone_ends_a_resize_waiting_on_its_lease(resizer, arbiter, reports):
    """A revoke lands on the lease in the moment before the apply starts: the target goes too."""
    client = SimClient()
    gate = asyncio.Event()
    is_loading, calls = client.is_loading, []

    async def slow_check():
        calls.append(1)
        if len(calls) == 2:
            await gate.wait()
        return await is_loading()

    client.is_loading = slow_check
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: len(calls) == 2)
    await arbiter.revoke_all()
    await _settled(resizer, client)
    assert _writes(client) == []
    assert reports == []
    assert resizer._entries[client.window_handle].target is None


async def test_a_lease_revoked_during_an_apply_reports_and_raises_nothing(resizer, arbiter, reports):
    client = SimClient()
    client.video_manager.apply_seconds = 0.03
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: client.video_manager.pending)
    await arbiter.revoke_all()
    await _settled(resizer, client)
    assert reports == [(client, _SMALL_SIZE)]


async def test_a_listener_that_raises_costs_nothing(arbiter, caplog):
    def bad(_client, _size):
        raise ValueError("a listener's bug")

    resizer = ClientResizer(arbiter, bad)
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE
    assert arbiter.holder(client) is None
    assert "listener" in caplog.text


# -- finding the nodes ----------------------------------------------------------------------------


async def test_it_finds_the_marked_pair_among_the_defaults():
    memory = SimClient().hook_handler
    assert await find_config_nodes(memory) == ConfigNodes(memory.ui_size_node, memory.resolution_node)


async def test_the_second_class_s_copies_are_not_it():
    """They come and go, some of them marked, and never hold the pointer."""
    memory = SimClient().hook_handler
    memory.place_node("UISize", vtable=COPY_VTABLE, marked=True)
    memory.place_node("UISize", vtable=COPY_VTABLE, marked=False)
    memory.place_node("Resolution", vtable=COPY_VTABLE, marked=True, kind=0)
    assert await find_config_nodes(memory) == ConfigNodes(memory.ui_size_node, memory.resolution_node)


async def test_a_marked_default_is_not_it_without_a_pointer():
    memory = SimClient().hook_handler
    memory.place_node("UISize", parent=DEFAULT_SETTINGS, marked=True)
    assert await find_config_nodes(memory) == ConfigNodes(memory.ui_size_node, memory.resolution_node)


async def test_the_resolution_follows_the_ui_size_s_parent_whichever_the_scan_finds_first():
    """Parent, not order: the defaults' Resolution holds a string too, "autodetect"."""
    memory = SimClient().hook_handler
    other = memory.place_node("Resolution", parent=0x3333_0000, text="1024x768", kind=0)
    nodes = await find_config_nodes(memory)
    assert nodes.resolution == memory.resolution_node != other


async def test_a_bare_string_with_the_name_is_no_node():
    memory = SimClient().hook_handler
    memory.place_text("UISize")
    memory.place_text("Resolution")
    assert await find_config_nodes(memory) == ConfigNodes(memory.ui_size_node, memory.resolution_node)


async def test_two_live_ui_sizes_are_no_answer():
    memory = SimClient().hook_handler
    memory.place_node("UISize", marked=True, text="2")
    assert await find_config_nodes(memory) is None


async def test_two_live_resolutions_are_no_answer():
    memory = SimClient().hook_handler
    memory.place_node("Resolution", text="800x600", kind=0)
    assert await find_config_nodes(memory) is None


async def test_no_node_at_all_is_no_answer():
    memory = SimClient().hook_handler
    memory.forget_node(memory.ui_size_node)
    memory.forget_node(memory.resolution_node)
    assert await find_config_nodes(memory) is None


# -- the static route ------------------------------------------------------------------------------


def _routed(**fields) -> SimClient:
    """A client of a build the config route's patterns were written for."""
    client = SimClient(**fields)
    client.hook_handler.place_slot_code()
    return client


@pytest.fixture
def fell_back(caplog):
    """The route's misses logged, each its message."""
    caplog.set_level(logging.INFO, logger=static_routes.logger.name)
    return lambda: [r.getMessage() for r in caplog.records if r.name == static_routes.logger.name]


async def test_the_route_finds_the_live_pair_with_no_heap_pass(fell_back):
    memory = _routed().hook_handler
    assert await find_config_nodes(memory) == ConfigNodes(memory.ui_size_node, memory.resolution_node)
    assert (memory.scans, memory.module_scans) == (0, 1)
    assert fell_back() == []


async def test_the_route_s_pattern_is_found_once_per_client():
    memory = _routed().hook_handler
    for _ in range(3):
        assert await find_config_nodes(memory) is not None
    assert (memory.scans, memory.module_scans) == (0, 1)


def _no_code(memory) -> None:
    memory.put(IMAGE_BASE, bytes(0x40))


def _empty_slot(memory) -> None:
    memory.put_u64(memory.config_slot, 0)


def _unreadable(memory) -> None:
    memory.put_u64(memory.config_slot, 0x5000_0000)


def _video_settings_renamed(memory) -> None:
    memory.put_string(memory.sections["VideoSettings"] + static_routes.TREE_KEY, "VideoSettingz")


def _another_group(memory) -> None:
    """VideoSettings holds a group with no such children: the user's is only in the heap."""
    stray = memory.alloc(0x40)
    groups = memory.sections["VideoSettings"] + static_routes.TREE_VALUE
    memory.put(groups, struct.pack("<QQ", stray, stray + 8))
    memory.put_u64(stray, stray + 0x20)


def _unmarked_and_moved(memory) -> ConfigNodes:
    """The group's UISize is no longer marked, and a live one stands elsewhere, which only the heap sees."""
    memory.put(memory.ui_size_node + 0x45, b"\x00")
    moved = memory.place_node("UISize", marked=True, text="2", value=2)
    return ConfigNodes(moved, memory.resolution_node)


def _another_parent(memory) -> ConfigNodes:
    """The group's two name another parent: the route's check, not the heap's."""
    memory.put_u64(memory.ui_size_node + 0x38, 0x3333_0000)
    memory.put_u64(memory.resolution_node + 0x38, 0x3333_0000)
    return ConfigNodes(memory.ui_size_node, memory.resolution_node)


@pytest.mark.parametrize(
    "breaking, reason",
    [
        (_no_code, "no single pattern match"),
        (_empty_slot, "the slot is empty"),
        (_unreadable, "a read failed (MemoryReadError)"),
        (_video_settings_renamed, "no VideoSettings section"),
        (_another_group, "no UISize and Resolution in VideoSettings"),
        (_unmarked_and_moved, "no single live pair"),
        (_another_parent, "no single live pair"),
    ],
)
async def test_a_route_that_misses_falls_back_to_the_heap_s_answer_saying_why(breaking, reason, fell_back):
    memory = _routed().hook_handler
    expected = breaking(memory) or ConfigNodes(memory.ui_size_node, memory.resolution_node)

    assert await find_config_nodes(memory) == expected

    assert memory.scans == 1
    (message,) = fell_back()
    assert reason in message
    assert "the config nodes" in message


async def test_a_miss_is_logged_once_per_client(fell_back):
    """Today's heap tests are this: a build whose patterns match nothing."""
    first, second = SimClient().hook_handler, SimClient().hook_handler
    for memory in (first, first, second, first):
        assert await find_config_nodes(memory) is not None
    assert len(fell_back()) == 2
    assert first.scans == 3, "the heap, every time"


async def test_a_resize_by_the_route_makes_no_heap_pass(resizer):
    client = _routed()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE
    assert client.hook_handler.scans == 0


async def test_the_size_named_has_its_nodes_by_the_route_at_once_whatever_the_grace(resizer, monkeypatch):
    """The background lookup at hook: no pass to wait its grace out for."""
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_BACKGROUND_GRACE", 30.0)
    client = _routed(game_size=_SMALL_SIZE)
    resizer.give_back_to(client, _LARGE_SIZE)
    entry = resizer._entries[client.window_handle]

    await asyncio.wait_for(entry.scan, 1)

    assert entry.nodes == ConfigNodes(client.hook_handler.ui_size_node, client.hook_handler.resolution_node)
    assert client.hook_handler.scans == 0


async def test_nodes_the_game_moved_are_found_again_by_the_route(resizer):
    """The group's vector reallocated: the old nodes wiped, the same names a vector further on."""
    client = _routed()
    memory = client.hook_handler
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)

    children = memory.alloc(3 * 0x78)
    memory.forget_node(memory.ui_size_node)
    memory.forget_node(memory.resolution_node)
    memory.resolution_node = memory.place_node("Resolution", at=children + 0x78, text="534x300", kind=0)
    memory.ui_size_node = memory.place_node("UISize", at=children + 2 * 0x78, marked=True, text="5", value=_MICRO)
    memory.put(memory.group + static_routes.GROUP_CHILDREN, struct.pack("<QQ", children, children + 3 * 0x78))
    resizer.resize(client, _LARGE_SIZE)
    await _settled(resizer, client)

    assert client.game_size == _LARGE_SIZE
    assert memory.scans == 0
    assert memory.module_scans == 1


# -- when it scans ------------------------------------------------------------------------------


async def test_with_one_client_nothing_scans_and_nothing_is_written(resizer):
    client = SimClient()
    resizer.prepare([client])
    await asyncio.sleep(0.01)
    assert client.hook_handler.scans == 0
    assert _writes(client) == []


async def test_a_second_client_starts_each_client_s_scan_once(resizer):
    first, second = SimClient(), SimClient()
    resizer.prepare([first, second])
    resizer.prepare([first, second])
    for client in (first, second):
        await resizer._entries[client.window_handle].scan
    resizer.prepare([first, second])
    await asyncio.sleep(0.01)
    assert first.hook_handler.scans == second.hook_handler.scans == 1  # Both names in one pass
    assert _writes(first) == _writes(second) == []


async def test_a_swap_after_prepare_does_not_scan_again(resizer):
    first, second = SimClient(), SimClient()
    resizer.prepare([first, second])
    await resizer._entries[first.window_handle].scan
    resizer.resize(first, _SMALL_SIZE)
    await _settled(resizer, first)
    assert first.hook_handler.scans == 1
    assert first.game_size == _SMALL_SIZE


async def test_a_resize_during_the_scan_waits_on_that_scan(resizer):
    first, second = SimClient(), SimClient()
    first.hook_handler.scan_seconds = 0.02
    resizer.prepare([first, second])
    resizer.resize(first, _SMALL_SIZE)
    await _settled(resizer, first)
    assert first.hook_handler.scans == 1
    assert first.game_size == _SMALL_SIZE


async def test_a_node_that_went_stale_is_found_again_before_any_write(resizer):
    client = SimClient()
    memory = client.hook_handler
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert memory.scans == 1

    memory.forget_node(memory.ui_size_node)
    memory.ui_size_node = memory.place_node("UISize", marked=True, text="5", value=_MICRO)
    resizer.resize(client, _LARGE_SIZE)
    await _settled(resizer, client)
    assert memory.scans == 2
    assert client.game_size == _LARGE_SIZE
    assert client.ui_size == ui_size_for(_HUGE, _LARGE_SIZE)


async def test_the_scan_holds_no_lease_and_a_follower_ticks_through_it(resizer, arbiter, monkeypatch):
    """The steering path: a resize finds its nodes before its lease, so a follower walks through the scan.

    No tick goes past probe_follow_jitter's 250 ms, against a pass of 300 ms spent off the loop,
    as wizwalker's are. What a real scan does to the loop is measured on a live client, by
    BartleBuddy's tools/probe_resize_scan.py.
    """
    monkeypatch.setattr(follow, "FOLLOW_TICK", 0.01)
    monkeypatch.setattr("bartleby.runtime.input.HELD_KEY_REPEAT", 0.01)
    leader, client = SimClient(position=(5000.0, 0.0, 0.0)), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    leader_reader.publish(position=leader.position, yaw=0.0, zone=leader.zone, loading=False, speed=0)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    trail.note(leader_reader.latest)
    follower = Follower(client, me, leader_reader, trail, arbiter, spacing=spacing_for(0))
    client.hook_handler.scan_seconds = 0.3

    ticks: list[float] = []
    plan = follower.plan

    def timed(*args):
        ticks.append(time.monotonic())
        return plan(*args)

    follower.plan = timed
    task = asyncio.create_task(follower.run())
    try:
        await _until(lambda: client.holding(GameAction.MOVE_FORWARD))
        ticks.clear()
        resizer.resize(client, _SMALL_SIZE)
        entry = resizer._entries[client.window_handle]
        await _until(lambda: entry.scan is not None)
        while not entry.scan.done():
            assert arbiter.holder(client) is None or arbiter.holder(client).owner is Owner.MOVEMENT
            await asyncio.sleep(0.01)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        assert client.hook_handler.scans == 1
        await _settled(resizer, client)
        assert client.game_size == _SMALL_SIZE
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert gaps, "the follower ticked during the scan"
    assert max(gaps) < 0.25


# -- failing --------------------------------------------------------------------------------------


async def test_a_bad_offset_gives_one_warning_and_no_further_attempt(resizer, warnings):
    client = SimClient()
    client.video_manager.error = VideoManagerError("The pending byte is tested at one place and cleared at another")
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert _writes(client) == []
    assert len(warnings()) == 1

    scans = client.hook_handler.scans
    resizer.resize(client, _LARGE_SIZE)
    resizer.prepare([client, SimClient()])
    await asyncio.sleep(0.01)
    assert _writes(client) == []
    assert client.hook_handler.scans == scans
    assert len(warnings()) == 1


async def test_a_pattern_that_does_not_match_is_the_same_warning(resizer, warnings):
    client = SimClient()
    client.video_manager.error = PatternFailed(b"\x48")
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert len(warnings()) == 1
    assert client.game_size == (800, 600)


async def test_no_node_gives_one_warning_and_no_write(resizer, warnings):
    client = SimClient()
    memory = client.hook_handler
    memory.forget_node(memory.ui_size_node)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert _writes(client) == []
    assert client.game_size == (800, 600)
    assert len(warnings()) == 1


async def test_a_prepare_that_finds_no_node_warns_once_and_a_swap_asks_no_more(resizer, warnings):
    client = SimClient()
    client.hook_handler.forget_node(client.hook_handler.ui_size_node)
    resizer.prepare([client, SimClient()])
    await resizer._entries[client.window_handle].scan
    assert len(warnings()) == 1
    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.01)
    assert _writes(client) == []
    assert len(warnings()) == 1


async def test_a_read_that_fails_during_the_wait_is_the_same_warning(resizer, warnings):
    client = SimClient()

    async def closed():
        raise OSError("the window is gone")

    client.is_loading = closed
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert len(warnings()) == 1


async def test_a_timeout_takes_the_byte_back_puts_the_nodes_back_warns_once_and_reports(
    resizer, reports, warnings, monkeypatch
):
    monkeypatch.setattr(client_resizer, "RESIZE_APPLY_TIMEOUT", 0.03)
    client = SimClient(game_size=_LARGE_SIZE, ui_size=_HUGE)
    client.video_manager.hangs = True
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)

    assert not client.video_manager.pending, "left set, the game would apply it whenever it next could"
    assert client.game_size == _LARGE_SIZE
    assert client.ui_size == _HUGE
    assert client.hook_handler.resolution_text() == "1280x720"
    assert reports == [(client, _LARGE_SIZE)]
    assert len(warnings()) == 1

    resizer.resize(client, _SMALL_SIZE)
    await asyncio.sleep(0.01)
    assert reports == [(client, _LARGE_SIZE)]
    assert len(warnings()) == 1


async def test_a_byte_set_that_failed_puts_the_ui_size_back(resizer, warnings):
    client = SimClient(game_size=_LARGE_SIZE, ui_size=_HUGE)
    apply_resolution = client.video_manager.apply_resolution

    async def fails(*_args):
        raise OSError("the write failed")

    client.video_manager.apply_resolution = fails
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.ui_size == _HUGE
    assert not client.video_manager.pending
    assert len(warnings()) == 1
    client.video_manager.apply_resolution = apply_resolution


async def test_it_can_try_again_once_hooked_again(resizer, warnings):
    client = SimClient()
    client.video_manager.error = VideoManagerError("a bad offset")
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert len(warnings()) == 1

    client.video_manager.error = None
    await resizer.forget(client)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.game_size == _SMALL_SIZE


async def test_forgetting_a_client_takes_its_waiting_scan_out_of_the_queue(resizer):
    """The one place held by another client's pass, so its scan is still waiting when it is forgotten."""
    busy = [SimClient(), SimClient()]
    for other in busy:
        other.hook_handler.scan_seconds = 0.1
    resizer.prepare(busy)
    await _until(lambda: heap_scan.scanner().running == 1)
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: heap_scan.scanner().waiting(client.hook_handler) == 1)
    await resizer.forget(client)
    await asyncio.sleep(0)
    assert heap_scan.scanner().waiting(client.hook_handler) == 0
    for other in busy:
        await resizer._entries[other.window_handle].scan
    assert client.hook_handler.scans == 0


async def test_a_scan_that_skipped_a_region_and_found_no_pair_is_made_once_more_and_resizes(resizer, warnings):
    """A client still settling after its hook: the region its nodes were in changed under the read."""
    client = SimClient()
    client.hook_handler.unsettled = 1
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.hook_handler.scans == 2
    assert client.game_size == _SMALL_SIZE
    assert warnings() == []


async def test_a_whole_scan_that_finds_no_pair_fails_at_once(resizer, warnings, monkeypatch):
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_RETRY_DELAY", 30.0)
    client = SimClient()
    client.hook_handler.forget_node(client.hook_handler.ui_size_node)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.hook_handler.scans == 1
    assert len(warnings()) == 1


async def test_a_retry_that_skips_a_region_too_fails_with_one_warning(resizer, warnings):
    client = SimClient()
    client.hook_handler.unsettled = 2
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.hook_handler.scans == 2, "once more, never a third"
    assert _writes(client) == []
    assert len(warnings()) == 1

    resizer.resize(client, _LARGE_SIZE)
    await asyncio.sleep(0.01)
    assert client.hook_handler.scans == 2
    assert len(warnings()) == 1


async def test_forgetting_a_client_ends_its_scan_s_wait_to_retry(resizer, warnings, monkeypatch):
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_RETRY_DELAY", 30.0)
    client = SimClient()
    client.hook_handler.unsettled = 1
    resizer.resize(client, _SMALL_SIZE)
    await _until(lambda: heap_scan.scanner().retrying(client.hook_handler) == 1)
    scan = resizer._entries[client.window_handle].scan
    await resizer.forget(client)
    await asyncio.wait_for(asyncio.gather(scan, return_exceptions=True), 5)
    assert scan.cancelled()
    assert heap_scan.scanner().retrying(client.hook_handler) == 0
    assert client.hook_handler.scans == 1
    assert warnings() == []


async def test_stop_all_ends_a_resize_waiting_on_a_retry_and_the_scan_runs_on(resizer, reports, monkeypatch):
    """As for any scan: Stop all ends the resize waiting on it, and what the scan finds is kept for the next."""
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_RETRY_DELAY", 30.0)
    client = SimClient()
    client.hook_handler.unsettled = 1
    resizer.resize(client, _SMALL_SIZE)
    entry = resizer._entries[client.window_handle]
    await _until(lambda: heap_scan.scanner().retrying(client.hook_handler) == 1)
    worker = entry.task
    await resizer.stop_all()
    assert worker.done()
    assert _writes(client) == []
    assert not entry.scan.done(), "the scan is worth keeping"

    for pause in list(heap_scan.scanner()._pausing[client.hook_handler]):
        heap_scan._end_pause(pause)  # as its delay would
    await asyncio.wait_for(entry.scan, 5)
    assert entry.nodes is not None
    assert (_writes(client), reports) == ([], [])
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.hook_handler.scans == 2
    assert client.game_size == _SMALL_SIZE


async def test_forgetting_a_waiting_client_ends_its_wait(resizer, reports):
    client = SimClient(loading=True)
    resizer.resize(client, _SMALL_SIZE)
    task = resizer._entries[client.window_handle].task
    await resizer.forget(client)
    assert task.done()
    assert reports == []
    await resizer.forget(client)


# -- giving back -----------------------------------------------------------------------------------


async def test_giving_back_returns_the_launch_size_and_its_ui_size(resizer, reports):
    client = SimClient(game_size=(800, 600), ui_size=_REGULAR)
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert client.ui_size == _MICRO

    await resizer.give_back(client)
    assert client.game_size == (800, 600)
    assert client.ui_size == _REGULAR
    assert client.hook_handler.resolution_text() == "800x600"
    assert reports[-1] == (client, (800, 600))


async def test_a_client_never_resized_has_nothing_to_give_back(resizer, reports):
    client = SimClient()
    await resizer.give_back(client)
    resizer.prepare([client, SimClient()])
    await resizer.give_back(client)
    assert _writes(client) == []
    assert reports == []


async def test_a_client_given_up_on_has_nothing_to_give_back(resizer, monkeypatch):
    monkeypatch.setattr(client_resizer, "RESIZE_APPLY_TIMEOUT", 0.02)
    client = SimClient()
    client.video_manager.hangs = True
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    writes = len(_writes(client))
    await resizer.give_back(client)
    assert len(_writes(client)) == writes


async def test_giving_back_runs_instead_of_any_target_still_waiting(resizer):
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    client.loading = True
    resizer.resize(client, _LARGE_SIZE)
    client.loading = False
    await resizer.give_back(client)
    assert client.game_size == (800, 600)
    assert "1280x720" not in [d for k, d in _writes(client) if k == "resolution"]


async def test_giving_back_reaches_a_client_in_a_fight(resizer):
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    client.battle = True
    await resizer.give_back(client)
    assert client.game_size == (800, 600)


async def test_giving_back_leaves_a_client_loading_with_a_warning_on_a_short_timeout(resizer, monkeypatch, warnings):
    monkeypatch.setattr(client_resizer, "RESIZE_GIVE_BACK_TIMEOUT", 0.03)
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    client.loading = True
    await resizer.give_back(client)
    assert client.game_size == _SMALL_SIZE
    assert len(warnings()) == 1
    assert resizer._entries[client.window_handle].task.done()


async def test_a_give_back_that_hangs_gives_one_warning(resizer, monkeypatch, warnings):
    monkeypatch.setattr(client_resizer, "RESIZE_GIVE_BACK_TIMEOUT", 0.03)
    client = SimClient()
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    client.video_manager.hangs = True
    await resizer.give_back(client)
    assert not client.video_manager.pending
    assert client.game_size == _SMALL_SIZE
    assert len(warnings()) == 1


async def test_giving_back_is_on_the_shorter_timeout():
    assert timing.RESIZE_GIVE_BACK_TIMEOUT < timing.RESIZE_APPLY_TIMEOUT


# -- giving back to the size named, for a client launched at another ----------------------------------


@pytest.fixture
def no_grace(monkeypatch):
    """A background scan runs at once, as one beside a key map's would."""
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_BACKGROUND_GRACE", 0.0)


async def test_a_client_launched_small_and_never_resized_goes_back_to_the_size_named_with_its_ui_size(
    resizer, reports, no_grace
):
    """Launched at its small tile's size, so nothing ever resized it: the user's own size and UI scale all the same."""
    client = SimClient(game_size=_SMALL_SIZE, ui_size=_MICRO)
    resizer.give_back_to(client, _LARGE_SIZE)

    await resizer.give_back(client)

    assert client.game_size == _LARGE_SIZE
    assert client.ui_size == ui_size_for(_HUGE, _LARGE_SIZE) == _HUGE
    assert client.hook_handler.resolution_text() == "1280x720"
    assert reports == [(client, _LARGE_SIZE)]


async def test_a_client_launched_small_and_resized_goes_back_to_the_size_named_not_the_one_it_opened_at(
    resizer, no_grace
):
    client = SimClient(game_size=_SMALL_SIZE, ui_size=_MICRO)
    resizer.give_back_to(client, _LARGE_SIZE)
    resizer.resize(client, (854, 480))
    await _settled(resizer, client)
    assert client.game_size == (854, 480)

    await resizer.give_back(client)

    assert client.game_size == _LARGE_SIZE
    assert client.ui_size == _HUGE


async def test_resizing_to_the_size_named_takes_the_user_s_ui_size_for_it(resizer, no_grace):
    """A lone client goes back up to it, through a resize, not a give back."""
    client = SimClient(game_size=_SMALL_SIZE, ui_size=_MICRO)
    resizer.give_back_to(client, _LARGE_SIZE)

    resizer.resize(client, _LARGE_SIZE)
    await _settled(resizer, client)

    assert (client.game_size, client.ui_size) == (_LARGE_SIZE, _HUGE)


async def test_the_size_named_has_its_nodes_found_at_once_in_the_background_and_nothing_written(resizer, no_grace):
    client = SimClient(game_size=_SMALL_SIZE)
    resizer.give_back_to(client, _LARGE_SIZE)
    entry = resizer._entries[client.window_handle]
    assert entry.background

    await entry.scan

    assert client.hook_handler.scans == 1
    assert entry.nodes is not None
    assert _writes(client) == []


async def test_a_resize_waiting_on_a_background_scan_makes_it_urgent(resizer, monkeypatch):
    """The background waits out its grace, and a resize asked meanwhile doesn't."""
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_BACKGROUND_GRACE", 30.0)
    client = SimClient(game_size=_SMALL_SIZE)
    resizer.give_back_to(client, _LARGE_SIZE)
    await _until(lambda: heap_scan.scanner().waiting(client.hook_handler) == 1)
    promoted = []
    real = heap_scan.promote
    monkeypatch.setattr(heap_scan, "promote", lambda reader: promoted.append(reader) or real(reader))

    resizer.resize(client, (854, 480))
    await _settled(resizer, client)

    assert promoted == [client.hook_handler]
    assert client.game_size == (854, 480)
    assert client.hook_handler.scans == 1


async def test_a_client_forgotten_forgets_the_size_named(resizer, no_grace):
    """An unhook: hooked again, it starts afresh, and the product names the size again."""
    client = SimClient(game_size=_SMALL_SIZE)
    resizer.give_back_to(client, _LARGE_SIZE)
    await resizer.forget(client)

    await resizer.give_back(client)

    assert _writes(client) == []
    assert client.game_size == _SMALL_SIZE


async def test_a_client_given_up_on_scans_no_more_for_a_size_named(resizer, warnings, no_grace):
    client = SimClient()
    client.video_manager.error = VideoManagerError("no pending byte")
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    scans = client.hook_handler.scans

    resizer.give_back_to(client, _LARGE_SIZE)
    await resizer.give_back(client)

    assert client.hook_handler.scans == scans
    assert _writes(client) == []
    assert len(warnings()) == 1


# -- the trace ----------------------------------------------------------------------------------


async def test_a_trace_places_each_part_of_a_resize(resizer, tmp_path):
    """Where a resize's time went: the load it waited out, finding the nodes, the lease and the apply."""
    client = SimClient(game_size=_LARGE_SIZE, loading=True)
    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    try:
        resizer.resize(client, _SMALL_SIZE)
        await asyncio.sleep(0.03)
        client.loading = False
        await _settled(resizer, client)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = {r["name"]: r for r in records if r["kind"] == "span"}
    worker = spans["resize.worker"]
    assert worker["slot"] == 3
    assert worker["attrs"]["target"] == list(_SMALL_SIZE)
    assert worker["attrs"]["loading_s"] > 0
    # The scan the worker had to start itself, as nothing prepared one, inside its wait for the nodes.
    assert spans["resize.find"]["parent"] == spans["resize.nodes"]["id"]
    assert spans["resize.config_nodes"]["parent"] == spans["resize.find"]["id"]
    # Its request waited inside the search for the nodes; the pass served it from outside, as its player's.
    assert spans["heap_scan.request"]["parent"] == spans["resize.config_nodes"]["id"]
    assert spans["heap_scan.request"]["attrs"]["pass_id"] == spans["heap_scan.pass"]["attrs"]["pass_id"]
    assert (spans["heap_scan.pass"]["slot"], spans["heap_scan.pass"]["parent"]) == (3, None)
    assert spans["heap_scan.pass"]["attrs"]["patterns"] == 2
    assert spans["resize.apply"]["parent"] == worker["id"]
    assert [r["name"] for r in records if r["kind"] == "mark"] == ["resize.leased"]


async def test_a_trace_shows_the_route_taken_inside_the_search_for_the_nodes(resizer, tmp_path):
    client = _routed()
    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    try:
        resizer.resize(client, _SMALL_SIZE)
        await _settled(resizer, client)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = {r["name"]: r for r in records if r["kind"] == "span"}
    route = spans["config.route"]
    assert route["parent"] == spans["resize.config_nodes"]["id"]
    assert (route["slot"], route["attrs"]["outcome"]) == (3, "route")
    assert "reason" not in route["attrs"]
    assert "heap_scan.request" not in spans


async def test_a_trace_shows_a_route_that_missed_and_the_heap_after_it(resizer, tmp_path):
    client = SimClient()
    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    try:
        resizer.resize(client, _SMALL_SIZE)
        await _settled(resizer, client)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = {r["name"]: r for r in records if r["kind"] == "span"}
    assert spans["config.route"]["attrs"] == {
        "module_scan": spans["config.route"]["attrs"]["module_scan"],
        "outcome": "heap",
        "reason": "no single pattern match",
    }
    assert spans["heap_scan.request"]["parent"] == spans["resize.config_nodes"]["id"]
    route, request = spans["config.route"], spans["heap_scan.request"]
    assert route["t"] + route["dur"] <= request["t"] + 0.0001, "the route first, then the heap"


async def test_a_trace_shows_the_scan_s_retry_inside_the_search_for_the_nodes(resizer, tmp_path):
    client = SimClient()
    client.hook_handler.unsettled = 1
    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    try:
        resizer.resize(client, _SMALL_SIZE)
        await _settled(resizer, client)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    (mark,) = [r for r in records if r["kind"] == "mark" and r["name"] == "heap_scan.retry"]
    assert (mark["caller"], mark["slot"], mark["hwnd"], mark["skipped"]) == (
        "resize.config_nodes",
        3,
        client.window_handle,
        1,
    )
    (search,) = [r for r in records if r.get("name") == "resize.config_nodes"]
    requests = [r for r in records if r.get("name") == "heap_scan.request"]
    assert [r["parent"] for r in requests] == [search["id"]] * 2, "both requests are the one search's"
    assert [r["attrs"]["skipped"] for r in records if r.get("name") == "heap_scan.pass"] == [1, 0]

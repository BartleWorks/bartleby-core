"""Resizing a running client, on SimClient: the order of its writes, its lease, its one warning, giving back."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from wizwalker.errors import PatternFailed, VideoManagerError

from bartleby.runtime import timing
from bartleby.runtime.arbiter import Arbiter, Owner, Preempted
from bartleby.runtime.clients import client_resizer
from bartleby.runtime.clients.client_resizer import ClientResizer, ConfigNodes, find_config_nodes
from bartleby.runtime.game.game_sizing import ui_size_for
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.launcher import launch_profile
from bartleby.runtime.movement import follow
from bartleby.runtime.movement.follow import Follower, spacing_for
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import StateReader
from bartleby.runtime.testing.sim_client import COPY_VTABLE, DEFAULT_SETTINGS, SimClient

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
    assert first.hook_handler.scans == second.hook_handler.scans == 2  # One for each name
    assert _writes(first) == _writes(second) == []


async def test_a_swap_after_prepare_does_not_scan_again(resizer):
    first, second = SimClient(), SimClient()
    resizer.prepare([first, second])
    await resizer._entries[first.window_handle].scan
    resizer.resize(first, _SMALL_SIZE)
    await _settled(resizer, first)
    assert first.hook_handler.scans == 2
    assert first.game_size == _SMALL_SIZE


async def test_a_resize_during_the_scan_waits_on_that_scan(resizer):
    first, second = SimClient(), SimClient()
    first.hook_handler.scan_seconds = 0.02
    resizer.prepare([first, second])
    resizer.resize(first, _SMALL_SIZE)
    await _settled(resizer, first)
    assert first.hook_handler.scans == 2
    assert first.game_size == _SMALL_SIZE


async def test_a_node_that_went_stale_is_found_again_before_any_write(resizer):
    client = SimClient()
    memory = client.hook_handler
    resizer.resize(client, _SMALL_SIZE)
    await _settled(resizer, client)
    assert memory.scans == 2

    memory.forget_node(memory.ui_size_node)
    memory.ui_size_node = memory.place_node("UISize", marked=True, text="5", value=_MICRO)
    resizer.resize(client, _LARGE_SIZE)
    await _settled(resizer, client)
    assert memory.scans == 4
    assert client.game_size == _LARGE_SIZE
    assert client.ui_size == ui_size_for(_HUGE, _LARGE_SIZE)


async def test_the_scan_holds_no_lease_and_a_follower_ticks_through_it(resizer, arbiter, monkeypatch):
    """The steering path: a resize finds its nodes before its lease, so a follower walks through the scan.

    No tick goes past probe_follow_jitter's 250 ms, against two scans of 300 ms spent off the loop,
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
        assert client.hook_handler.scans == 2
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

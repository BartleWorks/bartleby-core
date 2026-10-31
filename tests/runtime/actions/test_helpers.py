"""The helpers: each presses only through a lease, and only where there is something to press."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bartleby.runtime.actions import helpers, live_character
from bartleby.runtime.actions.helpers import AutoDialogue, Member, SpeedBoost
from bartleby.runtime.arbiter import Arbiter, Owner
from bartleby.runtime.clients.group_manager import GroupResult
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.testing.sim_client import SimClient


@pytest.fixture
def arbiter():
    return Arbiter()


def team(*clients):
    return [Member(c, i + 1) for i, c in enumerate(clients)]


async def test_interact_presses_only_where_the_prompt_shows(arbiter):
    near, far = SimClient(npc_range=True), SimClient()
    pressed = await helpers.interact(arbiter, team(near, far))
    assert [m.slot for m in pressed] == [1]
    assert near.presses(GameAction.INTERACT) == 1
    assert far.presses(GameAction.INTERACT) == 0


async def test_interact_takes_a_walking_client_from_movement(arbiter):
    client = SimClient(npc_range=True)
    walking = asyncio.Event()
    stopped = []

    async def walk():
        try:
            async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
                await lease.input.hold(GameAction.MOVE_FORWARD)
                walking.set()
                await asyncio.sleep(10)
        except helpers.LeaseLost:
            stopped.append(True)

    task = asyncio.create_task(walk())
    await walking.wait()
    await helpers.interact(arbiter, team(client))
    await task
    assert stopped and client.presses(GameAction.INTERACT) == 1


async def test_dialogue_is_pressed_only_where_it_is_open(arbiter):
    talking, quiet = SimClient(), SimClient()
    advanced = []

    async def visible(client, path):
        return client is talking

    async def advance(client):
        advanced.append(client)
        return True

    with (
        patch.object(helpers.node_helpers, "is_visible_by_path", side_effect=visible),
        patch.object(helpers.node_helpers, "advance_dialog_once", side_effect=advance),
    ):
        pressed = await helpers.advance_dialogue(arbiter, team(talking, quiet))
    assert [m.slot for m in pressed] == [1]
    assert advanced == [talking]


async def test_a_dialogue_watcher_skips_a_client_held_elsewhere_rather_than_waiting(arbiter):
    client = SimClient()
    with (
        patch.object(helpers.node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(helpers.node_helpers, "advance_dialog_once", AsyncMock(return_value=True)) as advance,
    ):
        async with arbiter.acquire(client, Owner.COMBAT):
            pressed = await asyncio.wait_for(
                asyncio.create_task(helpers.advance_dialogue(arbiter, team(client), wait=False)), 1
            )
    assert pressed == [] and not advance.called


async def test_auto_dialogue_keeps_pressing_until_switched_off(arbiter):
    client = SimClient()
    changes = []
    with (
        patch.object(helpers.node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(helpers.node_helpers, "advance_dialog_once", AsyncMock(return_value=True)) as advance,
    ):
        toggle = AutoDialogue(arbiter, lambda: team(client), on_change=lambda: changes.append(toggle.on))
        await toggle.set(True)
        await asyncio.sleep(0.2)
        await toggle.set(False)
    assert advance.await_count >= 2
    assert changes == [True, False]


async def test_speed_never_goes_above_2x_and_is_given_back(arbiter, monkeypatch):
    monkeypatch.setattr(helpers, "SPEED_REAPPLY_INTERVAL", 0.01)
    client = SimClient(speed=40)
    boost = SpeedBoost(arbiter, lambda: team(client), multiplier=lambda: 3.0)
    await boost.set(True)
    await asyncio.sleep(0.05)
    assert client.speed == 100
    # A zone change: the game writes its own value, and the boost writes over it again.
    client.speed = 55
    await asyncio.sleep(0.05)
    assert client.speed == 100
    await boost.set(False)
    assert client.speed == 55, "switching off gives back the game's latest value"


@pytest.fixture
def speed_on(arbiter, monkeypatch):
    """A running boost at 2.0x over *client*, ticking fast, switched off at the end of the test."""
    monkeypatch.setattr(helpers, "SPEED_REAPPLY_INTERVAL", 0.001)
    boosts = []

    async def start(client):
        boost = SpeedBoost(arbiter, lambda: team(client), multiplier=lambda: 2.0)
        boosts.append(boost)
        await boost.set(True)
        return boost

    return start


async def _until(predicate, ticks: int = 500) -> None:
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition never became true")


def _speed_writes(client) -> int:
    return sum(1 for r in client.inputs if r.kind == "speed")


async def test_speed_off_leaves_a_value_the_game_wrote_since(speed_on):
    client = SimClient(speed=0)
    boost = await speed_on(client)
    await _until(lambda: client.speed == 100)
    # Freeze the loop, so the game's write below is not overwritten.
    boost._task.cancel()
    await asyncio.sleep(0.005)
    client.speed = 25
    await boost.set(False)
    assert client.speed == 25


async def test_speed_skips_a_client_on_a_loading_screen(speed_on):
    client = SimClient(speed=0, loading=True)
    boost = await speed_on(client)
    await asyncio.sleep(0.01)
    await boost.set(False)
    assert _speed_writes(client) == 0


async def test_a_restored_client_gets_its_speed_back_while_the_boost_stays_on(speed_on):
    client = SimClient(speed=10)
    boost = await speed_on(client)
    await _until(lambda: client.speed == 100)
    await boost.restore(client)
    assert client.speed == 10
    await boost.set(False)


async def test_speed_never_writes_to_a_logged_out_characters_freed_object(speed_on):
    client = SimClient(speed=0)
    boost = await speed_on(client)
    await _until(lambda: client.speed == 100)
    writes = _speed_writes(client)

    client.log_out()
    await asyncio.sleep(0.02)
    await boost.set(False)
    assert _speed_writes(client) == writes
    assert client.speed == 0x1234


async def test_speed_never_writes_at_character_select(speed_on, monkeypatch):
    monkeypatch.setattr(live_character, "at_character_select", AsyncMock(return_value=True))
    client = SimClient(speed=0)
    boost = await speed_on(client)
    await asyncio.sleep(0.02)
    await boost.set(False)
    assert _speed_writes(client) == 0


async def test_speed_never_writes_while_the_character_id_keeps_changing(speed_on):
    client = SimClient(speed=0)
    ids = iter(range(1, 100_000))
    client.client_object.read_value_from_offset = AsyncMock(side_effect=lambda *_: next(ids))
    boost = await speed_on(client)
    await asyncio.sleep(0.02)
    await boost.set(False)
    assert _speed_writes(client) == 0


async def test_speed_boosts_a_new_character_and_gives_back_only_its_own_speed(speed_on):
    client = SimClient(speed=10)
    boost = await speed_on(client)
    await _until(lambda: client.speed == 100)

    # The next character loads: the hook hands over its object, at its own speed.
    client.character = 777
    client.speed = 0
    await _until(lambda: client.speed == 100)

    await boost.set(False)
    assert client.speed == 0


async def test_speed_off_gives_nothing_back_to_a_different_character(speed_on):
    client = SimClient(speed=10)
    boost = await speed_on(client)
    await _until(lambda: client.speed == 100)

    # Another character, whose field happens to hold our value.
    client.character = 777
    await boost.set(False)
    assert client.speed == 100


async def test_gather_ports_everyone_but_the_source_under_their_leases(arbiter):
    source, a, b = SimClient(), SimClient(), SimClient()
    members = team(source, a, b)
    seen_holders = []

    async def port(clients, name):
        seen_holders.extend(arbiter.holder(c).owner for c in clients)
        assert name == "luke skullhunter"
        return helpers.node_helpers.PortToFriendResult(seen_loading={0, 1})

    with (
        patch.object(helpers.wizard_names, "cached_name", return_value="Luke SkullHunter"),
        patch.object(helpers.node_helpers, "port_to_friend", side_effect=port),
    ):
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert seen_holders == [Owner.HOTKEY, Owner.HOTKEY]
    assert all(arbiter.holder(c) is None for c in (source, a, b))


async def test_gather_without_the_sources_name_ports_nobody(arbiter):
    members = team(SimClient(), SimClient())
    with (
        patch.object(helpers.wizard_names, "cached_name", return_value=None),
        patch.object(helpers.wizard_names, "read_name_from_memory", AsyncMock(return_value=None)),
        patch.object(helpers.node_helpers, "port_to_friend") as port,
    ):
        assert await helpers.gather(arbiter, members[0], members) == []
    assert not port.called


async def test_group_leads_from_the_source(arbiter):
    members = team(SimClient(), SimClient(), SimClient())
    with (
        patch.object(helpers.wizard_names, "resolve_names", AsyncMock(return_value=["A", "B", "C"])),
        patch.object(
            helpers.group_manager, "form_group", AsyncMock(return_value=GroupResult(success=True, added=["P1"]))
        ) as form,
    ):
        assert await helpers.group(arbiter, members[1], members)
    assert form.await_args.kwargs["leader_index"] == 1

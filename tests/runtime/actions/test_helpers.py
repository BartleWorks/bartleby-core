"""The helpers: each presses only through a lease, and only where there is something to press."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bartleby.runtime.actions import helpers
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

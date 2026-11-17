"""The helpers: each presses only through a lease, and only where there is something to press."""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, patch

import pytest

from bartleby.runtime.actions import helpers, live_character
from bartleby.runtime.actions.helpers import AutoDialogue, Member, RosterMemo, SpeedBoost
from bartleby.runtime.actions.node_helpers import PortToFriendResult
from bartleby.runtime.arbiter import Arbiter, Owner
from bartleby.runtime.clients.group_manager import GroupResult, GroupState
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

    async def advance(client, *, decline=True):
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


async def test_auto_dialogue_declines_side_quests_unless_told_not_to(arbiter, monkeypatch):
    monkeypatch.setattr(helpers, "DIALOG_FAST_POLL_INTERVAL", 0.001)
    client = SimClient()
    decline = [True]
    seen = []

    async def advance(_client, *, decline=True):
        seen.append(decline)
        return True

    with (
        patch.object(helpers.node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(helpers.node_helpers, "advance_dialog_once", side_effect=advance),
    ):
        toggle = AutoDialogue(arbiter, lambda: team(client), decline=lambda: decline[0])
        await toggle.set(True)
        await asyncio.sleep(0.05)
        decline[0] = False
        await asyncio.sleep(0.05)
        await toggle.set(False)
    assert seen[0] is True and seen[-1] is False, "read again at every page"


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


# -- Gather's two routes ----------------------------------------------------------------------

NAMES = ["Alice Ashblade", "Bob Battlecaster", "Carol Cloudrider", "Dave Dawnwhisper"]


class GatherWorld:
    """What Gather reads and ports through, faked: each client's HUD, names and roster.

    *leads* is the source's HUD; *grouped* each other client's, by client. *roster* is what
    a social-window visit on the source reads. The ports record the clients handed to them,
    and the owner each one was held by at the time.
    """

    def __init__(self, arbiter, clients, *, leads=True, grouped=None, roster=()):
        self.arbiter = arbiter
        self.leads = leads
        self.grouped = dict(grouped or {})
        self.roster = tuple(roster)
        self.names = dict(zip(clients, NAMES))
        self.roster_reads = 0
        self.source_held_by = []
        self.by_leader: list[list] = []
        self.by_friend: list[list] = []
        self.holders: list = []
        self.leader_result = None
        self.port_to_leader = self._port_to_leader
        self.port_to_friend = self._port_to_friend

    async def hud_leads(self, client):
        return self.leads

    async def hud_grouped(self, client):
        return self.grouped.get(client)

    async def read_group_state(self, client):
        self.roster_reads += 1
        self.source_held_by.append(self.arbiter.holder(client).owner)
        return GroupState(grouped=bool(self.roster), roster=self.roster)

    async def _port_to_leader(self, clients):
        self.by_leader.append(list(clients))
        self.holders.extend(self.arbiter.holder(c).owner for c in clients)
        if self.leader_result is not None:
            return self.leader_result
        return PortToFriendResult(seen_loading=set(range(len(clients))))

    async def _port_to_friend(self, clients, name):
        assert name == NAMES[0].lower()
        self.by_friend.append(list(clients))
        self.holders.extend(self.arbiter.holder(c).owner for c in clients)
        return PortToFriendResult(seen_loading=set(range(len(clients))))

    # What the patches call, so that a test can swap either port for a wrapper of its own.
    async def _leader_port(self, clients):
        return await self.port_to_leader(clients)

    async def _friend_port(self, clients, name):
        return await self.port_to_friend(clients, name)

    @contextlib.contextmanager
    def patched(self):
        with (
            patch.object(helpers.wizard_names, "cached_name", side_effect=lambda c: self.names.get(c)),
            patch.object(helpers.wizard_names, "read_name_from_memory", AsyncMock(return_value=None)),
            patch.object(helpers.group_manager, "hud_leads", side_effect=self.hud_leads),
            patch.object(helpers.group_manager, "hud_grouped", side_effect=self.hud_grouped),
            patch.object(helpers.group_manager, "read_group_state", side_effect=self.read_group_state),
            patch.object(helpers.group_manager, "port_to_leader", side_effect=self._leader_port),
            patch.object(helpers.node_helpers, "port_to_friend", side_effect=self._friend_port),
        ):
            yield self


def world(arbiter, count=3, **kwargs):
    clients = [SimClient() for _ in range(count)]
    return GatherWorld(arbiter, clients, **kwargs), team(*clients)


async def test_gather_takes_teleport_to_leader_for_a_mover_proven_in_the_sources_group(arbiter, caplog):
    w, members = world(arbiter, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    caplog.set_level(logging.INFO, logger=helpers.__name__)
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == [[members[1].client, members[2].client]]
    assert w.by_friend == []
    assert w.roster_reads == 1
    assert w.source_held_by == [Owner.HOTKEY]
    assert w.holders == [Owner.HOTKEY, Owner.HOTKEY]
    assert all(arbiter.holder(m.client) is None for m in members)
    assert "Gather: P2, P3 by Teleport to Leader" in caplog.messages
    assert not any(name in message for name in NAMES for message in caplog.messages)


async def test_gather_sends_a_mover_in_another_group_through_the_friends_list(arbiter):
    # P3 reads grouped, but the source's roster doesn't list it: it is in a stranger's group.
    w, members = world(arbiter, roster=(NAMES[0], NAMES[1], "Stranger One"))
    w.grouped = {members[1].client: True, members[2].client: True}
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == [[members[1].client]]
    assert w.by_friend == [[members[2].client]]


@pytest.mark.parametrize("leads", [False, None], ids=["not-leading", "cannot-tell-in-battle"])
async def test_a_source_that_does_not_read_leading_sends_everyone_through_the_friends_list(arbiter, leads):
    w, members = world(arbiter, leads=leads, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == []
    assert w.by_friend == [[members[1].client, members[2].client]]
    assert w.roster_reads == 0


async def test_a_mixed_team_splits_into_the_two_routes_ported_at_once(arbiter, caplog):
    # P2 proven, P3 ungrouped, P4 grouped but not on the roster.
    w, members = world(arbiter, count=4, roster=NAMES[:2])
    w.grouped = {members[1].client: True, members[2].client: False, members[3].client: True}
    both_started = asyncio.Event()
    started = []

    def meeting(port):
        async def wrapped(*args):
            started.append(port)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1.0)  # fails rather than hangs if sequential
            return await port(*args)

        return wrapped

    w.port_to_leader = meeting(w.port_to_leader)
    w.port_to_friend = meeting(w.port_to_friend)
    caplog.set_level(logging.INFO, logger=helpers.__name__)
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3, 4]
    assert w.by_leader == [[members[1].client]]
    assert w.by_friend == [[members[2].client, members[3].client]]
    assert "Gather: P2 by Teleport to Leader" in caplog.messages
    assert "Gather: P3, P4 through the friends list" in caplog.messages


async def test_an_unknown_source_name_still_lets_proven_movers_take_the_leader_route(arbiter, caplog):
    w, members = world(arbiter, roster=NAMES[:2])
    w.names[members[0].client] = None
    w.grouped = {members[1].client: True, members[2].client: False}
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2]
    assert w.by_leader == [[members[1].client]]
    assert w.by_friend == []
    assert "Gather: P1's wizard name is not known yet" in caplog.messages
    assert "Gather: P3 cannot port to P1 through the friends list" in caplog.messages


async def test_a_mover_without_a_cached_name_cannot_be_proven(arbiter):
    w, members = world(arbiter, count=2, roster=NAMES[:2])
    w.names[members[1].client] = None
    w.grouped = {members[1].client: True}
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
    assert w.by_leader == []
    assert w.by_friend == [[members[1].client]]


async def test_a_failed_roster_read_proves_no_one(arbiter):
    w, members = world(arbiter, count=2, roster=())
    w.grouped = {members[1].client: True}
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2]
    assert w.by_leader == []
    assert w.by_friend == [[members[1].client]]


async def test_a_second_gather_reuses_the_roster_the_first_read(arbiter):
    w, members = world(arbiter, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    memo = RosterMemo()
    with w.patched():
        await helpers.gather(arbiter, members[0], members, memo)
        await helpers.gather(arbiter, members[0], members, memo)
    assert w.roster_reads == 1
    assert len(w.by_leader) == 2


async def test_gather_without_a_memo_reads_the_roster_each_time(arbiter):
    w, members = world(arbiter, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
        await helpers.gather(arbiter, members[0], members)
    assert w.roster_reads == 2


async def test_gather_reuses_the_roster_group_left_and_reads_none(arbiter):
    w, members = world(arbiter, roster=())
    w.grouped = {members[1].client: True, members[2].client: True}
    roster = GroupState(grouped=True, roster=tuple(NAMES[:3]))
    memo = RosterMemo()
    with (
        w.patched(),
        patch.object(helpers.wizard_names, "resolve_names", AsyncMock(return_value=NAMES[:3])),
        patch.object(
            helpers.group_manager,
            "form_group",
            AsyncMock(return_value=GroupResult(success=True, states=[roster, roster, roster])),
        ),
    ):
        assert await helpers.group(arbiter, members[0], members, memo)
        went = await helpers.gather(arbiter, members[0], members, memo)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == [[members[1].client, members[2].client]]
    assert w.roster_reads == 0


async def test_a_group_that_read_nothing_leaves_no_memo():
    clients = [SimClient(), SimClient()]
    memo = RosterMemo()
    memo.record(clients[0], NAMES[:2])
    with patch.object(helpers.wizard_names, "cached_name", side_effect=dict(zip(clients, NAMES)).get):
        assert memo.lists(clients[0], clients[1])
        memo.record_group(clients, clients[0], None)
        assert not memo.lists(clients[0], clients[1])


async def test_a_memo_proves_nothing_for_another_source():
    clients = [SimClient(), SimClient(), SimClient()]
    memo = RosterMemo()
    memo.record(clients[0], NAMES[:3])
    with patch.object(helpers.wizard_names, "cached_name", side_effect=dict(zip(clients, NAMES)).get):
        assert not memo.lists(clients[1], clients[2])


async def test_the_memo_is_forgotten_once_the_source_no_longer_leads(arbiter):
    w, members = world(arbiter, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    memo = RosterMemo()
    with w.patched():
        await helpers.gather(arbiter, members[0], members, memo)
        w.leads = None  # in battle: it cannot tell, so the memo is not trusted after it either
        await helpers.gather(arbiter, members[0], members, memo)
        w.leads = True
        await helpers.gather(arbiter, members[0], members, memo)
    assert w.roster_reads == 2
    assert len(w.by_friend) == 1


async def test_a_mover_that_no_longer_reads_grouped_is_dropped_from_the_memo(arbiter):
    w, members = world(arbiter, roster=NAMES[:3])
    w.grouped = {members[1].client: True, members[2].client: True}
    memo = RosterMemo()
    with w.patched():
        await helpers.gather(arbiter, members[0], members, memo)
        w.grouped[members[2].client] = False  # P3 left
        await helpers.gather(arbiter, members[0], members, memo)
        assert w.roster_reads == 1
        # P3 grouped again, here in another group: the roster is read again, not trusted.
        w.grouped[members[2].client] = True
        w.roster = (NAMES[0], NAMES[1])
        await helpers.gather(arbiter, members[0], members, memo)
    assert w.roster_reads == 2
    assert w.by_leader[-1] == [members[1].client]
    assert w.by_friend[-1] == [members[2].client]


async def test_a_leader_port_that_fails_forgets_the_memo(arbiter):
    w, members = world(arbiter, count=2, roster=NAMES[:2])
    w.grouped = {members[1].client: True}
    w.leader_result = PortToFriendResult(seen_loading=set(), unstarted=frozenset({0}))
    memo = RosterMemo()
    with w.patched():
        assert await helpers.gather(arbiter, members[0], members, memo) == []
        await helpers.gather(arbiter, members[0], members, memo)
    assert w.roster_reads == 2

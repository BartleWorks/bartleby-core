"""The helpers: each presses only through a lease, and only where there is something to press."""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, patch

import pytest

from bartleby.runtime.actions import helpers, live_character
from bartleby.runtime.actions.helpers import AutoDialogue, Member, SpeedBoost
from bartleby.runtime.actions.node_helpers import PortToFriendResult
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
    """group_manager as Gather sees it, faked: the group record, the HUD and both ports.

    *record* is what members_led_by proves, the clients in a group the source leads, or None
    for no record of the source leading. *grouped* is each client's HUD, the source's
    included. *learned* is what learn_group finds, and becomes the record. The ports record
    the clients handed to them, and the owner each one was held by at the time.
    """

    def __init__(self, arbiter, clients, *, record=None, grouped=None, learned=()):
        self.arbiter = arbiter
        self.record = record
        self.grouped = dict(grouped or {})
        self.learned = list(learned)
        self.names = dict(zip(clients, NAMES))
        self.learns = 0
        self.source_held_by = []
        self.forgotten = []
        self.by_leader: list[list] = []
        self.by_friend: list[list] = []
        self.holders: list = []
        self.leader_result = None
        self.port_to_leader = self._port_to_leader
        self.port_to_friend = self._port_to_friend

    async def members_led_by(self, target, clients):
        if self.record is None:
            return None
        return [c for c in clients if c in self.record]

    async def learn_group(self, target, clients):
        self.learns += 1
        self.source_held_by.append(self.arbiter.holder(target).owner)
        self.record = list(self.learned)
        return [c for c in clients if c in self.learned]

    async def hud_grouped(self, client):
        return self.grouped.get(client)

    def forget_group(self, client):
        self.forgotten.append(client)
        self.record = None

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
        gm = helpers.group_manager
        with (
            patch.object(helpers.wizard_names, "cached_name", side_effect=lambda c: self.names.get(c)),
            patch.object(helpers.wizard_names, "read_name_from_memory", AsyncMock(return_value=None)),
            patch.object(gm, "members_led_by", side_effect=self.members_led_by),
            patch.object(gm, "learn_group", side_effect=self.learn_group),
            patch.object(gm, "hud_grouped", side_effect=self.hud_grouped),
            patch.object(gm, "forget_group", side_effect=self.forget_group),
            patch.object(gm, "port_to_leader", side_effect=self._leader_port),
            patch.object(helpers.node_helpers, "port_to_friend", side_effect=self._friend_port),
        ):
            yield self


def world(arbiter, count=3, **kwargs):
    clients = [SimClient() for _ in range(count)]
    return GatherWorld(arbiter, clients, **kwargs), team(*clients)


def everyone_grouped(members):
    return {m.client: True for m in members}


async def test_gather_takes_teleport_to_leader_for_a_mover_the_record_proves(arbiter, caplog):
    w, members = world(arbiter)
    w.record = [members[1].client, members[2].client]
    caplog.set_level(logging.INFO, logger=helpers.__name__)
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == [[members[1].client, members[2].client]]
    assert w.by_friend == []
    assert w.learns == 0, "a valid record is enough: nothing is clicked on the source"
    assert w.holders == [Owner.HOTKEY, Owner.HOTKEY]
    assert all(arbiter.holder(m.client) is None for m in members)
    assert "Gather: P2, P3 by Teleport to Leader" in caplog.messages
    assert not any(name in message for name in NAMES for message in caplog.messages)


async def test_gather_learns_the_sources_group_under_its_lease_when_there_is_no_record(arbiter):
    w, members = world(arbiter)
    w.grouped = everyone_grouped(members)
    w.learned = [members[1].client, members[2].client]
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.learns == 1
    assert w.source_held_by == [Owner.HOTKEY]
    assert w.by_leader == [[members[1].client, members[2].client]]


async def test_a_learned_group_is_not_learned_again(arbiter):
    w, members = world(arbiter)
    w.grouped = everyone_grouped(members)
    w.learned = [members[1].client, members[2].client]
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
        await helpers.gather(arbiter, members[0], members)
    assert w.learns == 1
    assert len(w.by_leader) == 2


@pytest.mark.parametrize("source_grouped", [False, None], ids=["source-ungrouped", "cannot-tell-in-battle"])
async def test_nothing_is_learned_on_a_source_that_does_not_read_grouped(arbiter, source_grouped):
    w, members = world(arbiter)
    w.grouped = {**everyone_grouped(members), members[0].client: source_grouped}
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.learns == 0
    assert w.by_leader == []
    assert w.by_friend == [[members[1].client, members[2].client]]


async def test_nothing_is_learned_when_no_mover_reads_grouped(arbiter):
    w, members = world(arbiter)
    w.grouped = {members[0].client: True, members[1].client: False}
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
    assert w.learns == 0
    assert w.by_friend == [[members[1].client, members[2].client]]


async def test_a_record_proving_no_one_sends_everyone_through_the_friends_list_without_learning(arbiter):
    """As in battle, where the record stands but the HUD cannot confirm it."""
    w, members = world(arbiter, record=[])
    w.grouped = everyone_grouped(members)
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
    assert w.learns == 0
    assert w.by_friend == [[members[1].client, members[2].client]]


async def test_gather_sends_a_mover_in_another_group_through_the_friends_list(arbiter):
    # P3 reads grouped, but the record doesn't prove it in the source's group.
    w, members = world(arbiter)
    w.record = [members[1].client]
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2, 3]
    assert w.by_leader == [[members[1].client]]
    assert w.by_friend == [[members[2].client]]


async def test_a_mixed_team_splits_into_the_two_routes_ported_at_once(arbiter, caplog):
    w, members = world(arbiter, count=4, record=[])
    w.record = [members[1].client]
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
    w, members = world(arbiter)
    w.record = [members[1].client]
    w.names[members[0].client] = None
    with w.patched():
        went = await helpers.gather(arbiter, members[0], members)
    assert [m.slot for m in went] == [2]
    assert w.by_leader == [[members[1].client]]
    assert w.by_friend == []
    assert "Gather: P1's wizard name is not known yet" in caplog.messages
    assert "Gather: P3 cannot port to P1 through the friends list" in caplog.messages


async def test_a_leader_port_that_fails_forgets_the_record(arbiter):
    w, members = world(arbiter, count=2)
    w.record = [members[1].client]
    w.leader_result = PortToFriendResult(seen_loading=set(), unstarted=frozenset({0}))
    with w.patched():
        assert await helpers.gather(arbiter, members[0], members) == []
    assert w.forgotten == [members[0].client]


async def test_a_leader_port_that_goes_keeps_the_record(arbiter):
    w, members = world(arbiter, count=2)
    w.record = [members[1].client]
    with w.patched():
        await helpers.gather(arbiter, members[0], members)
    assert w.forgotten == []


# -- Gather against the group manager itself, over its tests' FakeUI --------------------------


@pytest.fixture
def ui():
    from tests.runtime.clients.test_group_manager import FakeUI

    return FakeUI()


@contextlib.contextmanager
def _over(ui, clients):
    """FakeUI's window layer, each client's wizard name cached, and both ports recorded."""
    ported = {"leader": [], "friend": []}

    async def port_to_leader(movers):
        ported["leader"].append(list(movers))
        return PortToFriendResult(seen_loading=set(range(len(movers))))

    async def port_to_friend(movers, _name):
        ported["friend"].append(list(movers))
        return PortToFriendResult(seen_loading=set(range(len(movers))))

    with contextlib.ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(helpers.group_manager, "port_to_leader", side_effect=port_to_leader))
        stack.enter_context(patch.object(helpers.node_helpers, "port_to_friend", side_effect=port_to_friend))
        for client in clients:
            helpers.wizard_names.remember(client, ui.name_of[client])
        try:
            yield ported
        finally:
            for client in clients:
                helpers.wizard_names.forget(client)


async def test_gather_from_a_leader_whose_menu_is_closed_learns_its_group_and_takes_the_leader_route(arbiter, ui):
    """The game showed it: LeaderText reads hidden on a closed menu, so this went by the friends list."""
    clients = [ui.make_client(n) for n in NAMES[:3]]
    ui.put_in_group(*clients)  # formed by hand: no Group pressed, no record
    members = team(*clients)

    with _over(ui, clients) as ported:
        went = await helpers.gather(arbiter, members[0], members)

    assert [m.slot for m in went] == [2, 3]
    assert ported == {"leader": [clients[1:]], "friend": []}
    assert ui.menu_clicks == [clients[0], clients[0]], "one menu read on the source, put back"
    assert not ui.menu_open.get(clients[0])
    assert ui.social_button_clicks == [clients[0]], "one roster read, on the source"


async def test_gather_learns_again_for_a_wizard_added_to_the_group_by_hand(arbiter, ui):
    clients = [ui.make_client(n) for n in NAMES[:3]]
    members = team(*clients)

    with _over(ui, clients) as ported:
        assert await helpers.group(arbiter, members[0], members[:2])
        ui.put_in_group(clients[0], clients[2])  # by hand, after Group recorded the pair
        ui.menu_clicks.clear()
        ui.social_button_clicks.clear()
        went = await helpers.gather(arbiter, members[0], members)

    assert [m.slot for m in went] == [2, 3]
    assert ported == {"leader": [clients[1:]], "friend": []}
    assert ui.menu_clicks == [clients[0], clients[0]] and ui.social_button_clicks == [clients[0]]


async def test_gather_looks_once_at_a_mover_in_another_group(arbiter, ui):
    clients = [ui.make_client(n) for n in NAMES[:3]]
    members = team(*clients)

    with _over(ui, clients) as ported:
        assert await helpers.group(arbiter, members[0], members[:2])
        ui.put_in_foreign_group(clients[2], "Stranger One")
        ui.menu_clicks.clear()
        ui.social_button_clicks.clear()
        await helpers.gather(arbiter, members[0], members)
        assert ui.social_button_clicks == [clients[0]], "the group was learned again, once"
        ui.menu_clicks.clear()
        ui.social_button_clicks.clear()
        await helpers.gather(arbiter, members[0], members)

    assert ported == {"leader": [[clients[1]], [clients[1]]], "friend": [[clients[2]], [clients[2]]]}
    assert ui.menu_clicks == [] and ui.social_button_clicks == [], "the mover outside made it learn again"


async def test_gather_after_group_clicks_nothing_on_the_source(arbiter, ui):
    clients = [ui.make_client(n) for n in NAMES[:3]]
    members = team(*clients)

    with _over(ui, clients) as ported:
        assert await helpers.group(arbiter, members[0], members)
        ui.menu_clicks.clear()
        ui.social_button_clicks.clear()
        went = await helpers.gather(arbiter, members[0], members)

    assert [m.slot for m in went] == [2, 3]
    assert ported == {"leader": [clients[1:]], "friend": []}
    assert ui.menu_clicks == [] and ui.social_button_clicks == []

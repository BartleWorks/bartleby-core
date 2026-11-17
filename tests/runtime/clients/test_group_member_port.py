"""Tests for porting through the group: to a member by the Group tab, and to the leader by the HUD.

group_manager.teleport_to_group_member is the group route to a player, used by BartleBot's
port_map for two of its clients who are grouped but not friends. group_manager.teleport_to_leader
is the HUD's Teleport to Leader, whose button summons the whole group on the leader, which is
why most of its tests are about when it must click nothing. Both run against the group manager
tests' FakeUI, which models the social window and the HUD.
"""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartleby.runtime.actions.node_helpers as nh
import bartleby.runtime.clients.group_manager as gm
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.paths import (
    FRIENDLY_PLAYER_GO_TO_BUTTON_PATH,
    FRIENDLY_PLAYER_MENU_PATH,
    GROUP_HUD_BUTTON_PATH,
    GROUP_LEADER_TEXT_PATH,
    GROUP_TELEPORT_BUTTON_PATH,
    group_member_name_path,
)
from tests.runtime.clients.test_group_manager import FakeUI


@pytest.fixture
def ui():
    return FakeUI()


def _patches(ui: FakeUI, clicks: list, *, menu_opens: bool = True):
    """FakeUI's window layer, plus a roster line that opens the player menu when clicked."""
    menu_open: dict = {}

    async def find_windows_by_path(parent, path):
        # The "window" is its own path, so the click below can record which line it was.
        return [tuple(path)]

    async def click_window(client, window):
        clicks.append((client, list(window)))
        menu_open[client] = menu_opens
        return True

    async def is_visible_by_path(client, path):
        if path == FRIENDLY_PLAYER_MENU_PATH:
            return menu_open.get(client, False)
        return await ui.is_visible_by_path(client, path)

    async def click_by_path(client, path):
        if path == FRIENDLY_PLAYER_GO_TO_BUTTON_PATH:
            clicks.append((client, "go to player"))
            return menu_open.get(client, False)
        return await ui.click_by_path(client, path)

    return (
        *ui.patches(),
        patch.object(nh, "find_windows_by_path", new=AsyncMock(side_effect=find_windows_by_path)),
        patch.object(nh, "click_window", new=AsyncMock(side_effect=click_window)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=is_visible_by_path)),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click_by_path)),
    )


async def _with(patches, coro_factory):
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await coro_factory()


async def test_it_clicks_the_members_roster_line_then_go_to_player(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    clicks: list = []

    await _with(_patches(ui, clicks), lambda: gm.teleport_to_group_member(mover, ui.name_of[target].lower()))

    # Every client sees itself on Line1, so the target is on Line2.
    assert clicks == [(mover, group_member_name_path(2)), (mover, "go to player")]


async def test_a_player_not_on_the_roster_raises_and_leaves_the_window_closed(ui):
    mover, stranger = ui.make_client(), ui.make_client()
    clicks: list = []

    with pytest.raises(ValueError):
        await _with(_patches(ui, clicks), lambda: gm.teleport_to_group_member(mover, ui.name_of[stranger]))

    assert clicks == []
    assert ui.social_open[mover] is False


async def test_a_player_menu_that_never_opens_raises_and_leaves_the_window_closed(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    clicks: list = []

    with pytest.raises(ValueError):
        await _with(
            _patches(ui, clicks, menu_opens=False),
            lambda: gm.teleport_to_group_member(mover, ui.name_of[target]),
        )

    assert clicks == [(mover, group_member_name_path(2))]
    assert ui.social_open[mover] is False


async def test_port_to_group_member_closes_the_social_window_after_the_port(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0}))

    async def go():
        ui.social_open[mover] = True  # the port leaves it up
        return await gm.port_to_group_member([mover], ui.name_of[target].lower())

    result = await _with((*_patches(ui, []), patch.object(nh, "port_clients", port)), go)

    assert result.seen_loading == {0}
    port.assert_awaited_once()
    assert ui.social_open[mover] is False


# ---------------------------------------------------------------------------
# teleport_to_leader and port_to_leader
# ---------------------------------------------------------------------------


class _GroupMenu:
    """The HUD's group menu on top of FakeUI: its button toggles it, its teleport button ports.

    `clicks` records every click on either button, as (client, "menu") or (client, "teleport"),
    so a test can assert that nothing at all was clicked. `opens` False models a menu button
    whose click never lands; `teleport_clickable` False a teleport button that is missing or
    grayed. A teleport click on a client in `jumps_on_teleport` moves it, which port_clients
    reads as a port within the zone.
    """

    def __init__(self, ui: FakeUI):
        self.ui = ui
        self.open: dict = {}
        self.clicks: list = []
        self.opens = True
        self.teleport_clickable = True
        self.jumps_on_teleport: set = set()

    def clicked(self, client) -> list[str]:
        return [what for c, what in self.clicks if c is client]

    async def is_visible_by_path(self, client, path):
        if path == GROUP_TELEPORT_BUTTON_PATH:
            # Hidden while the menu is closed, as in the game, and with the HUD's group controls.
            return self.open.get(client, False) and self.ui.grouped.get(client, False)
        return await self.ui.is_visible_by_path(client, path)

    async def click_by_path(self, client, path):
        if path == GROUP_HUD_BUTTON_PATH:
            self.clicks.append((client, "menu"))
            if self.opens:
                self.open[client] = not self.open.get(client, False)
            return True
        if path == GROUP_TELEPORT_BUTTON_PATH:
            if not self.teleport_clickable or not await self.is_visible_by_path(client, path):
                return False
            self.clicks.append((client, "teleport"))
            if client in self.jumps_on_teleport:
                client.body.position = AsyncMock(return_value=XYZ(900.0, -400.0, 0.0))
            return True
        return await self.ui.click_by_path(client, path)

    def patches(self):
        return (
            *self.ui.patches(),
            patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=self.is_visible_by_path)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=self.click_by_path)),
        )


@pytest.fixture
def menu(ui):
    return _GroupMenu(ui)


def _group(ui: FakeUI):
    """A leader and a member of one group: the first client in a group leads it."""
    leader, member = ui.make_client(), ui.make_client()
    ui.put_in_group(leader, member)
    assert leader in ui.leads and member not in ui.leads
    return leader, member


async def test_a_member_with_the_menu_closed_opens_it_then_clicks_teleport(ui, menu):
    _, member = _group(ui)

    await _with(menu.patches(), lambda: gm.teleport_to_leader(member))

    assert menu.clicked(member) == ["menu", "teleport"]
    assert menu.open[member] is True, "the menu is left open for port_to_leader to close"


async def test_a_menu_already_open_is_not_toggled(ui, menu):
    _, member = _group(ui)
    menu.open[member] = True

    await _with(menu.patches(), lambda: gm.teleport_to_leader(member))

    assert menu.clicked(member) == ["teleport"]
    assert menu.open[member] is True


async def test_the_leader_is_refused_and_nothing_is_clicked(ui, menu):
    """On the leader the teleport button is Ask Group to Teleport, which summons everyone."""
    leader, _ = _group(ui)

    with pytest.raises(ValueError, match="leads"):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(leader))

    assert menu.clicks == []


async def test_the_leader_is_refused_even_with_its_menu_already_open(ui, menu):
    leader, _ = _group(ui)
    menu.open[leader] = True

    with pytest.raises(ValueError, match="leads"):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(leader))

    assert menu.clicks == []


async def test_a_hud_that_cannot_tell_who_leads_is_refused_and_nothing_is_clicked(ui, menu):
    """hud_leads None: the group button shows, but LeaderText is not in the window tree."""
    _, member = _group(ui)

    async def leader_text_missing(client, path):
        if path == GROUP_LEADER_TEXT_PATH:
            return None
        return await ui.visible_if_present(client, path)

    patches = (*menu.patches(), patch.object(nh, "visible_if_present", new=AsyncMock(side_effect=leader_text_missing)))
    with pytest.raises(ValueError, match="cannot tell"):
        await _with(patches, lambda: gm.teleport_to_leader(member))

    assert menu.clicks == []


async def test_a_client_in_battle_is_refused_and_nothing_is_clicked(ui, menu):
    """The HUD's group controls hide in battle, so it cannot say who leads."""
    _, member = _group(ui)
    member.in_battle.return_value = True

    with pytest.raises(ValueError):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(member))

    assert menu.clicks == []


async def test_an_ungrouped_client_is_refused_and_nothing_is_clicked(ui, menu):
    loner = ui.make_client()

    with pytest.raises(ValueError, match="not show this client in a group"):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(loner))

    assert menu.clicks == []


async def test_no_refusal_names_a_player(ui, menu):
    leader, _ = _group(ui)
    loner = ui.make_client()

    for client in (leader, loner):
        with pytest.raises(ValueError) as raised:
            await _with(menu.patches(), lambda c=client: gm.teleport_to_leader(c))
        assert not any(name in str(raised.value) for name in ui.name_of.values())


async def test_a_member_made_leader_while_the_menu_opens_is_refused_before_teleport(ui, menu):
    """The check is read again once the menu opens, so a leadership change then clicks nothing more."""
    leader, member, other = ui.make_client(), ui.make_client(), ui.make_client()
    ui.put_in_group(leader, member, other)
    real_click = menu.click_by_path

    async def leader_leaves_on_open(client, path):
        result = await real_click(client, path)
        if client is member and path == GROUP_HUD_BUTTON_PATH:
            ui.remove_from_group(leader)  # the group passes to the next member: this one
        return result

    patches = (*menu.patches(), patch.object(nh, "click_by_path", new=AsyncMock(side_effect=leader_leaves_on_open)))
    with pytest.raises(ValueError, match="leads"):
        await _with(patches, lambda: gm.teleport_to_leader(member))

    assert member in ui.leads
    assert menu.clicked(member) == ["menu"]


async def test_a_menu_that_never_opens_raises_without_clicking_teleport(ui, menu):
    _, member = _group(ui)
    menu.opens = False

    with pytest.raises(ValueError, match="did not open"):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(member))

    assert menu.clicked(member) == ["menu"]


async def test_a_teleport_button_that_cannot_be_clicked_raises(ui, menu):
    _, member = _group(ui)
    menu.teleport_clickable = False

    with pytest.raises(ValueError, match="could not be clicked"):
        await _with(menu.patches(), lambda: gm.teleport_to_leader(member))


async def test_it_waits_out_a_form_group_in_progress(ui, menu):
    _, member = _group(ui)

    async def go():
        lock = gm._form_group_lock()
        await lock.acquire()
        task = asyncio.create_task(gm.teleport_to_leader(member))
        await asyncio.sleep(0.01)
        assert menu.clicks == [], "nothing is clicked while the group may be changing"
        lock.release()
        await task

    await _with(menu.patches(), go)

    assert menu.clicked(member) == ["menu", "teleport"]


async def test_close_group_menu_closes_an_open_menu_and_leaves_a_closed_one(ui, menu):
    leader, member = _group(ui)
    menu.open[member] = True

    async def go():
        await gm.close_group_menu(member)
        await gm.close_group_menu(leader)

    await _with(menu.patches(), go)

    assert menu.open[member] is False
    assert menu.clicked(member) == ["menu"]
    assert menu.clicked(leader) == []


async def test_close_group_menu_never_raises(ui, menu):
    _, member = _group(ui)
    broken = AsyncMock(side_effect=RuntimeError("window tree unreadable"))

    await _with(
        (*menu.patches(), patch.object(nh, "is_visible_by_path", new=broken)), lambda: gm.close_group_menu(member)
    )


async def test_port_to_leader_closes_the_menu_afterwards_and_leaves_a_closed_one_alone(ui, menu):
    leader, member, other = ui.make_client(), ui.make_client(), ui.make_client()
    ui.put_in_group(leader, member, other)
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0, 1}))

    async def go():
        menu.open[member] = True  # the port left it up; `other`'s was never opened
        return await gm.port_to_leader([member, other])

    result = await _with((*menu.patches(), patch.object(nh, "port_clients", port)), go)

    assert result.seen_loading == {0, 1}
    assert port.await_args.args[1] is gm.teleport_to_leader
    assert menu.open[member] is False
    assert menu.clicked(member) == ["menu"]
    assert menu.clicked(other) == []


async def test_port_to_leader_ports_a_member_and_closes_its_menu(ui, menu):
    _, member = _group(ui)
    menu.jumps_on_teleport.add(member)

    result = await _with(menu.patches(), lambda: gm.port_to_leader([member], trigger_timeout=0.05, ceiling=1.0))

    assert result.jumped == {0} and result.missing(1) == []
    assert menu.clicked(member) == ["menu", "teleport", "menu"]
    assert menu.open[member] is False


async def test_port_to_leader_leaves_the_leader_out_and_ports_the_rest(ui, menu):
    leader, member = _group(ui)
    menu.jumps_on_teleport.add(member)

    result = await _with(menu.patches(), lambda: gm.port_to_leader([leader, member], trigger_timeout=0.05, ceiling=1.0))

    assert result.unstarted == {0} and result.jumped == {1}
    assert menu.clicked(leader) == []


async def test_port_to_leader_with_only_the_leader_raises_and_clicks_nothing(ui, menu):
    leader, _ = _group(ui)

    with pytest.raises(ValueError, match="leads"):
        await _with(menu.patches(), lambda: gm.port_to_leader([leader]))

    assert menu.clicks == []


async def test_a_refusal_after_teleport_to_leader_flows_through_port_clients(ui, menu):
    """Refused with the friends list's own dialogs, which port_clients reports the same way."""
    _, member = _group(ui)

    async def resolve_modal(client, **_kwargs):
        if "teleport" in menu.clicked(client):
            return nh.ModalOutcome(answered=True, refusal=dialog_captions.FRIEND_BUSY)
        return nh.ModalOutcome(answered=False)

    patches = (*menu.patches(), patch.object(nh, "resolve_modal", new=AsyncMock(side_effect=resolve_modal)))
    result = await _with(patches, lambda: gm.port_to_leader([member], trigger_timeout=0.05, ceiling=1.0))

    assert result.refusal == dialog_captions.FRIEND_BUSY and result.refused_index == 0
    assert menu.open[member] is False, "the menu is closed after a refusal too"

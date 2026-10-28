"""Tests for node_helpers zone matching utilities."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartleby.runtime.actions.node_helpers as nh
from bartleby.runtime.actions.node_helpers import (
    _get_window_from_path,
    find_windows_by_path,
    resolve_path_match,
    strip_html,
    window_text_matches,
)
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.paths import (
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    GO_TO_FRIEND_BUTTON_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
)
from tests.conftest import (
    FakeClient,
    FakeEntity,
    ModalScreen,
)

#: Somewhere and somewhere else — reach_sigil_prompt only ever passes these through.
_SIGIL = XYZ(0.0, 0.0, 0.0)
_RESET = XYZ(1.0, 0.0, 0.0)

# ---------------------------------------------------------------------------
# zone_matches — pure function, no async
# ---------------------------------------------------------------------------


# A dungeon and its Challenge Mode copy are one zone to a bot (#143). One case per suffix the
# game uses, since no dungeon shares another's.
_COPIES = [
    ("Aquila/AQ_Z02_Atlantea", "Aquila/AQ_Z02_Atlantea_Hard"),
    ("Marleybone/G14_Gauntlet/MB_Z06_Rooftop", "Marleybone/G14_Gauntlet/MB_Z06_Rooftop_Challenge"),
    ("Karamelle/Interiors/KM_Z10_ThroneRoom_B", "Karamelle/Interiors/KM_Z10_ThroneRoom_B_Adv"),
    ("Darkmoor/Interiors/DM_Z04I08_BogOfVengeance", "Darkmoor/Interiors/DM_Z04I08_BogOfVengeanceAdv"),
]


_PA_01_03 = "Housing_PA_Gauntlet/PA_Gauntlet_01/PA_Gauntlet_01_03"
_PA_40_03 = "Housing_PA_Gauntlet/PA_Gauntlet_40/PA_Gauntlet_40_03"


# ---------------------------------------------------------------------------
# entity_name_matches — pure function, no async
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# find_closest_entity_by_name — async
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# entity_name_predicate — the matcher both search helpers hand to WizSprinter
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# all_in_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


def _make_client(zone: str):
    c = AsyncMock()
    c.zone_name = AsyncMock(return_value=zone)
    return c


# ---------------------------------------------------------------------------
# all_left_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _get_window_from_path — traversal across ambiguously named siblings
#
# Wizard101 reuses window names between siblings: wndCharacter has two children both
# named "ButtonLayout", only one of which holds the group invite button. These tests
# pin the backtracking that makes INVITE_TO_GROUP_BUTTON_PATH resolve correctly.
# ---------------------------------------------------------------------------


def _make_window(name: str, children: list | None = None, text: str = ""):
    from unittest.mock import MagicMock

    w = MagicMock()
    w.name = AsyncMock(return_value=name)
    w.children = AsyncMock(return_value=list(children or []))
    w.maybe_text = AsyncMock(return_value=text)
    return w


async def test_get_window_backtracks_past_a_dead_end_sibling():
    """The first same-named sibling does not lead anywhere; the second does."""
    target = _make_window("btnInvite")
    root = _make_window(
        "root",
        [
            _make_window(
                "wndCharacter",
                [
                    _make_window("ButtonLayout", [_make_window("btnSomethingElse")]),
                    _make_window("ButtonLayout", [target]),
                ],
            )
        ],
    )

    found = await _get_window_from_path(root, ["wndCharacter", "ButtonLayout", "btnInvite"])
    assert found is target


async def test_get_window_returns_none_when_no_sibling_completes_the_path():
    root = _make_window(
        "root",
        [
            _make_window(
                "wndCharacter",
                [
                    _make_window("ButtonLayout", [_make_window("btnOther")]),
                    _make_window("ButtonLayout", [_make_window("btnAnother")]),
                ],
            )
        ],
    )

    assert await _get_window_from_path(root, ["wndCharacter", "ButtonLayout", "btnInvite"]) is None


# ---------------------------------------------------------------------------
# find_windows_by_path / resolve_path_match — paths that match several elements
#
# A list menu whose rows are structurally identical is ambiguous by path: every row
# completes it. The first match wins silently unless the caller narrows it by the text
# the row displays or by its position.
# ---------------------------------------------------------------------------


def _row_menu():
    """A menu of three identically-named rows, told apart only by their label text."""
    rows = [
        _make_window("Row", [_make_window("Label", text="<center>Krokotopia")]),
        _make_window("Row", [_make_window("Label", text="<center>Marleybone")]),
        _make_window("Row", [_make_window("Label", text="<center>Mooshu")]),
    ]
    return _make_window("root", [_make_window("wndMenu", rows)]), rows


def _client_with(root):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.root_window = root
    return client


async def test_find_windows_returns_every_match_in_tree_order():
    root, rows = _row_menu()
    found = await find_windows_by_path(root, ["wndMenu", "Row"])
    assert found == rows


async def test_find_windows_returns_empty_list_when_nothing_matches():
    root, _ = _row_menu()
    assert await find_windows_by_path(root, ["wndMenu", "Missing"]) == []


async def test_get_window_still_takes_the_first_of_several_matches():
    """The single-result walker is unchanged by the multi-match resolver."""
    root, rows = _row_menu()
    assert await _get_window_from_path(root, ["wndMenu", "Row"]) is rows[0]


async def test_window_text_matches_reads_a_descendants_label():
    _, rows = _row_menu()
    assert await window_text_matches(rows[1], "Marleybone")
    assert not await window_text_matches(rows[1], "Krokotopia")


async def test_window_text_matches_is_case_insensitive_and_strips_html():
    """The game wraps labels in HTML; a user typing the visible text must still match."""
    _, rows = _row_menu()
    assert await window_text_matches(rows[0], "krokoTOPIA")
    assert await window_text_matches(rows[0], "Krokotopia", exact=True)


async def test_window_text_matches_shallow_ignores_children():
    _, rows = _row_menu()
    assert not await window_text_matches(rows[0], "Krokotopia", deep=False)


async def test_window_text_exact_rejects_a_substring():
    _, rows = _row_menu()
    assert await window_text_matches(rows[2], "Moo")
    assert not await window_text_matches(rows[2], "Moo", exact=True)


async def test_resolve_path_match_defaults_to_the_first_match_with_a_count():
    root, rows = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"])
    assert window is rows[0]
    assert count == 3


async def test_resolve_path_match_selects_by_text():
    root, rows = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Marleybone")
    assert window is rows[1]
    assert count == 3


async def test_resolve_path_match_selects_by_index():
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=2)
    assert window is rows[2]


async def test_resolve_path_match_index_counts_from_the_end():
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=-1)
    assert window is rows[2]


async def test_resolve_path_match_indexes_into_the_text_filtered_matches():
    """Match Index applies after Match Text, not to the raw match list."""
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Moo", match_index=0)
    assert window is rows[2]


async def test_resolve_path_match_reports_the_count_when_text_rejects_everything():
    """The count is what the PATH matched, so the caller can say 'none of the 3 rows'."""
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Dragonspyre")
    assert window is None
    assert count == 3


async def test_resolve_path_match_returns_none_when_index_is_out_of_range():
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=7)
    assert window is None
    assert count == 3


async def test_resolve_path_match_returns_zero_count_when_path_matches_nothing():
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Missing"])
    assert window is None
    assert count == 0


def test_strip_html_removes_the_games_wrapping():
    assert strip_html("<center>Unicorn") == "Unicorn"
    assert strip_html("  plain  ") == "plain"
    assert strip_html("") == ""


# ---------------------------------------------------------------------------
# click_until_gone — close buttons need retrying
#
# A single close click is unreliable: an overlapping window can swallow it, and clicks
# sent mid-animation get dropped. Retrying until the button itself disappears turns
# "we clicked it" into "it actually closed".
# ---------------------------------------------------------------------------


class _Closeable:
    """A window that needs `clicks_needed` clicks before it disappears."""

    def __init__(self, clicks_needed: int):
        self.clicks_needed = clicks_needed
        self.clicks = 0

    @property
    def visible(self) -> bool:
        return self.clicks < self.clicks_needed


def _closeable_patches(target: _Closeable):
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    async def is_visible(_client, _path):
        return target.visible

    async def click(_client, _path):
        target.clicks += 1
        return True

    return (
        patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=is_visible)),
        patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)),
    )


async def test_click_until_gone_retries_until_the_window_closes():
    from contextlib import ExitStack

    import bartleby.runtime.actions.node_helpers as nh_mod

    window = _Closeable(clicks_needed=3)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=0)

    assert result is True
    assert window.clicks == 3


async def test_click_until_gone_does_not_click_an_already_closed_window():
    """These paths are often toggles — a stray click would reopen what we just closed."""
    from contextlib import ExitStack

    import bartleby.runtime.actions.node_helpers as nh_mod

    window = _Closeable(clicks_needed=0)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=0)

    assert result is True
    assert window.clicks == 0


async def test_click_until_gone_returns_false_when_it_never_closes():
    from contextlib import ExitStack

    import bartleby.runtime.actions.node_helpers as nh_mod

    window = _Closeable(clicks_needed=999)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=0.05, poll_interval=0.01)

    assert result is False
    assert window.clicks > 0


@pytest.mark.parametrize("sent", [True, False], ids=["clicks-sent", "button-grayed"])
async def test_click_until_gone_says_how_many_clicks_were_never_sent(sent, caplog):
    """A grayed button and a covered one time out alike; only the count tells them apart."""
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    clicks = {"count": 0}

    async def click(_client, _path):
        clicks["count"] += 1
        return sent

    async def window_is_open():
        return True

    with (
        patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)),
        caplog.at_level(logging.WARNING, logger=nh_mod.__name__),
    ):
        result = await nh_mod.click_until_gone(
            None, ["Some", "Close"], is_open=window_is_open, timeout=0.05, poll_interval=0.01
        )

    assert result is False
    unsent = 0 if sent else clicks["count"]
    assert f"({unsent} of {clicks['count']} clicks not sent" in caplog.text


async def test_click_until_gone_returns_as_soon_as_the_window_goes():
    """poll_interval bounds the next CLICK, not the next look.

    A close that took effect immediately — the common case — used to be invisible until the
    full retry interval had elapsed, which put a floor under every window close in the app.
    Group formation closes several per invite, so that was among the largest fixed costs in
    a regroup.
    """
    from contextlib import ExitStack

    import bartleby.runtime.actions.node_helpers as nh_mod

    window = _Closeable(clicks_needed=1)
    loop = asyncio.get_event_loop()
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        start = loop.time()
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=2.0)
        elapsed = loop.time() - start

    assert result is True
    assert window.clicks == 1
    assert elapsed < 1.0, f"waited out the retry interval after the window had already closed ({elapsed:.2f}s)"


async def test_press_key_until_gone_returns_as_soon_as_the_panel_goes():
    """The keyboard counterpart of the same rule — see click_until_gone above."""
    import bartleby.runtime.actions.node_helpers as nh_mod
    from bartleby.runtime.game.key_bindings import GameAction

    panel = _Closeable(clicks_needed=1)

    async def send_key(_key, _seconds=0):
        panel.clicks += 1

    client = FakeClient()
    client.send_key = AsyncMock(side_effect=send_key)

    async def is_open():
        return panel.visible

    loop = asyncio.get_event_loop()
    start = loop.time()
    result = await nh_mod.press_key_until_gone(
        client, GameAction.FRIENDS, is_open=is_open, timeout=5, poll_interval=2.0
    )
    elapsed = loop.time() - start

    assert result is True
    assert panel.clicks == 1
    assert elapsed < 1.0, f"waited out the retry interval after the panel had already closed ({elapsed:.2f}s)"


async def test_press_key_until_gone_presses_a_keycode_as_itself():
    """ESC is no player's to rebind, so it skips the key bindings and goes to send_key unchanged."""
    from wizwalker import Keycode

    import bartleby.runtime.actions.node_helpers as nh_mod

    panel = _Closeable(clicks_needed=1)

    async def send_key(_key, _seconds=0):
        panel.clicks += 1

    client = FakeClient()
    client.send_key = AsyncMock(side_effect=send_key)

    async def is_open():
        return panel.visible

    result = await nh_mod.press_key_until_gone(client, Keycode.ESC, is_open=is_open, timeout=5, poll_interval=2.0)

    assert result is True
    client.send_key.assert_awaited_once_with(Keycode.ESC, 0.1)


async def test_click_until_gone_uses_the_supplied_predicate_not_the_button():
    """A close button reports visible=True even inside a hidden window, so polling the
    button never terminates. The caller must be able to poll the window instead."""
    from contextlib import ExitStack
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    window_open = {"value": True}
    clicks = {"count": 0}

    async def button_always_visible(_client, _path):
        return True  # the trap: the button never disappears

    async def click(_client, _path):
        clicks["count"] += 1
        window_open["value"] = False
        return True

    async def window_is_open():
        return window_open["value"]

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=button_always_visible))
        )
        stack.enter_context(patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)))
        result = await nh_mod.click_until_gone(
            None, ["Some", "Close"], is_open=window_is_open, timeout=5, poll_interval=0
        )

    assert result is True
    assert clicks["count"] == 1, "should stop as soon as the WINDOW closed"


async def test_click_until_gone_without_a_predicate_polls_the_button_and_times_out():
    """Documents why the predicate exists: the default is wrong for permanently-visible
    close buttons, which is the situation in the friends list."""
    from contextlib import ExitStack
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    async def button_always_visible(_client, _path):
        return True

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=button_always_visible))
        )
        stack.enter_context(patch.object(nh_mod, "click_by_path", new=AsyncMock(return_value=True)))
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=0.05, poll_interval=0.01)

    assert result is False


async def test_click_until_gone_reads_its_default_timeout_when_called(monkeypatch):
    """A default bound at definition time would ignore a compressed WINDOW_CLOSE_TIMEOUT,
    and every test of a close that never takes would wait the full six seconds."""
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    monkeypatch.setattr(nh_mod, "WINDOW_CLOSE_TIMEOUT", 0.05)
    monkeypatch.setattr(nh_mod, "WINDOW_CLOSE_RETRY_INTERVAL", 0.01)
    loop = asyncio.get_event_loop()
    started = loop.time()
    with (
        patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh_mod, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await nh_mod.click_until_gone(None, ["Some", "Close"])

    assert result is False
    assert loop.time() - started < 1.0


class _DialogStack:
    """Message boxes, top first, read by caption; the first `dropped` clicks are lost."""

    def __init__(self, captions: list[str], *, dropped: int = 0):
        self.captions = list(captions)
        self.dropped = dropped
        self.clicks: list = []

    async def text(self, _client, path):
        return self.captions[0] if self.captions and path == MESSAGE_BOX_CAPTION_PATH else ""

    async def click(self, _client, path):
        self.clicks.append(path)
        if self.dropped:
            self.dropped -= 1
        elif self.captions:
            self.captions.pop(0)
        return True

    def patches(self):
        return (
            patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=self.text)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=self.click)),
        )


class _Spellbook:
    """DeckConfiguration, open until a click on its close button lands; the first `dropped` are lost."""

    def __init__(self, *, dropped: int = 0, open_: bool = True):
        self.dropped = dropped
        self.open = open_
        self.clicks: list = []

    async def visible(self, _client, path):
        from bartleby.runtime.paths import BACKPACK_CLOSE_BUTTON_PATH, DECK_CONFIGURATION_PATH

        if path == DECK_CONFIGURATION_PATH:
            return self.open
        return path == BACKPACK_CLOSE_BUTTON_PATH  # the trap: the button reads visible regardless

    async def click(self, _client, path):
        self.clicks.append(path)
        if self.dropped:
            self.dropped -= 1
        else:
            self.open = False
        return True


# ---------------------------------------------------------------------------
# open_friends_list and the two friends-list windows
# ---------------------------------------------------------------------------


class _HudWindow:
    """A window of the HUD: a name, its children, and whether it shows."""

    def __init__(self, name: str, *children, visible: bool = True):
        self._name, self._children, self.visible = name, list(children), visible

    async def name(self):
        return self._name

    async def children(self):
        return self._children

    async def is_visible(self):
        return self.visible

    async def is_control_grayed(self):
        return False

    async def get_windows_with_name(self, name):
        raise AssertionError(f"the whole UI was searched for {name}")


def _hud(visible_names: set[str], *, compact_exists: bool = True):
    """The HUD's friends lists and button; the windows named in `visible_names` show."""
    lists = [_HudWindow("wndFriendsList", visible="wndFriendsList" in visible_names)]
    if compact_exists:
        lists.append(_HudWindow("NewFriendsListWindow", visible="NewFriendsListWindow" in visible_names))
    hud = _HudWindow("windowHUD", _HudWindow("windowForTopRowButtons", _HudWindow("btnFriends")), *lists)
    root = _HudWindow(
        "root",
        # Windows of the same names elsewhere, which a search by name could find first
        _HudWindow("NewFriendsListWindow", visible=False),
        _HudWindow("btnFriends"),
        _HudWindow("WorldView", hud),
    )
    return root, hud


async def _open_friends_list_with(visible_names: set[str], *, compact_exists: bool = True):
    """Run open_friends_list against a fake tree; the actions it took, and the window it returned."""
    from unittest.mock import MagicMock, patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    actions: list[str] = []
    client = MagicMock()
    client.root_window, hud = _hud(visible_names, compact_exists=compact_exists)

    async def closer(_client):
        actions.append("close")
        for window in hud._children:
            window.visible = False
        return True

    async def click_window(btn):
        # The button under the HUD, not the decoy at the root
        assert btn in hud._children[0]._children
        actions.append(f"click {btn._name}")
        if not compact_exists:
            hud._children.append(_HudWindow("NewFriendsListWindow"))

    client.mouse_handler.click_window = AsyncMock(side_effect=click_window)

    with patch.object(nh_mod, "close_friends_list", new=AsyncMock(side_effect=closer)):
        window = await nh_mod.open_friends_list(client)

    return actions, window


async def test_open_friends_list_closes_the_large_list_first():
    """Visibility is judged from the compact window alone, so from the LARGE list
    this read "closed" and clicked btnFriends anyway — and what the game does with that
    click was never established. Close first, so the state is known."""
    actions, _ = await _open_friends_list_with({"wndFriendsList"})
    assert actions == ["close", "click btnFriends"]


async def test_open_friends_list_does_not_touch_a_list_that_is_already_compact():
    """The normal path must stay one read and no input: the list is reused between invites."""
    actions, window = await _open_friends_list_with({"NewFriendsListWindow"})
    assert actions == []
    assert window._name == "NewFriendsListWindow" and window.visible


async def test_open_friends_list_clicks_when_nothing_is_open():
    actions, window = await _open_friends_list_with(set())
    assert actions == ["click btnFriends"]
    assert window._name == "NewFriendsListWindow"


async def test_open_friends_list_finds_a_list_that_exists_only_once_opened():
    actions, window = await _open_friends_list_with(set(), compact_exists=False)
    assert actions == ["click btnFriends"]
    assert window._name == "NewFriendsListWindow"


@pytest.mark.parametrize(
    "visible_names, open_",
    [(set(), False), ({"NewFriendsListWindow"}, True), ({"wndFriendsList"}, True)],
)
async def test_the_friends_list_is_open_while_either_list_shows(visible_names, open_):
    from unittest.mock import MagicMock

    import bartleby.runtime.actions.node_helpers as nh_mod

    client = MagicMock()
    client.root_window, _ = _hud(visible_names)
    assert await nh_mod.is_friends_list_open(client) is open_


# ---------------------------------------------------------------------------
# The friend profile popup's buttons: the #280 diagnostic
# ---------------------------------------------------------------------------


class _Rect:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


class _Win:
    def __init__(self, name, rect=(0, 0, 1, 1), visible=True, children=()):
        self._name, self._rect, self._visible, self._children = name, rect, visible, list(children)

    async def name(self):
        return self._name

    async def children(self):
        return self._children

    async def is_visible(self):
        return self._visible

    async def scale_to_client(self):
        return _Rect(*self._rect)


class _RootHolder:
    def __init__(self, root_window):
        self.root_window = root_window


def _profile_tree():
    invite = _Win("btnInvite", (439, 133, 464, 158))
    wishlist = _Win("btnCharacterWishlistInspection", (439, 199, 464, 224), visible=False)
    layout = _Win("ButtonLayout", children=[invite, wishlist])
    other_layout = _Win("ButtonLayout")
    popup = _Win("wndCharacter", (400, 90, 500, 300), children=[other_layout, layout, _Win("lblCharacterName")])
    root = _Win("root", children=[_Win("WorldView", children=[_Win("windowHUD", children=[popup])])])
    return _RootHolder(root)


# ---------------------------------------------------------------------------
# Waiting for the profile buttons to settle (#280)
# ---------------------------------------------------------------------------


class _MovingWin(_Win):
    """A window whose rectangle is read from a list, one entry per read, the last repeated."""

    def __init__(self, name, rects):
        super().__init__(name, rects[0])
        self._rects = list(rects)

    async def scale_to_client(self):
        rect = self._rects.pop(0) if len(self._rects) > 1 else self._rects[0]
        return _Rect(*rect)


class _AlwaysMovingWin(_Win):
    async def scale_to_client(self):
        self._rect = (self._rect[0], self._rect[1] + 1, self._rect[2], self._rect[3] + 1)
        return _Rect(*self._rect)


def _profile_with(invite):
    layout = _Win("ButtonLayout", children=[invite])
    popup = _Win("wndCharacter", (431, 0, 615, 359), children=[layout])
    return _RootHolder(_Win("root", children=[_Win("WorldView", children=[_Win("windowHUD", children=[popup])])]))


_STALE = (439, 199, 464, 224)
_SETTLED = (439, 133, 464, 158)


async def _settle(client, timeout=1.0):
    with patch.object(nh, "POLL_INTERVAL", 0), patch.object(nh, "FRIEND_PROFILE_SETTLE_TIMEOUT", timeout):
        return await nh.await_settled_friend_profile(client)


async def test_the_settle_wait_outlasts_a_layout_still_moving():
    """What #280 caught: the first read had Invite two slots low, over the wishlist's spot."""
    layout, reads = await _settle(_profile_with(_MovingWin("btnInvite", [_STALE, _SETTLED])))

    assert nh.profile_button_rect(layout, "btnInvite") == _SETTLED
    assert reads == 3


async def test_a_popup_already_laid_out_settles_in_two_reads():
    layout, reads = await _settle(_profile_with(_Win("btnInvite", _SETTLED)))

    assert nh.profile_button_rect(layout, "btnInvite") == _SETTLED
    assert reads == 2


async def test_the_settle_wait_gives_up_on_a_layout_that_never_stops():
    layout, _ = await _settle(_profile_with(_AlwaysMovingWin("btnInvite", _SETTLED)), timeout=0.05)
    assert layout is None


async def test_a_missing_popup_never_counts_as_settled():
    """Two reads that both found nothing agree, and must not pass for a laid-out popup."""
    layout, _ = await _settle(_RootHolder(_Win("root")), timeout=0.05)
    assert layout is None


# ---------------------------------------------------------------------------
# identify_modal / modal_showing — caption first, buttons as fallback
# ---------------------------------------------------------------------------


def _caption_patch(text: str):
    """Patch the caption read so the client appears to be showing `text`."""
    from unittest.mock import patch

    import bartleby.runtime.actions.node_helpers as nh_mod

    return patch.object(nh_mod, "get_text_by_path", new=AsyncMock(return_value=text))


_EXIT_CAPTION = (
    "If you leave a Dungeon you will lose all your progress in the Dungeon and will "
    "have to start over at the beginning.<br>Are you sure you want to leave now?"
)


async def test_identify_modal_names_the_dialog_on_screen():
    import bartleby.runtime.actions.node_helpers as nh_mod
    from bartleby.runtime.game import dialog_captions

    with _caption_patch(_EXIT_CAPTION):
        assert await nh_mod.identify_modal(FakeClient()) == dialog_captions.INSTANCE_EXIT


async def test_identify_modal_returns_none_for_an_unknown_caption():
    import bartleby.runtime.actions.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has cataloged"):
        assert await nh_mod.identify_modal(FakeClient()) is None


async def test_modal_showing_is_true_for_an_unrecognized_dialog():
    """identify_modal() conflates 'nothing up' with 'not in the snapshot'; this separates them."""
    import bartleby.runtime.actions.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has cataloged"):
        assert await nh_mod.modal_showing(FakeClient()) is True
    with _caption_patch(""):
        assert await nh_mod.modal_showing(FakeClient()) is False


# ---------------------------------------------------------------------------
# resolve_modal — confirm questions, dismiss refusals, report the refusals
# ---------------------------------------------------------------------------


async def _resolve_with(caption: str, buttons):
    """Run resolve_modal against a stated dialog. Returns (outcome, clicked paths)."""

    import bartleby.runtime.actions.node_helpers as nh_mod
    from tests.conftest import message_box_patches as mbp

    patches, clicked = mbp(buttons)
    with patches[0], patches[1], _caption_patch(caption):
        outcome = await nh_mod.resolve_modal(FakeClient())
    return outcome, clicked


async def test_resolve_modal_confirms_a_question():
    outcome, clicked = await _resolve_with(_EXIT_CAPTION, (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert outcome.answered is True
    assert outcome.refusal is None
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_dismisses_a_refusal_and_names_it():
    """The node needs the key so it can fail with the reason a player would have read."""
    from bartleby.runtime.game import dialog_captions

    outcome, clicked = await _resolve_with("Your friend is busy right now.", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.FRIEND_BUSY
    assert clicked == [MESSAGE_BOX_RIGHT]
    assert "not accepting teleports" in dialog_captions.describe(outcome.refusal)


async def test_resolve_modal_dismisses_a_one_button_refusal_with_the_center():
    """A refusal with only an OK button puts it in the center, not the right."""
    from bartleby.runtime.game import dialog_captions

    outcome, clicked = await _resolve_with(
        "You are not allowed to switch Realms from this location", (MESSAGE_BOX_CENTER,)
    )

    assert outcome.refusal == dialog_captions.REALM_NOT_ALLOWED_HERE
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_reports_nothing_when_no_dialog_is_up():
    outcome, clicked = await _resolve_with("", ())

    assert outcome == (False, None)
    assert clicked == []


async def test_resolve_modal_treats_an_unknown_dismiss_only_dialog_as_a_refusal():
    """A dialog reworded by a patch must keep failing fast rather than start timing out.

    No center button means the game offers no way to proceed, whatever the words say.
    """
    from bartleby.runtime.game import dialog_captions

    outcome, clicked = await _resolve_with("Wording nobody has cataloged", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.UNRECOGNIZED_REFUSAL
    assert clicked == [MESSAGE_BOX_RIGHT]


async def test_resolve_modal_confirms_an_unknown_dialog_that_offers_a_way_forward():
    outcome, clicked = await _resolve_with("Wording nobody has cataloged", (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert outcome.answered is True
    assert outcome.refusal is None
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_leaves_another_handlers_dialog_alone():
    """A recognized dialog that belongs to somebody else is not clicked at all.

    "already in a group" is group_manager's to answer. Being recognized is what protects
    it: an unknown dismiss-only dialog would be treated as a refusal and dismissed, which
    is the right default but the wrong answer here.
    """
    from bartleby.runtime.game import dialog_captions

    outcome, clicked = await _resolve_with("Somebody is already in a group.", (MESSAGE_BOX_RIGHT,))

    assert dialog_captions.identify_caption("Somebody is already in a group.") == dialog_captions.ALREADY_IN_GROUP
    assert outcome == (False, None)
    assert clicked == []


# ---------------------------------------------------------------------------
# ModalAwareRetry and await_instance_prompt
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# teleport_via_friends_list: Go To Friend, once the profile has settled
# ---------------------------------------------------------------------------

_GO_STALE = (439, 166, 464, 191)  # where Friend Finder settles: what the misclick hit
_GO_SETTLED = (439, 68, 464, 92)


def _go_layout(rect):
    return (("popup", True, (431, 0, 615, 359)), ("btnGoToFriend", True, rect))


class _ProfileScreen:
    """A friend's profile popup and the box Go To Friend raises, answering what node_helpers sends.

    The profile's buttons are read from `layouts`, one per read, the last repeated. The box
    asks only after the Go To Friend clicks numbered in `box_on`: a click that lands brings
    it up, and one that misses brings nothing.
    """

    def __init__(self, layouts, *, box_on=(1,)):
        self.layouts = list(layouts)
        self.box_on = set(box_on)
        self.reads = 0
        self.go_clicks = 0
        self.opened = []
        self.events = []
        self.profile_open = False
        self.caption = None

    async def open_profile(self, _client, name):
        self.opened.append(name)
        self.events.append("open profile")
        self.profile_open = True

    async def read(self, _client):
        layout = self.layouts[min(self.reads, len(self.layouts) - 1)]
        self.reads += 1
        return layout, nh.profile_layout_text(layout)

    async def visible(self, _client, path):
        return path == CHARACTER_PROFILE_PATH and self.profile_open

    async def click(self, _client, path):
        self.events.append((path[-1], self.reads))
        if path == GO_TO_FRIEND_BUTTON_PATH:
            self.go_clicks += 1
            if self.go_clicks in self.box_on:
                self.caption = "Teleport to your friend?"
        elif path == MESSAGE_BOX_CENTER:
            self.caption = None
        elif path == CHARACTER_PROFILE_CLOSE_PATH:
            self.profile_open = False
        return True

    async def text(self, _client, path):
        return self.caption if path == MESSAGE_BOX_CAPTION_PATH else None

    def clicked(self) -> list[str]:
        return [event[0] for event in self.events if isinstance(event, tuple)]


async def _teleport(screen: _ProfileScreen, *, identify=None):
    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch.object(nh, "POLL_INTERVAL", 0))
        stack.enter_context(patch.object(nh, "FRIEND_PROFILE_SETTLE_TIMEOUT", 1.0))
        stack.enter_context(patch.object(nh, "FRIEND_TELEPORT_CONFIRM_TIMEOUT", 0.05))
        stack.enter_context(patch.object(nh, "WINDOW_CLOSE_RETRY_INTERVAL", 0))
        stack.enter_context(patch.object(nh, "open_friend_profile", new=AsyncMock(side_effect=screen.open_profile)))
        stack.enter_context(patch.object(nh, "read_friend_profile", new=AsyncMock(side_effect=screen.read)))
        stack.enter_context(patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=screen.visible)))
        stack.enter_context(patch.object(nh, "click_by_path", new=AsyncMock(side_effect=screen.click)))
        stack.enter_context(patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=screen.text)))
        if identify is not None:
            stack.enter_context(patch.object(nh, "identify_modal", new=AsyncMock(return_value=identify)))
        await nh.teleport_via_friends_list(FakeClient(), "wizardfriend")


async def test_go_to_friend_is_clicked_only_once_the_profile_buttons_settle():
    """Clicked the moment the profile showed, it landed on Friend Finder below, or on nothing."""
    screen = _ProfileScreen([_go_layout(_GO_STALE), _go_layout(_GO_SETTLED)])

    await _teleport(screen)

    go = next(event for event in screen.events if isinstance(event, tuple) and event[0] == "btnGoToFriend")
    assert go == ("btnGoToFriend", 3)  # the stale read, then the two that agreed
    assert screen.clicked() == ["btnGoToFriend", "centerButton", "btnCharacterClose"]
    assert screen.opened == ["wizardfriend"]


async def test_a_go_to_friend_that_brings_up_no_box_is_tried_again_from_a_fresh_profile():
    screen = _ProfileScreen([_go_layout(_GO_SETTLED)], box_on=(2,))

    await _teleport(screen)

    assert screen.opened == ["wizardfriend", "wizardfriend"]
    assert screen.clicked() == [
        "btnGoToFriend",
        "btnCharacterClose",
        "btnGoToFriend",
        "centerButton",
        "btnCharacterClose",
    ]


async def test_go_to_friend_that_never_brings_up_the_box_cannot_start():
    screen = _ProfileScreen([_go_layout(_GO_SETTLED)], box_on=())

    with pytest.raises(ValueError) as raised:
        await _teleport(screen)

    assert screen.go_clicks == nh.FRIEND_TELEPORT_ATTEMPTS
    assert "wizardfriend" not in str(raised.value)
    assert not screen.profile_open


async def test_a_refusal_after_go_to_friend_is_left_for_port_clients_to_report():
    screen = _ProfileScreen([_go_layout(_GO_SETTLED)])

    await _teleport(screen, identify=dialog_captions.FRIEND_BUSY)

    assert screen.clicked() == ["btnGoToFriend"]


# ---------------------------------------------------------------------------
# list_online_friends / resolve_friend_name
# ---------------------------------------------------------------------------


def _friends_list_patches(list_text, monkeypatch):
    """Stand in for the WizWalker internals list_online_friends imports lazily."""
    import sys
    import types

    import bartleby.runtime.actions.node_helpers as nh_mod

    async def _named_window(_parent, _name):
        window = types.SimpleNamespace()
        window.maybe_text = AsyncMock(return_value=list_text)
        return window

    utils = types.ModuleType("wizwalker.extensions.scripting.utils")
    utils._cycle_to_online_friends = AsyncMock()
    utils._maybe_get_named_window = _named_window
    # The real entry regex, which is what makes this exercise the actual parse.
    import re

    utils._friend_list_entry = re.compile(r"<icon>.*?</icon>(?P<name>[^<]+)")
    monkeypatch.setitem(sys.modules, "wizwalker.extensions.scripting.utils", utils)
    monkeypatch.setattr(nh_mod, "open_friends_list", AsyncMock(return_value=object()))


async def test_list_online_friends_returns_every_entry(monkeypatch):
    """The picker needs the whole list, not one match — this is the read resolve_friend_name
    was doing all along with the filter baked in."""
    import bartleby.runtime.actions.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade<icon>x</icon>Bob FrostGem", monkeypatch)

    assert await nh_mod.list_online_friends(object()) == ["Alice StormBlade", "Bob FrostGem"]


async def test_list_online_friends_raises_when_nobody_is_online(monkeypatch):
    """An empty read and an empty list are indistinguishable from the window text, so the
    caller is told rather than handed a plausible-looking empty list."""
    import bartleby.runtime.actions.node_helpers as nh_mod

    _friends_list_patches("", monkeypatch)

    with pytest.raises(ValueError, match="No friends online"):
        await nh_mod.list_online_friends(object())


async def test_resolve_friend_name_matches_case_insensitively(monkeypatch):
    """WizWalker compares names case-sensitively, so this is where the exact casing has
    to be recovered."""
    import bartleby.runtime.actions.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade<icon>x</icon>Bob FrostGem", monkeypatch)

    assert await nh_mod.resolve_friend_name(object(), "bob frostgem") == "Bob FrostGem"


async def test_resolve_friend_name_raises_when_the_name_is_absent(monkeypatch):
    import bartleby.runtime.actions.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade", monkeypatch)

    with pytest.raises(ValueError, match="Could not find friend") as raised:
        await nh_mod.resolve_friend_name(object(), "bob frostgem")
    # Callers log this message, so it must not carry the wizard name (#121).
    assert "frostgem" not in str(raised.value).casefold()


# ---------------------------------------------------------------------------
# reach_prompt / clear_stale_prompt
#
# The reset-and-retry routine every prompt-raising node shares. Driven here with
# INTERACT_PROMPT, whose checks read FakeClient.is_in_npc_range directly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# reach_sigil_prompt
#
# The half enter_dungeon shares between its normal and Team-Up tails. It lived
# twice, once per node, until the two were merged — the node tests cover
# it through each tail; these cover it directly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# poll_each_until_seen — stop_fn early exit
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# wait_for_zone_with_recovery
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# wait_for_zone_with_recovery — a straggler that is still arriving (#70)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# skip_while_loading
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _attempt_zone_recovery
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# fight_target_rank / find_closest_fight_target — which entity a fight's enemy name means
# ---------------------------------------------------------------------------


def _world(client: FakeClient, *entities: tuple[str, float]) -> dict:
    """Give *client* a world of named entities at distances, searched as WizSprinter would."""
    placed = {}
    for name, distance in entities:
        entity = FakeEntity(name)
        placed[name] = (entity, distance)

    async def closest(pred):
        matching = [(d, e) for e, d in placed.values() if await pred(e)]
        return min(matching, key=lambda m: m[0])[1] if matching else None

    client.find_closest_by_predicate.side_effect = closest
    return {name: entity for name, (entity, _d) in placed.items()}


# ---------------------------------------------------------------------------
# clear_modals — back out of whatever is up, never confirm it
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# wait_for_zone_with_recovery — a straggler at an instance prompt gets it answered (#281)
# ---------------------------------------------------------------------------

_BUSY_CAPTION = "Your friend is busy right now."


def _client_in_zone_once_answered(screen: ModalScreen, zone: str = "Dst"):
    """A client that stays in Src until every modal over it has been answered, then reads as in `zone`."""
    client = FakeClient()
    client.zone_name = AsyncMock(side_effect=lambda: "Src" if screen.modals else zone)
    return client


# ── read_zones ───────────────────────────────────────────────────────────────


# ── in_expected_zone ─────────────────────────────────────────────────────────


# ── _not_with ────────────────────────────────────────────────────────────────


# ── pick_up ──────────────────────────────────────────────────────────────────

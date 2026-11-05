"""The player's focus: which game they last gave the keyboard to, and handing it back to them.

BartleBuddy pauses a wizard the player is on without it being the Active one (epic #14, Stage
5.3), so it needs the game the player chose, which the poll alone can't give: a refit leaves
the keyboard on whichever game it embedded last, and the poll would see that game too. The
player's focus is a click on a game, a detached window brought to the front, or the keyboard
target given its keyboard.

The press on a caption is reported before the keyboard is given, which with the queues joined
takes 20 to 50 ms (Stage 5.1's probe).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QPushButton, QWidget

from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed import game_view as game_view_module
from bartleby.qt.game_embed.client_tile import ClientTile
from bartleby.qt.game_embed.win32_window import WM_PARENTNOTIFY
from bartleby.runtime.clients.tile_slots import TileSlot
from bartleby.runtime.game.tile_layouts import ONE_LARGE
from tests.qt.game_embed import test_game_view_layout as layout
from tests.qt.game_embed.views import HeldPool

GAME = 0x1234
WM_LBUTTONDOWN = 0x0201

_Sized = layout._Sized
_press = layout._press
by_rung = layout.by_rung
asking = layout.asking


class _Focusable(_Sized):
    """``_Sized`` whose game holds the keyboard once given it, until the test says otherwise."""

    def __init__(self, hwnd: int) -> None:
        super().__init__(hwnd)
        self.has_focus = False

    def focus(self) -> None:
        super().focus()
        self.has_focus = True

    def is_focused(self) -> bool:
        return self.has_focus


@pytest.fixture
def tile(qtbot, monkeypatch):
    monkeypatch.setattr(client_tile, "_refit_pool", HeldPool(held=True))
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    _Sized.sizes = {GAME: (746, 420)}
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.resize(1000, 700)
    tile.show()
    return tile


def _click_on_game(tile) -> None:
    """What Windows sends the tile for a press that reaches its game: WM_PARENTNOTIFY."""
    msg = ctypes.wintypes.MSG(message=WM_PARENTNOTIFY, wParam=WM_LBUTTONDOWN)
    tile.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))


def _pill(tile) -> QPoint:
    return tile._pill_rect.topLeft() + QPoint(4, 4)


def _body(tile) -> QPoint:
    return QPoint(tile.width() // 2, tile.height() - 20)


# -- the tile ----------------------------------------------------------------------------------


def test_a_caption_press_is_reported_before_the_keyboard_is_given(tile):
    order: list[str] = []
    tile.caption_clicked.connect(lambda _point: order.append("caption"))
    tile._window.focus = lambda: order.append("focus")

    _press(tile, _pill(tile))

    assert order == ["caption", "focus"]


def test_a_click_on_the_game_is_the_players(tile):
    heard: list[bool] = []
    tile.game_clicked.connect(lambda: heard.append(True))

    _click_on_game(tile)

    assert heard == [True]
    assert tile._window.focused == 1


def test_a_press_on_a_tile_letting_clicks_through_is_the_players(tile):
    heard: list[bool] = []
    tile.game_clicked.connect(lambda: heard.append(True))

    _press(tile, _body(tile))
    _press(tile, _pill(tile))

    assert heard == [True, True]


def test_a_swallowed_press_gives_the_player_no_game(tile):
    heard: list[bool] = []
    tile.game_clicked.connect(lambda: heard.append(True))
    tile.swallow_clicks(True)

    _press(tile, _body(tile))
    _press(tile, _pill(tile))

    assert heard == []


def test_focus_game_called_by_the_app_is_not_the_players(tile):
    heard: list[bool] = []
    tile.game_clicked.connect(lambda: heard.append(True))

    tile.focus_game()

    assert heard == []


# -- the view ----------------------------------------------------------------------------------


@pytest.fixture
def user32(monkeypatch):
    """The view's user32 for the poll and the hand-back: the app in front unless a test says not."""
    fake = SimpleNamespace(foreground=None, set_focus=[])
    fake.GetForegroundWindow = lambda: fake.foreground
    fake.SetFocus = fake.set_focus.append
    monkeypatch.setattr(game_view_module, "_user32", fake)
    return fake


@pytest.fixture
def focusing(asking, monkeypatch, user32):
    """Two hooked tiles, 100 large and 101 small, whose games hold the keyboard once given it."""
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Focusable)
    game, _timer, _asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    for hwnd, size in list(game._asked.items()):
        game.game_resized(hwnd, size)
    client_tile._refit_pool.run()
    user32.foreground = int(game.window().winId())
    heard: list[int] = []
    game.player_focus_changed.connect(heard.append)
    return game, heard


def _tile(game, hwnd: int) -> ClientTile:
    return next(tile for tile in game._tiles.values() if tile.hwnd == hwnd)


def test_a_click_on_a_game_is_the_players_focus(focusing):
    game, heard = focusing

    _click_on_game(_tile(game, 101))

    assert heard == [101]
    assert game.player_focus == 101


def test_the_keyboard_target_given_its_keyboard_is_the_players_focus(focusing):
    game, heard = focusing

    game.set_keyboard_target(101)

    assert heard == [101]


def test_the_poll_never_makes_a_game_the_players_focus(focusing):
    """It sees the game a refit left the keyboard on as well as one the player clicked."""
    game, heard = focusing
    _tile(game, 100)._window.has_focus = True

    game._poll_focus()

    assert heard == []
    assert game.focused_hwnd == 100, "the poll still says where the keyboard is"


def test_a_detached_window_brought_to_the_front_is_the_players_focus(focusing, user32):
    game, heard = focusing
    game._detach_client(1)

    user32.foreground = 101
    game._poll_focus()

    assert heard == [101]


def test_the_players_focus_holds_while_another_application_has_the_keyboard(focusing, user32):
    game, heard = focusing
    _click_on_game(_tile(game, 101))

    user32.foreground = 0xBEEF
    game._poll_focus()

    assert game.focused_hwnd is None
    assert game.player_focus == 101
    assert heard == [101]


def test_a_target_the_player_just_clicked_is_not_given_the_keyboard_again(focusing):
    game, _heard = focusing
    small = _tile(game, 101)
    small._window.focused = 0
    _click_on_game(small)

    game.set_keyboard_target(101)

    assert small._window.focused == 1, "only the click's"


def test_the_keyboard_is_given_back_to_its_target_after_a_press_took_it(focusing):
    game, heard = focusing
    game.set_keyboard_target(101)
    small = _tile(game, 101)._window
    small.has_focus = False  # A press on one of the app's own widgets.

    game.give_keyboard_back()

    assert small.focused == 2
    assert heard == [101]


def test_giving_the_keyboard_back_does_nothing_while_the_game_still_has_it(focusing):
    game, _heard = focusing
    game.set_keyboard_target(101)

    game.give_keyboard_back()

    assert _tile(game, 101)._window.focused == 1


def test_giving_the_keyboard_back_waits_for_a_refit_under_way(focusing):
    game, _heard = focusing
    game.set_keyboard_target(101)
    small = _tile(game, 101)
    small._window.has_focus = False
    client_tile._refit_pool.held = True
    small.refit(854, 480)

    game.give_keyboard_back()
    client_tile._refit_pool.run()
    assert small._window.focused == 1, "under way, and then off its target"

    game.game_resized(101, (534, 300))
    client_tile._refit_pool.run()
    assert small._window.focused == 2


def test_with_no_target_nothing_is_given_back(focusing):
    """BartleBot names none."""
    game, _heard = focusing

    game.give_keyboard_back()

    assert [_tile(game, hwnd)._window.focused for hwnd in (100, 101)] == [0, 0]


def _press_event() -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        QPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_a_press_on_a_widget_that_keeps_the_keyboard_leaves_it_with_the_game(focusing, user32, qtbot):
    game, _heard = focusing
    _tile(game, 100)._window.has_focus = True
    bar = QWidget()
    qtbot.addWidget(bar)
    button = QPushButton(bar)
    elsewhere = QPushButton()
    qtbot.addWidget(elsewhere)
    game.keep_keyboard_over(bar)

    game.eventFilter(button, _press_event())
    assert user32.set_focus == []

    game.eventFilter(elsewhere, _press_event())
    assert user32.set_focus == [int(game.window().winId())], "any other widget takes it, as before"


# -- detached windows --------------------------------------------------------------------------


def test_the_view_says_which_windows_are_detached_and_when_one_comes_back(focusing):
    game, _heard = focusing
    back: list[int] = []
    game.window_reattached.connect(back.append)

    game._detach_client(1)
    assert game.detached_hwnds == {101}

    game.reattach_client(1)
    assert game.detached_hwnds == frozenset()
    assert back == [101]


def test_a_tile_whose_window_is_not_in_game_never_swallows(by_rung):
    """It has no player to swap to, so a press can only give it the keyboard."""
    game = by_rung((1920, 971), [(746, 420), (746, 420)])
    game.sync_tiles(
        [TileSlot(slot=0, hwnd=100, label="P1", player=0), TileSlot(slot=1, hwnd=101, label="P2", player=None)]
    )
    game.set_swap_first(True)

    game.set_layout(ONE_LARGE, [True], by_rung=True)

    assert not game._widget_at(1).swallowing_clicks

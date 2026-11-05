"""A small tile's first click only swaps, and a swap moves no game at the size it is leaving.

Swap first (BartleBuddy epic #14, Stage 5.2): ``EnableWindow(hwnd, False)`` sends a press on
the game to its tile instead, and the game keeps drawing and taking what the runtime posts
(Stage 1's probe). The tile reports the press, and the view says which tiles swallow: the
Side tiles, under swap first.

Held moves: a swap places the tiles and asks for the new sizes at once, and a move at the old
size in between was a call into each game, about 30 ms of the GUI thread a swap, for a place
the refit's reembed sets anyway.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint

from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed.client_tile import ClientTile
from bartleby.qt.game_embed.win32_window import GWL_STYLE, WS_DISABLED, EmbeddedWindow, child_style
from bartleby.runtime.game.tile_layouts import ALL_EQUAL, ONE_LARGE
from tests.qt.game_embed import test_game_view_layout as layout
from tests.qt.game_embed.test_window_restore import OVERLAPPED, FakeKernel32, FakeUser32
from tests.qt.game_embed.views import HeldPool

GAME = 0x1234
TILE = 0x5678

_Sized = layout._Sized
_press = layout._press
by_rung = layout.by_rung
asking = layout.asking


@pytest.fixture(autouse=True)
def _fake_monitors(monkeypatch):
    monkeypatch.setattr("bartleby.qt.game_embed.win32_window._monitor_rects", lambda: [(0, 0, 1920, 1080)])


# -- the window ---------------------------------------------------------------------------------


@pytest.fixture
def user32() -> FakeUser32:
    return FakeUser32()


@pytest.fixture
def window(user32) -> EmbeddedWindow:
    window = EmbeddedWindow(GAME, user32=user32, kernel32=FakeKernel32())
    window.embed(TILE)
    user32.calls.clear()
    return window


def test_input_is_kept_from_the_game_and_let_through_again_with_enable_window(window, user32):
    window.set_input_enabled(False)
    assert not window.input_enabled
    window.set_input_enabled(True)

    assert [c for c in user32.calls if c[0] == "EnableWindow"] == [
        ("EnableWindow", GAME, False),
        ("EnableWindow", GAME, True),
    ]
    assert window.input_enabled


def test_a_reembed_keeps_a_game_kept_from_input_kept_from_it(window, user32):
    """The style is written whole, and a plain child style would let the clicks through again."""
    window.set_input_enabled(False)
    window.reembed(TILE, 0, 0, 534, 300)
    window.set_input_enabled(True)
    window.reembed(TILE, 0, 0, 746, 420)

    styles = [c[3] for c in user32.calls if c[0] == "SetWindowLongW" and c[2] == GWL_STYLE]
    assert styles == [child_style(OVERLAPPED) | WS_DISABLED, child_style(OVERLAPPED)]


def test_restore_lets_the_input_through_before_anything_else(window, user32):
    window.set_input_enabled(False)
    user32.calls.clear()

    window.restore()

    assert user32.calls[0] == ("EnableWindow", GAME, True)
    assert window.input_enabled


def test_restore_of_a_game_taking_input_makes_no_enable_call(window, user32):
    window.restore()

    assert "EnableWindow" not in user32.names


# -- the tile -----------------------------------------------------------------------------------


@pytest.fixture
def tile(qtbot, monkeypatch):
    """A tile over a stand-in window at 746x420, shown and painted so its pill is placed."""
    monkeypatch.setattr(client_tile, "_refit_pool", HeldPool(held=True))
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    _Sized.sizes = {GAME: (746, 420)}
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.resize(1000, 700)
    tile.show()  # Or a resize waits for the show, and no move is held or made.
    tile.grab()
    tile._window.moves.clear()
    return tile


def _signals(tile) -> dict[str, list]:
    heard: dict[str, list] = {"caption": [], "body": []}
    tile.caption_clicked.connect(lambda point: heard["caption"].append(point))
    tile.body_clicked.connect(lambda: heard["body"].append(True))
    return heard


def _body(tile) -> QPoint:
    return QPoint(tile.width() // 2, tile.height() - 20)


def test_swallowing_keeps_input_from_the_game_and_letting_through_gives_it_back(tile):
    tile.swallow_clicks(True)
    assert tile.swallowing_clicks and not tile._window.input_enabled

    tile.swallow_clicks(False)
    assert not tile.swallowing_clicks and tile._window.input_enabled


def test_swallowing_twice_makes_one_call(tile, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(tile._window, "set_input_enabled", calls.append)

    tile.swallow_clicks(True)
    tile.swallow_clicks(True)

    assert calls == [False]


def test_a_swallowed_press_on_the_body_is_body_clicked_and_gives_no_focus(tile):
    heard = _signals(tile)
    tile.swallow_clicks(True)

    _press(tile, _body(tile))

    assert heard == {"caption": [], "body": [True]}
    assert tile._window.focused == 0
    assert not tile._window.input_enabled, "nothing lands in the game"


def test_a_swallowed_press_on_the_caption_still_swaps_and_gives_no_focus(tile):
    heard = _signals(tile)
    tile.swallow_clicks(True)

    _press(tile, tile._pill_rect.topLeft() + QPoint(4, 4))

    assert len(heard["caption"]) == 1 and heard["body"] == []
    assert tile._window.focused == 0
    assert not tile._window.input_enabled


def test_letting_clicks_through_a_press_around_the_game_focuses_it_as_before(tile):
    heard = _signals(tile)

    _press(tile, _body(tile))

    assert heard == {"caption": [], "body": []}
    assert tile._window.focused == 1


def test_focus_game_lets_the_input_through_first(tile):
    """A disabled window takes no keys: the keyboard given after a swap must reach the game."""
    tile.swallow_clicks(True)

    tile.focus_game()

    assert tile._window.input_enabled and not tile.swallowing_clicks
    assert tile._window.focused == 1


def test_sync_input_puts_back_what_a_style_write_undid_and_otherwise_calls_nothing(window, user32):
    window.set_input_enabled(False)
    user32.calls.clear()
    window.sync_input()
    assert "EnableWindow" not in user32.names, "a call into the game, for nothing"

    user32.enabled = True  # What a reembed that read the flag before the change leaves.
    window.sync_input()

    assert user32.calls == [("EnableWindow", GAME, False)]


@pytest.mark.parametrize("swallowing", [True, False])
def test_a_refit_puts_back_whether_the_game_takes_input(qtbot, monkeypatch, swallowing):
    """The worker reads it before writing the style whole, and it may have changed since."""
    user32 = FakeUser32()
    monkeypatch.setattr(client_tile, "_refit_pool", HeldPool(held=True))
    monkeypatch.setattr(
        client_tile, "EmbeddedWindow", lambda hwnd: EmbeddedWindow(hwnd, user32=user32, kernel32=FakeKernel32())
    )
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.swallow_clicks(swallowing)
    tile.refit(534, 300)
    user32.enabled = swallowing  # What a change between the read and the write leaves.

    client_tile._refit_pool.run()

    assert user32.enabled is not swallowing


def test_unembedding_a_swallowing_tile_lets_the_input_through_before_the_restore(qtbot, monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(
        client_tile, "EmbeddedWindow", lambda hwnd: EmbeddedWindow(hwnd, user32=user32, kernel32=FakeKernel32())
    )
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.swallow_clicks(True)
    user32.calls.clear()

    tile.unembed()

    assert user32.calls[0] == ("EnableWindow", GAME, True)


def test_held_moves_wait_for_the_release_and_move_once_with_no_refit_coming(tile):
    tile.hold_moves()
    tile.resize(900, 650)
    tile.resize(800, 600)
    assert tile._window.moves == [] and tile.move_owed

    tile.release_moves(refit_coming=False)

    assert tile._window.moves == [(*tile._place(746, 420), 746, 420)]
    assert not tile.move_owed


def test_a_release_with_nothing_held_moves_nothing(tile):
    tile.hold_moves()
    tile.release_moves(refit_coming=False)

    assert tile._window.moves == []


def test_a_held_move_is_left_to_the_refit_which_places_the_game(tile):
    tile.hold_moves()
    tile.resize(800, 600)
    tile.release_moves(refit_coming=True)
    assert tile._window.moves == []

    tile.refit(534, 300)
    client_tile._refit_pool.run()

    assert tile._window.moves == [], "the reembed placed it"
    assert tile._window.places == [tile._place(534, 300)]
    assert not tile.move_owed and not tile._move_timer.isActive()


def test_a_held_move_is_made_after_the_grace_if_no_refit_comes(tile):
    """A resize can wait out a loading screen, and a client that can't be resized never answers."""
    tile.hold_moves()
    tile.resize(800, 600)
    tile.release_moves(refit_coming=True)
    assert tile._move_timer.isActive()

    tile._move_timer.timeout.emit()

    assert tile._window.moves == [(*tile._place(746, 420), 746, 420)]


def test_a_held_move_is_made_at_once_when_the_answer_needs_no_refit(tile):
    tile.hold_moves()
    tile.resize(800, 600)
    tile.release_moves(refit_coming=True)

    assert not tile.refit(746, 420)

    assert tile._window.moves == [(*tile._place(746, 420), 746, 420)]


# -- the view -----------------------------------------------------------------------------------


def _swallowing(game) -> list[bool]:
    return [game._widget_at(slot).swallowing_clicks for slot in sorted(game._tiles)]


def test_under_swap_first_the_small_tiles_swallow_and_the_large_one_does_not(by_rung):
    game = by_rung((3532, 2051), [(1280, 720)] * 3)
    game.set_layout(ONE_LARGE, [True, False, False], by_rung=True)
    assert _swallowing(game) == [False, False, False], "off until a product turns it on"

    game.set_swap_first(True)
    assert _swallowing(game) == [False, True, True]

    game.set_layout(ONE_LARGE, [False, True, False], by_rung=True, at_once=True)
    assert _swallowing(game) == [True, False, True]

    game.set_swap_first(False)
    assert _swallowing(game) == [False, False, False]


def test_an_equal_grid_has_no_small_tiles_to_swallow(by_rung):
    game = by_rung((3532, 2051), [(1280, 720)] * 3)
    game.set_swap_first(True)

    game.set_layout(ALL_EQUAL, [True, False, False], by_rung=True)
    assert _swallowing(game) == [False, False, False]

    game.set_layout(None)
    assert _swallowing(game) == [False, False, False]


def test_a_small_tile_s_body_press_reports_its_player(by_rung):
    game = by_rung((3532, 2051), [(1280, 720)] * 3)
    game.set_swap_first(True)
    game.set_layout(ONE_LARGE, [True, False, False], by_rung=True)
    clicked: list[int] = []
    game.body_clicked.connect(clicked.append)

    for slot in (0, 2):
        tile = game._widget_at(slot)
        tile.grab()
        _press(tile, _body(tile))

    assert clicked == [2], "the large tile acts in the game"


def test_a_tile_moved_to_another_slot_reports_the_player_it_now_has(by_rung):
    game = by_rung((3532, 2051), [(1280, 720)] * 3)
    game.set_swap_first(True)
    game.set_layout(ONE_LARGE, [True, False, False], by_rung=True)
    clicked: list[int] = []
    game.body_clicked.connect(clicked.append)
    moved = game._widget_at(1)

    game.sync_tiles(
        [layout.TileSlot(slot=i, hwnd=hwnd, label=f"P{i + 1}", player=i) for i, hwnd in enumerate((100, 102, 101))]
    )
    assert game._widget_at(2) is moved
    moved.grab()
    _press(moved, _body(moved))

    assert clicked == [2]


def test_which_tiles_swallow_is_settled_before_a_swap_asks_for_sizes(asking):
    """A refit started from the answer reads it on its worker."""
    game, _timer, _asked = asking()
    game.set_swap_first(True)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    seen: list[list[bool]] = []
    game.game_size_wanted.connect(lambda *_args: seen.append(_swallowing(game)))

    game.set_layout(ONE_LARGE, [False, True], by_rung=True, at_once=True)

    assert seen and all(state == [True, False] for state in seen)


def _shown(game):
    """Shown, or a tile's resize waits for the show, and no move is held or made."""
    game.window().show()
    return game


def test_a_swap_moves_no_game_at_the_size_it_is_leaving(asking):
    game, _timer, _asked = asking()
    _shown(game)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    for hwnd, size in list(game._asked.items()):
        game.game_resized(hwnd, size)
    windows = [game._widget_at(slot)._window for slot in (0, 1)]
    for window in windows:
        window.moves.clear()
        window.places.clear()

    game.set_layout(ONE_LARGE, [False, True], by_rung=True, at_once=True)
    assert [window.moves for window in windows] == [[], []]

    for hwnd, size in list(game._asked.items()):
        game.game_resized(hwnd, size)
    assert [window.moves for window in windows] == [[], []], "each refit placed its game"
    assert [len(window.places) for window in windows] == [1, 1]


def test_a_drag_still_moves_each_game_as_its_tile_changes(asking):
    game, _timer, _asked = asking()
    _shown(game)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    window = game._widget_at(0)._window
    window.moves.clear()

    game.resize(1700, 971)

    assert window.moves, "nothing is asked for at once, so the game follows its tile"


def test_saying_swap_first_again_places_nothing(by_rung, monkeypatch):
    """BartleBuddy says it on every placing, several times a second while a team moves."""
    game = by_rung((3532, 2051), [(1280, 720)] * 2)
    game.set_swap_first(True)
    placed: list[bool] = []
    monkeypatch.setattr(game, "_recompute_geometry", lambda: placed.append(True))

    game.set_swap_first(True)

    assert placed == []

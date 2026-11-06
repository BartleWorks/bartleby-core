"""A swap shows the ring in each resized tile, and not its game, until every game is in.

Without it a swap showed each game out of place, then white, as its refit landed: the picture
jumped 4 to 7 times in about 650 ms. Each tile whose game a swap resizes is clipped to its pill
strip, with one pixel of its game left showing, and the view paints the ring behind it; the
clips all come off together once every refit is in (BartleBuddy epic #14, Stage 7, #23).

The game windows are stand-ins, and ``client_tile._refit_pool`` is a ``HeldPool``, so a refit
is mid-flight until the test runs it.
"""

from __future__ import annotations

import math

import pytest
from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QRegion
from PyQt6.QtWidgets import QApplication, QWidget

from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed.client_tile import RING_LIMIT_MS, ClientTile, _RingBehind
from bartleby.qt.game_embed.game_view import RING_LIFT_MS
from bartleby.runtime.game.game_sizing import LABEL_RESERVE
from bartleby.runtime.game.tile_layouts import ALL_EQUAL, ONE_LARGE
from tests.qt.game_embed import test_game_view_layout as layout
from tests.qt.game_embed.views import HeldPool

GAME = 0x1234

_Sized = layout._Sized
by_rung = layout.by_rung
asking = layout.asking


@pytest.fixture
def tile(qtbot, monkeypatch):
    """A tile in a host, over a stand-in game at 746x420, whose refits wait for the test to run them."""
    pool = HeldPool(held=True)
    monkeypatch.setattr(client_tile, "_refit_pool", pool)
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    _Sized.sizes = {GAME: (746, 420)}
    # A 100% display whatever this one is; a test of another scale says so.
    monkeypatch.setattr(ClientTile, "devicePixelRatio", lambda self: 1.0)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(1400, 1000)
    host.show()
    tile = ClientTile(GAME, "P1", parent=host)
    tile.setGeometry(20, 30, 1000, 700)
    tile.show()
    tile._window.moves.clear()
    tile.pool = pool
    yield tile
    tile.unembed()


def _strip(tile) -> QRegion:
    return QRegion(0, 0, tile.width(), LABEL_RESERVE)


def _pixel(place: tuple[int, int], dpr: float = 1.0) -> QRegion:
    return QRegion(math.ceil(place[0] / dpr), math.ceil(place[1] / dpr), 1, 1)


def _deferred_deletes() -> None:
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _rings(parent) -> list[_RingBehind]:
    _deferred_deletes()
    return parent.findChildren(_RingBehind)


# -- the clip ------------------------------------------------------------------------------------


def test_with_the_ring_on_the_mask_is_the_pill_strip_and_one_pixel_of_the_game(tile):
    tile.show_ring(True)

    assert tile.ring_shown
    assert tile.mask() == _strip(tile).united(_pixel(tile._place(746, 420)))


def test_with_the_ring_off_there_is_no_mask(tile):
    tile.show_ring(True)
    tile.show_ring(False)

    assert not tile.ring_shown
    assert tile.mask().isEmpty()


def test_saying_it_again_changes_nothing(tile):
    tile.show_ring(True)
    ring, mask = tile._ring_behind, tile.mask()
    tile.show_ring(True)

    assert tile._ring_behind is ring
    assert tile.mask() == mask
    assert len(_rings(tile.parentWidget())) == 1


def test_at_125_percent_the_pixel_is_rounded_up_into_the_game(tile, monkeypatch):
    monkeypatch.setattr(ClientTile, "devicePixelRatio", lambda self: 1.25)
    tile._game_at = (127, 49)
    tile.show_ring(True)

    assert tile.mask() == _strip(tile).united(QRegion(102, 40, 1, 1))


def test_while_a_refit_is_under_way_the_mask_holds_two_pixels_and_then_the_new_one(tile):
    old = tile._place(746, 420)
    tile.show_ring(True)

    tile.refit(534, 300)
    new = tile._place(534, 300)
    assert tile.mask() == _strip(tile).united(_pixel(old)).united(_pixel(new))

    tile.pool.run()
    assert tile.mask() == _strip(tile).united(_pixel(new))


def test_a_held_move_holds_the_pixel_where_the_game_still_is(tile):
    before = tile._place(746, 420)
    tile.show_ring(True)
    tile.hold_moves()

    tile.resize(1400, 900)
    assert tile.mask() == _strip(tile).united(_pixel(before)), "the game hasn't moved"

    tile.release_moves(refit_coming=False)
    assert tile.mask() == _strip(tile).united(_pixel(tile._place(746, 420)))


def test_after_the_grace_the_move_takes_the_pixel_with_it(tile):
    tile.show_ring(True)
    tile.hold_moves()
    tile.resize(1400, 900)
    tile.release_moves(refit_coming=True)
    assert tile.mask() != _strip(tile).united(_pixel(tile._place(746, 420)))

    tile._move_timer.timeout.emit()

    assert tile.mask() == _strip(tile).united(_pixel(tile._place(746, 420)))


def test_the_ring_comes_off_by_itself_at_the_limit(tile):
    tile.show_ring(True)

    assert tile._ring_limit.interval() == RING_LIMIT_MS
    assert tile._ring_limit.isActive()
    tile._ring_limit.timeout.emit()

    assert not tile.ring_shown
    assert tile.mask().isEmpty()


def test_a_tile_with_no_game_embedded_shows_no_ring(tile):
    tile._window.is_embedded = False

    tile.show_ring(True)

    assert not tile.ring_shown
    assert tile.mask().isEmpty()


# -- the ring behind ---------------------------------------------------------------------------


def test_the_ring_behind_is_the_view_s_over_the_tile_s_body_and_turning(tile):
    tile.show_ring(True)
    ring = tile._ring_behind

    assert ring.parentWidget() is tile.parentWidget()
    assert ring.geometry() == QRect(20, 30 + LABEL_RESERVE, 1000, 700 - LABEL_RESERVE)
    assert ring.isVisibleTo(tile.parentWidget())
    assert ring.turning
    assert not ring.isWindow() and not ring.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)


def test_the_ring_behind_follows_the_tile(tile):
    tile.show_ring(True)

    tile.setGeometry(200, 100, 800, 600)

    assert tile._ring_behind.geometry() == QRect(200, 100 + LABEL_RESERVE, 800, 600 - LABEL_RESERVE)


def test_off_the_ring_behind_hides_and_stops(tile):
    tile.show_ring(True)
    tile.show_ring(False)

    assert not tile._ring_behind.isVisibleTo(tile.parentWidget())
    assert not tile._ring_behind.turning


def test_unembed_with_the_ring_on_leaves_no_mask_and_no_ring_behind(tile):
    host = tile.parentWidget()
    tile.show_ring(True)

    tile.unembed()

    assert tile.mask().isEmpty()
    assert not tile.ring_shown
    assert _rings(host) == []


# -- the pill ----------------------------------------------------------------------------------


def _pill_after_paint(tile) -> QRect:
    tile.grab()
    return tile._pill_rect


def test_with_the_ring_on_the_pill_is_in_the_strip_over_a_small_game_in_a_large_tile(tile):
    """A swap's large tile still holds its small game, centered well below the strip, until the refit."""
    assert _pill_after_paint(tile).top() > LABEL_RESERVE, "off, the pill sits above the game"

    tile.show_ring(True)

    pill = _pill_after_paint(tile)
    assert QRect(0, 0, tile.width(), LABEL_RESERVE).contains(pill)
    assert pill.center().x() == pytest.approx(tile.width() / 2, abs=1)


# -- the view ----------------------------------------------------------------------------------


def _held(asking, **kwargs):
    game, timer, asked = asking(**kwargs)
    client_tile._refit_pool.held = True
    return game, timer, asked, client_tile._refit_pool


def _shown(game) -> set[int]:
    return {tile.hwnd for tile in game._tiles.values() if tile.ring_shown}


def _answer(game, asked) -> None:
    for hwnd, size in asked:
        game.game_resized(hwnd, size)


def test_a_swap_shows_the_ring_on_exactly_the_tiles_it_asks_a_new_size_for(asking):
    game, _timer, asked, _pool = _held(asking)

    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)

    assert asked and _shown(game) == {hwnd for hwnd, _size in asked}


def test_the_rings_show_before_any_tile_changes_shape(asking, monkeypatch):
    game, _timer, _asked, _pool = _held(asking)
    before = {tile.hwnd: tile.geometry() for tile in game._tiles.values()}
    seen: list[dict] = []
    show = ClientTile.show_ring

    def watching(self, on):
        if on:
            seen.append({tile.hwnd: tile.geometry() for tile in game._tiles.values()})
        show(self, on)

    monkeypatch.setattr(ClientTile, "show_ring", watching)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)

    assert seen and all(geometry == before for geometry in seen)
    assert {tile.hwnd: tile.geometry() for tile in game._tiles.values()} != before


def test_a_swap_among_four_rings_only_the_two_that_trade(by_rung):
    game = layout._four_on_4k(by_rung)
    game.set_resizable(100 + i for i in range(4))
    client_tile._refit_pool.held = True
    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True, at_once=True)
    for hwnd, size in list(game._asked.items()):
        game.game_resized(hwnd, size)
    client_tile._refit_pool.run()
    game._lift_timer.timeout.emit()
    assert _shown(game) == set()

    asked: list = []
    game.game_size_wanted.connect(lambda hwnd, size: asked.append(hwnd))
    game.set_layout(ONE_LARGE, [False, False, True, False], by_rung=True, at_once=True)

    assert sorted(asked) == [100, 102]
    assert _shown(game) == {100, 102}


def test_a_drag_shows_no_ring(asking):
    game, timer, _asked, _pool = _held(asking)

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    game.resize(1700, 971)

    assert _shown(game) == set()


def test_a_change_that_asks_nothing_shows_no_ring(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    _answer(game, asked)
    pool.run()
    game._lift_timer.timeout.emit()
    asked.clear()

    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)

    assert asked == []
    assert _shown(game) == set()


def test_the_rings_come_off_together_after_the_last_refit_and_never_before(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    rings = _shown(game)

    _answer(game, asked)
    pool.run(1)
    assert _shown(game) == rings and not game._lift_timer.isActive(), "the other refit is still under way"
    pool.run()
    assert _shown(game) == rings, "not at once: each game draws a frame at its new size first"
    assert game._lift_timer.isActive()
    assert game._lift_timer.interval() == RING_LIFT_MS

    game._lift_timer.timeout.emit()
    assert _shown(game) == set()


def test_the_rings_wait_for_every_answer(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)

    game.game_resized(*asked[0])
    pool.run()

    assert not game._lift_timer.isActive()
    assert len(_shown(game)) == 2


def test_a_new_swap_before_the_lift_puts_it_off(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    _answer(game, asked)
    pool.run()
    assert game._lift_timer.isActive()

    game.set_layout(ONE_LARGE, [False, True], by_rung=True, at_once=True)

    assert not game._lift_timer.isActive()
    assert len(_shown(game)) == 2


def test_a_ring_whose_resize_never_comes_comes_off_by_itself(asking, qtbot, monkeypatch):
    monkeypatch.setattr(client_tile, "RING_LIMIT_MS", 30)
    game, _timer, _asked, _pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    assert len(_shown(game)) == 2

    qtbot.waitUntil(lambda: _shown(game) == set(), timeout=1000)


def test_a_tile_detached_with_its_ring_on_leaves_no_mask_or_ring_and_drops_out_of_the_wait(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    leaving = game._widget_at(1)

    game._detach_client(1)
    assert leaving.mask().isEmpty()
    staying = game._widget_at(0)
    game.game_resized(*next(item for item in asked if item[0] == staying.hwnd))
    pool.run()

    assert game._lift_timer.isActive(), "the detached tile isn't waited for"
    game._lift_timer.timeout.emit()
    assert _shown(game) == set()
    assert len(_rings(game)) == 1, "only the staying tile's, hidden"
    assert not _rings(game)[0].isVisibleTo(game)


@pytest.mark.parametrize("how", ["detach_all", "unembed_all"])
def test_detach_all_or_a_quit_with_the_rings_on_leaves_none_behind(asking, how):
    game, _timer, _asked, _pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    tiles = list(game._tiles.values())

    getattr(game, how)()

    assert all(tile.mask().isEmpty() and not tile.ring_shown for tile in tiles)
    assert _rings(game) == []
    assert not game._lift_timer.isActive()


def test_all_equal_rings_every_tile_it_resizes(asking):
    game, _timer, asked, pool = _held(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    _answer(game, asked)
    pool.run()
    game._lift_timer.timeout.emit()
    asked.clear()

    game.set_layout(ALL_EQUAL, [True, False], by_rung=True, at_once=True)

    assert asked and _shown(game) == {hwnd for hwnd, _size in asked}


def test_bartlebot_s_layout_never_asks_at_once_and_shows_no_ring(asking):
    game, timer, _asked, _pool = _held(asking)

    game.set_layout(None, by_rung=True)
    timer.fire()
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)
    timer.fire()

    assert _shown(game) == set()

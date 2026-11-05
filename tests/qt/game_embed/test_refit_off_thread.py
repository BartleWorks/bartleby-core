"""A refit runs its Win32 calls on a worker, and the tile and the view finish it on the GUI thread.

``SetParent`` waits on every game whose input queue the GUI thread is joined to, for 300 ms
or more a call, and two refits in turn on the GUI thread were a swap of about 1.4 s. On
workers, with the queues still joined, a swap is under 0.75 s (BartleBuddy epic #14, Stage 5).

``client_tile._refit_pool`` is replaced by ``HeldPool`` wherever a test needs a refit
mid-flight, and left as the real pool where the thread is the point.
"""

from __future__ import annotations

import threading
import time

import pytest

from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed.client_tile import ClientTile
from bartleby.qt.game_embed.win32_window import EmbeddedWindow
from bartleby.runtime.game.tile_layouts import ALL_EQUAL, ONE_LARGE
from tests.qt.game_embed import test_game_view_layout as layout
from tests.qt.game_embed.test_window_restore import FakeKernel32, FakeUser32
from tests.qt.game_embed.views import HeldPool

GAME = 0x1234

# The layout tests' stand-in window, and their fixtures: two clients on 1080p, hooked.
_Sized = layout._Sized
by_rung = layout.by_rung
asking = layout.asking


class _ThreadedUser32(FakeUser32):
    """``FakeUser32`` that notes the thread of each write, and answers GetParent as SetParent left it."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: dict[str, set[int]] = {}
        self.release = threading.Event()
        self.release.set()

    def _record(self, name: str, *args) -> None:
        self.threads.setdefault(name, set()).add(threading.get_ident())
        super()._record(name, *args)

    def SetParent(self, hwnd, parent):  # noqa: N802 - Win32's
        self.release.wait(5)
        self._parent = parent
        return super().SetParent(hwnd, parent)


@pytest.fixture
def real_window(qtbot, monkeypatch):
    """A tile over a real ``EmbeddedWindow`` whose user32 is the fake, on the real pool."""
    user32 = _ThreadedUser32()
    monkeypatch.setattr(
        client_tile, "EmbeddedWindow", lambda hwnd: EmbeddedWindow(hwnd, user32=user32, kernel32=FakeKernel32())
    )
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.resize(1000, 700)
    user32.calls.clear()
    user32.threads.clear()  # The embed, on the GUI thread, as it always was.
    return tile, user32


@pytest.fixture
def held(qtbot, monkeypatch):
    """A tile over a stand-in window at 746x420, whose refits wait for the test to run them."""
    pool = HeldPool(held=True)
    monkeypatch.setattr(client_tile, "_refit_pool", pool)
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    _Sized.sizes = {GAME: (746, 420)}
    tile = ClientTile(GAME, "P1")
    qtbot.addWidget(tile)
    tile.resize(1000, 700)
    tile._window.moves.clear()
    return tile, pool


# -- the tile ----------------------------------------------------------------------------------


def test_the_win32_calls_of_a_refit_run_off_the_gui_thread(real_window, qtbot):
    tile, user32 = real_window

    with qtbot.waitSignal(tile.refitted, timeout=2000):
        assert tile.refit(534, 300)

    gui = threading.get_ident()
    for name in ("SetParent", "SetWindowLongW", "SetWindowPos"):
        assert user32.threads[name] and gui not in user32.threads[name], name
    assert tile.game_size == (534, 300)
    assert not tile.refitting


def test_the_gui_thread_is_free_while_a_refit_waits_on_the_game(real_window, qtbot):
    tile, user32 = real_window
    user32.release.clear()

    began = time.perf_counter()
    tile.refit(534, 300)
    returned = time.perf_counter() - began
    assert tile.refitting
    user32.release.set()
    qtbot.waitSignal(tile.refitted, timeout=2000).wait()

    assert returned < 0.1, "refit returns without waiting on SetParent"


def test_one_refit_runs_at_a_time_and_the_latest_waiting_wins(held, qtbot):
    tile, pool = held
    done: list[tuple[int, int]] = []
    tile.refitted.connect(lambda: done.append(tile.game_size))

    tile.refit(1280, 720)
    tile.refit(534, 300)
    tile.refit(960, 540)

    assert len(pool.waiting) == 1, "the others wait for the first to finish"
    assert tile.coming_size == (960, 540)
    pool.run()
    assert tile._window.reembeds == [(1280, 720)]
    assert done == [], "not finished while another waits"
    pool.run()

    assert tile._window.reembeds == [(1280, 720), (960, 540)]
    assert done == [(960, 540)]
    assert tile.game_size == (960, 540)


def test_a_size_waiting_that_the_first_refit_reached_is_not_run_again(held):
    tile, pool = held

    tile.refit(1280, 720)
    tile.refit(1280, 720)
    pool.run()

    assert pool.waiting == []
    assert tile._window.reembeds == [(1280, 720)]


def test_the_tile_is_left_alone_mid_flight_and_placed_at_the_finish_if_it_moved(held):
    tile, pool = held
    tile.refit(534, 300)

    tile.resize(800, 600)
    assert tile._window.moves == [], "a move now would be at the old size, or before SetParent"

    pool.run()
    assert tile._window.moves == [(*tile._place(534, 300), 534, 300)]


def test_a_refit_into_a_tile_that_kept_its_size_moves_nothing_after(held):
    tile, pool = held
    tile.refit(534, 300)
    pool.run()

    assert tile._window.places == [tile._place(534, 300)], "the worker placed it"
    assert tile._window.moves == []


def test_unembed_waits_for_a_refit_under_way_and_drops_the_one_waiting(real_window, qtbot):
    tile, user32 = real_window
    user32.release.clear()
    tile.refit(534, 300)
    tile.refit(960, 540)

    threading.Timer(0.2, user32.release.set).start()
    tile.unembed()
    qtbot.wait(50)

    names = user32.names
    assert names.index("SetWindowPos") < names.index("ShowWindow"), "the refit finished before the restore"
    assert names.count("SetParent") == 2, "the refit's and the restore's, and no second refit"
    assert not tile.refitting


def test_a_refit_that_fails_on_the_worker_keeps_the_size_and_still_finishes(held, monkeypatch):
    tile, pool = held

    def fails(*_args):
        raise OSError("gone")

    monkeypatch.setattr(tile._window, "reembed", fails)
    tile.refit(534, 300)
    pool.run()

    assert tile.game_size == (746, 420)
    assert not tile.refitting


# -- the view ----------------------------------------------------------------------------------


def _two_large_and_small(asking):
    game, timer, asked = asking()
    client_tile._refit_pool.held = True
    return game, timer, asked, client_tile._refit_pool


def test_a_swap_asks_at_once_and_a_drag_still_settles(asking):
    game, timer, asked = asking()

    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    assert asked == [(100, (1280, 720)), (101, (534, 300))], "no settle on a swap"

    asked.clear()
    starts = timer.starts
    game.resize(1700, 971)
    game._recompute_geometry()
    assert asked == []
    assert timer.starts > starts, "a drag waits"


def test_game_resized_strikes_what_was_asked_at_once_and_finishes_on_refitted(asking):
    game, timer, asked, pool = _two_large_and_small(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    starts = timer.starts

    game.game_resized(101, (534, 300))
    assert 101 not in game._asked
    assert timer.starts == starts, "what reads game_size waits for the refit"

    pool.run()
    assert timer.starts == starts + 1


def test_a_settle_mid_flight_asks_for_nothing_the_refit_is_bringing(asking):
    game, timer, asked, _pool = _two_large_and_small(asking)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True, at_once=True)
    asked.clear()

    game.game_resized(100, (1280, 720))
    timer.fire()

    assert asked == []


def test_the_keyboard_goes_to_its_target_after_the_last_refit_and_only_then(asking):
    game, _timer, _asked, pool = _two_large_and_small(asking)
    game.set_layout(ONE_LARGE, [False, True], by_rung=True, at_once=True)
    game.set_keyboard_target(101)
    small, large = game._widget_at(0)._window, game._widget_at(1)._window

    game.game_resized(101, (1280, 720))
    game.game_resized(100, (534, 300))
    pool.run(1)
    assert large.focused == 0, "the other refit is still under way"
    pool.run()

    assert large.focused == 1
    assert small.focused == 0


def test_the_keyboard_waits_for_every_answer(asking):
    game, _timer, _asked, pool = _two_large_and_small(asking)
    game.set_layout(ONE_LARGE, [False, True], by_rung=True, at_once=True)
    game.set_keyboard_target(101)

    game.game_resized(101, (1280, 720))
    pool.run()

    assert game._widget_at(1)._window.focused == 0, "the small tile's answer has not come"


def test_a_new_target_with_nothing_to_move_has_the_keyboard_at_once(asking):
    game, _timer, _asked = asking()

    game.set_keyboard_target(100)

    assert game._widget_at(0)._window.focused == 1


def test_the_keyboard_is_given_once_and_not_again_on_a_report_that_moves_nothing(asking):
    game, _timer, _asked = asking()
    game.set_keyboard_target(100)
    game.set_keyboard_target(100)

    game.game_resized(100, (746, 420))

    assert game._widget_at(0)._window.focused == 1


def test_with_no_target_a_refit_moves_no_keyboard(asking):
    """BartleBot never names one."""
    game, _timer, _asked, pool = _two_large_and_small(asking)
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True, at_once=True)

    for hwnd, size in list(game._asked.items()):
        game.game_resized(hwnd, size)
    pool.run()

    assert [game._widget_at(slot)._window.focused for slot in (0, 1)] == [0, 0]

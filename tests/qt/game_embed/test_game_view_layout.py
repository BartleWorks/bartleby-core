"""What a product hands the game view beyond BartleBot's grid: a layout, captions, a caption click.

BartleBuddy's window places Main and Side tiles by ``tile_layouts``, captions each tile with
what holds its wizard, and opens a menu from a caption. BartleBot hands none of it, and
gets the equal grid it always had. The game windows are stand-ins: ``EmbeddedWindow`` is
replaced, so a ``ClientTile`` is built over a window that is never there.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QWidget

from bartleby.qt import screen_fit
from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed.game_view import GameView
from bartleby.qt.widgets.buttons.link_button import LinkButton
from bartleby.qt.widgets.cards.add_card import AddCard
from bartleby.runtime.clients.tile_slots import TileSlot
from bartleby.runtime.game import tile_layouts
from bartleby.runtime.game.game_sizing import required_game_area, tile_size
from bartleby.runtime.game.tile_layouts import ALL_EQUAL, ONE_LARGE, TileSize
from bartleby.shared import game_dpi
from tests.qt.game_embed.views import HeldPool, make_game_view, make_placeholder

GAME = (400, 300)


class _Window:
    """``EmbeddedWindow`` with no window behind it: a 400x300 game that is never moved."""

    is_embedded = True

    def __init__(self, hwnd: int) -> None:
        self.game_size = GAME

    def embed(self, parent: int) -> None:
        pass

    def move(self, *args) -> None:
        pass

    def restore(self) -> None:
        self.is_embedded = False

    def focus(self) -> None:
        pass

    def is_focused(self) -> bool:
        return False

    def is_home(self) -> bool:
        return True

    def reembed(self, parent: int, x: int, y: int, width: int, height: int) -> None:
        self.game_size = (width, height)

    input_enabled = True

    def set_input_enabled(self, enabled: bool) -> None:
        self.input_enabled = enabled

    def sync_input(self) -> None:
        pass


class _Sized(_Window):
    """``_Window`` at the size this hwnd has now, for tests where the sizes matter."""

    sizes: dict[int, tuple[int, int]] = {}

    def __init__(self, hwnd: int) -> None:
        self.game_size = self.sizes[hwnd]
        self.home = True
        self.reembeds: list[tuple[int, int]] = []
        self.places: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int, int, int]] = []
        self.focused = 0

    def is_home(self) -> bool:
        return self.home

    def move(self, x: int, y: int, width: int, height: int) -> None:
        self.moves.append((x, y, width, height))

    def focus(self) -> None:
        self.focused += 1

    def reembed(self, parent: int, x: int, y: int, width: int, height: int) -> None:
        self.game_size = (width, height)
        self.home = True
        self.reembeds.append((width, height))
        self.places.append((x, y))


class _Timer:
    """The settle timer on a clock the test drives: it fires when the test says, and counts its restarts."""

    def __init__(self, fire) -> None:
        self.fire = fire
        self.starts = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        pass

    def isActive(self) -> bool:  # noqa: N802 - QTimer's
        return False


@pytest.fixture
def view(qapp, monkeypatch):
    """A game view of four stand-in tiles, with its app-wide hooks disarmed."""
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Window)
    monkeypatch.setattr(client_tile, "_refit_pool", HeldPool())
    monkeypatch.setattr(game_dpi, "window_is_scaled", lambda hwnd: False)
    game = make_game_view()
    game._focus_timer.stop()
    qapp.removeEventFilter(game)
    game.resize(1600, 1000)
    game.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(4)])
    yield game
    game.unembed_all()
    game.deleteLater()


def _rects_of(game, count: int) -> list[tuple[int, int, int, int]]:
    return [tuple(game.tile_geometry(slot).getRect()) for slot in range(count)]


def _rects(game) -> list[tuple[int, int, int, int]]:
    return _rects_of(game, 4)


def _grid(game) -> list[tuple[int, int, int, int]]:
    """The equal grid of four in *game*: two by two, each a quarter of the view."""
    w, h = game.width() // 2, game.height() // 2
    return [(0, 0, w, h), (w, 0, w, h), (0, h, w, h), (w, h, w, h)]


def test_with_no_layout_the_tiles_are_the_equal_grid(view):
    assert _rects(view) == _grid(view)


def test_a_layout_places_main_and_side_tiles_as_tile_layouts_arranges_them(view):
    played = [False, True, False, False]
    view.set_layout(ONE_LARGE, played)

    expected = tile_layouts.arrange(ONE_LARGE, played, (view.width(), view.height()), aspect=GAME[0] / GAME[1])
    assert _rects(view) == [(r.x, r.y, r.w, r.h) for r in expected]
    # The wizard the user moves has the Main tile, the largest by far.
    assert _rects(view)[1][2] > 2 * _rects(view)[0][2]


def test_the_minimum_is_the_area_the_main_and_side_tiles_need(view):
    played = [True, False, False, False]
    view.set_layout(ONE_LARGE, played)

    sizes = tile_layouts.sizes(ONE_LARGE, played)
    assert TileSize.SIDE in sizes
    need = required_game_area(GAME, sizes, view.devicePixelRatio() or 1.0)
    assert (view.minimumWidth(), view.minimumHeight()) == need


def test_a_layout_that_leaves_no_side_tile_is_the_equal_grid(view):
    view.set_layout(ALL_EQUAL, [True] * 4)

    assert _rects(view) == _grid(view)


def test_back_to_no_layout_is_back_to_the_grid(view):
    view.set_layout(ONE_LARGE, [True, False, False, False])
    view.set_layout(None)

    assert _rects(view) == _grid(view)


def test_captions_replace_the_manager_s_labels_by_player(view):
    view.set_captions(["P1 · with P2", None, "P3 · following P2"])

    labels = [view._widget_at(slot).label for slot in range(4)]
    assert labels == ["P1 · with P2", "P2", "P3 · following P2", "P4"]


def test_a_sync_keeps_the_captions(view):
    view.set_captions(["P1 · idle"])
    view.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(4)])

    assert view._widget_at(0).label == "P1 · idle"


def test_a_press_on_a_caption_reports_the_player_and_where(view):
    clicked: list[tuple[int, QPoint]] = []
    view.caption_clicked.connect(lambda player, point: clicked.append((player, point)))
    tile = view._widget_at(2)
    tile.grab()  # The pill's rect is laid out by the paint.
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(tile._pill_rect.topLeft() + QPoint(4, 4)),
        QPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tile.mousePressEvent(press)

    assert clicked == [(2, tile.mapToGlobal(tile._pill_rect.bottomLeft()))]


def test_a_press_away_from_the_caption_reports_nothing(view):
    clicked: list = []
    view.caption_clicked.connect(lambda *args: clicked.append(args))
    tile = view._widget_at(0)
    tile.grab()
    below = QPointF(tile.width() / 2, tile.height() - 5)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        below,
        QPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tile.mousePressEvent(press)

    assert clicked == []


def _press(tile, point: QPoint) -> None:
    tile.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_a_tile_moved_to_another_slot_reports_the_player_it_now_has(view):
    """A drag on the team re-keys the tiles, and a caption that swaps must name the wizard it shows."""
    clicked: list[int] = []
    view.caption_clicked.connect(lambda player, _point: clicked.append(player))
    moved = view._widget_at(0)
    view.sync_tiles(
        [TileSlot(slot=i, hwnd=hwnd, label=f"P{i + 1}", player=i) for i, hwnd in enumerate((101, 100, 102, 103))]
    )
    assert view._widget_at(1) is moved

    moved.grab()
    _press(moved, moved._pill_rect.topRight() + QPoint(-30, 4))

    assert clicked == [1]


def test_by_default_a_pill_has_no_role_and_no_active_mark(view):
    """BartleBot hands neither, so its pill is as it was."""
    tile = view._widget_at(0)
    tile.grab()

    assert tile.pill_role is None and not tile.active_mark
    assert tile._role_rect.isEmpty()


def test_roles_and_the_active_mark_land_on_the_pills_by_player_and_survive_a_sync(view):
    view.set_pill_roles([("star_filled", "Quester"), None, ("pause", "None")])
    view.set_active_player(2)
    view.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(4)])

    tiles = [view._widget_at(slot) for slot in range(4)]
    assert [t.pill_role for t in tiles] == ["star_filled", None, "pause", None]
    assert [t.active_mark for t in tiles] == [False, False, True, False]

    view.set_active_player(None)
    assert not any(t.active_mark for t in tiles)


def test_the_role_and_the_mark_widen_the_pill_and_move_its_text_right(view):
    tile = view._widget_at(0)
    tile.grab()
    # A copy, since the tile replaces its rect on the next paint.
    plain = QRect(tile._pill_rect)

    view.set_pill_roles([("star_filled", "Quester")])
    view.set_active_player(0)
    tile.grab()

    assert tile._pill_rect.width() > plain.width()
    assert tile._pill_rect.left() < tile._role_rect.left() < tile._icon_rect.left()


def test_a_press_on_the_role_opens_the_menu_and_leaves_focus_alone(view, monkeypatch):
    roles: list[tuple[int, QPoint]] = []
    captions: list = []
    view.role_clicked.connect(lambda player, point: roles.append((player, point)))
    view.caption_clicked.connect(lambda *args: captions.append(args))
    view.set_pill_roles([None, ("star_filled", "Quester · click to change")])
    tile = view._widget_at(1)
    focused: list[bool] = []
    monkeypatch.setattr(tile, "focus_game", lambda: focused.append(True))
    tile.grab()

    _press(tile, tile._role_rect.center())

    assert roles == [(1, tile.mapToGlobal(tile._role_rect.bottomLeft()))]
    assert captions == [] and focused == []


def test_the_role_s_tooltip_is_the_one_its_product_gave(view):
    view.set_pill_roles([("star_filled", "Quester · click to change")])
    tile = view._widget_at(0)
    tile.grab()

    assert tile._icon_at(tile._role_rect.center()) == (tile._role_rect, "Quester · click to change")
    assert tile._icon_at(tile._icon_rect.center()) == (tile._icon_rect, "Detach this window")


# -- tiles by rung -------------------------------------------------------------------------


def test_by_default_there_are_no_targets_and_the_tiles_are_the_fixed_strip(view):
    played = [True, False, False, False]
    view.set_layout(ONE_LARGE, played)

    assert view.game_targets() == {}
    expected = tile_layouts.arrange(ONE_LARGE, played, (view.width(), view.height()), aspect=GAME[0] / GAME[1])
    assert _rects(view) == [(r.x, r.y, r.w, r.h) for r in expected]


def test_with_no_layout_there_are_no_targets_even_by_rung(view):
    view.set_layout(None, by_rung=True)

    assert view.game_targets() == {}
    assert _rects(view) == _grid(view)


@pytest.fixture
def by_rung(qapp, monkeypatch):
    """A game view at a 100% display whose windows launched at the sizes it is built with."""
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    monkeypatch.setattr(client_tile, "_refit_pool", HeldPool())
    monkeypatch.setattr(game_dpi, "window_is_scaled", lambda hwnd: False)
    monkeypatch.setattr(GameView, "devicePixelRatio", lambda self: 1.0)
    # A screen everything here fits on, since CI's is small; a test of a small one says so.
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((3840, 2160), 1.0))
    views, hosts = [], []

    def build(area: tuple[int, int], launches: list[tuple[int, int]]) -> GameView:
        _Sized.sizes = {100 + i: size for i, size in enumerate(launches)}
        # In a host, because Windows clamps a top-level window to the screen, and CI's is small.
        host = QWidget()
        hosts.append(host)
        game = make_game_view(host)
        game._focus_timer.stop()
        qapp.removeEventFilter(game)
        game.setMinimumSize(0, 0)
        game.resize(*area)
        game.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(len(launches))])
        views.append(game)
        return game

    yield build
    for game in views:
        game.unembed_all()
    for host in hosts:
        host.deleteLater()


def test_by_rung_each_tile_has_its_target_and_holds_it_whole(by_rung):
    """The core case: two accounts on 1080p, the sidebar hidden, launched as an equal grid."""
    game = by_rung((1920, 971), [(854, 480), (854, 480)])

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    assert game.game_targets() == {100: (1280, 720), 101: (534, 300)}
    assert (game.tile_geometry(0).width(), game.tile_geometry(0).height()) == tile_size((1280, 720))
    side_w, side_h = tile_size((534, 300))
    assert game.tile_geometry(1).width() >= side_w and game.tile_geometry(1).height() >= side_h


def test_by_rung_with_no_side_tile_every_target_is_the_launch_size(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])

    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    assert game.game_targets() == {100: (854, 480), 101: (854, 480)}
    assert _rects_of(game, 2) == [(0, 0, 960, 971), (960, 0, 960, 971)]


def test_by_rung_one_account_asks_for_nothing_new(by_rung):
    game = by_rung((1612, 971), [(1280, 720)])

    game.set_layout(ONE_LARGE, [True], by_rung=True)

    assert game.game_targets() == {100: (1280, 720)}


def test_by_rung_where_no_strip_fits_the_tiles_are_the_view_s_equal_grid(by_rung):
    """Three followers on 1080p with the sidebar shown: the same grid BartleBot's tiles take."""
    game = by_rung((1612, 971), [(746, 420)] * 4)

    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True)

    assert game.game_targets() == {100 + i: (746, 420) for i in range(4)}
    assert _rects(game) == _grid(game)


def test_by_rung_a_client_with_no_ladder_has_no_target(by_rung):
    game = by_rung((3532, 2051), [(854, 480), (1280, 1024)])

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    assert game.game_targets() == {100: (2560, 1440)}


def test_by_rung_with_no_ladder_at_all_the_tiles_are_the_fixed_strip(by_rung):
    game = by_rung((3532, 2051), [(1280, 1024), (1280, 1024)])

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    assert game.game_targets() == {}
    expected = tile_layouts.arrange(ONE_LARGE, [True, False], (3532, 2051), aspect=1280 / 1024)
    assert _rects_of(game, 2) == [(r.x, r.y, r.w, r.h) for r in expected]


def test_by_rung_the_largest_game_with_a_ladder_picks_the_family_the_tiles_are_placed_by(by_rung):
    game = by_rung((1920, 971), [(854, 480), (640, 480)])

    game.set_layout(ONE_LARGE, [False, True], by_rung=True)

    # 854x480 has the most pixels, so the tiles are 16:9's, and the 4:3 client in Main takes
    # the largest of its own family that its 1292x771 tile holds.
    assert game.game_targets() == {100: (534, 300), 101: (800, 600)}
    assert (game.tile_geometry(1).width(), game.tile_geometry(1).height()) == tile_size((1280, 720))


def test_by_rung_a_new_area_is_a_new_target(by_rung):
    game = by_rung((1920, 971), [(746, 420), (746, 420)])
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    game.resize(1612, 971)
    # A hidden widget's resize event waits for its show.
    game._recompute_geometry()

    assert game.game_targets() == {100: (960, 540), 101: (534, 300)}


def test_by_rung_the_launch_size_survives_a_window_coming_back_at_another_size(by_rung):
    """Detached and re-attached, a tile is built over the window at whatever size it is now."""
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    game._detach_client(1)
    _Sized.sizes[101] = (534, 300)
    game.reattach_client(1)

    assert game.game_targets()[101] == (854, 480)


def test_by_rung_a_slot_that_empties_forgets_its_window(by_rung):
    """A window handle can be reused, and the next window to have it launched at its own size, here 4:3."""
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    game.sync_tiles([TileSlot(slot=0, hwnd=100, label="P1", player=0)])
    _Sized.sizes[101] = (640, 480)
    game.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(2)])

    # The largest of its own family that its 960x971 tile holds.
    assert game.game_targets()[101] == (800, 600)


def _four_on_4k(by_rung):
    return by_rung((3532, 2051), [(1280, 720)] * 4)


def test_when_main_changes_hands_only_those_two_tiles_move(by_rung):
    game = _four_on_4k(by_rung)
    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True)
    before = _rects_of(game, 4)

    game.set_layout(ONE_LARGE, [False, False, True, False], by_rung=True)
    after = _rects_of(game, 4)

    assert after[2] == before[0] and after[0] == before[2]
    assert after[1] == before[1] and after[3] == before[3]


def test_all_equal_is_in_slot_order_and_one_large_comes_back_in_the_order_it_had(by_rung):
    game = _four_on_4k(by_rung)
    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True)
    game.set_layout(ONE_LARGE, [False, False, True, False], by_rung=True)
    traded = _rects_of(game, 4)

    game.set_layout(ALL_EQUAL, [False, False, True, False], by_rung=True)
    assert _rects(game) == _grid(game)

    game.set_layout(ONE_LARGE, [False, False, True, False], by_rung=True)
    assert _rects_of(game, 4) == traded


def test_a_swap_made_in_all_equal_trades_places_with_the_last_large_tile(by_rung):
    """A swap during a fight's All equal: back in One large, the new large tile and the old one trade."""
    game = _four_on_4k(by_rung)
    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True)
    before = _rects_of(game, 4)

    game.set_layout(ALL_EQUAL, [True, False, False, False], by_rung=True)
    game.set_layout(ALL_EQUAL, [False, True, False, False], by_rung=True)
    game.set_layout(ONE_LARGE, [False, True, False, False], by_rung=True)
    after = _rects_of(game, 4)

    assert after[1] == before[0] and after[0] == before[1]
    assert after[2:] == before[2:]


def test_by_rung_all_equal_grows_each_game_to_what_its_tile_holds_at_most_the_cap(by_rung):
    game = by_rung((1920, 971), [(640, 360), (640, 360)])

    game.set_layout(ALL_EQUAL, [True, False], by_rung=True)
    assert game.game_targets() == {100: (854, 480), 101: (854, 480)}

    game.set_layout(ALL_EQUAL, [True, False], by_rung=True, cap=(746, 420))
    assert game.game_targets() == {100: (746, 420), 101: (746, 420)}


def test_by_rung_the_cap_never_touches_the_large_tile(by_rung):
    game = by_rung((1920, 971), [(640, 360), (640, 360)])

    game.set_layout(ONE_LARGE, [True, False], by_rung=True, cap=(746, 420))

    assert game.game_targets() == {100: (1280, 720), 101: (534, 300)}


def test_a_sidebar_can_widen_to_600_beside_four_clients_launched_at_1600x900_on_4k_at_125(by_rung, monkeypatch):
    """The floor is the equal grid at the smallest rung, so the window's floor leaves room for it (#14)."""
    monkeypatch.setattr(GameView, "devicePixelRatio", lambda self: 1.25)
    game = by_rung((3072 - 308, 1611), [(1600, 900)] * 4)

    game.set_layout(ONE_LARGE, [True, False, False, False], by_rung=True)

    assert game.minimumWidth() <= 3072 - 600 - 8
    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((534, 300), 4, 1.25)


def test_by_rung_a_detached_window_has_no_target_and_keeps_its_tile(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    game._detach_client(1)

    assert game.game_targets() == {100: (1280, 720)}
    assert game.tile_geometry(1) is not None


def test_turning_rungs_off_goes_back_to_the_fixed_strip(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    game.set_layout(ONE_LARGE, [True, False])

    assert game.game_targets() == {}
    expected = tile_layouts.arrange(ONE_LARGE, [True, False], (game.width(), game.height()), aspect=854 / 480)
    assert _rects_of(game, 2) == [(r.x, r.y, r.w, r.h) for r in expected]


# -- asking for a size, and hearing back ------------------------------------------------------


@pytest.fixture
def asking(by_rung):
    """Two accounts launched at 746x420 on 1080p with the sidebar hidden, hooked, on a driven settle timer."""

    def build(launches=((746, 420), (746, 420)), area=(1920, 971)):
        game = by_rung(area, list(launches))
        game._settle_timer = timer = _Timer(game._ask_for_targets)
        asked: list[tuple[int, tuple[int, int]]] = []
        game.game_size_wanted.connect(lambda hwnd, size: asked.append((hwnd, size)))
        game.set_resizable(100 + i for i in range(len(launches)))
        return game, timer, asked

    return build


def test_a_target_is_asked_for_once_after_it_settles(asking):
    game, timer, asked = asking()

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    assert asked == [], "nothing until it has held"

    timer.fire()
    assert asked == [(100, (1280, 720)), (101, (534, 300))]

    timer.fire()
    assert len(asked) == 2, "what has been asked for is not asked for again"


def test_dragging_a_splitter_asks_for_nothing_until_it_stops(asking):
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    for hwnd, size in list(asked):
        game.game_resized(hwnd, size)
    asked.clear()

    starts = timer.starts
    for width in range(1900, 1600, -20):
        game.resize(width, 971)
        game._recompute_geometry()
    # Every step of the drag put it off, across a rung or not.
    assert timer.starts - starts == 15
    assert asked == []

    timer.fire()
    assert asked == [(100, (960, 540))]


def test_a_team_view_that_changes_nothing_does_not_put_a_resize_off(asking):
    """A team view comes several times a second while a follower walks, and places the tiles each time."""
    game, timer, _asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    starts = timer.starts

    for _ in range(5):
        game.set_layout(ONE_LARGE, [True, False], by_rung=True)
        game.set_resizable([100, 101])

    assert timer.starts == starts


def test_the_settle_timer_really_runs_for_the_settling_time(by_rung, qtbot):
    game = by_rung((1920, 971), [(746, 420), (746, 420)])
    game.set_resizable([100, 101])

    assert game._settle_timer.interval() == 500, "the real one, from timing.py"
    game._settle_timer.setInterval(10)
    with qtbot.waitSignal(game.game_size_wanted, timeout=2000):
        game.set_layout(ONE_LARGE, [True, False], by_rung=True)


def test_a_result_reaches_the_tile_of_that_window_and_no_other(asking):
    game, timer, _asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()

    game.game_resized(101, (534, 300))

    assert game._widget_at(1).game_size == (534, 300)
    assert game._widget_at(0).game_size == (746, 420)
    assert game._widget_at(1)._window.reembeds == [(534, 300)]


def test_a_result_at_the_size_the_tile_has_already_moves_nothing(asking):
    """The resizer reports a size it was already at, so a refit there has to be harmless."""
    game, _timer, _asked = asking()

    game.game_resized(100, (746, 420))

    assert game._widget_at(0)._window.reembeds == []


def test_a_game_taken_out_at_the_size_it_had_is_still_embedded_again(asking):
    """A failed apply reports the size it measures, with the window already out of its tile."""
    game, _timer, _asked = asking()
    window = game._widget_at(0)._window
    window.home = False

    game.game_resized(100, (746, 420))

    assert window.reembeds == [(746, 420)]
    assert window.home


def test_a_result_for_a_window_with_no_tile_is_ignored(asking):
    game, _timer, _asked = asking()

    game._detach_client(1)
    game.game_resized(101, (534, 300))
    game.game_resized(999, (534, 300))

    assert game._widget_at(0).game_size == (746, 420)


def test_a_failed_resize_is_not_asked_for_again(asking):
    """It reports the size it stayed at, and the target stands: asking again would only be refused."""
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    asked.clear()

    game.game_resized(100, (746, 420))
    timer.fire()

    assert asked == []


def test_a_new_target_while_the_last_is_under_way_is_asked_for(asking):
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    asked.clear()

    # Main changes hands before the first answer.
    game.set_layout(ONE_LARGE, [False, True], by_rung=True)
    timer.fire()

    assert asked == [(100, (534, 300)), (101, (1280, 720))]


def test_going_back_while_the_first_is_under_way_asks_for_the_size_it_still_has(asking):
    """The resizer runs the latest target, so the first one has to be replaced."""
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    asked.clear()

    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)
    timer.fire()

    assert asked == [(100, (854, 480)), (101, (854, 480))]


def test_with_no_side_tile_and_every_game_at_the_size_its_tile_holds_nothing_is_asked_for(asking):
    """One account, and two in All equal already at the largest that fits: no scan and no write."""
    game, timer, asked = asking(launches=[(854, 480), (854, 480)])
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)
    timer.fire()
    assert asked == []

    alone, timer, asked = asking(launches=[(1280, 720)], area=(1612, 971))
    alone.set_layout(ALL_EQUAL, [True], by_rung=True)
    timer.fire()
    assert asked == []


def test_nothing_is_asked_for_a_window_with_no_runtime_behind_it(asking):
    game, timer, asked = asking()
    game.set_resizable([100])

    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()

    assert asked == [(100, (1280, 720))]


def test_a_window_hooked_again_is_asked_for_afresh(asking):
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    asked.clear()

    game.set_resizable([100])
    game.set_resizable([100, 101])
    timer.fire()

    assert asked == [(101, (534, 300))]


def test_with_rungs_off_nothing_is_asked_for_and_what_was_is_dropped(asking):
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    timer.fire()
    asked.clear()

    game.set_layout(ONE_LARGE, [True, False])
    timer.fire()

    assert asked == [] and game._asked == {}


def test_a_detached_window_says_so_and_is_not_asked_for(asking):
    game, timer, asked = asking()
    left: list[int] = []
    game.window_detached.connect(left.append)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    game._detach_client(1)
    timer.fire()

    assert left == [101]
    assert asked == [(100, (1280, 720))]


def test_detaching_them_all_says_so_for_each(asking):
    game, _timer, _asked = asking()
    left: list[int] = []
    game.window_detached.connect(left.append)

    game.detach_all()

    assert sorted(left) == [100, 101]


def test_a_window_attached_again_is_asked_for_its_tile_s_size(asking):
    game, timer, asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    game._detach_client(0)
    timer.fire()
    asked.clear()

    game.reattach_client(0)
    timer.fire()

    assert asked == [(100, (1280, 720))]


def test_the_window_can_be_restored_from_maximized_with_a_grown_main(asking):
    """The floor is the equal grid at the smallest rung, so the Main a maximized window grew never holds it open."""
    game, _timer, _asked = asking()
    floor = required_game_area((534, 300), 2)
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    assert (game.minimumWidth(), game.minimumHeight()) == floor

    game.game_resized(100, (1920, 1080))
    assert (game.minimumWidth(), game.minimumHeight()) == floor

    game.resize(*floor)
    assert (game.width(), game.height()) == floor


def test_the_floor_with_no_ladder_is_still_measured_as_it_was(by_rung):
    game = by_rung((3532, 2051), [(1280, 1024), (1280, 1024)])
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)

    sizes = tile_layouts.sizes(ONE_LARGE, [True, False])
    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((1280, 1024), sizes)


def test_the_floor_with_rungs_off_follows_the_size_given_back(asking):
    game, _timer, _asked = asking()
    game.set_layout(ONE_LARGE, [True, False], by_rung=True)
    game.game_resized(100, (1280, 720))

    game.set_layout(ONE_LARGE, [True, False])
    sizes = tile_layouts.sizes(ONE_LARGE, [True, False])
    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((1280, 720), sizes)

    game.game_resized(100, (746, 420))
    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((746, 420), sizes)


# -- a game the launch fit never judged ------------------------------------------------------


@pytest.fixture
def on_1080p(monkeypatch):
    """A 1080p screen at 100%, once the view has been built."""
    return lambda: monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((1920, 1040), 1.0))


LEFT_RUNNING = ((1280, 720), (560, 420))


def test_the_floor_never_passes_the_screen_when_a_game_can_follow_its_tile_down(asking, on_1080p):
    """Clients left from an earlier session, one since set to 1280x720: their grid is 2,584 wide."""
    game, _timer, _asked = asking(launches=LEFT_RUNNING, area=(1612, 971))
    on_1080p()

    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    budget, _dpr = screen_fit.budget_for(game)
    assert game.minimumWidth() <= budget[0]
    assert game.minimumHeight() <= budget[1]
    assert game.minimumWidth() < required_game_area(LEFT_RUNNING[0], 2)[0]


def test_in_all_equal_each_game_follows_its_tile_down_and_back_up_in_its_own_family(asking, on_1080p):
    game, timer, asked = asking(launches=LEFT_RUNNING, area=(1612, 971))
    on_1080p()
    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    game.setMinimumSize(0, 0)
    game.resize(1612, 971)
    game._recompute_geometry()
    timer.fire()
    # 806 wide each: 746x420 for the 16:9 one, and 720x540, its own family's, for the 4:3.
    assert asked == [(100, (746, 420)), (101, (720, 540))]
    game.game_resized(100, (746, 420))
    game.game_resized(101, (720, 540))
    asked.clear()

    game.resize(3000, 971)
    game._recompute_geometry()
    timer.fire()
    assert asked == [(100, (1366, 768)), (101, (1024, 768))]


def test_beside_another_the_floor_is_the_equal_grid_at_the_smallest_rung(asking, on_1080p):
    game, _timer, _asked = asking()
    on_1080p()

    game.set_layout(ALL_EQUAL, [True, True], by_rung=True)

    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((534, 300), 2)


def test_a_lone_client_keeps_its_floor_whatever_the_screen(asking, on_1080p):
    game, timer, asked = asking(launches=[(2560, 1440)], area=(1612, 971))
    on_1080p()

    game.set_layout(ALL_EQUAL, [True], by_rung=True)
    timer.fire()

    assert (game.minimumWidth(), game.minimumHeight()) == required_game_area((2560, 1440), 1)
    assert asked == []


# -- the ring, while a game is out of its tile -------------------------------------------------


def test_the_tile_draws_the_ring_for_as_long_as_its_game_is_really_away(asking):
    game, _timer, _asked = asking()
    tile = game._widget_at(0)
    assert not tile.game_away

    tile._window.home = False
    tile.watch_game()
    assert tile.game_away
    assert tile._ring_timer.isActive()
    tile.grab()

    tile.refit(1280, 720)
    assert not tile.game_away
    assert not tile._ring_timer.isActive()


def test_the_ring_takes_the_place_of_the_focus_highlight(asking):
    game, _timer, _asked = asking()
    tile = game._widget_at(0)
    tile.set_focused(True)
    corner = (tile._game_rect().topLeft() + QPoint(2, 2)) * tile.devicePixelRatio()
    focused = tile.grab().toImage().pixelColor(corner)

    tile._window.home = False
    tile.watch_game()
    away = tile.grab().toImage().pixelColor(corner)

    assert away != focused, "with nothing over it, the highlight would fill the place the game left"


def test_the_game_is_left_alone_while_an_apply_has_it(asking):
    game, _timer, _asked = asking()
    tile = game._widget_at(0)
    moved: list[tuple] = []
    tile._window.move = lambda *args: moved.append(args)
    tile._window.home = False

    tile._reposition()

    assert moved == [], "it is a top-level window, and those are tile coordinates"


def test_a_tile_whose_window_was_restored_neither_watches_nor_embeds(asking):
    game, _timer, _asked = asking()
    tile = game._widget_at(0)
    tile.unembed()
    tile._window.home = False

    tile.watch_game()
    tile.refit(1280, 720)

    assert not tile.game_away
    assert tile._window.reembeds == []


# -- a launch screen that edits nothing -----------------------------------------------------


def _accounts_and_a_profile() -> None:
    settings = screen_fit.settings()
    settings.accounts = [{"username": "p1@example.com", "alias": "P1"}]
    settings.launch_profiles = [{"name": "Team", "accounts": ["p1@example.com"]}]


def test_a_launch_screen_that_edits_nothing_offers_no_verb_add_card_or_settings_link(qapp):
    _accounts_and_a_profile()
    screen = make_placeholder(editing=False)

    cards = [*screen.accounts_column.cards, *screen.profiles_column.cards]
    assert len(cards) == 2
    assert all(card._verbs() == [] for card in cards)
    assert screen.findChildren(AddCard) == []
    assert all(link.isHidden() for link in screen.findChildren(LinkButton))


def test_an_editing_launch_screen_keeps_them(qapp):
    _accounts_and_a_profile()
    screen = make_placeholder()

    assert all(card._verbs() for card in [*screen.accounts_column.cards, *screen.profiles_column.cards])
    assert len(screen.findChildren(AddCard)) == 2


def test_a_launch_screen_that_edits_nothing_still_launches(qapp, monkeypatch):
    # A screen everything fits on: a card refuses to launch on one too small for it, as CI's is.
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((3840, 2160), 1.0))
    _accounts_and_a_profile()
    screen = make_placeholder(editing=False)
    launched: list[str] = []
    screen.launch_profile_requested.connect(launched.append)

    screen.select(screen.profiles_column.cards[0])
    screen.launch_selected()

    assert launched == ["Team"]


def test_an_empty_accounts_column_says_what_the_product_hands_it(qapp):
    screen = make_placeholder(editing=False, empty_accounts_hint="Add accounts elsewhere.")
    screen.show()

    assert "Add accounts elsewhere." in [label.text() for label in screen.findChildren(type(screen._launch_note))]
    screen.close()


def test_building_a_launch_screen_that_edits_nothing_never_shows_a_parentless_widget(qapp, parentless_shows):
    _accounts_and_a_profile()
    make_placeholder(editing=False)

    assert parentless_shows == []

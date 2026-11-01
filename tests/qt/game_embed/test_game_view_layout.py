"""What a product hands the game view beyond BartleBot's grid: a layout, captions, a caption click.

BartleBuddy's window places Main and Side tiles by ``tile_layouts``, captions each tile with
what holds its wizard, and opens a menu from a caption. BartleBot hands none of it, and
gets the equal grid it always had. The game windows are stand-ins: ``EmbeddedWindow`` is
replaced, so a ``ClientTile`` is built over a window that is never there.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
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
from bartleby.runtime.game.tile_layouts import FOCUS, GRID, TileSize
from bartleby.shared import game_dpi
from tests.qt.game_embed.views import make_game_view, make_placeholder

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


class _Sized(_Window):
    """``_Window`` at the size this hwnd has now, for tests where the sizes matter."""

    sizes: dict[int, tuple[int, int]] = {}

    def __init__(self, hwnd: int) -> None:
        self.game_size = self.sizes[hwnd]


@pytest.fixture
def view(qapp, monkeypatch):
    """A game view of four stand-in tiles, with its app-wide hooks disarmed."""
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Window)
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
    view.set_layout(FOCUS, played)

    expected = tile_layouts.arrange(FOCUS, played, (view.width(), view.height()), aspect=GAME[0] / GAME[1])
    assert _rects(view) == [(r.x, r.y, r.w, r.h) for r in expected]
    # The wizard the user moves has the Main tile, the largest by far.
    assert _rects(view)[1][2] > 2 * _rects(view)[0][2]


def test_the_minimum_is_the_area_the_main_and_side_tiles_need(view):
    played = [True, False, False, False]
    view.set_layout(FOCUS, played)

    sizes = tile_layouts.sizes(FOCUS, played)
    assert TileSize.SIDE in sizes
    need = required_game_area(GAME, sizes, view.devicePixelRatio() or 1.0)
    assert (view.minimumWidth(), view.minimumHeight()) == need


def test_a_layout_that_leaves_no_side_tile_is_the_equal_grid(view):
    view.set_layout(GRID, [True] * 4)

    assert _rects(view) == _grid(view)


def test_back_to_no_layout_is_back_to_the_grid(view):
    view.set_layout(FOCUS, [True, False, False, False])
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


# -- tiles by rung -------------------------------------------------------------------------


def test_by_default_there_are_no_targets_and_the_tiles_are_the_fixed_strip(view):
    played = [True, False, False, False]
    view.set_layout(FOCUS, played)

    assert view.game_targets() == {}
    expected = tile_layouts.arrange(FOCUS, played, (view.width(), view.height()), aspect=GAME[0] / GAME[1])
    assert _rects(view) == [(r.x, r.y, r.w, r.h) for r in expected]


def test_with_no_layout_there_are_no_targets_even_by_rung(view):
    view.set_layout(None, by_rung=True)

    assert view.game_targets() == {}
    assert _rects(view) == _grid(view)


@pytest.fixture
def by_rung(qapp, monkeypatch):
    """A game view at a 100% display whose windows launched at the sizes it is built with."""
    monkeypatch.setattr(client_tile, "EmbeddedWindow", _Sized)
    monkeypatch.setattr(game_dpi, "window_is_scaled", lambda hwnd: False)
    monkeypatch.setattr(GameView, "devicePixelRatio", lambda self: 1.0)
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

    game.set_layout(FOCUS, [True, False], by_rung=True)

    assert game.game_targets() == {100: (1280, 720), 101: (534, 300)}
    assert (game.tile_geometry(0).width(), game.tile_geometry(0).height()) == tile_size((1280, 720))
    side_w, side_h = tile_size((534, 300))
    assert game.tile_geometry(1).width() >= side_w and game.tile_geometry(1).height() >= side_h


def test_by_rung_with_no_side_tile_every_target_is_the_launch_size(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])

    game.set_layout(GRID, [True, True], by_rung=True)

    assert game.game_targets() == {100: (854, 480), 101: (854, 480)}
    assert _rects_of(game, 2) == [(0, 0, 960, 971), (960, 0, 960, 971)]


def test_by_rung_one_account_asks_for_nothing_new(by_rung):
    game = by_rung((1612, 971), [(1280, 720)])

    game.set_layout(FOCUS, [True], by_rung=True)

    assert game.game_targets() == {100: (1280, 720)}


def test_by_rung_where_no_strip_fits_the_tiles_are_the_view_s_equal_grid(by_rung):
    """Three followers on 1080p with the sidebar shown: the same grid BartleBot's tiles take."""
    game = by_rung((1612, 971), [(746, 420)] * 4)

    game.set_layout(FOCUS, [True, False, False, False], by_rung=True)

    assert game.game_targets() == {100 + i: (746, 420) for i in range(4)}
    assert _rects(game) == _grid(game)


def test_by_rung_a_client_with_no_ladder_has_no_target(by_rung):
    game = by_rung((3532, 2051), [(854, 480), (1280, 1024)])

    game.set_layout(FOCUS, [True, False], by_rung=True)

    assert game.game_targets() == {100: (2560, 1440)}


def test_by_rung_with_no_ladder_at_all_the_tiles_are_the_fixed_strip(by_rung):
    game = by_rung((3532, 2051), [(1280, 1024), (1280, 1024)])

    game.set_layout(FOCUS, [True, False], by_rung=True)

    assert game.game_targets() == {}
    expected = tile_layouts.arrange(FOCUS, [True, False], (3532, 2051), aspect=1280 / 1024)
    assert _rects_of(game, 2) == [(r.x, r.y, r.w, r.h) for r in expected]


def test_by_rung_the_largest_game_with_a_ladder_picks_the_family_the_tiles_are_placed_by(by_rung):
    game = by_rung((1920, 971), [(854, 480), (640, 480)])

    game.set_layout(FOCUS, [False, True], by_rung=True)

    # 854x480 has the most pixels, so the tiles are 16:9's, and the 4:3 client in Main takes
    # the largest of its own family that its 1292x771 tile holds.
    assert game.game_targets() == {100: (534, 300), 101: (800, 600)}
    assert (game.tile_geometry(1).width(), game.tile_geometry(1).height()) == tile_size((1280, 720))


def test_by_rung_a_new_area_is_a_new_target(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(FOCUS, [True, False], by_rung=True)

    game.resize(1612, 971)
    # A hidden widget's resize event waits for its show.
    game._recompute_geometry()

    assert game.game_targets() == {100: (960, 540), 101: (534, 300)}


def test_by_rung_the_launch_size_survives_a_window_coming_back_at_another_size(by_rung):
    """Detached and re-attached, a tile is built over the window at whatever size it is now."""
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(GRID, [True, True], by_rung=True)

    game._detach_client(1)
    _Sized.sizes[101] = (534, 300)
    game.reattach_client(1)

    assert game.game_targets()[101] == (854, 480)


def test_by_rung_a_slot_that_empties_forgets_its_window(by_rung):
    """A window handle can be reused, and the next window to have it launched at its own size."""
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(GRID, [True, True], by_rung=True)

    game.sync_tiles([TileSlot(slot=0, hwnd=100, label="P1", player=0)])
    _Sized.sizes[101] = (640, 360)
    game.sync_tiles([TileSlot(slot=i, hwnd=100 + i, label=f"P{i + 1}", player=i) for i in range(2)])

    assert game.game_targets()[101] == (640, 360)


def test_by_rung_a_detached_window_has_no_target_and_keeps_its_tile(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(FOCUS, [True, False], by_rung=True)

    game._detach_client(1)

    assert game.game_targets() == {100: (1280, 720)}
    assert game.tile_geometry(1) is not None


def test_turning_rungs_off_goes_back_to_the_fixed_strip(by_rung):
    game = by_rung((1920, 971), [(854, 480), (854, 480)])
    game.set_layout(FOCUS, [True, False], by_rung=True)

    game.set_layout(FOCUS, [True, False])

    assert game.game_targets() == {}
    expected = tile_layouts.arrange(FOCUS, [True, False], (game.width(), game.height()), aspect=854 / 480)
    assert _rects_of(game, 2) == [(r.x, r.y, r.w, r.h) for r in expected]


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

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

from bartleby.qt import screen_fit
from bartleby.qt.game_embed import client_tile
from bartleby.qt.widgets.buttons.link_button import LinkButton
from bartleby.qt.widgets.cards.add_card import AddCard
from bartleby.runtime.clients.tile_slots import TileSlot
from bartleby.runtime.game import tile_layouts
from bartleby.runtime.game.game_sizing import required_game_area
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


def _rects(game) -> list[tuple[int, int, int, int]]:
    return [tuple(game.tile_geometry(slot).getRect()) for slot in range(4)]


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

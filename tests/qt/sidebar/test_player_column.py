"""The Players tab's column, on views: the drag, the buckets, the re-keying and the lock.

Moved from BartleBot's ``tests/gui/sidebar/test_player_card_widgets.py`` with the column
(Stage 8 of BartleBot #167), where they drove it through clients. The sharp edges are the
same:

- the client manager's ``clients`` is hooked-only and contiguous, so a drag must not be
  able to land an active card among the unhooked ones. That is enforced by *which
  container holds what*, and by an unhooked card having no grip to press.
- a rebuild must not reset what a card holds, because with a drag it is one gesture away.

The drag is driven by ``bartleby.qt.testing.drag_helpers``: real mouse events on the grip,
against real geometry, with the settle waited out.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QScrollArea

from bartleby.qt.sidebar.player_card import PlayerCard, PlayerVerb
from bartleby.qt.sidebar.player_column import PlayerColumn
from bartleby.qt.testing.drag_helpers import drag_card, menu_move, row_height, show
from bartleby.runtime.views import PlayerRow, PlayerView

INSPECT = PlayerVerb("inspect", "Inspect", "search", "Inspect this player")


def _hooked(*hwnds: int) -> list[PlayerView]:
    return [PlayerView(hwnd=hwnd, slot=i + 1) for i, hwnd in enumerate(hwnds)]


def _unhooked(*hwnds: int) -> list[PlayerView]:
    return [PlayerView(hwnd=hwnd, slot=i + 1, unhooked=True) for i, hwnd in enumerate(hwnds)]


def _column(qapp, active=(1, 2, 3), unhooked=(), **kwargs) -> PlayerColumn:
    column = PlayerColumn(**kwargs)
    column.set_players(_hooked(*active), _unhooked(*unhooked))
    show(qapp, column, height=700)
    return column


def _step(column: PlayerColumn) -> int:
    return row_height(column.column.cards, column.column.scroll_area.widget().layout().spacing())


# ---------------------------------------------------------------------------
# Reordering
# ---------------------------------------------------------------------------


def test_a_drag_reports_the_windows_in_their_new_order(qapp):
    column = _column(qapp)
    seen: list[list[int]] = []
    column.order_changed.connect(seen.append)

    drag_card(qapp, column.column.cards[0], _step(column))

    assert seen == [[2, 1, 3]]


def test_a_move_by_keyboard_reports_the_same_way(qapp):
    """Every drag-reordered list takes the keyboard too: Move down from the card's menu."""
    column = _column(qapp)
    seen: list[list[int]] = []
    column.order_changed.connect(seen.append)

    assert menu_move(column.column.cards[1], "Move down")

    assert seen == [[1, 3, 2]]


def test_the_cards_are_renumbered_rather_than_rebuilt_after_a_reorder(qapp):
    column = _column(qapp, active=(1, 2))
    first, second = column.column.cards

    column.set_players(_hooked(2, 1), [])

    assert column.cards == [second, first], "both cards survived the rebuild"
    assert (second.index, first.index) == (0, 1)
    assert (second.pill_text, first.pill_text) == ("P1", "P2")


# ---------------------------------------------------------------------------
# Hooked and unhooked are two containers
# ---------------------------------------------------------------------------


def test_only_the_hooked_cards_are_in_the_column(qapp):
    """The column clamps a drop to the card list it holds, so it must hold only these."""
    column = _column(qapp, active=(1, 2), unhooked=(8, 9))

    assert len(column.column.cards) == 2
    assert all(not c.is_unhooked for c in column.column.cards)
    assert [c.hwnd for c in column.cards] == [1, 2, 8, 9]


def test_an_unhooked_card_has_no_grip_and_cannot_be_dragged(qapp):
    column = _column(qapp, active=(1, 2), unhooked=(9,))
    unhooked = column.card_for(9)

    assert unhooked.grip is None


def test_the_unhooked_section_appears_only_when_there_is_something_in_it(qapp):
    column = _column(qapp, active=(1, 2))
    assert not column.unhooked_section.isVisible()

    column.set_players(_hooked(1), _unhooked(9))
    qapp.processEvents()
    assert column.unhooked_section.isVisible()

    column.set_players(_hooked(1), [])
    qapp.processEvents()
    assert not column.unhooked_section.isVisible()


def test_a_card_that_changes_bucket_is_rebuilt(qapp):
    """Whether a card has a grip is fixed at construction, so a bucket change is a new card."""
    column = _column(qapp, active=(1,))
    before = column.cards[0]

    column.set_players([], _unhooked(1))

    after = column.cards[0]
    assert after is not before
    assert after.is_unhooked and after.grip is None


def test_a_card_whose_verbs_change_is_rebuilt(qapp):
    """A face button is built once, so a view naming other verbs needs a new card."""
    column = _column(qapp, active=(1,), verbs={INSPECT.id: INSPECT})
    before = column.cards[0]

    column.set_players([PlayerView(hwnd=1, slot=1, verbs=("inspect",))], [])

    assert column.cards[0] is not before
    assert column.cards[0].action_button("Inspect") is not None


def test_a_refresh_draws_each_view_on_its_card_and_builds_nothing(qapp):
    column = _column(qapp, active=(1, 2))
    cards = column.cards

    column.show_views([PlayerView(hwnd=2, slot=2, zone="Colossus Boulevard"), PlayerView(hwnd=5, slot=3)])

    assert column.cards == cards
    assert column.card_for(2).rows["Zone"].display_text == "Colossus Boulevard"
    assert column.card_for(5) is None, "a window with no card is ignored"


def test_a_rebuild_keeps_what_a_card_drew(qapp):
    column = _column(qapp, active=(1,))
    column.show_views([PlayerView(hwnd=1, slot=1, rows=(PlayerRow("Holder", "idle"),))])
    card = column.cards[0]

    column.set_players([card.view], [])

    assert column.cards[0] is card
    assert card.rows["Holder"].display_text == "idle"


# ---------------------------------------------------------------------------
# The lock, the verbs, the bulk row
# ---------------------------------------------------------------------------


def test_a_lock_takes_unhook_the_grip_and_unhook_all_and_leaves_rehook_alone(qapp):
    column = _column(qapp, active=(1, 2), unhooked=(9,))
    seen: list[list[int]] = []
    column.order_changed.connect(seen.append)
    hooked, unhooked = column.card_for(1), column.card_for(9)

    column.set_locked(True, "Busy")

    assert not hooked.action_button("Unhook").isEnabled()
    assert not hooked.grip.isEnabled() and "Busy" in hooked.grip.toolTip()
    assert unhooked.action_button("Rehook").isEnabled()
    assert not column._unhook_all_btn.isEnabled()
    assert column._rehook_all_btn.isEnabled()
    drag_card(qapp, hooked, _step(column))
    assert seen == [], "a locked grip must not start a drag"

    column.set_locked(False)
    assert hooked.action_button("Unhook").isEnabled()
    assert column._unhook_all_btn.isEnabled()


def test_a_card_that_arrives_while_locked_is_locked_too(qapp):
    """A client hooked while locked gets a fresh card, and it must not arrive draggable."""
    column = _column(qapp, active=(1,))

    column.set_locked(True, "Busy")
    column.set_players(_hooked(1, 2), [])

    assert [card.grip.isEnabled() for card in column.column.cards] == [False, False]


def test_a_card_s_requests_reach_the_column(qapp):
    column = _column(qapp, active=(1, 2), unhooked=(9,), verbs={INSPECT.id: INSPECT})
    column.set_players([PlayerView(hwnd=1, slot=1, verbs=("inspect",)), PlayerView(hwnd=2, slot=2)], _unhooked(9))
    asked: list[tuple] = []
    column.unhook_requested.connect(lambda i: asked.append(("unhook", i)))
    column.rehook_requested.connect(lambda h: asked.append(("rehook", h)))
    column.verb_triggered.connect(lambda h, v: asked.append((v, h)))

    column.card_for(2).action_button("Unhook").click()
    column.card_for(9).action_button("Rehook").click()
    column.card_for(1).action_button("Inspect").click()

    assert asked == [("unhook", 1), ("rehook", 9), ("inspect", 1)]


def test_a_product_s_card_factory_builds_every_card(qapp):
    class Mine(PlayerCard):
        pass

    column = _column(qapp, active=(1,), unhooked=(9,), card_factory=lambda view: Mine(view))

    assert all(isinstance(card, Mine) for card in column.cards)


def test_the_column_draws_no_heading_and_has_one_scroll_area(qapp):
    """The tab label already says "Players"; there is nothing to add to a list of clients."""
    column = _column(qapp, active=(1,))

    assert column.column.add_card is None
    assert len(column.findChildren(QScrollArea)) == 1


def test_building_the_column_never_shows_a_parentless_widget(qapp, parentless_shows):
    column = PlayerColumn()
    column.set_players(_hooked(1, 2), _unhooked(9))

    assert parentless_shows == []

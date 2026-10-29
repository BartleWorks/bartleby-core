"""The Players tab's contents: a drag-reorderable column of player cards, and the unhooked rest.

Moved from BartleBot's ``sidebar/sections/client_section.py`` (Stage 8 of BartleBot
#167). What stayed there is the refresh loop that reads the clients and builds the
views, since the client is there; this is the widget that draws them. It is handed
:class:`~bartleby.runtime.views.PlayerView`\\ s and emits signals, and holds no client.

TWO CONTAINERS, NOT ONE COLUMN
------------------------------

The client manager's ``clients`` is hooked-only and **contiguous** -- a hole in it is not
expressible -- so a drag must never land an active card in among the unhooked ones.
``CardColumn._index_at`` clamps to the whole card list it holds, which means a single
column holding both kinds *will* allow exactly that drop. So the column is handed the
active cards and nothing else, and the unhooked ones go into a ``SidebarSection`` beneath
it that is shown only when there is something in it. The unhooked cards are built with no
grip besides, so there is no gesture to make there in the first place.

The column is built with no title and no Add card. There is nothing to add to a list of
running game clients, and the tab's own label already says "Players" -- a bold heading
under it would be the panel saying the word twice. Both are one argument each to
``CardColumn``; the empty-title case is the small change the Players tab card rework asked to be checked for.

The Unhook All / Rehook All row stays where it was, above both. It acts on both buckets
at once, so putting half of it in each section header would split one control into two
that each look like they act on the section they sit in.

WHY THE CARDS ARE RE-KEYED
--------------------------

Rebuilding every card on each change of the client list reset what a card keeps
(BartleBot's freecam toggle) and blanked every row until the next view. With ▲/▼ buttons
nobody noticed -- a swap is rare and a blank row for a hundred milliseconds reads as a
redraw. With a drag it is one gesture away and visible, and the drag is what the Players
tab card rework was *for*. So a card is held against its ``hwnd`` and re-used while that
window is still in the same bucket, re-numbered rather than rebuilt
(``PlayerCard.set_index``). A card whose client crossed between hooked and unhooked is
rebuilt, because which bucket it is in decides whether it has a grip at all, and that is
fixed at construction; so is one whose view names other product verbs.

Survivors are re-parented off the column's layout *before* ``set_cards`` runs, because
that method ``deleteLater``s everything it finds there.

A PRODUCT'S OWN CARD
--------------------

*card_factory* builds each card from its first view. Left out, it is core's
``PlayerCard`` with the product's *verbs*; a product that adds to the card passes a
factory building its subclass, as BartleBot does for its freecam pair and BartleBuddy for
its control button.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartleby.qt.game_embed.card_columns import CardColumn
from bartleby.qt.sidebar.player_card import PlayerCard, PlayerVerb
from bartleby.qt.sidebar.sidebar_section import SidebarSection
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.runtime.views import PlayerView
from bartleby.shared.product import current

NO_PLAYERS_HINT = "No players hooked — launch a game client to see it here"


class PlayerColumn(QWidget):
    """The Players tab's contents: the hooked column, the unhooked section, the bulk row.

    Lives in a product's Players tab, which supplies the margins and nothing else -- the
    scrolling is the column's own. This widget draws no header of its own since the tab
    label already names it.

    No ``ThemedMixin``: the only thing it ever re-derived from ``THEME`` was the two
    glyphs on the bulk row, and those are the buttons' own to re-derive now.
    """

    #: The hooked windows in the order a drag, or a move by keyboard, left them in. The
    #: windows rather than a permutation, since this widget only knows the cards by them;
    #: a product holding the list it built the views from turns it into the permutation its
    #: client manager's ``reorder_clients`` takes.
    order_changed = pyqtSignal(list)
    unhook_requested = pyqtSignal(int)  # index of active client to unhook
    rehook_requested = pyqtSignal(int)  # HWND of unhooked client to rehook
    unhook_all_requested = pyqtSignal()
    rehook_all_requested = pyqtSignal()
    verb_triggered = pyqtSignal(int, str)  # HWND, the product verb's id

    _BULK_ICON_SIZE = QSize(14, 14)

    def __init__(
        self,
        verbs: Mapping[str, PlayerVerb] | None = None,
        *,
        card_factory: Callable[[PlayerView], PlayerCard] | None = None,
        empty_hint: str = NO_PLAYERS_HINT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._verbs = dict(verbs or {})
        self._card_factory = card_factory or (lambda view: PlayerCard(view, self._verbs))
        self._cards: list[PlayerCard] = []
        self._locked = False
        self._lock_reason = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._build_bulk_row())

        self._column = CardColumn("", empty_hint, add_text="", show_settings_link=False)
        self._column.reordered.connect(self._on_reordered)
        layout.addWidget(self._column, 1)

        self._unhooked_section = SidebarSection("Unhooked")
        self._unhooked_layout = QVBoxLayout()
        self._unhooked_layout.setSpacing(4)
        self._unhooked_section.set_content_layout(self._unhooked_layout)
        self._unhooked_section.setVisible(False)
        layout.addWidget(self._unhooked_section)

    def _build_bulk_row(self) -> QWidget:
        """Row of Unhook All / Rehook All buttons pinned above both buckets."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Two halves of the same bulk verb, so neither outranks the other. The glyphs are
        # the button's own now — ``ActionButton`` re-derives them on a theme pass, which is
        # what the hand-written ``apply_theme_styles`` here used to be for.
        self._unhook_all_btn = ActionButton("unhook", "Unhook All", icon_size=self._BULK_ICON_SIZE.width())
        self._unhook_all_btn.setToolTip("Unhook every client (leaves all game windows open)")
        self._unhook_all_btn.setEnabled(False)
        self._unhook_all_btn.clicked.connect(self.unhook_all_requested)
        layout.addWidget(self._unhook_all_btn)

        self._rehook_all_btn = ActionButton("hook", "Rehook All", icon_size=self._BULK_ICON_SIZE.width())
        self._rehook_all_btn.setToolTip(f"Re-hook every unhooked client into {current().name}")
        self._rehook_all_btn.setEnabled(False)
        self._rehook_all_btn.clicked.connect(self.rehook_all_requested)
        layout.addWidget(self._rehook_all_btn)

        return row

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_players(self, active: list[PlayerView], unhooked: list[PlayerView]) -> None:
        """Rebuild the two buckets, re-using the card for any window still in the same one.

        Each card then draws its view. For a change of the client list; a view that only
        refreshes what a card says goes to :meth:`show_views`, which builds nothing.
        """
        survivors = {(c.hwnd, c.is_unhooked): c for c in self._cards}
        active_cards = [self._card_for(survivors, view) for view in active]
        unhooked_cards = [self._card_for(survivors, view) for view in unhooked]
        keep = set(map(id, active_cards + unhooked_cards))

        # Off the layouts before either container tears its rows down: ``set_cards``
        # deleteLater's everything it finds in the column, and re-parenting is what takes a
        # survivor out of that sweep. The card is parented to this widget in the gap, so it
        # is never briefly parentless -- which is to say never briefly a top-level window.
        for card in self._cards:
            if id(card) in keep:
                card.setParent(self)

        for card in self._cards:
            if id(card) not in keep:
                card.setParent(None)
                card.deleteLater()

        self._cards = active_cards + unhooked_cards
        self._column.set_cards(active_cards)
        self._fill_unhooked(unhooked_cards)

        self._update_bulk_buttons()
        for card in active_cards:
            card.set_locked(self._locked, self._lock_reason)

    def _card_for(self, survivors: dict, view: PlayerView) -> PlayerCard:
        """The existing card for this window in this bucket, showing *view* -- or a new one."""
        card = survivors.get((view.hwnd, view.unhooked))
        if card is not None and card.verb_ids == tuple(v for v in view.verbs if v in self._verbs):
            card.show_view(view)
            return card
        card = self._card_factory(view)
        card.setParent(self)
        card.unhook_requested.connect(self.unhook_requested)
        card.rehook_requested.connect(self.rehook_requested)
        card.verb_triggered.connect(self.verb_triggered)
        return card

    def show_views(self, views: list[PlayerView]) -> None:
        """Hand each card its newest view, by window. A view with no card here is ignored.

        What a refresh tick calls. It builds and moves nothing, so a view naming a window
        whose card is gone -- one unhooked while the tick read it -- changes nothing.
        """
        cards = {(card.hwnd, card.is_unhooked): card for card in self._cards}
        for view in views:
            card = cards.get((view.hwnd, view.unhooked))
            if card is not None:
                card.show_view(view)

    def _fill_unhooked(self, cards: list[PlayerCard]) -> None:
        """Put *cards* in the Unhooked section, and hide the section when there are none."""
        while self._unhooked_layout.count():
            item = self._unhooked_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for card in cards:
            self._unhooked_layout.addWidget(card)
        self._unhooked_section.setVisible(bool(cards))

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """Lock every hooked card's Unhook and grip, and Unhook All, or let them go.

        *reason* is what a locked grip says. Rehooking is never locked: it is how a client
        that dropped out is brought back. A card built while locked arrives locked.
        """
        self._locked = locked
        self._lock_reason = reason
        for card in self._cards:
            card.set_locked(locked, reason)
        self._update_bulk_buttons()

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def cards(self) -> list[PlayerCard]:
        """Every card on the tab, hooked ones first. Public for the callers that count them."""
        return list(self._cards)

    def card_for(self, hwnd: int) -> PlayerCard | None:
        """The card for the window *hwnd*, in either bucket, or None."""
        return next((card for card in self._cards if card.hwnd == hwnd), None)

    @property
    def column(self) -> CardColumn:
        """The hooked players' column -- the only thing here a drag can reorder."""
        return self._column

    @property
    def unhooked_section(self) -> SidebarSection:
        """The collapsible section beneath the column, shown only when it holds a card."""
        return self._unhooked_section

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_reordered(self, order: list) -> None:
        """The column's permutation, as the windows in their new order."""
        hooked = [card for card in self._cards if not card.is_unhooked]
        if sorted(order) != list(range(len(hooked))):
            return
        self.order_changed.emit([hooked[index].hwnd for index in order])

    def _update_bulk_buttons(self) -> None:
        """Enable each bulk button only when it has clients to act on.

        Unhook All follows the same lock as the per-client Unhook verb.
        """
        has_active = any(not c.is_unhooked for c in self._cards)
        has_unhooked = any(c.is_unhooked for c in self._cards)
        self._unhook_all_btn.setEnabled(has_active and not self._locked)
        self._rehook_all_btn.setEnabled(has_unhooked)

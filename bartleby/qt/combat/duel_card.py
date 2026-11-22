"""One duel a product's settings hold: the duel in use, whose it is, and the two ways to change it.

BartleBot's Settings › Combat has one each for the Solo and Team defaults, and BartleBuddy's
one per kind of fight and team size. A heading line, the duel in use drawn as the same
``StrategyCard`` the Combat Strategies window lists, two verbs and a note. A ``SettingRow``'s
control slot is 210px and holds a field; a duel's summary is a name, a source pill and a line
of facets, which is what the user is choosing between, so it is drawn whole.

**Change…** opens the product's Combat Strategies window in Pick mode on the library's duels,
filtered to the team size the card answers for as a chip the user can remove. The second verb
goes back to no choice, and the product names it, since what answers then is the product's to
say (:class:`Unset`): the duel that ships, another card's, a shared default.

The card stores nothing. :attr:`DuelCard.choice` is pending until the dialog's OK, and the
product's tab writes it (``settings/rows``, the apply rule).

Moved from BartleBot's ``gui/settings/combat_defaults_tab.py`` (BartleBuddy #31, Stage 7).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from bartleby.qt.art_signal import follow_art
from bartleby.qt.combat.dialog import DialogMode
from bartleby.qt.combat.dsl_checks import DeferredDslChecks
from bartleby.qt.settings.rows import SettingCard
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.cards.strategy_card import StrategyCard
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.runtime.combat.strategy_summary import summarize_duel
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.combat_defaults import SIZE_RANGE, TeamSize
from bartleby.shared.store_files import held_listings
from bartleby.shared.strategy_library import StrategyKind

CHANGE = "Change…"
CHOSEN_NOTE = "Chosen by you."
#: The team size Change… filters to: the smallest the card answers for.
PICK_TEAM_SIZE = {size: sizes[0] for size, sizes in SIZE_RANGE.items()}


@dataclass(frozen=True)
class DuelCardText:
    """What a product says on one card."""

    title: str
    summary: str
    change_tooltip: str
    #: The verb that goes back to no choice, and its tooltip.
    unset: str
    unset_tooltip: str
    #: What the note says when no duel can be read, the one that answers with no choice included.
    unreadable: str


@dataclass(frozen=True)
class Unset:
    """What answers while the user has chosen nothing: the duel shown, and the note under it."""

    duel_id: str | None
    note: str


class DuelCard(SettingCard):
    """One duel of a product's settings. See the module docstring.

    *chosen* is the duel chosen, or None. *unset* says what answers with none, asked at each
    redraw; *notes* what is wrong with a choice, in the user's words, read from the library
    handed to it. *library* reads the library as it is now. *window* builds the product's
    Combat Strategies window over a working set, taking the window's own keywords.

    *same_as_unset* is a duel id that counts as no choice, for a product whose recommended
    duel is one duel: picking it by name is the same as going back to it.
    """

    #: The choice changed: the duel id, or None for none.
    changed = pyqtSignal(object)

    def __init__(
        self,
        size: TeamSize,
        text: DuelCardText,
        chosen: str | None,
        *,
        library: Callable[[], StrategyWorkingSet],
        unset: Callable[[StrategyWorkingSet], Unset],
        notes: Callable[[StrategyWorkingSet, str | None], list[str]],
        window: Callable[..., QDialog],
        same_as_unset: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._text = text
        self._library = library
        self._unset = unset
        self._notes = notes
        self._window = window
        self._same_as_unset = same_as_unset
        self._chosen = None if chosen == same_as_unset else chosen
        self._shown_id: str | None = None
        self._strategy_card: StrategyCard | None = None
        # The card's health is drawn from the DSL checks already known, so opening
        # Settings parses nothing; the rest are checked off the GUI thread.
        self._checks = DeferredDslChecks(self, lambda _keys: self.redraw())

        body = QWidget(self)
        column = QVBoxLayout(body)
        column.setContentsMargins(12, 8, 12, 10)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(0)
        self._title = ThemedTextLabel(text.title, color="text", css=f"font-size: {THEME.font_body_px}px;")
        self._summary = ThemedTextLabel(text.summary, css=f"font-size: {THEME.font_subtext_px}px;")
        words.addWidget(self._title)
        words.addWidget(self._summary)
        head.addLayout(words, 1)
        self._change_btn = ActionButton("search", CHANGE, style=ActionStyle.SECONDARY, fill=False)
        self._change_btn.setToolTip(text.change_tooltip)
        self._change_btn.clicked.connect(self._on_change)
        head.addWidget(self._change_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._unset_btn = ActionButton("revert", text.unset, fill=False)
        self._unset_btn.setToolTip(text.unset_tooltip)
        self._unset_btn.clicked.connect(lambda: self.set_choice(None))
        head.addWidget(self._unset_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(head)

        # Rebuilt per choice: a StrategyCard is drawn from one summary and has no setter.
        self._card_slot = QVBoxLayout()
        self._card_slot.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self._card_slot)

        self._note = ThemedTextLabel("", css=f"font-size: {THEME.font_subtext_px}px;")
        self._note.setWordWrap(True)
        column.addWidget(self._note)

        self.layout().addWidget(body)
        self.redraw()
        # The card draws its schools as medallions or as words once, when it is made, and
        # Settings may be open behind the extraction one of its own tabs starts.
        follow_art(self, self.redraw)

    # -- reading --------------------------------------------------------

    @property
    def choice(self) -> str | None:
        """The duel id chosen, or None for no choice. Pending until the dialog's OK."""
        return self._chosen

    @property
    def shown_id(self) -> str | None:
        """The duel drawn: the one chosen, or the one that answers with none. None when neither can be read."""
        return self._shown_id

    @property
    def change_button(self) -> ActionButton:
        return self._change_btn

    @property
    def unset_button(self) -> ActionButton:
        return self._unset_btn

    @property
    def strategy_card(self) -> StrategyCard | None:
        """The duel in use, as the Combat Strategies window draws it, or None when none can be read."""
        return self._strategy_card

    @property
    def note_text(self) -> str:
        return self._note.text()

    # -- writing --------------------------------------------------------

    def set_choice(self, duel_id: str | None) -> None:
        """Choose *duel_id*, or None for no choice, which the duel that counts as none is too."""
        duel_id = None if duel_id == self._same_as_unset else duel_id
        if duel_id == self._chosen:
            return
        self._chosen = duel_id
        self.redraw()
        self.changed.emit(duel_id)

    def _on_change(self) -> None:
        """Pick a duel from the library, filtered to the team this card answers for.

        Select-only. Opened from a click, a synchronous slot, as ``exec()`` must be
        (``bartleby/qt/CLAUDE.md``, qasync).
        """
        ws = self._library()
        dialog = self._window(
            ws,
            kind=StrategyKind.DUEL,
            mode=DialogMode.PICK,
            current_id=self._chosen or self._unset(ws).duel_id,
            allow_none=False,
            team_size=PICK_TEAM_SIZE[self._size],
            parent=self,
        )
        if dialog.exec() and dialog.selected_id():
            self.set_choice(dialog.selected_id())

    def redraw(self) -> None:
        """Draw the duel in use again: the library, or what answers with no choice, may have changed."""
        with held_listings():
            self._redraw_now()

    def _redraw_now(self) -> None:
        self._checks.reset()
        library = self._library()
        unset = self._unset(library)
        duel = library.get(StrategyKind.DUEL, self._chosen) if self._chosen else None
        shown = duel or (library.get(StrategyKind.DUEL, unset.duel_id) if unset.duel_id else None)
        self._shown_id = shown.id if shown is not None else None

        if self._strategy_card is not None:
            self._card_slot.removeWidget(self._strategy_card)
            self._strategy_card.deleteLater()
            self._strategy_card = None
        if shown is not None:
            summary = summarize_duel(shown, library.players, name=shown.name, validate=self._checks.validator())
            # With its source pill, as the window draws it: Official for a shipped one, and
            # whatever the user's choice is.
            summary = replace(summary, source=library.status(StrategyKind.DUEL, shown.id))
            self._strategy_card = StrategyCard(summary, parent=self)
            self._card_slot.addWidget(self._strategy_card)

        if shown is None:
            lead = self._text.unreadable
        elif self._chosen is None:
            lead = unset.note
        else:
            lead = CHOSEN_NOTE
        self._note.setText(" ".join([lead, *self._notes(library, self._chosen)]))
        self._unset_btn.setEnabled(self._chosen is not None)
        self._checks.request_unknown()

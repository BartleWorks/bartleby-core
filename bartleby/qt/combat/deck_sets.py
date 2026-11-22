"""Deck sets in Settings: their cards, and the editor of one (``bartleby.shared.deck_sets``).

:class:`DeckSetList` is a product's list of them: a card per set that edits, duplicates and
deletes it, and the card that adds one. **A set is saved as it is edited**, OK or not, as a
launch profile is on the Game tab: it is the user's, in a file every product reads, and not one
product's setting. What a set is *for* is the product's own setting, which waits for OK.

:class:`DeckSetDialog` edits one: its name, everyone's deck, and an exception for each wizard
that differs, all three by :class:`~bartleby.qt.widgets.pickers.deck_name_picker.DeckNamePicker`.
An exception whose wheels are empty is a wizard that keeps the deck it has on.

**Which wizards can be excepted** is every one the app knows: the team's, and every character its
accounts' rosters hold (``runtime/clients/character_roster``). The team's, with the decks each
owns, come from the game, so they are asked for as the editor opens and arrive when they have
been read: :class:`DeckWizards` is that crossing, which a product answers. The set holds a
wizard's character id, and its name is only ever shown, through Streamer Mode.

A set another product has changed since the editor opened is never overwritten: the user keeps
theirs as a copy, or lets it go.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable, Sequence

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartleby.qt import streamer_mode
from bartleby.qt.dialogs.confirm import confirm_delete
from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.settings.rows import CARD_GAP_PX
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.buttons.icon_button import IconButton
from bartleby.qt.widgets.cards.add_card import AddCard
from bartleby.qt.widgets.cards.card_frame import CardFrame, CardMode, CardVerb
from bartleby.qt.widgets.labels.elided_label import ElidedLabel
from bartleby.qt.widgets.labels.rule_heading import RuleHeading
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.qt.widgets.pickers.deck_name_picker import DeckNamePicker
from bartleby.qt.widgets.pills.player_badge import PlayerBadge
from bartleby.runtime.clients import character_roster
from bartleby.runtime.clients.credentials import account_label
from bartleby.runtime.views import DeckWizard
from bartleby.shared.account_slots import account_color_keys
from bartleby.shared.deck_names import DeckName
from bartleby.shared.deck_sets import DeckSet, DeckSetChanged, DeckSetStore
from bartleby.shared.store_files import unique_name

logger = logging.getLogger(__name__)

ADD_SET = "Add deck set"
EMPTY_HINT = "A deck set says which of its decks each wizard puts on, by the deck's name in the game."
ADD_TITLE = "Add Deck Set"
EDIT_TITLE = "Edit Deck Set"
DELETE_TITLE = "Delete Deck Set"
EVERYONE_NONE = "Each wizard keeps its deck"
KEEPS = "Keeps its deck"
EXCEPTIONS_HINT = "A wizard listed here puts on a deck of its own."
ADD_WIZARD = "Add wizard…"
NO_WIZARDS = "Log in with a wizard to list it here"
UNKNOWN_WIZARD = "Unknown wizard"
UNKNOWN_TIP = "This wizard isn't one of your accounts' known wizards"

#: Wide enough for a wizard, the three wheels and the name they make, on one row.
_MIN_WIDTH = 680
#: The exceptions' part of the editor: about four rows before it scrolls.
_EXCEPTIONS_HEIGHT = 150
#: What an exception's wizard is drawn in before its wheels start.
_WIZARD_WIDTH = 190


# ---------------------------------------------------------------------------
# The wizards
# ---------------------------------------------------------------------------


class DeckWizards(QObject):
    """The team's wizards and their decks, as they were last read, for the set editor.

    The product answers :attr:`requested` by reading them where its clients are, and hands
    them to :meth:`show`.
    """

    requested = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._team: tuple[DeckWizard, ...] = ()

    @property
    def team(self) -> tuple[DeckWizard, ...]:
        return self._team

    def request(self) -> None:
        self.requested.emit()

    def show(self, team: Sequence[DeckWizard]) -> None:
        team = tuple(team)
        if team != self._team:
            self._team = team
            self.changed.emit()


def known_wizards(team: Iterable[DeckWizard], accounts: Sequence[dict]) -> list[DeckWizard]:
    """Every wizard the app knows: the team's, in slot order, then each account's others.

    *accounts* are the vendor's, whose rosters are read. A wizard of the team takes its name
    from its roster where the game's hasn't been read.
    """
    colors = account_color_keys(list(accounts))
    by_id: dict[int, DeckWizard] = {}
    for account in accounts:
        username = account.get("username", "")
        for place, entry in enumerate(character_roster.load(username) if username else []):
            by_id[entry["gid"]] = DeckWizard(
                character_id=entry["gid"],
                wizard_name=entry["name"] or None,
                color_key=colors.get(username),
                account_label=account_label(username),
                roster_place=place,
            )
    known = []
    for wizard in sorted(team, key=lambda w: w.slot or 0):
        rostered = by_id.pop(wizard.character_id, None)
        if rostered is not None:
            wizard = DeckWizard(
                character_id=wizard.character_id,
                wizard_name=wizard.wizard_name or rostered.wizard_name,
                slot=wizard.slot,
                color_key=wizard.color_key,
                account_label=wizard.account_label,
                roster_place=rostered.roster_place,
                decks=wizard.decks,
            )
        known.append(wizard)
    return [*known, *by_id.values()]


def wizard_text(wizard: DeckWizard) -> str:
    """A wizard's name, through Streamer Mode, with its account's: two accounts can both have a "Character 1"."""
    name = streamer_mode.display_name(wizard.wizard_name, wizard.roster_place, placeholder=UNKNOWN_WIZARD)
    return f"{name} ({wizard.account_label})" if wizard.account_label else name


def exceptions_text(deck_set: DeckSet, wizards: Iterable[DeckWizard]) -> str:
    """A set's exceptions on one line: ``P2 Piercing Deck, P4 keeps its deck``."""
    by_id = {w.character_id: w for w in wizards}
    parts = []
    for character, name in deck_set.exceptions.items():
        wizard = by_id.get(character)
        if wizard is None:
            who = UNKNOWN_WIZARD
        elif wizard.slot is not None:
            who = f"P{wizard.slot}"
        else:
            who = streamer_mode.display_name(wizard.wizard_name, wizard.roster_place, placeholder=UNKNOWN_WIZARD)
        parts.append(f"{who} {_lower_first(KEEPS) if name is None else name.text()}")
    return ", ".join(parts)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------


class DeckSetCard(CardFrame):
    """One deck set: its name, everyone's deck and its exceptions. A click edits it."""

    FRAME_NAME = "DeckSetCard"

    edit_requested = pyqtSignal(str)
    duplicate_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(self, deck_set: DeckSet, wizards: Sequence[DeckWizard] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent, mode=CardMode.MANAGE)
        self._set = deck_set
        layout = self._build_frame("deck sets", gutter=False)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._name = ElidedLabel(deck_set.name)
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)
        header.addWidget(self._name, 1)
        self._add_face_verbs(header)
        layout.addLayout(header)
        everyone = EVERYONE_NONE if deck_set.everyone is None else deck_set.everyone.text()
        self._everyone = ThemedTextLabel(everyone, color="text" if deck_set.everyone else "text_muted")
        layout.addWidget(self._everyone)
        self._exceptions = ThemedTextLabel(
            exceptions_text(deck_set, wizards), css=f"font-size: {THEME.font_subtext_px}px;"
        )
        self._exceptions.setWordWrap(True)
        self._exceptions.setVisible(bool(deck_set.exceptions))
        layout.addWidget(self._exceptions)
        self.activated.connect(lambda: self.edit_requested.emit(self._set.id))
        self.refresh_theme_styles()

    @property
    def deck_set(self) -> DeckSet:
        return self._set

    @property
    def texts(self) -> list[str]:
        """What the card reads, line by line."""
        lines = [self._name.full_text(), self._everyone.text()]
        return [*lines, self._exceptions.text()] if self._set.exceptions else lines

    def _verbs(self) -> list[CardVerb]:
        name = self._set.name
        return [
            CardVerb(
                "Edit", "pencil", f"Edit the '{name}' deck set", lambda: self.edit_requested.emit(self._set.id), True
            ),
            CardVerb(
                "Duplicate",
                "",
                f"Duplicate the '{name}' deck set",
                lambda: self.duplicate_requested.emit(self._set.id),
            ),
            CardVerb(
                "Delete",
                "trash",
                f"Delete the '{name}' deck set",
                lambda: self.delete_requested.emit(self._set.id),
                True,
            ),
        ]


class DeckSetList(QWidget):
    """Every deck set, as a card each, and the card that adds one. Saved as they are edited."""

    #: A set was added, changed or deleted.
    changed = pyqtSignal()

    def __init__(
        self,
        store: DeckSetStore,
        wizards: DeckWizards,
        accounts: Callable[[], Sequence[dict]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._wizards = wizards
        self._accounts = accounts
        self._cards: list[DeckSetCard] = []
        self._column = QVBoxLayout(self)
        # The card gap of the settings column it sits in, below it too.
        self._column.setContentsMargins(0, 0, 0, CARD_GAP_PX)
        self._column.setSpacing(CARD_GAP_PX)
        self._hint = ThemedTextLabel(EMPTY_HINT)
        self._hint.setWordWrap(True)
        self._column.addWidget(self._hint)
        self._add = AddCard(ADD_SET, gutter=False)
        self._add.add_requested.connect(self.add)
        self._column.addWidget(self._add)
        wizards.changed.connect(self.refresh)
        self.refresh()
        wizards.request()

    @property
    def cards(self) -> list[DeckSetCard]:
        return list(self._cards)

    @property
    def add_card(self) -> AddCard:
        return self._add

    def known(self) -> list[DeckWizard]:
        return known_wizards(self._wizards.team, self._accounts())

    def refresh(self) -> None:
        """Draw the sets as the file has them now."""
        for card in self._cards:
            self._column.removeWidget(card)
            card.deleteLater()
        self._cards = []
        sets = self._store.sets()
        known = self.known() if any(s.exceptions for s in sets) else []
        for at, deck_set in enumerate(sets):
            card = DeckSetCard(deck_set, known, parent=self)
            card.edit_requested.connect(self.edit)
            card.duplicate_requested.connect(self.duplicate)
            card.delete_requested.connect(self.delete)
            self._column.insertWidget(1 + at, card)
            self._cards.append(card)
        self._hint.setVisible(not sets)

    # -- the verbs. Each opens its dialog from a click, a synchronous slot ------------------------

    def add(self) -> None:
        self._edit(DeckSet(), None)

    def edit(self, set_id: str) -> None:
        deck_set = self._store.get(set_id)
        if deck_set is not None:
            self._edit(deck_set, self._store.fingerprint(set_id))

    def _edit(self, deck_set: DeckSet, fingerprint: str | None) -> None:
        dialog = DeckSetDialog(
            self._store, deck_set, fingerprint, wizards=self._wizards, accounts=self._accounts(), parent=self
        )
        dialog.exec()
        self._after()

    def duplicate(self, set_id: str) -> None:
        self._written(lambda: self._store.duplicate(set_id))

    def delete(self, set_id: str) -> None:
        deck_set, fingerprint = self._store.get(set_id), self._store.fingerprint(set_id)
        if deck_set is None:
            self._after()
        elif confirm_delete(self, DELETE_TITLE, f"Delete deck set '{deck_set.name}'?"):
            self._written(lambda: self._store.delete(set_id, expected=fingerprint))

    def _written(self, write: Callable[[], object]) -> None:
        try:
            write()
        except DeckSetChanged:
            QMessageBox.warning(self, EDIT_TITLE, "Another app changed this deck set. Look at it again first.")
        except OSError:
            logger.exception("Could not write the deck sets")
            QMessageBox.warning(self, EDIT_TITLE, "The deck sets couldn't be saved. The log has the details.")
        self._after()

    def _after(self) -> None:
        self.refresh()
        self.changed.emit()


# ---------------------------------------------------------------------------
# The editor
# ---------------------------------------------------------------------------


class _ExceptionRow(QWidget):
    """One wizard's exception: who, the wheels, and the cross that takes it out."""

    removed = pyqtSignal(object)  # itself

    def __init__(self, character_id: int, name: DeckName | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.character_id = character_id
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        who = QWidget(self)
        who.setFixedWidth(_WIZARD_WIDTH)
        self._who = QHBoxLayout(who)
        self._who.setContentsMargins(0, 0, 0, 0)
        self._who.setSpacing(6)
        self._badge: PlayerBadge | None = None
        self._label = ElidedLabel(UNKNOWN_WIZARD, who)
        self._who.addWidget(self._label, 1)
        row.addWidget(who, 0)
        self.picker = DeckNamePicker(name, none_text=KEEPS, parent=self)
        row.addWidget(self.picker, 1)
        self.remove_button = IconButton(
            "dismiss", tooltip="Take this wizard out of the exceptions", size=22, parent=self
        )
        self.remove_button.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignVCenter)

    @property
    def who_text(self) -> str:
        return self._label.full_text()

    def show_wizard(self, wizard: DeckWizard | None) -> None:
        """Say who it is, and offer the decks it owns; a wizard not known is named nothing."""
        if self._badge is not None:
            self._who.removeWidget(self._badge)
            self._badge.deleteLater()
            self._badge = None
        if wizard is None:
            self._label.setText(UNKNOWN_WIZARD)
            self._label.setToolTip(UNKNOWN_TIP)
            return
        if wizard.slot is not None:
            self._badge = PlayerBadge(wizard.slot)
            self._badge.set_color_key(wizard.color_key)
            self._badge.set_account_label(wizard.account_label)
            self._who.insertWidget(0, self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label.setText(wizard_text(wizard))
        self._label.setToolTip(wizard_text(wizard))
        self.picker.set_wizards((wizard,))


class DeckSetDialog(QDialog):
    """The editor of one deck set. Accepted once the set is written."""

    def __init__(
        self,
        store: DeckSetStore,
        deck_set: DeckSet,
        fingerprint: str | None,
        *,
        wizards: DeckWizards,
        accounts: Sequence[dict] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._set = deck_set
        self._fingerprint = fingerprint
        self._wizards = wizards
        self._accounts = accounts
        self._rows: list[_ExceptionRow] = []
        self.setWindowTitle(ADD_TITLE if fingerprint is None else EDIT_TITLE)
        self.setMinimumWidth(_MIN_WIDTH)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit(deck_set.name)
        self._name.setPlaceholderText("Bosses")
        form.addRow("Name:", self._name)
        self._everyone = DeckNamePicker(deck_set.everyone, none_text=EVERYONE_NONE, parent=self)
        form.addRow("Everyone:", self._everyone)
        layout.addLayout(form)

        layout.addWidget(RuleHeading("Exceptions"))
        hint = ThemedTextLabel(EXCEPTIONS_HINT, css=f"font-size: {THEME.font_subtext_px}px;")
        layout.addWidget(hint)
        holder = QWidget()
        self._exceptions = QVBoxLayout(holder)
        self._exceptions.setContentsMargins(0, 4, 0, 4)
        self._exceptions.setSpacing(6)
        self._exceptions.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(_EXCEPTIONS_HEIGHT)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        self._add = ActionButton("plus", ADD_WIZARD, fill=False)
        self._add.clicked.connect(self._open_add_menu)
        row = QHBoxLayout()
        row.addWidget(self._add)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addLayout(dialog_footer(self, commit_text="Save", on_commit=self.save).layout)

        for character, name in deck_set.exceptions.items():
            self._add_row(character, name)
        self._show_wizards()
        wizards.changed.connect(self._show_wizards)
        wizards.request()

    # -- reading ------------------------------------------------------------------------------

    @property
    def name_edit(self) -> QLineEdit:
        return self._name

    @property
    def everyone(self) -> DeckNamePicker:
        return self._everyone

    @property
    def rows(self) -> list[_ExceptionRow]:
        return list(self._rows)

    @property
    def add_button(self) -> ActionButton:
        return self._add

    def edited(self) -> DeckSet:
        """The set as the dialog has it."""
        return self._set.model_copy(
            update={
                "name": self._name.text().strip(),
                "everyone": self._everyone.name,
                "exceptions": {row.character_id: row.picker.name for row in self._rows},
            }
        )

    # -- the wizards ----------------------------------------------------------------------------

    def _show_wizards(self) -> None:
        """The team was read, or wasn't yet: who each exception is, the decks to pick from, and who can be added."""
        self._known = known_wizards(self._wizards.team, self._accounts)
        by_id = {w.character_id: w for w in self._known}
        self._everyone.set_wizards(self._wizards.team)
        for row in self._rows:
            row.show_wizard(by_id.get(row.character_id))
        self._refresh_add()

    def _refresh_add(self) -> None:
        offered = self.addable()
        self._add.setEnabled(bool(offered))
        self._add.setToolTip("" if offered else NO_WIZARDS)

    def addable(self) -> list[DeckWizard]:
        """The wizards known and not listed yet."""
        listed = {row.character_id for row in self._rows}
        return [w for w in self._known if w.character_id not in listed]

    def add_wizard(self, wizard: DeckWizard) -> None:
        self._add_row(wizard.character_id, None).show_wizard(wizard)
        self._refresh_add()

    def add_menu(self):
        menu = popup_menu(self._add)
        for wizard in self.addable():
            text = wizard_text(wizard)
            action = menu.addAction(text if wizard.slot is None else f"P{wizard.slot}  {text}")
            action.triggered.connect(lambda _checked=False, w=wizard: self.add_wizard(w))
        return menu

    def _open_add_menu(self) -> None:
        self.add_menu().exec(self._add.mapToGlobal(QPoint(0, self._add.height())))

    def _add_row(self, character_id: int, name: DeckName | None) -> _ExceptionRow:
        row = _ExceptionRow(character_id, name, parent=self)
        row.removed.connect(self._remove_row)
        self._rows.append(row)
        self._exceptions.insertWidget(self._exceptions.count() - 1, row)
        row.show()
        return row

    def _remove_row(self, row: _ExceptionRow) -> None:
        self._rows.remove(row)
        self._exceptions.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._refresh_add()

    # -- saving ------------------------------------------------------------------------------------

    def save(self) -> None:
        """Write the set and close; a set another app changed meanwhile is asked about first."""
        edited = self.edited()
        if not edited.name:
            QMessageBox.warning(self, self.windowTitle(), "Enter a name for the deck set.")
            return
        try:
            self._store.save(edited, expected=self._fingerprint)
        except DeckSetChanged:
            self._resolve(edited)
        except OSError:
            self._report()
        else:
            self.accept()

    def _resolve(self, edited: DeckSet) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(self.windowTitle())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Another app changed '{edited.name}'. Save yours as a copy, or discard your changes.")
        copy = box.addButton("Save as copy", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
        box.exec()
        if box.clickedButton() is copy:
            name = unique_name(edited.name, [s.name for s in self._store.sets()])
            try:
                self._store.save(edited.model_copy(update={"id": str(uuid.uuid4()), "name": name}), expected=None)
            except OSError:
                self._report()
            else:
                self.accept()
        elif box.clickedButton() is discard:
            self.reject()

    def _report(self) -> None:
        logger.exception("Could not write the deck sets")
        QMessageBox.warning(self, self.windowTitle(), "The deck set couldn't be saved. The log has the details.")

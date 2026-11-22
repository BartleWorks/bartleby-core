"""``DeckNamePicker``: a deck's name on the game's three wheels, with the name it makes beside them.

The wheels are the rename window's own (``bartleby.shared.deck_names``): a school or world, a
kind and a number, each with an empty choice. All three empty names no deck, and *none_text*
says what that means where the picker is.

**The button beside them lists the decks the user's wizards own**, with which of them own each,
and puts the one picked on the wheels. The wheels can name a deck nobody owns, and some of
their choices read alike ("Healer" is two keys), so only a deck that exists says which is meant.
It is gray while no wizard's decks are known.

The choices are English, as the app's text is. A name is stored by its keys, so it is the same
deck in a game of any language.
"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.widgets.buttons.icon_button import IconButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.runtime.views import DeckWizard
from bartleby.shared.deck_names import DeckName, Wheel, choices

#: Each wheel's name, for the tooltip and for a screen reader.
_WHEELS = {Wheel.CLASS: "School or world", Wheel.TYPE: "Kind", Wheel.NUM: "Number"}
#: How many entries a wheel's list shows before it scrolls.
_VISIBLE = 16

OWNED_TIP = "Pick one of your wizards' decks"
NO_OWNED_TIP = "Start a game to pick one of your wizards' decks"


def owned_names(wizards: Iterable[DeckWizard]) -> list[tuple[DeckName, list[int]]]:
    """Each deck *wizards* own and the slots that own it, by name: most owners first, then as it reads."""
    owners: dict[DeckName, list[int]] = {}
    for wizard in wizards:
        for name in wizard.decks:
            owners.setdefault(name, [])
            if wizard.slot is not None:
                owners[name].append(wizard.slot)
    return sorted(owners.items(), key=lambda item: (-len(item[1]), item[0].text().casefold(), item[0].keys))


class DeckNamePicker(QWidget):
    """A deck's name, or none. See the module docstring."""

    #: The name picked changed: a DeckName, or None.
    changed = pyqtSignal(object)

    def __init__(self, name: DeckName | None = None, *, none_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._none_text = none_text
        self._wizards: tuple[DeckWizard, ...] = ()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._wheels: dict[Wheel, QComboBox] = {}
        for wheel, title in _WHEELS.items():
            combo = QComboBox(self)
            combo.setToolTip(title)
            combo.setAccessibleName(title)
            combo.setMaxVisibleItems(_VISIBLE)
            for choice in choices(wheel):
                combo.addItem(choice.label, choice.key)
            combo.currentIndexChanged.connect(self._on_wheel)
            row.addWidget(combo, 0)
            self._wheels[wheel] = combo
        self._owned = IconButton("search", tooltip=NO_OWNED_TIP, size=22, icon_size=16, parent=self)
        self._owned.clicked.connect(self._open_owned)
        row.addWidget(self._owned, 0)
        self._preview = ThemedTextLabel("", color="text", parent=self)
        row.addWidget(self._preview, 1)
        self.set_wizards(())
        self.set_name(name)

    # -- reading ------------------------------------------------------------------------------

    @property
    def name(self) -> DeckName | None:
        """The name on the wheels, or None with all three empty."""
        class_key, type_key, num_key = (self._wheels[w].currentData() or "" for w in Wheel)
        if not (class_key or type_key or num_key):
            return None
        return DeckName(class_key=class_key, type_key=type_key, num_key=num_key)

    @property
    def wheels(self) -> dict[Wheel, QComboBox]:
        return dict(self._wheels)

    @property
    def owned_button(self) -> IconButton:
        return self._owned

    @property
    def preview_text(self) -> str:
        """What the wheels make, or what naming no deck means here."""
        return self._preview.text()

    # -- writing --------------------------------------------------------------------------------

    def set_name(self, name: DeckName | None) -> None:
        """Put *name* on the wheels. A key newer than the snapshot is added to its wheel, so it stays."""
        before = self.name
        keys = ("", "", "") if name is None else name.keys
        for combo, key in zip(self._wheels.values(), keys, strict=True):
            combo.blockSignals(True)
            at = combo.findData(key)
            if at < 0:
                combo.addItem(key, key)
                at = combo.count() - 1
            combo.setCurrentIndex(at)
            combo.blockSignals(False)
        self._show()
        if self.name != before:
            self.changed.emit(self.name)

    def set_wizards(self, wizards: Iterable[DeckWizard]) -> None:
        """Offer the decks *wizards* own behind the button."""
        self._wizards = tuple(wizards)
        known = any(wizard.decks for wizard in self._wizards)
        self._owned.setEnabled(known)
        self._owned.setToolTip(OWNED_TIP if known else NO_OWNED_TIP)

    def owned_menu(self):
        """The decks owned, each with the players that own it."""
        menu = popup_menu(self._owned)
        for name, slots in owned_names(self._wizards):
            who = " ".join(f"P{slot}" for slot in sorted(slots))
            # What follows a tab is drawn in the menu's own column for shortcuts.
            action = menu.addAction(f"{_label(name)}\t{who}" if who else _label(name))
            action.triggered.connect(lambda _checked=False, n=name: self.set_name(n))
        return menu

    def _open_owned(self) -> None:
        self.owned_menu().exec(self._owned.mapToGlobal(QPoint(0, self._owned.height())))

    def _on_wheel(self, _index: int) -> None:
        self._show()
        self.changed.emit(self.name)

    def _show(self) -> None:
        name = self.name
        self._preview.setText(self._none_text if name is None else name.text())
        self._preview.set_color("text_muted" if name is None else "text")


def _label(name: DeckName) -> str:
    """*name* as the menu lists it: numbered, as on its wheel, where another choice reads the same."""
    parts = []
    for wheel, key in zip(Wheel, name.keys, strict=True):
        choice = next((c for c in choices(wheel) if c.key == key), None)
        if key and choice is not None and choice.label != choice.text:
            parts.append(choice.label)
    return f"{name.text()} ({', '.join(parts)})" if parts else name.text()

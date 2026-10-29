"""``PlayerBadge``: an inert ``P{n}`` pill, colored by whoever is in that slot right now.

WHY THE PILL STILL READS "P1"
-----------------------------

The color is the account's; the *text* is not. A slot is a place in the order, and the same
slot holds a different account tomorrow, so a pill reading ``Farmer`` would be a claim
that goes stale. The slot is the label, always, and the account is the *color*, which is
exactly the job color does everywhere else in the family (``theming/account_accent.py``): a
match target, not an identifier. The account's name is in the tooltip, which is where a
claim that can go stale belongs.

Moved from BartleBot's ``widgets/pills/player_pills.py``, which keeps the pills that toggle
a slot and the combat order's row of badges with the game's gems.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

from bartleby.qt.theming.account_accent import account_accent, account_tint
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from bartleby.qt.widgets.pills.pill import pill_height

#: ``NeutralPill``'s height, not that of BartleBot's ``PlayerSlotPill``: a badge sits inside a caption line
#: on a sidebar card, where an 18px pill would make the line taller than the text beside
#: it. Taken from ``pill_height()`` rather than copied as a number -- it was 14 here and
#: 14 there until the body text moved, and then this one clipped its own text while the
#: pill it is meant to match grew.
#: One pixel less than ``NeutralPill``'s padding, because a badge always draws a 1px
#: border -- in the fill color when uncolored -- so the text sits at the same x whether
#: or not an account is known, and a badge does not change width when one arrives.
_BADGE_PAD_X = 5


class PlayerBadge(QLabel):
    """An inert ``P{n}`` pill, colored by whoever is in that slot right now.

    The read-only form of BartleBot's ``PlayerSlotPill``: the same claim -- the text is the slot,
    the color is the account -- with nothing to toggle. BartleBot's Combat tab wears it on
    its order, assignment lines and deck cards, and every player card wears it
    (``sidebar/player_card.py``), so a slot reads the same pill in both places. Two states rather than three:

    * **colored** -- outlined and tinted in the account's color, as ``AccountPill`` is.
    * **uncolored** -- the gray ``NeutralPill`` look. Nothing hooked, or an identity not
      learned yet, is a normal state, and it must not pick a default hue (``bartleby/qt/CLAUDE.md``,
      *Account colors*).
    """

    def __init__(self, player: int, parent: QWidget | None = None) -> None:
        super().__init__(f"P{player}", parent)
        self._player = player
        self._color_key: int | None = None
        self._account_label = ""
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(pill_height())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._restyle()

    @property
    def player(self) -> int:
        """The 1-based slot this badge names."""
        return self._player

    @property
    def color_key(self) -> int | None:
        """The account palette slot it wears, or None for the uncolored look."""
        return self._color_key

    def set_player(self, player: int) -> None:
        """Re-point this badge at another slot, keeping the widget."""
        if player == self._player:
            return
        self._player = player
        self.setText(f"P{player}")
        self._restyle()

    def set_color_key(self, key: int | None) -> None:
        if key == self._color_key:
            return
        self._color_key = key
        self._restyle()

    def set_account_label(self, label: str) -> None:
        """Name the account in this slot, for the tooltip. Empty when nobody is known."""
        if label == self._account_label:
            return
        self._account_label = label
        self._restyle()

    def _restyle(self) -> None:
        # Guarded as NeutralPill._restyle is: setStyleSheet delivers StyleChange straight back
        # to changeEvent, and the recursion kills the app during construction.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            if self._color_key is None:
                fill = border = THEME.pill_neutral_bg
                text = THEME.pill_text
            else:
                fill, border, text = account_tint(self._color_key), account_accent(self._color_key), THEME.text
            set_style_sheet(
                self,
                f"background: {fill}; color: {text}; font-size: {THEME.font_micro_px}px; font-weight: bold;"
                f" border-radius: {pill_height() // 2}px; border: 1px solid {border}; padding: 0px {_BADGE_PAD_X}px;",
            )
            who = f" — {self._account_label}" if self._account_label else ""
            self.setToolTip(f"Player {self._player}{who}")
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()

"""What a shared widget is shown, as frozen values: never a client, never a read.

A shared widget in ``bartleby.qt`` draws one of these and emits signals; it holds no
wizwalker client and reads no memory. Each product builds the view where its client is:
BartleBot in its Players tab's refresh loop, on the GUI's own loop, and BartleBuddy in its
session from the state reader, on the runtime thread, sending it across as a queued
signal. A view crosses that boundary whole, which is why it is frozen and holds only
values (core's ``docs/ARCHITECTURE.md``, "Three cuts").

Qt-free, so the runtime tier can build one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: :attr:`CardBadge.kind` values, each drawn as one of core's pills.
BADGE_ACCENT = "accent"
BADGE_NEUTRAL = "neutral"
BADGE_WARNING = "warning"


@dataclass(frozen=True)
class PlayerRow:
    """One ``Label  value`` row a product adds to a player card, under its Zone row.

    *copy_text* is what a click puts on the clipboard, when that is not the value as shown:
    BartleBot's XYZ row reads at one decimal and copies as the JSON a node takes. A row whose
    value is the card's placeholder copies nothing. *tooltip* says what a click copies.
    *elide_left* cuts a long value from the left, for a value whose tail is what tells two
    apart, as a zone path's is.
    """

    label: str
    value: str
    copy_text: str | None = None
    tooltip: str = ""
    elide_left: bool = False


@dataclass(frozen=True)
class CardBadge:
    """A short word on a player card's header, after the name: BartleBuddy's leader.

    *kind* is one of :data:`BADGE_ACCENT`, :data:`BADGE_NEUTRAL` and :data:`BADGE_WARNING`.
    """

    text: str
    kind: str = BADGE_NEUTRAL
    tooltip: str = ""


@dataclass(frozen=True)
class PlayerView:
    """One game client as a player card draws it.

    *slot* is the client's place in its list, counting from one: the ``P{n}`` a hooked card
    wears, and the index, less one, an Unhook asks for. An *unhooked* client wears no ``P{n}``,
    since it is not in the order until it is hooked back in.

    *color_key* is its account's palette slot, or ``None`` while the account is unknown,
    which is drawn as no color at all (core's ``bartleby/qt/CLAUDE.md``, "Account colors").
    *account_label* names that account for the badge's tooltip: its alias, never its
    username.

    *wizard_name* is the cached name, never one detected for the view: detection can open the
    character page and flash the game's UI. The card applies Streamer Mode to it.

    *loading* says the client is between zones. Its reads are stale or slow then, so a
    builder that reads the game skips them and sends the rows it had, and the card keeps
    the *zone* it drew last.

    *verbs* are the ids of the product's own verbs this card offers, which the product
    describes to the card (``bartleby.qt.sidebar.player_card.PlayerVerb``).
    """

    hwnd: int
    slot: int
    account_label: str = ""
    color_key: int | None = None
    wizard_name: str | None = None
    zone: str | None = None
    unhooked: bool = False
    loading: bool = False
    rows: tuple[PlayerRow, ...] = ()
    badges: tuple[CardBadge, ...] = ()
    verbs: tuple[str, ...] = ()

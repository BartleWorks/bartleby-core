"""``PlayerCard``: one game client in the Players tab, drawn as a card from a ``PlayerView``.

Moved from BartleBot's ``sidebar/player_card.py`` (Stage 8 of BartleBot #167), where it
held a live wizwalker client and read memory itself. It draws a
:class:`~bartleby.runtime.views.PlayerView` now, and holds no client: each product builds
the view where its client is and hands it to :meth:`PlayerCard.show`. What a product adds
arrives three ways: *rows* and *badges* in the view, its own verbs as :class:`PlayerVerb`\\ s
(a click is ``verb_triggered(hwnd, id)``), and a subclass for anything more, such as
BartleBot's freecam pair or BartleBuddy's control button.

It replaced ``ClientWidget``, which was the surface BartleBot's game view rework left behind: an unframed stack of
rows closed by a horizontal rule, with four 48px ``Copy`` buttons and a pair of ▲/▼
arrows for order, sitting next to accounts and launch profiles that had become cards.

What is new here beyond the frame:

**The color is the account's.** Launch the blue account and its player card is blue,
striped down the left edge with the same slot the blue pill on that launch profile card
names. An account runs one character at a time, so one client is one account and the
stripe is unambiguous. ``None`` means *no stripe* -- a game window the app did not
launch, or an identity not learned yet -- and because that identity arrives late (it is
filled by the client manager's own loop, see ``account_for``) the key arrives in every
view rather than being resolved once in the constructor. A card colored only at
construction is an uncolored card that never recovers.

**The value is the button.** The old row was ``Zone: <value>  [ Copy ]`` -- a 48px
labeled button per row, four of them down a 300px panel, and pressing one changed
nothing on screen. Here the value itself is the hit target and a 16px glyph sits at the
end of the row, swapping to a checkmark for a moment on copy. The noise drops and the hit
target grows, which is the usual failure mode of icon-ifying a button and is worth having
avoided; the confirmation is plausibly the bigger win, since there was none at all.

Not hover-revealed, deliberately. That is the launch screen's bargain, and the judgement
recorded in ``CardFrame`` is that hiding verbs behind hover on a surface that exists to
*run* them is backwards. The Players tab is that kind of surface.

**MEMBER mode, not MANAGE.** The Players tab card rework asked for "``CardMode.MANAGE`` semantics -- chrome
pinned open, nothing refuses", and those are the two properties named. MEMBER has both,
and MANAGE carries a third this card must not have: a body verb. A MANAGE card wears the
pointing-hand cursor over its whole face, lights up on hover and emits ``activated`` on a
click -- three promises that clicking a player card does something, when it does not.
There is no "open this player" to wire them to. So the mode is the one whose definition
this card actually matches, and ``CardMode``'s docstring now names both surfaces that
mean it.

THE SIDEBAR FLOOR IS NOT TOUCHED

This card needs *less* than the one it replaced -- three 22px icon buttons where there
were three labeled ones, and elided rows where there were labeled rows each closed by a
48px ``Copy``. The floor it left behind was measured from that old tool row, and stayed
as a conservative bound until the window-sizing change it was waiting for arrived: the
right sidebar's floor is now measured from the Log tab's header row, the widest row the
panel still has, and the card sets no floor of its own at all. See
``screen_fit.right_sidebar_floor`` and "The sidebar floors are measured, not fixed" in
``docs/WINDOW_SIZING.md``. Each product's
``test_the_player_card_fits_the_right_sidebar_under_every_available_style`` is what holds
this card under that floor.

THE REFRESH CONTRACT

Whoever builds the view keeps it, since that is where the client is -- see "Player cards
are drawn from views" in ``bartleby/qt/CLAUDE.md``:

- the wizard name is a ``wizard_names.cached_name`` dict lookup and never triggers
  detection; ``resolve_names()`` can open the character page and flash the game UI
- a builder checks ``is_loading()`` first and sends a ``loading`` view, with the rows it
  had, because pointers are stale during a zone transition and reads are slow or produce
  garbage; this card keeps the zone it drew last
- each read is wrapped in ``wait_for(CLIENT_REFRESH_TIMEOUT)``
- Streamer Mode is this card's: :func:`player_name_text` routes through
  ``streamer_mode.display_name``
- **no wizard name, and no ``m_nameKeys``, is ever logged** (core's ``CLAUDE.md``, *No
  real player data*)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

import pyperclip
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bartleby.qt import streamer_mode
from bartleby.qt.sidebar.sidebar_section import field_label
from bartleby.qt.theming.account_accent import account_accent, account_color_keys
from bartleby.qt.theming.icon_utils import svg_pixmap
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.cards.card_frame import CardFrame, CardMode, CardVerb
from bartleby.qt.widgets.labels.elided_label import ElidedLabel
from bartleby.qt.widgets.pills.pill import AccentPill, NeutralPill, WarningPill
from bartleby.qt.widgets.pills.player_badge import PlayerBadge
from bartleby.runtime.views import BADGE_ACCENT, BADGE_WARNING, CardBadge, PlayerRow, PlayerView
from bartleby.shared.product import current

#: What every row reads before anything is known, and the one value nothing will copy.
PLACEHOLDER = "—"

#: Width of the account stripe down a card's left edge. The same number as BartleBot's launch
#: cards' ``STRIPE_WIDTH``, and deliberately not imported from them: a sidebar module
#: reaching into ``game_embed`` for a constant is the coupling ``CardFrame`` was lifted
#: out to avoid. If the two ever have to move together they belong in the theme.
STRIPE_WIDTH = 3

#: How long the copy glyph stays a checkmark. Long enough to be seen by someone who was
#: looking at the value rather than at the glyph, short enough that a second copy of a
#: different row is not read as the first one still confirming.
COPY_CONFIRM_MS = 1200

#: Side of the copy glyph, and of the checkmark that replaces it.
GLYPH_PX = 16

#: The Zone row's caption, and the key :attr:`PlayerCard.rows` holds it under.
ZONE = "Zone"

#: The header's name, as a key of :attr:`PlayerCard.rows`.
WIZARD = "Wizard"


# ---------------------------------------------------------------------------
# Pure helpers. Separated from the widgets so they can be tested without a
# QApplication, as core's CLAUDE.md requires of anything inside a QWidget subclass.
# ---------------------------------------------------------------------------


def account_stripe_keys(usernames: list[str | None], accounts: list[dict]) -> list[int | None]:
    """A palette slot per client, given the account each was launched under.

    *usernames* is what ``ClientManager.account_for`` said about each client in display
    order, ``None`` for one it could not name. The answer keeps that ``None``: an
    unidentified window gets **no stripe** rather than a default color, because a color
    on this card is a claim about which account is running and there is nothing to claim.

    Resolved through :func:`account_color_keys` over the *whole* account list rather than
    per username, because a color is a property of the list -- see account_accent.py.
    A username no longer in that list also falls through to ``None``: an account that has
    been deleted has no slot, and picking one for it would put a color on the card that
    matches nothing anywhere else on screen.
    """
    keys = account_color_keys(accounts)
    return [None if username is None else keys.get(username) for username in usernames]


def player_name_text(cached_name: str | None, index: int) -> str:
    """What the card's header reads, honoring Streamer Mode.

    Streamer Mode substitutes "Character N" rather than blanking the header: the card's
    pill already says which player this is, so the substitute is redundant -- but blanking
    it would read as "not detected yet", which is a real state the user needs to be able
    to see. Redundant beats ambiguous.
    """
    return streamer_mode.display_name(cached_name, index, placeholder=PLACEHOLDER)


def player_pill_text(index: int) -> str:
    """The ordinal on a hooked player's card: ``P1`` for the client at index 0."""
    return f"P{index + 1}"


class PlayerVerb(NamedTuple):
    """One of a product's own verbs on a player card: what :attr:`PlayerView.verbs` names.

    A product describes each verb once, to the column, and each view says which of them its
    card offers by *id*. A click is ``PlayerCard.verb_triggered(hwnd, id)``, which the
    product answers where its client is. *on_face* and the rest mean what they do on a
    ``CardVerb``.
    """

    id: str
    label: str
    icon: str
    tooltip: str
    on_face: bool = True


# ---------------------------------------------------------------------------
# The copy affordance
# ---------------------------------------------------------------------------


class CopyValue(ThemedMixin, QWidget):
    """A live value that copies itself when clicked, with a glyph at the end of the row.

    The whole widget is the hit target, not the glyph: the value is the longest thing on
    the row and the easiest to aim at, and making the 16px mark the only target would be
    the icon-ification that shrinks what it was meant to declutter. The glyph is the
    *announcement* -- it says the row is copyable and, for a second after a copy, that the
    copy happened.

    A row showing the placeholder is not copyable. Nothing is written to the clipboard, no
    confirmation is shown and the cursor stays an arrow: an em-dash pasted into a bot
    param is a silently wrong value, and a confirmation for it would be a lie.

    Display text and clipboard text are set separately, because for some rows they
    genuinely differ -- BartleBot's XYZ row reads at one decimal and copies as JSON.
    """

    #: ``objectName``, so the hover rule below lands on this widget and not on its labels.
    OBJECT_NAME = "CopyValue"

    def __init__(
        self,
        tooltip: str,
        *,
        bold: bool = False,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tooltip = tooltip
        self._copy_text = ""
        self._hovered = False
        self._confirming = False

        self._value = ElidedLabel(PLACEHOLDER, self, elide_mode=elide_mode)
        if bold:
            font = self._value.font()
            font.setBold(True)
            self._value.setFont(font)
        # Transparent to the mouse, so a press anywhere across the row -- label included --
        # reaches this widget's own handler rather than being eaten by the child under it.
        self._value.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._glyph = QLabel(self)
        self._glyph.setFixedSize(GLYPH_PX, GLYPH_PX)
        self._glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(3, 1, 3, 1)
        row.setSpacing(4)
        row.addWidget(self._value, 1)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        # Parented to this widget, so it dies with it: a bare ``singleShot`` would fire
        # into a deleted label on a card the refresh loop rebuilt inside the second.
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.setInterval(COPY_CONFIRM_MS)
        self._reset.timeout.connect(self._stop_confirming)

        self._apply_affordance()
        self.refresh_theme_styles()

    # -- state ---------------------------------------------------------

    def set_value(self, display: str, copy_text: str | None = None) -> None:
        """Show *display*, and copy *copy_text* -- which defaults to the same string."""
        self._value.setText(display)
        self._copy_text = display if copy_text is None else copy_text
        self._apply_affordance()

    def set_tooltip(self, tooltip: str) -> None:
        """Say what a click copies, shown while there is a value to copy."""
        if tooltip == self._tooltip:
            return
        self._tooltip = tooltip
        self._apply_affordance()

    @property
    def display_text(self) -> str:
        """What the row currently reads, untruncated."""
        return self._value.full_text()

    @property
    def copy_text(self) -> str:
        """What a click would put on the clipboard."""
        return self._copy_text

    @property
    def copyable(self) -> bool:
        """Whether there is a real value here, as opposed to the placeholder."""
        return bool(self._copy_text) and self._copy_text != PLACEHOLDER

    def copy(self) -> bool:
        """Put the value on the clipboard and confirm it. False if there was nothing to copy.

        Public because it is the whole behavior of this widget, and a test asserting that
        a click copies the value the row displays should be able to ask for the copy
        without synthesising a mouse.
        """
        if not self.copyable:
            return False
        pyperclip.copy(self._copy_text)
        self._confirming = True
        self._reset.start()
        self._apply_affordance()
        return True

    @property
    def confirming(self) -> bool:
        """Whether the glyph is currently showing the checkmark rather than the copy mark."""
        return self._confirming

    def _stop_confirming(self) -> None:
        self._confirming = False
        self._apply_affordance()

    def _apply_affordance(self) -> None:
        """Re-derive everything that depends on *is there a value* and *did we just copy*."""
        copyable = self.copyable
        self.setCursor(Qt.CursorShape.PointingHandCursor if copyable else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self._tooltip if copyable else "")
        # Hidden rather than blank on an uncopyable row: a mark saying "click to copy"
        # over a row that will not copy is the affordance lying about itself.
        self._glyph.setVisible(copyable)
        self._glyph.setPixmap(
            svg_pixmap(
                "checkmark" if self._confirming else "copy",
                GLYPH_PX,
                tint=THEME.accent if self._confirming else THEME.icon_tint,
            )
        )
        self.refresh_theme_styles()

    # -- painting ------------------------------------------------------

    def apply_theme_styles(self) -> None:
        """The row's own fill, and the value's color.

        Hover is state rather than a ``:hover`` pseudo-class, the same finding as
        ``CardFrame``: this pass colors the fill and the label together, and a
        pseudo-class would restyle one of them and leave the other in the old palette
        after a theme switch.
        """
        lit = self._hovered and self.copyable
        background = THEME.node_fill if lit else "transparent"
        self.setStyleSheet(f"#{self.OBJECT_NAME} {{ background: {background}; border-radius: 3px; }}")
        self._value.setStyleSheet(
            f"color: {THEME.text if self.copyable else THEME.text_subtle}; background: transparent;"
        )

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Copy on release, and consume the click either way.

        On release rather than on press for the same reason the card is -- and consumed
        rather than passed up regardless of whether anything was copied, so that a press
        aimed at a row that happens to be showing the placeholder does not become a press
        on the card behind it.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.copy()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def _badge_pill(badge: CardBadge, parent: QWidget) -> QLabel:
    """*badge* as one of core's pills: the accent for a claim, warning for a fault, gray otherwise."""
    if badge.kind == BADGE_ACCENT:
        pill: QLabel = AccentPill(badge.text, parent)
    elif badge.kind == BADGE_WARNING:
        pill = WarningPill(badge.text, parent)
    else:
        pill = NeutralPill(badge.text, parent)
    pill.setToolTip(badge.tooltip)
    return pill


class PlayerCard(CardFrame):
    """One game client: who it is, where it is, and what you can do to it.

    Built for one bucket and one only. A hooked client's card carries a ``P{n}`` pill, a
    grip and an Unhook verb; an unhooked one carries none of the first two and a Rehook
    verb instead. Nothing turns one into the other in place, because the grip is decided
    at construction and because the two live in different containers: ``PlayerColumn``
    rebuilds a card that changes bucket and re-uses one that has not.

    The product's verbs are fixed at construction too, from the first view's ``verbs``,
    since a face button is built once. A view naming other verbs is a new card, which the
    column makes.

    A subclass adds what a view cannot carry: :meth:`_header_extras` puts widgets on the
    header, before the face verbs, and :meth:`_build_extras` adds to the body, under every
    row. Both are called during construction, before the first view is shown.
    """

    #: Its own name, so the frame rules written here cannot collide with ``#LaunchCard``.
    FRAME_NAME = "PlayerCard"

    unhook_requested = pyqtSignal(int)  # index of an active client
    rehook_requested = pyqtSignal(int)  # HWND of an unhooked client
    verb_triggered = pyqtSignal(int, str)  # HWND, the product verb's id

    def __init__(
        self,
        view: PlayerView,
        verbs: Mapping[str, PlayerVerb] | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        # MEMBER, not MANAGE -- see the module docstring. The two agree on everything the Players tab card rework
        # named and disagree about whether the body is a button, which this card's is not.
        super().__init__(parent, mode=CardMode.MEMBER)
        self._view = view
        self._hwnd = view.hwnd
        self._index = view.slot - 1
        self._is_unhooked = view.unhooked
        self._product_verbs = [verbs[v] for v in view.verbs if verbs is not None and v in verbs]
        self._stripe_key: int | None = None
        self._last_zone = PLACEHOLDER
        self._rows: dict[str, CopyValue] = {}
        self._badge_pills: list[QLabel] = []
        self._shown_badges: tuple[CardBadge, ...] = ()
        self._build()
        self.show_view(view)
        self.refresh_theme_styles()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # An unhooked card has no grip: the client manager's ``clients`` is hooked-only and
        # contiguous, so there is no order here for a drag to land in. It keeps the
        # gutter's indent so its frame lines up with the hooked cards above it.
        layout = self._build_frame("players", draggable=not self._is_unhooked)
        self._layout = layout

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # Ahead of the name, where an ordinal belongs, and only on a hooked card: an
        # unhooked client is not Player anything until it is hooked back in. The same
        # account-colored pill BartleBot's Combat tab's deck card for this slot wears (#40).
        self._pill = PlayerBadge(self._index + 1) if not self._is_unhooked else None
        if self._pill is not None:
            header.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self._name = CopyValue("Click to copy this wizard's name", bold=True, parent=self)
        header.addWidget(self._name, 1)
        # Where the view's badges go, between the name and the verbs.
        self._badge_row = QHBoxLayout()
        self._badge_row.setContentsMargins(0, 0, 0, 0)
        self._badge_row.setSpacing(4)
        header.addLayout(self._badge_row)
        for widget in self._header_extras():
            header.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._add_face_verbs(header)
        layout.addLayout(header)

        # Zone is elided from the LEFT: a zone path's tail (…/WC_Streets/Interior) is what
        # distinguishes it, and the prefix is shared by everything in the world.
        self._zone = self._data_row(ZONE, "Click to copy this zone name", Qt.TextElideMode.ElideLeft)
        self._build_extras(layout)

    def _data_row(
        self,
        caption: str,
        tooltip: str,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> CopyValue:
        """One ``Caption  <value>  [glyph]`` row, and the copy affordance that is its value.

        Inserted after the rows already there, so a row a later view adds still sits above
        whatever :meth:`_build_extras` put under them.
        """
        value = CopyValue(tooltip, elide_mode=elide_mode, parent=self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(field_label(caption), 0)
        row.addWidget(value, 1)
        # One for the header, then one per row before this one.
        self._layout.insertLayout(1 + len(self._rows), row)
        self._rows[caption] = value
        return value

    def _header_extras(self) -> list[QWidget]:
        """Widgets a subclass puts on the header, after the badges and before the face verbs."""
        return []

    def _build_extras(self, layout: QVBoxLayout) -> None:
        """Add a subclass's own part of the body to *layout*, under every row."""

    def _verbs(self) -> list[CardVerb]:
        """The product's verbs, then the hook toggle.

        The product's verbs are a ``PlayerVerb`` each, and run by naming themselves on
        ``verb_triggered``. BartleBot's are its Entities and UI Tree tools, which need the
        client behind the card, and it answers them where that client is. They are
        pinned open on the face, since this surface exists to run them; the tooltips carry
        the full names the labels could not.

        The hook verb is fixed at construction rather than toggled, because a card is
        built for one bucket -- see the class docstring.
        """
        app = current().name
        if self._is_unhooked:
            hook = CardVerb(
                "Rehook",
                "hook",
                f"Re-hook this client into {app}",
                lambda: self.rehook_requested.emit(self._hwnd),
                on_face=True,
            )
        else:
            hook = CardVerb(
                "Unhook",
                "unhook",
                "Unhook this client (leaves the game window open)",
                lambda: self.unhook_requested.emit(self._index),
                on_face=True,
            )
        product = [
            CardVerb(
                verb.label,
                verb.icon,
                verb.tooltip,
                # ``*_`` takes a button's ``clicked(bool)``, which would otherwise fill the default.
                lambda *_, verb_id=verb.id: self.verb_triggered.emit(self._hwnd, verb_id),
                on_face=verb.on_face,
            )
            for verb in self._product_verbs
        ]
        return [*product, hook]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_view(self, view: PlayerView) -> None:
        """Draw *view*: the client's place, color, name, zone, rows and badges.

        A ``loading`` view keeps the zone drawn last, since a client between zones has no
        zone worth reading. Everything else lands: a builder that reads the game sends the
        rows it had, and what a product knows without reading it, such as BartleBuddy's
        holder, goes on changing while the zone loads.
        """
        self._view = view
        self.set_index(view.slot - 1)
        self.set_color_key(view.color_key)
        if self._pill is not None:
            self._pill.set_account_label(view.account_label)
        self._name.set_value(player_name_text(view.wizard_name, self._index))
        self._show_badges(view.badges)
        for row in view.rows:
            self._show_row(row)
        if view.loading:
            return
        zone = view.zone or PLACEHOLDER
        self._last_zone = zone
        self._zone.set_value(zone)

    def _show_row(self, row: PlayerRow) -> None:
        value = self._rows.get(row.label)
        if value is None:
            elide = Qt.TextElideMode.ElideLeft if row.elide_left else Qt.TextElideMode.ElideRight
            value = self._data_row(row.label, row.tooltip, elide)
        value.set_tooltip(row.tooltip)
        value.set_value(row.value, row.copy_text)

    def _show_badges(self, badges: tuple[CardBadge, ...]) -> None:
        """Rebuild the header's badges only when they changed, since every tick sends them."""
        if badges == self._shown_badges:
            return
        self._shown_badges = badges
        for pill in self._badge_pills:
            pill.setParent(None)
            pill.deleteLater()
        self._badge_pills = [_badge_pill(badge, self) for badge in badges]
        for pill in self._badge_pills:
            self._badge_row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)

    @property
    def view(self) -> PlayerView:
        """The view this card last drew."""
        return self._view

    @property
    def hwnd(self) -> int:
        """The game window this card is keyed by, across a rebuild of the column."""
        return self._hwnd

    @property
    def verb_ids(self) -> tuple[str, ...]:
        """The ids of the product verbs this card was built with."""
        return tuple(verb.id for verb in self._product_verbs)

    @property
    def is_unhooked(self) -> bool:
        return self._is_unhooked

    @property
    def index(self) -> int:
        """This card's position in its own list -- the player number, less one."""
        return self._index

    @property
    def last_zone(self) -> str:
        """Most recent zone name shown, or the placeholder if unknown."""
        return self._last_zone

    @property
    def color_key(self) -> int | None:
        """The account palette slot this card is striped with, or None for no stripe."""
        return self._stripe_key

    @property
    def pill_text(self) -> str | None:
        """The ordinal on this card's header, or None on an unhooked one, which has none."""
        return None if self._pill is None else self._pill.text()

    @property
    def badge_texts(self) -> list[str]:
        """The words on this card's badges, in header order."""
        return [pill.text() for pill in self._badge_pills]

    @property
    def rows(self) -> dict[str, CopyValue]:
        """This card's copy affordances, by the caption each sits behind: the name, Zone, then the view's rows.

        Keyed by caption rather than handed back as a list, because which row is which is
        the whole question a caller has -- "does the zone row copy the zone" -- and a
        position is a worse way to ask it than the word already printed beside the value.
        """
        return {WIZARD: self._name, **self._rows}

    def set_index(self, index: int) -> None:
        """Re-number the card after a reorder, without rebuilding it.

        Called rather than the card being replaced, because everything else on it is live:
        rebuilding to change a pill would reset what a subclass keeps (BartleBot's freecam
        toggle) and blank every row until the next tick. The index is read back by the
        Unhook verb, so it has to move with the pill and not merely beside it.
        """
        if index == self._index:
            return
        self._index = index
        if self._pill is not None:
            self._pill.set_player(index + 1)

    def set_color_key(self, key: int | None) -> None:
        """Stripe the card's left edge with an account's color, or None to drop the stripe.

        Arrives in every view rather than being resolved once: ``account_for`` learns an
        identity from inside the client manager's loop and signals nothing when it does,
        so a card colored in its constructor is one that was built before the answer
        existed and never asks again.

        The ``P{n}`` pill takes the same key, so the pill and the stripe cannot disagree.
        """
        if self._pill is not None:
            self._pill.set_color_key(key)
        if key == self._stripe_key:
            return
        self._stripe_key = key
        self.refresh_theme_styles()

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """Lock the Unhook verb and the drag grip, or let them go; *reason* is the grip's tooltip.

        For whatever must not have its clients pulled out or reordered under it. In
        BartleBot that is a bot run: unhooking mid-run pulls a client out from under the
        FSM, and reordering mid-run changes which client a node's player index means while
        that node is running. Its client manager refuses a reorder then too; this is what
        stops the gesture being offered. An unhooked card is not touched: rehooking is
        how a client that dropped out is brought back, and it has no grip.
        """
        if self._is_unhooked:
            return
        button = self.action_button("Unhook")
        if button is not None:
            button.setEnabled(not locked)
        if self.grip is not None:
            self.grip.set_locked(locked, reason)

    # ------------------------------------------------------------------
    # The theme pass
    # ------------------------------------------------------------------

    def _frame_extra_qss(self) -> str:
        """The account stripe down the card's left edge, or "" for a client with no account.

        Handed to the frame's rule rather than written into one, so that every palette
        keeps it without having to remember to -- see ``CardFrame._apply_frame_palette``.
        """
        if self._stripe_key is None:
            return ""
        return f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._stripe_key)};"

    def _apply_state_palette(self) -> bool:
        """Sit an unhooked card back on the disabled surface, keeping its stripe.

        Grayed, but not by the launch cards' rule: this state is not a refusal. The client
        is still a real client on a real account and its card still says which, so the
        stripe survives -- what has changed is that nothing on it is live any more. The
        fill and the captions both move, because either alone reads as enabled
        (BartleBot's tests/gui/theming/test_disabled_affordance.py).
        """
        if not self._is_unhooked:
            return False
        self._apply_frame_palette(THEME.surface_disabled, THEME.border)
        return True

"""GameView: tiled display of embedded Wizard101 game windows.

Moved from BartleBot's ``gui/game_embed/game_view.py`` (Stage 9 of BartleBot #167). What it
read of BartleBot is handed in: a ``GameViewSettings`` for the accounts, the launch profiles,
the recent launches and the resolution; and the product's name and version, and the color
of its logo disc, from the ``Product`` in use.

Its tiles are BartleBot's equal grid unless a product hands it a ``tile_layouts.Layout``
(:meth:`GameView.set_layout`), as BartleBuddy does: then the Main tile goes to the wizard
the user plays and the rest sit Side in a strip. Placed by rung, each game can follow its
tile's size: this says what each should be (:attr:`GameView.game_size_wanted`) and embeds
it again when told how that went (:meth:`GameView.game_resized`).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
from collections.abc import Iterable, Sequence
from typing import Protocol

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartleby.qt.game_embed.card_columns import (
    ACCOUNTS_TITLE,
    ADD_ACCOUNT,
    EMPTY_ACCOUNTS_HINT,
    EMPTY_PROFILES_HINT,
    CardColumn,
)
from bartleby.qt.game_embed.client_tile import ClientTile, DetachedTile, PendingTile, ScaledTile
from bartleby.qt.game_embed.launch_cards import AccountCard, ProfileCard, launch_note_for, refresh_column_fit
from bartleby.qt.logo import logo_disc_image
from bartleby.qt.theming.account_accent import account_color_keys
from bartleby.qt.theming.icon_utils import svg_icon
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.buttons.link_button import LinkButton
from bartleby.qt.widgets.labels.logo_disc import LogoDisc
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.runtime.clients.tile_slots import TileKey, TileSlot, plan_tile_changes
from bartleby.runtime.game import tile_layouts
from bartleby.runtime.game.game_sizing import (
    Tiles,
    aspect_family,
    required_game_area,
    rung_tiles,
    tile_targets,
)
from bartleby.runtime.game.tile_layouts import Layout, TileSize
from bartleby.runtime.timing import RESIZE_SETTLE
from bartleby.shared import game_dpi, product

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
#: ``ShowWindow``'s command for a minimized window back at its place and size.
_SW_RESTORE = 9
# How long a swap's rings stay up once its last refit is in (ClientTile.show_ring), so each
# game has drawn a frame at its new size. What the probe ran (BartleBuddy #23): 150 ms
# cleared the white frame left at the lift no better (BartleBuddy #24), and nothing shorter
# was tried.
RING_LIFT_MS = 60

_OVERLAY_BTN_SIZE = 34
_OVERLAY_BTN_MARGIN = 4

_LOGO_SIZE = 160  # diameter of the logo disc

# How wide "Launch <name>" may draw before the name is elided, and how wide the line under
# the button wraps at. The center column is as wide as either card column, and a long
# profile name must not push it wider.
_LAUNCH_LABEL_WIDTH = 220
_LAUNCH_NOTE_WIDTH = 240


class GameViewSettings(Protocol):
    """What the game view and its launch screen read of a product's settings."""

    @property
    def accounts(self) -> list[dict]:
        """The vendor's accounts, in launch order."""
        ...

    @property
    def launch_profiles(self) -> list[dict]:
        """The vendor's launch profiles."""
        ...

    @property
    def recent_launches(self) -> list[dict]:
        """``{type, id}`` per recent launch, newest first: what the launch screen preselects."""
        ...

    @property
    def game_resolution(self) -> tuple[int, int]:
        """The resolution a launch that names none opens at."""
        ...


class _GamePlaceholder(QWidget):
    """Placeholder shown in GameView when no clients are embedded.

    Three-column layout:
        [Accounts column] | [Name + version, logo, Launch, settings link] | [Launch Profiles column]

    The two outer columns are ``CardColumn``s -- the same widget the Game settings tab is
    built from, so the surface that launches an account and the surface that edits one
    draw it identically. Everything a column does, the drag that reorders it
    included, lives there; this class supplies the cards and decides what a click on one
    means.

    SELECT, THEN LAUNCH

    A click on a card selects it and the Launch button launches the selection (#266);
    ``card_frame.py``'s click model has why. Five things make that cheap:

    - **One selection across both columns.** Two would leave "what does Launch do?" with
      two answers.
    - **The last launch is preselected**, read from ``recent_launches``, so a returning
      user launches in one click -- the button -- as they did when the card was the button.
    - **The button names what it launches**, so a selection the user did not notice is
      caught before it costs anything.
    - **A double-click on a card, or Enter, launches** without reaching for the button.
      The arrow keys move the selection: up and down within a column, left and right
      across.
    - **A card that will not fit can be selected**, and the button then says why it is off,
      rather than the card ignoring the click.
    """

    launch_account_requested = pyqtSignal(str, str)
    launch_profile_requested = pyqtSignal(str)  # (profile_name)
    # The card actions and the column reorders. Forwarded rather than acted
    # on: what an edit, a delete or a reorder has to refresh afterwards -- the Launch
    # menu, the window minimum, these cards -- is the main window's business and not this
    # widget's.
    edit_account_requested = pyqtSignal(str)  # (username)
    delete_account_requested = pyqtSignal(str)  # (username)
    edit_profile_requested = pyqtSignal(str)  # (profile_name)
    duplicate_profile_requested = pyqtSignal(str)  # (profile_name)
    delete_profile_requested = pyqtSignal(str)  # (profile_name)
    add_account_requested = pyqtSignal()
    add_profile_requested = pyqtSignal()
    reorder_accounts_requested = pyqtSignal(list)  # (old indices, in their new order)
    reorder_profiles_requested = pyqtSignal(list)  # (old indices, in their new order)
    manage_accounts_requested = pyqtSignal()

    def __init__(
        self,
        settings: GameViewSettings,
        parent=None,
        *,
        editing: bool = True,
        empty_accounts_hint: str = EMPTY_ACCOUNTS_HINT,
    ) -> None:
        """*editing* False draws the cards with no verbs and the columns with no Add card or
        settings link: a product that edits no account itself (BartleBuddy, until its own
        Settings) offers nothing it cannot do. *empty_accounts_hint* is what an empty
        Accounts column says instead.
        """
        super().__init__(parent)
        self._settings = settings
        self._editing = editing
        self._empty_accounts_hint = empty_accounts_hint
        # Set before _build_ui: the constructor's own refresh() reads it.
        self._roster_source = None
        # The selected card's ``launch_key``, held as a key rather than as the card: every
        # refresh replaces the cards, and the selection has to survive that.
        self._selected_key: tuple[str, str] | None = None
        # So a click on the blank space between the columns takes focus from wherever it
        # was, and the arrow keys and Enter then reach the shortcuts below.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._build_ui()
        self._build_shortcuts()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # Title
        title = ThemedTextLabel("Quick Launch")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPixelSize(THEME.font_heading_px)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        # Three-column body
        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        # -- Left: Accounts --------------------------------------------
        editing = self._editing
        self._accounts_col = CardColumn(
            ACCOUNTS_TITLE, self._empty_accounts_hint, ADD_ACCOUNT if editing else "", show_settings_link=editing
        )
        self._accounts_col.add_requested.connect(self.add_account_requested)
        self._accounts_col.reordered.connect(self.reorder_accounts_requested)
        self._accounts_col.settings_requested.connect(self.manage_accounts_requested)
        body.addWidget(self._accounts_col, stretch=1)

        # -- Center: Logo + links --------------------------------------
        center = QVBoxLayout()
        center.setSpacing(8)
        center.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        center.addStretch(1)

        # The app's name and this build's version, over the logo, so a screenshot of the
        # launch screen says which build it came from.
        running = product.current()
        name = ThemedTextLabel(running.name, color="text")
        name.setFont(THEME.ui_font(THEME.font_heading_px + 7, bold=True))
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center.addWidget(name, alignment=Qt.AlignmentFlag.AlignHCenter)
        version = ThemedTextLabel(f"v{running.version}", css=f"font-size: {THEME.font_small_px}px;")
        version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center.addWidget(version, alignment=Qt.AlignmentFlag.AlignHCenter)
        center.addSpacing(8)

        logo = LogoDisc(_LOGO_SIZE, logo_disc_image)
        logo_wrapper = QHBoxLayout()
        logo_wrapper.setContentsMargins(0, 0, 0, 0)
        logo_wrapper.addStretch()
        logo_wrapper.addWidget(logo)
        logo_wrapper.addStretch()
        center.addLayout(logo_wrapper)

        center.addSpacing(16)
        self._launch_btn = ActionButton("play", "Launch", style=ActionStyle.PRIMARY, fill=False)
        self._launch_btn.clicked.connect(self.launch_selected)
        center.addWidget(self._launch_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        # What the button cannot say on its face: that nothing is selected yet, or why the
        # selection will not launch.
        self._launch_note = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        self._launch_note.setWordWrap(True)
        self._launch_note.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # A fixed width, and the height it needs at that width set as its minimum on every
        # change of text (``_set_launch_note``). Height-for-width does not survive the trip
        # through the body's nested box layouts, so left to them the label got one line's
        # height and drew a two-line note cut off mid-sentence.
        self._launch_note.setFixedWidth(_LAUNCH_NOTE_WIDTH)
        center.addWidget(self._launch_note, alignment=Qt.AlignmentFlag.AlignHCenter)

        center.addStretch(1)

        self._manage_btn = LinkButton("Game settings")
        self._manage_btn.clicked.connect(self.manage_accounts_requested)
        if not editing:
            # Hidden, never shown: a show before the layout parents it is a window of its own.
            self._manage_btn.hide()
        center.addWidget(self._manage_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        body.addLayout(center, stretch=1)

        # -- Right: Launch Profiles ------------------------------------
        self._profiles_col = CardColumn(
            "Launch profiles", EMPTY_PROFILES_HINT, "Add launch profile" if editing else "", show_settings_link=editing
        )
        self._profiles_col.add_requested.connect(self.add_profile_requested)
        self._profiles_col.reordered.connect(self.reorder_profiles_requested)
        self._profiles_col.settings_requested.connect(self.manage_accounts_requested)
        body.addWidget(self._profiles_col, stretch=1)

    def _build_shortcuts(self) -> None:
        """Enter launches the selection; the arrow keys move it.

        Shortcuts on this widget rather than a key handler, because the focus is usually
        on a card, inside a scroll area whose own key handler would take the arrows to
        scroll with. Scoped to this widget and its children, so they do nothing while
        the keyboard is anywhere else in the window.
        """
        keys = [
            (Qt.Key.Key_Return, self.launch_selected),
            (Qt.Key.Key_Enter, self.launch_selected),
            (Qt.Key.Key_Up, lambda: self.move_selection(-1)),
            (Qt.Key.Key_Down, lambda: self.move_selection(1)),
            (Qt.Key.Key_Left, lambda: self.select_in_column(0)),
            (Qt.Key.Key_Right, lambda: self.select_in_column(1)),
        ]
        for key, slot in keys:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def launch_button(self) -> ActionButton:
        return self._launch_btn

    @property
    def launch_note(self) -> str:
        """What the line under the Launch button says -- "" while it is hidden."""
        return self._launch_note.text() if not self._launch_note.isHidden() else ""

    @property
    def selected_card(self) -> AccountCard | ProfileCard | None:
        """The card the Launch button would launch, or None."""
        return next((card for card in self._launch_cards() if card.launch_key == self._selected_key), None)

    def select(self, card: AccountCard | ProfileCard | None) -> None:
        """Make *card* the selection, in whichever column it is, or clear it."""
        self._selected_key = None if card is None else card.launch_key
        self._show_selection()

    def launch_selected(self) -> None:
        """Launch the selected card, as the Launch button does. Nothing if it cannot."""
        card = self.selected_card
        if card is not None:
            card.activate()

    @property
    def accounts_column(self) -> CardColumn:
        """The Accounts column, for the fit pass and for anyone measuring what it drew."""
        return self._accounts_col

    @property
    def profiles_column(self) -> CardColumn:
        """The Launch Profiles column."""
        return self._profiles_col

    def set_roster_source(self, source) -> None:
        """Supply the callable that answers ``characters_for(username)``.

        Injected rather than imported because the rosters are cached on ClientManager,
        which this widget does not own and must not construct. Without it the account
        cards simply show no characters, which is also the correct display for an
        install that has never launched anything.
        """
        self._roster_source = source
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the account and profile cards from current settings.

        Each card is asked whether what it would launch fits this screen, and grayed out
        with the reason if it does not -- see bartleby/qt/screen_fit.py for why an
        unfittable launch is refused rather than warned about. The screen is only known
        once the widget is on one, so a card built before the window is shown is measured
        against the primary screen and re-measured on the way back in, which showEvent
        already triggers.
        """
        sm = self._settings
        accounts = sm.accounts

        account_cards: list[QWidget] = []
        for account in accounts:
            card = AccountCard(
                account, self._characters_for(account.get("username", "")), accounts, editable=self._editing
            )
            card.picked.connect(lambda c=card: self.select(c))
            card.launch_account_requested.connect(self.launch_account_requested)
            card.edit_account_requested.connect(self.edit_account_requested)
            card.delete_account_requested.connect(self.delete_account_requested)
            account_cards.append(card)
        self._accounts_col.set_cards(account_cards)

        profile_cards: list[QWidget] = []
        for profile in sm.launch_profiles:
            card = ProfileCard(profile, accounts, editable=self._editing)
            card.picked.connect(lambda c=card: self.select(c))
            card.launch_profile_requested.connect(self.launch_profile_requested)
            card.edit_profile_requested.connect(self.edit_profile_requested)
            card.duplicate_profile_requested.connect(self.duplicate_profile_requested)
            card.delete_profile_requested.connect(self.delete_profile_requested)
            profile_cards.append(card)
        self._profiles_col.set_cards(profile_cards)

        # The selection outlives the cards it was made on. When its card is gone -- deleted,
        # renamed, or never there on a first build -- the most recent launch that is still
        # on screen takes its place.
        keys = {card.launch_key for card in self._launch_cards()}
        if self._selected_key not in keys:
            recent = (
                (entry.get("type"), entry.get("id"))
                for entry in sm.recent_launches
                if (entry.get("type"), entry.get("id")) in keys
            )
            self._selected_key = next(recent, None)

        self.refresh_fit()

    def refresh_fit(self) -> None:
        """Re-judge every card against the game area as it is now -- see refresh_column_fit.

        The Launch button is re-judged with them: the selected card can go from fitting to
        not fitting on a splitter drag, and the button has to follow it.
        """
        refresh_column_fit(self._accounts_col, self._profiles_col, self, self.game_area_size)
        self._show_selection()

    @property
    def game_area_size(self) -> tuple[int, int]:
        """The space the tiles would have: this widget fills the GameView exactly.

        ``GameView._recompute_geometry`` sets the placeholder to the view's full rect, so
        measuring self and measuring the game area are the same measurement.
        """
        return self.width(), self.height()

    def resizeEvent(self, event) -> None:
        """Re-judge on a splitter drag, which is what makes the answer track the layout.

        In BartleBot, sidebar widths and the canvas height are startup seeds written only
        by the settings dialog -- nothing persists a drag -- so without this the cards go on
        describing a layout the user has already moved away from.
        """
        super().resizeEvent(event)
        self.refresh_fit()

    def showEvent(self, event) -> None:
        """Rebuild on the way back in.

        The launch screen is hidden for the whole life of an embedded client, which is
        exactly when the things it describes change: characters are learned as they are
        played, and the roster is what the account cards are for. Closing the last game
        window used to reveal cards built before the session that taught them anything.
        """
        super().showEvent(event)
        self.refresh()

    def _launch_cards(self) -> list[AccountCard | ProfileCard]:
        """Every card on the screen, accounts first, in the order they are drawn."""
        return [*self._accounts_col.cards, *self._profiles_col.cards]

    def _show_selection(self) -> None:
        """Draw the selection on its card and say on the Launch button what it launches."""
        selected = None
        for card in self._launch_cards():
            card.set_selected(card.launch_key == self._selected_key)
            if card.is_selected:
                selected = card

        if selected is None:
            self._launch_btn.setText("Launch")
            self._launch_btn.setEnabled(False)
            self._launch_btn.setToolTip("")
            note = "Select an account or a launch profile to launch it" if self._launch_cards() else ""
            self._set_launch_note(note, "text_muted")
            return

        label = selected.launch_label
        text = self._launch_btn.fontMetrics().elidedText(
            f"Launch {label}", Qt.TextElideMode.ElideRight, _LAUNCH_LABEL_WIDTH
        )
        self._launch_btn.setText(text)
        refusal = selected.refusal_text
        self._launch_btn.setEnabled(not refusal)
        self._launch_btn.setToolTip(refusal or f"Launch {label} (Enter, or double-click its card)")
        self._set_launch_note(launch_note_for(selected.unavailable_reason) if refusal else "", "warning")

    def _set_launch_note(self, text: str, color: str) -> None:
        self._launch_note.setText(text)
        self._launch_note.setMinimumHeight(self._launch_note.heightForWidth(_LAUNCH_NOTE_WIDTH) if text else 0)
        self._launch_note.set_color(color)
        self._launch_note.setVisible(bool(text))

    def move_selection(self, step: int) -> None:
        """Move the selection *step* cards up or down its column, stopping at the ends.

        With nothing selected, the first card on the screen is the one taken.
        """
        columns = self._columns()
        where = self._selected_position(columns)
        if where is None:
            first = next((cards[0] for cards in columns if cards), None)
            self._select_from_keyboard(first)
            return
        column, row = where
        cards = columns[column]
        self._select_from_keyboard(cards[max(0, min(len(cards) - 1, row + step))])

    def select_in_column(self, column: int) -> None:
        """Move the selection to column *column*, at the same row or the last one above it."""
        columns = self._columns()
        cards = columns[column]
        if not cards:
            return
        where = self._selected_position(columns)
        row = 0 if where is None else where[1]
        self._select_from_keyboard(cards[min(row, len(cards) - 1)])

    def _columns(self) -> list[list]:
        return [self._accounts_col.cards, self._profiles_col.cards]

    def _selected_position(self, columns: list[list]) -> tuple[int, int] | None:
        for column, cards in enumerate(columns):
            for row, card in enumerate(cards):
                if card.launch_key == self._selected_key:
                    return column, row
        return None

    def _select_from_keyboard(self, card) -> None:
        """Select *card* from the keyboard: scrolled into view, and given the focus.

        The focus moves with it so that Alt+Up and Alt+Down, which reorder the card that
        has the focus, act on the card the user can see is selected.
        """
        if card is None:
            return
        self.select(card)
        column = self._accounts_col if card in self._accounts_col.cards else self._profiles_col
        column.scroll_area.ensureWidgetVisible(card)
        card.setFocus(Qt.FocusReason.OtherFocusReason)

    def _characters_for(self, username: str) -> list[dict]:
        """The known characters for *username*, or none if no source has been supplied."""
        if self._roster_source is None or not username:
            return []
        try:
            return self._roster_source(username)
        except Exception:
            logger.debug("Could not read the character roster for the launch screen", exc_info=True)
            return []


class GameView(ThemedMixin, QWidget):
    """Container that shows N ClientTile widgets in a grid layout.

    Tiles are positioned manually (not via a QLayout) so that we can recompute
    geometry precisely on resize without Qt interfering with the Win32-embedded
    child windows.

    *settings*, *editing* and *empty_accounts_hint* are the launch screen's; see
    ``_GamePlaceholder``.
    """

    embed_state_changed = pyqtSignal(bool)  # True = ≥1 client embedded
    launch_account_requested = pyqtSignal(str, str)
    launch_profile_requested = pyqtSignal(str)
    edit_account_requested = pyqtSignal(str)
    delete_account_requested = pyqtSignal(str)
    edit_profile_requested = pyqtSignal(str)
    duplicate_profile_requested = pyqtSignal(str)
    delete_profile_requested = pyqtSignal(str)
    add_account_requested = pyqtSignal()
    add_profile_requested = pyqtSignal()
    reorder_accounts_requested = pyqtSignal(list)
    reorder_profiles_requested = pyqtSignal(list)
    manage_accounts_requested = pyqtSignal()
    game_embed_settings_requested = pyqtSignal()
    # A failed launch's tile was clicked away. Carries the account, not the slot: a pending
    # tile's slot can still move under it, and the launch is keyed by account throughout.
    dismiss_launch_requested = pyqtSignal(str)
    # The game window holding the keyboard right now (an hwnd), or None when the app's
    # own widgets or another application have it. What arms the authoring hotkeys: they
    # are system-wide keys, and armed anywhere else they would eat F1 in every other app.
    game_focus_changed = pyqtSignal(object)
    # The game the player last gave the keyboard to (an hwnd): a click on an embedded game, a
    # detached window brought to the front, or the keyboard target given it (set_keyboard_target).
    # Unlike game_focus_changed it holds while the app's own widgets or another application
    # have the keyboard, and never names a game a refit left the keyboard on for the moment.
    player_focus_changed = pyqtSignal(object)
    # A tile's caption was clicked: the player index it is captioned from, and the point
    # under the caption, global, for a menu to open at. Nothing here acts on it.
    caption_clicked = pyqtSignal(int, QPoint)
    # A tile's role icon was clicked (set_pill_roles): the player index, and the point under
    # the icon, global, for the product's role menu. Nothing here acts on it.
    role_clicked = pyqtSignal(int, QPoint)
    # A press on a small tile's body under swap first (set_swap_first), by player index.
    body_clicked = pyqtSignal(int)
    # A window should run at another size (an hwnd, and a (width, height)), and that has held
    # for RESIZE_SETTLE. Answered through game_resized, whenever the runtime has done it.
    game_size_wanted = pyqtSignal(object, object)
    # A window has just left its tile for the desktop (an hwnd): its launch size is due back.
    window_detached = pyqtSignal(object)
    # A detached window is back in its tile (an hwnd).
    window_reattached = pyqtSignal(object)

    def __init__(
        self,
        settings: GameViewSettings,
        parent=None,
        *,
        editing: bool = True,
        empty_accounts_hint: str = EMPTY_ACCOUNTS_HINT,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._tiles: dict[int, ClientTile] = {}
        # See game_focus_changed and last_focused_hwnd.
        self._focused_hwnd: int | None = None
        self._last_focused_hwnd: int | None = None
        # See player_focus_changed, and keep_keyboard_over.
        self._player_focus: int | None = None
        self._keeps_keyboard: list[QWidget] = []
        self._detached_tiles: dict[int, DetachedTile] = {}
        # Slots holding a launch that has not produced a window yet. A third tile kind
        # rather than a flag on the other two: it has no hwnd, so almost nothing the
        # other dicts are asked for has an answer here. See PendingTile.
        self._pending_tiles: dict[int, PendingTile] = {}
        # Account per pending slot. Kept beside the widgets because it is the pending
        # tile's identity in plan_tile_changes -- a PendingTile itself only knows the
        # label it is drawn with, which is a display name and not a key.
        self._pending_accounts: dict[int, str] = {}
        # Last known wizard name per client index. Kept so a tile created later — a new
        # client, or a detach/reattach — gets its name immediately instead of waiting for
        # the name to change again, which for a stable character never happens.
        self._wizard_names: list = []
        self._detached_hwnds: dict[int, int] = {}  # slot → hwnd
        # Player index per slot, as of the last sync. The tiles are keyed by slot and the
        # wizard names arrive keyed by player, so this is the join between them; a slot
        # holding a window that is not in game maps to None. See bartleby.runtime.clients.tile_slots.
        self._tile_players: dict[int, int | None] = {}
        # Last announced embed state, so sync_tiles can stay silent on a tick that changed
        # nothing rather than re-emitting several times a second.
        self._embed_state = False
        # See set_layout: None is the equal grid, and the rest is by player index.
        self._layout: Layout | None = None
        self._played: list[bool] = []
        self._by_rung = False
        # See set_layout: the most pixels an equal grid by rung may grow a game to, or None.
        self._cap: tuple[int, int] | None = None
        # The placement order of the strip, by slot, and the slot that last had the Main tile:
        # a new Main trades places with the old one (tile_layouts.trade_places).
        self._placement: list[int] = []
        self._main_slot: int | None = None
        # The size each window was first seen at, by hwnd. Kept because a ClientTile only
        # knows the size it was built over, and a window that has been resized is not at
        # its launch size when a re-attach builds a new tile over it.
        self._launch_sizes: dict[int, tuple[int, int]] = {}
        # See set_resizable: the windows a size may be asked for, by hwnd.
        self._resizable: frozenset[int] = frozenset()
        # What has been asked for and not reached, by hwnd, so it is asked for once.
        self._asked: dict[int, tuple[int, int]] = {}
        # The area and the targets the settle timer is running for: see _note_targets.
        self._settling: tuple = ()
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(round(RESIZE_SETTLE * 1000))
        self._settle_timer.timeout.connect(self._ask_for_targets)
        # See set_keyboard_target: the game the keyboard goes to once the tiles settle, and
        # whether it is still owed one since the target changed or a refit took it away.
        self._keyboard_target: int | None = None
        self._keyboard_owed = False
        # See set_captions: a product's caption per player index, over the manager's label.
        self._captions: list[str | None] = []
        # See set_pill_roles and set_active_player, by player index as the captions are.
        self._pill_roles: list[tuple[str, str] | None] = []
        self._active_player: int | None = None
        # See set_swap_first.
        self._swap_first = False
        # See set_layout: the rings a swap showed all come off this long after it settles.
        self._lift_timer = QTimer(self)
        self._lift_timer.setSingleShot(True)
        self._lift_timer.setInterval(RING_LIFT_MS)
        self._lift_timer.timeout.connect(self._lift_rings)

        # Placeholder shown when no clients are embedded or detached
        self._placeholder = _GamePlaceholder(settings, self, editing=editing, empty_accounts_hint=empty_accounts_hint)
        self._placeholder.launch_account_requested.connect(self.launch_account_requested)
        self._placeholder.launch_profile_requested.connect(self.launch_profile_requested)
        self._placeholder.edit_account_requested.connect(self.edit_account_requested)
        self._placeholder.delete_account_requested.connect(self.delete_account_requested)
        self._placeholder.edit_profile_requested.connect(self.edit_profile_requested)
        self._placeholder.duplicate_profile_requested.connect(self.duplicate_profile_requested)
        self._placeholder.delete_profile_requested.connect(self.delete_profile_requested)
        self._placeholder.add_account_requested.connect(self.add_account_requested)
        self._placeholder.add_profile_requested.connect(self.add_profile_requested)
        self._placeholder.reorder_accounts_requested.connect(self.reorder_accounts_requested)
        self._placeholder.reorder_profiles_requested.connect(self.reorder_profiles_requested)
        self._placeholder.manage_accounts_requested.connect(self.manage_accounts_requested)
        self._placeholder.show()

        # Overlay button: detach-all (when embedded) / re-attach-all (when detached)
        self._overlay_btn = QToolButton(self)
        self._overlay_btn.setAutoRaise(True)
        self._overlay_btn.setFixedSize(_OVERLAY_BTN_SIZE, _OVERLAY_BTN_SIZE)
        self._overlay_btn.clicked.connect(self._on_overlay_clicked)
        # Drawn over the tiles, which are native: an alien widget would be painted under
        # them. Native by request, since siblings are no longer promoted (native_widgets).
        self._overlay_btn.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        # A native window needs a cursor of its own, or it borrows GameView's (#258).
        self._overlay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._overlay_btn.setVisible(False)
        self._overlay_btn.raise_()

        # Poll every 50 ms to keep each tile's focus border in step with its game window
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(50)
        self._focus_timer.timeout.connect(self._poll_focus)
        self._focus_timer.start()

        QApplication.instance().installEventFilter(self)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Re-tint the overlay button.

        svg_icon bakes THEME.icon_tint in at render time, and which of the two icons
        belongs there depends on whether the tiles are embedded or detached —
        _update_overlay already answers that, so the restyle defers to it rather than
        duplicating the rule.
        """
        self._update_overlay()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_tiles(self, tiles: list[TileSlot]) -> None:
        """Make the grid show exactly *tiles*, one entry per window that belongs on screen.

        The single entry point for everything that changes the grid: a launch, a crash, a
        recovery, a player reorder. ClientManager calls it on every detection tick and it
        is a no-op when nothing moved, which is what lets a window appearing at character
        select — an event that changes no client list, because it is not a client — still
        reach the screen.

        Slot and player are different numbers here; see bartleby.runtime.clients.tile_slots for why. A
        tile sits at its ``slot`` and is captioned from its ``player``, so a window with no
        player index is drawn under its account name rather than a number it does not have.
        """
        current: dict[int, TileKey] = {slot: ("hwnd", tile.hwnd) for slot, tile in self._tiles.items()}
        current.update({slot: ("hwnd", hwnd) for slot, hwnd in self._detached_hwnds.items()})
        current.update({slot: ("pending", name) for slot, name in self._pending_accounts.items()})
        changes = plan_tile_changes(current, tiles)

        if not changes.is_empty:
            for slot in changes.removed:
                self._destroy_tile(slot)
            self._apply_moves(changes.moved)
            for tile in changes.added:
                self._create_tile(tile)

        # Captions and names are refreshed even when nothing moved: a client hooking gives
        # a tile that is already on screen its player number for the first time.
        self._tile_players = {t.slot: t.player for t in tiles}
        # Account colors ride the same per-tick push, because a window's account is learned
        # from its title after the tile exists and nothing announces it. Resolved over the
        # whole account list, for the reason account_stripe_keys gives: a color is only
        # distinct with respect to the others. A tile with no account gets no color.
        color_keys = account_color_keys(self._settings.accounts)
        for t in tiles:
            widget = self._widget_at(t.slot)
            if widget is not None:
                widget.set_label(self._caption_for(t.player) or t.label)
                widget.set_wizard_name(self._wizard_name_for(t.player))
                widget.set_color_key(color_keys.get(t.username) if t.username else None)
            self._mark_pill(t.slot, t.player)
            # Pushed on every sync alongside the captions, and for the same reason: a
            # launch changing stage moves no tile, so the reconciler has nothing to report
            # and the only thing that changed is what the tile says.
            pending = self._pending_tiles.get(t.slot)
            if pending is not None:
                pending.set_status(t.status, t.status_since, t.failed)

        if changes.is_empty:
            return
        self._placeholder.setVisible(not self._occupied_slots())
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def _create_tile(self, spec: TileSlot) -> None:
        """Build the tile for a slot that does not have one yet, embedded or pending."""
        if spec.is_pending:
            self._create_pending_tile(spec)
            return
        if game_dpi.window_is_scaled(spec.hwnd):
            self._hold_scaled_window(spec)
            return
        tile = ClientTile(spec.hwnd, self._caption_for(spec.player) or spec.label, parent=self)
        tile.set_wizard_name(self._wizard_name_for(spec.player))
        self._tiles[spec.slot] = tile
        self._launch_sizes.setdefault(spec.hwnd, tile.game_size)
        tile.detach_requested.connect(lambda slot=spec.slot: self._detach_client(slot))
        tile.caption_clicked.connect(lambda point, slot=spec.slot: self._on_caption_clicked(slot, point))
        tile.role_clicked.connect(lambda point, slot=spec.slot: self._on_role_clicked(slot, point))
        tile.body_clicked.connect(lambda slot=spec.slot: self._on_body_clicked(slot))
        # By the tile, not the slot: a refit's finish belongs to its window wherever it moved.
        tile.refitted.connect(lambda tile=tile: self._after_refit(tile))
        tile.game_clicked.connect(lambda tile=tile: self._note_player_focus(tile.hwnd))
        self._mark_pill(spec.slot, spec.player)
        tile.show()
        self._remove_detached_tile(spec.slot)
        self._remove_pending_tile(spec.slot)

    def _hold_scaled_window(self, spec: TileSlot) -> None:
        """Leave a window Windows is scaling where it is, detached, and say why in its slot.

        Embedded, it would not fit its tile and would show nothing (see ScaledTile). Held
        as a detached window it still draws correctly, and every move and removal already
        handles a detached slot.
        """
        logger.warning("Windows is scaling the game window in slot %d, so it is left unembedded", spec.slot)
        self._remove_pending_tile(spec.slot)
        self._remove_detached_tile(spec.slot)
        self._detached_hwnds[spec.slot] = spec.hwnd
        dtile = ScaledTile(spec.label, spec.hwnd, parent=self)
        dtile.set_wizard_name(self._wizard_name_for(spec.player))
        dtile.show()
        self._detached_tiles[spec.slot] = dtile

    def _create_pending_tile(self, spec: TileSlot) -> None:
        """Put a busy tile in a slot whose account is launching but has no window yet."""
        self._remove_pending_tile(spec.slot)
        tile = PendingTile(spec.label, parent=self, resolution=spec.resolution)
        tile.set_status(spec.status, spec.status_since, spec.failed)
        self._pending_tiles[spec.slot] = tile
        self._pending_accounts[spec.slot] = spec.username or ""
        # Connected to the account rather than to the slot, unlike the detach and reattach
        # buttons: a pending tile's slot can still move under it, and the thing being
        # dismissed is the launch, which is keyed by account all the way down.
        username = spec.username or ""
        tile.dismiss_requested.connect(lambda u=username: self.dismiss_launch_requested.emit(u))
        tile.show()

    def _remove_pending_tile(self, slot: int) -> None:
        """Tear down the pending tile in *slot*, if there is one."""
        self._pending_accounts.pop(slot, None)
        tile = self._pending_tiles.pop(slot, None)
        if tile is None:
            return
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()

    def _destroy_tile(self, slot: int) -> None:
        """Tear down whatever occupies *slot*, embedded, detached or pending."""
        self._launch_sizes.pop(self._detached_hwnds.pop(slot, None), None)
        self._remove_detached_tile(slot)
        self._remove_pending_tile(slot)
        tile = self._tiles.pop(slot, None)
        if tile is None:
            return
        self._launch_sizes.pop(tile.hwnd, None)
        self._asked.pop(tile.hwnd, None)
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()

    def _apply_moves(self, moves: tuple[tuple[int, int], ...]) -> None:
        """Re-key tiles from one slot to another without touching the game windows.

        Every vacated slot is cleared before any is filled, and always against the
        *original* mapping. Doing it move by move loses a tile on a swap: clearing slot 1
        for the tile arriving from slot 0 throws away the tile that had already been
        written into slot 1 by the move out of it. Re-keying rather than rebuilding is the
        whole point — rebuilding a tile un-parents the game window and parents it again,
        which flashes.
        """
        if not moves:
            return
        vacated = {old for old, _new in moves}
        tiles = {slot: tile for slot, tile in self._tiles.items() if slot not in vacated}
        detached_tiles = {slot: tile for slot, tile in self._detached_tiles.items() if slot not in vacated}
        detached_hwnds = {slot: hwnd for slot, hwnd in self._detached_hwnds.items() if slot not in vacated}
        pending_tiles = {slot: tile for slot, tile in self._pending_tiles.items() if slot not in vacated}
        pending_accounts = {slot: name for slot, name in self._pending_accounts.items() if slot not in vacated}
        for old, new in moves:
            if old in self._tiles:
                tiles[new] = self._tiles[old]
            if old in self._detached_tiles:
                detached_tiles[new] = self._detached_tiles[old]
            if old in self._detached_hwnds:
                detached_hwnds[new] = self._detached_hwnds[old]
            if old in self._pending_tiles:
                pending_tiles[new] = self._pending_tiles[old]
            if old in self._pending_accounts:
                pending_accounts[new] = self._pending_accounts[old]
        self._tiles = tiles
        self._detached_tiles = detached_tiles
        self._detached_hwnds = detached_hwnds
        self._pending_tiles = pending_tiles
        self._pending_accounts = pending_accounts
        for slot, tile in self._tiles.items():
            self._reconnect_detach(tile, slot)
        for slot, dtile in self._detached_tiles.items():
            self._reconnect_reattach(dtile, slot)

    def _reconnect_detach(self, tile: ClientTile, slot: int) -> None:
        """Point a tile's detach icon, caption and role icon at the slot it now occupies."""
        for signal in (tile.detach_requested, tile.caption_clicked, tile.role_clicked, tile.body_clicked):
            with contextlib.suppress(TypeError):
                signal.disconnect()
        tile.detach_requested.connect(lambda s=slot: self._detach_client(s))
        tile.caption_clicked.connect(lambda point, s=slot: self._on_caption_clicked(s, point))
        tile.body_clicked.connect(lambda s=slot: self._on_body_clicked(s))
        tile.role_clicked.connect(lambda point, s=slot: self._on_role_clicked(s, point))

    def _reconnect_reattach(self, dtile: DetachedTile, slot: int) -> None:
        """Point a placeholder's attach button at the slot it now occupies."""
        with contextlib.suppress(TypeError):
            dtile.reattach_requested.disconnect()
        dtile.reattach_requested.connect(lambda s=slot: self.reattach_client(s))

    def _set_embed_state(self, embedded: bool) -> None:
        """Announce the embedded/not-embedded state, but only when it actually changed.

        sync_tiles runs on every detection tick; a signal per tick would have the main
        window relaying a layout change several times a second.
        """
        if embedded != self._embed_state:
            self._embed_state = embedded
            self.embed_state_changed.emit(embedded)

    def unembed_client(self, client_idx: int) -> None:
        """Unembed and remove the tile for *client_idx* (client closed, not detached)."""
        if client_idx not in self._tiles:
            return
        self._destroy_tile(client_idx)
        self._placeholder.setVisible(not self._occupied_slots())
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def unembed_all(self) -> None:
        """Unembed every client."""
        for idx in list(self._tiles.keys()):
            self.unembed_client(idx)

    def detach_all(self) -> None:
        """Save current HWNDs and detach all clients (windows float free, slots show placeholders)."""
        for idx, tile in list(self._tiles.items()):
            self._detached_hwnds[idx] = tile.hwnd
            self._create_detached_tile(idx, tile.label)
        # Unembed the game windows without touching _detached_tiles
        for idx in list(self._tiles.keys()):
            tile = self._tiles.pop(idx)
            self._asked.pop(tile.hwnd, None)
            tile.unembed()
            tile.hide()
            tile.setParent(None)  # type: ignore[arg-type]
            tile.deleteLater()
            self.window_detached.emit(tile.hwnd)
        self._placeholder.setVisible(False)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(False)
        self._update_overlay()

    def reattach_all(self) -> None:
        """Re-embed previously detached windows."""
        for idx in sorted(self._detached_hwnds):
            self.reattach_client(idx)

    def reattach_client(self, client_idx: int) -> None:
        """Re-embed a single previously detached window."""
        if isinstance(self._detached_tiles.get(client_idx), ScaledTile):
            # Still scaled: nothing short of restarting the game changes that.
            return
        hwnd = self._detached_hwnds.pop(client_idx, None)
        if hwnd is None:
            return
        if client_idx in self._tiles:
            return
        label = self._detached_tiles[client_idx].label if client_idx in self._detached_tiles else ""
        self._create_tile(TileSlot(slot=client_idx, hwnd=hwnd, label=label, player=self._player_for(client_idx)))
        self._placeholder.setVisible(False)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()
        self.window_reattached.emit(hwnd)

    @property
    def detached_hwnds(self) -> frozenset[int]:
        """The windows out of their tiles, on the desktop: detached by the player, or held out scaled."""
        return frozenset(self._detached_hwnds.values())

    @property
    def has_detached(self) -> bool:
        return bool(self._detached_hwnds)

    @property
    def overlay_button(self) -> QToolButton:
        """The detach-all / re-attach-all button drawn over the tiles."""
        return self._overlay_btn

    @property
    def pending_slots(self) -> dict[int, str]:
        """Slot → account, for every launch drawn here that has no window yet.

        Public because "what is this view still waiting for" is a question about the
        launch rather than about the widgets, and the only alternative is reaching into
        the tile dicts from outside.
        """
        return dict(self._pending_accounts)

    def pending_failed(self, slot: int) -> bool:
        """Whether the pending tile in *slot* is showing a launch that gave up."""
        tile = self._pending_tiles.get(slot)
        return tile is not None and tile.failed

    def pending_status(self, slot: int) -> str | None:
        """What the pending tile in *slot* currently says, or None if it is not pending.

        Public for the same reason as ``pending_slots``: the caption is a fact about the
        launch, and reading it is otherwise a reach into the tile widgets.
        """
        tile = self._pending_tiles.get(slot)
        return None if tile is None else tile.status_text

    def tile_geometry(self, slot: int):
        """The rectangle the tile in *slot* occupies, or None if that slot is empty.

        The grid is laid out by hand rather than by a QLayout — see _recompute_geometry —
        so where a tile ended up is not readable from anything Qt exposes about this
        widget, and a tile left out of the layout silently overlaps the one in slot 0.
        """
        widget = self._widget_at(slot)
        return None if widget is None else widget.geometry()

    @property
    def game_area_size(self) -> tuple[int, int]:
        """The logical pixels the tile grid actually has, right now.

        Named rather than left as ``width()``/``height()`` because this is the live
        budget a launch is judged against (see ``bartleby.qt.screen_fit.budget_for``), and
        "the size of this widget" does not say that. It is exact where the configured
        layout is an estimate: the panes, the title bar and the game panel's own header are
        already subtracted, because they are simply not in it.
        """
        return self.width(), self.height()

    @property
    def largest_game_size(self) -> tuple[int, int] | None:
        """The widest and tallest game window on screen, or None if there is none.

        Componentwise, not one tile's size: mixed-resolution fleets are supported —
        ``_group_by_resolution`` launches a batch per resolution — so the grid has to be
        measured against the largest of each axis rather than against whichever tile a
        dict happened to yield first, which understated it whenever they differed.

        Detached windows are not counted: they are not parented here, so their size
        cannot be read without going back to Win32, and a re-attach re-measures anyway.
        """
        sizes = [tile.game_size for tile in self._tiles.values()]
        if not sizes:
            return None
        return max(size[0] for size in sizes), max(size[1] for size in sizes)

    @property
    def client_count(self) -> int:
        """How many windows this view is holding a place for, embedded or detached.

        A detached window still owns its slot and comes back into the same grid, so it
        counts: the question this answers is how many tiles the layout has to fit, not
        how many are currently parented here. So does a launching one, for the same
        reason and one step earlier -- its window is coming, and the grid it lands in
        should be the grid that was already drawn for it rather than a resize on arrival.
        """
        return len(self._occupied_slots())

    def set_wizard_names(self, names: list) -> None:
        """Show each client's wizard name on its tile pill.

        *names* is positionally aligned with the live client list — the **player** index,
        not the slot the tile sits in. The two are only the same number while every
        account is in game (see bartleby.runtime.clients.tile_slots), so each tile is looked up by the
        player index it was last synced with rather than by its own key.

        A None entry clears the name rather than leaving a stale one on screen — that is
        what a character switch looks like before the new name has been read.
        """
        self._wizard_names = list(names)
        for slot, player in self._tile_players.items():
            tile = self._widget_at(slot)
            if tile is not None:
                tile.set_wizard_name(self._wizard_name_for(player))

    def set_layout(
        self,
        layout: Layout | None,
        played: Sequence[bool] = (),
        *,
        by_rung: bool = False,
        cap: tuple[int, int] | None = None,
        at_once: bool = False,
    ) -> None:
        """Place the tiles by *layout*, or as the equal grid for None.

        *played* says, per **player** index, whether the user plays that wizard, as
        ``set_wizard_names``' list is aligned: those fill the layout's Main tiles and the
        rest sit Side (``tile_layouts.sizes``). A layout that leaves no Side tile is the
        equal grid all the same, BartleBot's layout, since ``Layout.grid`` means every
        tile equal. When the Main tile changes hands, the old Main takes the new one's place
        in the strip, and the rest stay put. The equal grid is in slot order, and the strip
        comes back in the order it had.

        *by_rung* is for a product whose clients can be resized: each Side tile holds its
        whole game and each Main tile is exactly a rung (``tile_layouts.arrange``), an equal
        grid grows each game to the largest rung its tile holds, with no more pixels than
        *cap*, and ``game_targets`` says what size each client should be. A smaller Main
        with no resize behind it only costs the user room, so it stays off until one can
        follow.

        *at_once* asks for the targets now instead of after ``RESIZE_SETTLE``: for a change
        the player made in one press, a swap or a change of view, where the settle only adds
        half a second. A drag still waits, since its targets change all the way through. A
        game whose new size is asked for isn't moved at its old one meanwhile
        (``ClientTile.hold_moves``), since its refit puts it in place. Each of those tiles
        shows the ring and not its game (``ClientTile.show_ring``) from before any tile
        changes shape until every refit is in, when all the rings come off together after
        ``RING_LIFT_MS``. BartleBot never asks at once, so it shows none.
        """
        self._layout = layout
        self._played = list(played)
        self._by_rung = by_rung
        self._cap = None if cap is None else tuple(cap)
        if not by_rung:
            self._asked.clear()
        if not at_once:
            self._recompute_geometry()
            self._update_minimum_size()
            return
        tiles = list(self._tiles.values())
        for tile in tiles:
            tile.hold_moves()
        try:
            self._lift_timer.stop()
            self._show_rings_for_targets()
            self._recompute_geometry()
            self._update_minimum_size()
            self._settle_timer.stop()
            self._ask_for_targets()
        finally:
            for tile in tiles:
                tile.release_moves(refit_coming=tile.hwnd in self._asked)
        self._watch_rings()

    def _show_rings_for_targets(self) -> None:
        """Show the ring on each tile whose game is about to be asked for a new size, before any tile moves.

        The targets are worked out from where the tiles are going, as ``_ask_for_targets``
        will ask for them once they are there.
        """
        slots = self._occupied_slots()
        if not slots or not self._rung_family_in_use():
            return
        _tiles, rects, _placement, _main = self._plan_geometry(slots)
        targets = self._targets_at(slots, [(rect.w, rect.h) for rect in rects])
        for tile in self._tiles.values():
            target = targets.get(tile.hwnd)
            if tile.hwnd in self._resizable and target is not None:
                if target != self._asked.get(tile.hwnd, tile.coming_size):
                    tile.show_ring(True)

    def _watch_rings(self) -> None:
        """Start the rings' lift once the tiles have settled, and put it off while they haven't."""
        if not any(tile.ring_shown for tile in self._tiles.values()):
            self._lift_timer.stop()
        elif not self._settled():
            self._lift_timer.stop()
        elif not self._lift_timer.isActive():
            self._lift_timer.start()

    def _lift_rings(self) -> None:
        if not self._settled():
            return
        for tile in self._tiles.values():
            tile.show_ring(False)

    def _settled(self) -> bool:
        """Nothing asked for, no refit under way, and every game at its target."""
        if self._asked:
            return False
        tiles = {tile.hwnd: tile for tile in self._tiles.values()}
        if any(tile.refitting for tile in tiles.values()):
            return False
        return all(tiles[hwnd].game_size == target for hwnd, target in self._targets_to_ask_for().items())

    def set_swap_first(self, on: bool) -> None:
        """Say whether a small tile's first click only swaps, for a product with an Active wizard.

        On, each Side tile swallows clicks (``ClientTile.swallow_clicks``): a press on its
        body is ``body_clicked`` and nothing lands in its game. A Main tile and every tile of
        an equal grid let clicks through, as every tile does with it off. BartleBot never
        calls this. Saying it again changes nothing, so a product may say it on every placing.
        """
        if on == self._swap_first:
            return
        self._swap_first = on
        self._recompute_geometry()

    def set_resizable(self, hwnds: Iterable[int]) -> None:
        """Say which windows a size may be asked for: those with a runtime to do it, so the hooked.

        A window that joins is asked for afresh, since nothing was going to answer it before.
        """
        self._resizable = frozenset(hwnds)
        self._asked = {hwnd: size for hwnd, size in self._asked.items() if hwnd in self._resizable}
        self._note_targets()
        self._watch_rings()

    def game_resized(self, hwnd: int, size: tuple[int, int]) -> None:
        """A window now runs at *size*, as the runtime measured it: its tile embeds it there.

        Nothing if it has no tile any more, as a window detached mid-apply does not.
        """
        tile = next((tile for tile in self._tiles.values() if tile.hwnd == hwnd), None)
        if tile is None:
            return
        # Struck at once: the tile's coming_size stands for it until the refit is in.
        if self._asked.get(hwnd) == tuple(size):
            del self._asked[hwnd]
        if not tile.refit(*size):
            self._after_refit(tile, moved=False)

    def _after_refit(self, tile: ClientTile, *, moved: bool = True) -> None:
        """A tile has its game at the size reported: what reads ``game_size`` runs now.

        *moved* is whether the game was embedded again, which is what takes the keyboard
        from where it was (``set_keyboard_target``).
        """
        if tile not in self._tiles.values():
            return
        self._update_minimum_size()
        # A size nobody asked for, as giving back reports, may not be the one wanted now.
        if not self._settle_timer.isActive():
            self._settle_timer.start()
        if moved:
            self._keyboard_owed = True
        self._give_keyboard()
        self._watch_rings()

    def set_keyboard_target(self, hwnd: int | None) -> None:
        """Say which game the keyboard goes to once the tiles have settled, or None for none.

        A refit embeds a game again, and the keyboard is left on whichever game was embedded
        last. Once every refit is in, with nothing asked for and every game at its target,
        the target's game is given the keyboard. A new target is given it on the same terms,
        at once if nothing is moving, and not at all if its game has the keyboard already, as
        one the player just clicked does. BartleBuddy's window names the game the player is
        on: the Active wizard's, or one they clicked into since. BartleBot never calls this,
        and nothing here moves its keyboard.
        """
        if hwnd == self._keyboard_target:
            return
        self._keyboard_target = hwnd
        self._keyboard_owed = hwnd is not None and not self._has_keyboard(hwnd)
        self._give_keyboard()

    def give_keyboard_back(self) -> None:
        """Give the keyboard target's game the keyboard again, after a press on the app took it.

        For a product's buttons and menus that act on the games, so the player's keys go on
        reaching the game they were in. As after a refit, it waits until nothing is moving,
        and does nothing with no target or when the game still has the keyboard.
        """
        if self._keyboard_target is None or self._has_keyboard(self._keyboard_target):
            return
        self._keyboard_owed = True
        self._give_keyboard()

    def bring_to_front(self, hwnd: int) -> bool:
        """Bring the window the game in *hwnd* is played in to the front: its own when detached, else the app's.

        For a product's hotkey, which may be pressed in a detached game's window or in the
        app: the window the game is played in comes in front of it, restored if minimized,
        and the keyboard goes there with it. Windows lets a process do this only when the
        player's last input was its own, which a hotkey's press is
        (``SetForegroundWindow``). An embedded game's keyboard is still the keyboard
        target's, given once the tiles settle (``set_keyboard_target``). Whether the window
        is in front; False for a game this view holds neither way.
        """
        if hwnd in self._detached_hwnds.values():
            window = hwnd
        elif any(tile.hwnd == hwnd for tile in self._tiles.values()):
            window = int(self.window().winId())
        else:
            return False
        if _user32.GetForegroundWindow() == window:
            return True
        if _user32.IsIconic(window):
            _user32.ShowWindow(window, _SW_RESTORE)
        if not _user32.SetForegroundWindow(window):
            logger.info("Windows kept window %#x from coming to the front", window)
            return False
        return True

    def keep_keyboard_over(self, widget: QWidget) -> None:
        """Let a press on *widget*, or anything inside it, leave the keyboard with the game.

        A press on the app's own widgets takes the keyboard from the game, so a field typed
        into gets the keys. A product's bar of actions about the games is no such field.
        """
        self._keeps_keyboard.append(widget)

    def _has_keyboard(self, hwnd: int) -> bool:
        """Whether the game in *hwnd* is embedded and has the keyboard, with the app in front."""
        tile = next((tile for tile in self._tiles.values() if tile.hwnd == hwnd), None)
        return (
            tile is not None and tile.is_game_focused() and _user32.GetForegroundWindow() == int(self.window().winId())
        )

    def _give_keyboard(self) -> None:
        if not self._keyboard_owed or self._keyboard_target is None or not self._settled():
            return
        tile = next((tile for tile in self._tiles.values() if tile.hwnd == self._keyboard_target), None)
        if tile is None:
            return
        self._keyboard_owed = False
        tile.focus_game()
        self._note_player_focus(tile.hwnd)

    def _note_player_focus(self, hwnd: int) -> None:
        """The player's keys now go to the game in *hwnd*: see player_focus_changed."""
        if hwnd != self._player_focus:
            self._player_focus = hwnd
            self.player_focus_changed.emit(hwnd)

    @property
    def player_focus(self) -> int | None:
        """The game the player last gave the keyboard to, or None if none yet. See player_focus_changed."""
        return self._player_focus

    def game_targets(self) -> dict[int, tuple[int, int]]:
        """The size each embedded window should run at, by hwnd, as things stand.

        The largest rung of its aspect that fits its tile, at most the cap in an equal grid,
        and left out if it has no ladder (``game_sizing.tile_targets``). A lone client keeps
        its launch size. Empty unless the tiles are placed by rung.
        """
        slots = self._occupied_slots()
        if not self._rung_family_in_use() or not slots:
            return {}
        areas = [self._widget_at(slot).size() for slot in slots]
        return self._targets_at(slots, [(area.width(), area.height()) for area in areas])

    def _targets_at(self, slots: list[int], areas: list[tuple[int, int]]) -> dict[int, tuple[int, int]]:
        """``game_targets`` for tiles of these sizes, by hwnd: where they are, or where they are going."""
        targets = tile_targets(
            [self._launch_size_at(slot) for slot in slots],
            areas,
            self._tile_sizes(slots),
            self.devicePixelRatio() or 1.0,
            grow=True,
            cap=self._cap,
        )
        return {
            self._tiles[slot].hwnd: target
            for slot, target in zip(slots, targets)
            if slot in self._tiles and target is not None
        }

    def _targets_to_ask_for(self) -> dict[int, tuple[int, int]]:
        return {hwnd: size for hwnd, size in self.game_targets().items() if hwnd in self._resizable}

    def _note_targets(self) -> None:
        """Start the settle timer again if the area or a target moved.

        Not on every call: a team view places the tiles again several times a second with
        nothing changed, and restarting on those would put a resize off for as long as a
        follower walks. The area counts as well as the targets, so a drag that stays within
        a rung for a moment still has not stopped.
        """
        settling = ((self.width(), self.height()), self._targets_to_ask_for())
        if settling != self._settling:
            self._settling = settling
            self._settle_timer.start()

    def _ask_for_targets(self) -> None:
        """Ask for each target that has settled and differs from what the window has or was asked for."""
        tiles = {tile.hwnd: tile for tile in self._tiles.values()}
        for hwnd, target in self._targets_to_ask_for().items():
            if target != self._asked.get(hwnd, tiles[hwnd].coming_size):
                self._asked[hwnd] = target
                self.game_size_wanted.emit(hwnd, target)

    def _launch_size_at(self, slot: int) -> tuple[int, int] | None:
        """What the window in *slot* launched at, or its launch's word for it, or None."""
        pending = self._pending_tiles.get(slot)
        if pending is not None:
            return pending.resolution
        tile = self._tiles.get(slot)
        hwnd = tile.hwnd if tile is not None else self._detached_hwnds.get(slot)
        return self._launch_sizes.get(hwnd)

    def _rung_family_in_use(self) -> tuple[tuple[int, int], ...] | None:
        """The family the tiles are placed by: the one of the largest game with a ladder.

        A client of another family keeps its own in ``tile_targets``, and may be cut by a
        tile reserved for this one. None with no layout, with rungs off, or with no ladder.
        """
        if self._layout is None or not self._by_rung:
            return None
        launches = [self._launch_size_at(slot) for slot in self._occupied_slots()]
        known = [size for size in launches if size is not None] or [self._settings.game_resolution]
        for size in sorted(known, key=lambda s: s[0] * s[1], reverse=True):
            family = aspect_family(size)
            if family is not None:
                return family
        return None

    def set_captions(self, captions: Sequence[str | None]) -> None:
        """Caption each tile by **player** index, over the label the client manager gives it.

        For a product whose caption says more than the slot, as BartleBuddy's names what
        holds the wizard. A None entry, or a player past the end, keeps the manager's.
        """
        self._captions = list(captions)
        for slot, player in self._tile_players.items():
            widget = self._widget_at(slot)
            caption = self._caption_for(player)
            if widget is not None and caption is not None:
                widget.set_label(caption)

    def _caption_for(self, player: int | None) -> str | None:
        if player is not None and 0 <= player < len(self._captions):
            return self._captions[player]
        return None

    def set_pill_roles(self, roles: Sequence[tuple[str, str] | None]) -> None:
        """Put a role icon on each tile's pill by **player** index, as ``(icon, tooltip)``.

        For a product with a role per wizard, as BartleBuddy's Quester, Helper and None: a
        press on the icon is ``role_clicked``. A None entry, or a player past the end, has none.
        """
        self._pill_roles = list(roles)
        self._mark_pills()

    def set_active_player(self, player: int | None) -> None:
        """Mark the pill of the wizard being played, by **player** index, or none with None."""
        self._active_player = player
        self._mark_pills()

    def _mark_pills(self) -> None:
        for slot, player in self._tile_players.items():
            self._mark_pill(slot, player)

    def _mark_pill(self, slot: int, player: int | None) -> None:
        tile = self._tiles.get(slot)
        if tile is None:
            return
        role = self._pill_roles[player] if player is not None and 0 <= player < len(self._pill_roles) else None
        tile.set_pill_role(*(role or (None, "")))
        tile.set_active_mark(player is not None and player == self._active_player)

    def _on_caption_clicked(self, slot: int, point: QPoint) -> None:
        player = self._player_for(slot)
        if player is not None:
            self.caption_clicked.emit(player, point)

    def _on_body_clicked(self, slot: int) -> None:
        player = self._player_for(slot)
        if player is not None:
            self.body_clicked.emit(player)

    def _swallow_small_tiles(self, slots: list[int], sizes: list[TileSize] | None) -> None:
        """Under swap first, the Side tiles swallow clicks and the rest let them through: see set_swap_first.

        A window that isn't in game never swallows: it has no player to swap to, so a press
        on it can only give it the keyboard.
        """
        for i, slot in enumerate(slots):
            tile = self._tiles.get(slot)
            if tile is not None:
                small = sizes is not None and sizes[i] is TileSize.SIDE
                tile.swallow_clicks(self._swap_first and small and self._player_for(slot) is not None)

    def _on_role_clicked(self, slot: int, point: QPoint) -> None:
        player = self._player_for(slot)
        if player is not None:
            self.role_clicked.emit(player, point)

    def _tile_sizes(self, slots: list[int]) -> Tiles:
        """Each occupied slot's tile size, or a plain count for the equal grid."""
        if self._layout is None:
            return len(slots)
        played = self._played_at(slots)
        sizes = tile_layouts.sizes(self._layout, played)
        if TileSize.SIDE not in sizes:
            return len(slots)
        family = self._rung_family_in_use()
        if family is None:
            return sizes
        tiles = rung_tiles(family, self.devicePixelRatio() or 1.0)
        return (
            sizes if tile_layouts.strip_fits(self._layout, played, (self.width(), self.height()), tiles) else len(slots)
        )

    def _played_at(self, slots: list[int]) -> list[bool]:
        """Whether the user moves the wizard in each of *slots*; a slot with no player is not moved."""
        players = [self._player_for(slot) for slot in slots]
        return [p is not None and 0 <= p < len(self._played) and self._played[p] for p in players]

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch account and profile lists from settings.

        Public because the placeholder is GameView's own widget: a caller that has just
        added an account or recorded a launch needs the lists rebuilt without reaching
        through to it.
        """
        self._placeholder.refresh()

    def refresh_launch_fit(self) -> None:
        """Re-judge which Quick Launch entries can launch, without rebuilding them.

        The cheap half of ``refresh_launch_lists``: what a card *says* has not changed,
        only whether the space is there for it. Called on every geometry change, so it
        must stay cheap — see the placeholder's ``refresh_fit``.
        """
        self._placeholder.refresh_fit()

    def set_roster_source(self, source) -> None:
        """Supply what the account cards ask for their characters — see the placeholder."""
        self._placeholder.set_roster_source(source)

    def repaint_tile_labels(self) -> None:
        """Repaint every tile's caption without changing what it is showing.

        Toggling Streamer Mode changes how ``pill_text`` renders a name it already has,
        and nothing else would prompt a repaint: a tile only updates when its name or
        label *changes*, and neither did. Called from the settings-applied signal.
        """
        for tile in (*self._tiles.values(), *self._detached_tiles.values(), *self._pending_tiles.values()):
            tile.update()

    def _wizard_name_for(self, player: int | None) -> str | None:
        """The last known wizard name for a **player** index, or None if there is not one.

        None in, None out: a window that is not in game has no player index and therefore
        no wizard name — nothing has read one, because nothing can.
        """
        if player is not None and 0 <= player < len(self._wizard_names):
            return self._wizard_names[player]
        return None

    def _player_for(self, slot: int) -> int | None:
        """The player index last synced for *slot*, or None if it has none."""
        return self._tile_players.get(slot)

    def _occupied_slots(self) -> list[int]:
        """Every slot holding a tile of any kind, in grid order.

        One question with one answer, because the three dicts have to be consulted
        together everywhere it is asked -- the grid layout, the minimum size, the tile
        count and whether the launch screen shows. Each of those read two of the three
        before pending tiles existed, and a pending tile missing from any one of them is a
        tile drawn on top of another.
        """
        return sorted(set(self._tiles) | set(self._detached_tiles) | set(self._pending_tiles))

    def _widget_at(self, slot: int):
        """Whatever tile occupies *slot*, of any kind, or None."""
        return self._tiles.get(slot) or self._detached_tiles.get(slot) or self._pending_tiles.get(slot)

    def toggle(self) -> None:
        """Show or hide the GameView (used by the toggle_embed keybinding)."""
        visible = not self.isVisible()
        self.setVisible(visible)
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "show_game_view"):
                parent.show_game_view(visible)
                return
            parent = parent.parent()

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._recompute_geometry()

    # ------------------------------------------------------------------
    # Internal geometry management
    # ------------------------------------------------------------------

    def _recompute_geometry(self) -> None:
        """Lay out tiles in the grid and update placeholder/overlay position."""
        self._placeholder.setGeometry(0, 0, self.width(), self.height())
        # Re-judged here rather than left to the placeholder's own resizeEvent: Qt defers
        # a resize event to a hidden widget until it is shown, and the launch screen is
        # hidden for the whole life of an embedded client. Its own handler stays as well,
        # for a resize that reaches it directly.
        self._placeholder.refresh_fit()

        self._overlay_btn.move(
            self.width() - _OVERLAY_BTN_SIZE - _OVERLAY_BTN_MARGIN,
            _OVERLAY_BTN_MARGIN,
        )

        all_slots = self._occupied_slots()
        n = len(all_slots)
        if n == 0:
            return

        tiles, rects, self._placement, self._main_slot = self._plan_geometry(all_slots)
        for slot, rect in zip(all_slots, rects):
            widget = self._widget_at(slot)
            if widget is not None:
                widget.setGeometry(rect.x, rect.y, rect.w, rect.h)
        # Before the targets: a refit started from them reads whether its game takes input.
        self._swallow_small_tiles(all_slots, None if isinstance(tiles, int) else tiles)
        self._note_targets()
        # A tile that left drops out of what the rings wait for.
        self._watch_rings()

    def _plan_geometry(self, slots: list[int]) -> tuple[Tiles, list, list[int], int | None]:
        """Where each of *slots* goes, without moving anything.

        The tile sizes, the rects in slot order, and the placement order and Main slot that
        come with them, for ``_recompute_geometry`` to set.
        """
        tiles = self._tile_sizes(slots)
        if isinstance(tiles, int):
            return tiles, list(tile_layouts.equal_grid(len(slots), self.width(), self.height())), *self._strip()
        return tiles, *self._arrangement(slots, tiles)

    def _strip(self) -> tuple[list[int], int | None]:
        return self._placement, self._main_slot

    def _arrangement(self, slots: list[int], sizes: list[TileSize]) -> tuple[list, list[int], int | None]:
        """Main and Side tiles by the layout (``tile_layouts.arrange``), at the game's aspect.

        In the placement order: a slot new to it goes last, and a new Main tile trades places
        with the old one, so the rest of the strip keeps where it was. The rects, and the
        placement and Main slot to keep once they are set.
        """
        width, height = self._largest_size() or self._settings.game_resolution
        family = self._rung_family_in_use()
        placement = [slot for slot in self._placement if slot in slots]
        placement += [slot for slot in slots if slot not in placement]
        main_slot = self._main_slot
        mains = [slot for slot, size in zip(slots, sizes) if size is TileSize.MAIN]
        if len(mains) == 1:
            placement = tile_layouts.trade_places(placement, main_slot, mains[0])
            main_slot = mains[0]
        rects = tile_layouts.arrange(
            self._layout,
            self._played_at(slots),
            (self.width(), self.height()),
            aspect=width / height,
            rung_tiles=rung_tiles(family, self.devicePixelRatio() or 1.0) if family else (),
            order=[slots.index(slot) for slot in placement],
        )
        return list(rects), placement, main_slot

    def _largest_size(self) -> tuple[int, int] | None:
        """The largest game on screen or launching, componentwise; see ``_update_minimum_size``."""
        sizes = [size for size in (self.largest_game_size, *self._pending_resolutions()) if size is not None]
        if not sizes:
            return None
        return max(size[0] for size in sizes), max(size[1] for size in sizes)

    def _update_minimum_size(self) -> None:
        """Set GameView minimum size to fit the current client grid plus padding.

        This is what stops the window and the splitters being dragged to where the game
        windows get clipped: a tile shows its client at 1:1 and cannot scale, so anything
        narrower than the grid needs simply cuts the game off. Resolutions are capped to
        the screen before launch (see bartleby/runtime/game/game_sizing.py), so the minimum this
        derives is always satisfiable rather than a size the window cannot reach.

        The grid is measured against the **largest** client on screen, not an arbitrary
        one. Mixed-resolution fleets are supported — ``_group_by_resolution`` launches a
        batch per resolution — so reading whichever tile the dict happened to yield first
        understated the minimum whenever they differed, and the biggest client was the one
        that got clipped.

        Tiles by rung are measured as the equal grid at the smallest rung (``_rung_floor``), and
        never at the sizes the games have now. A floor taken from a Main grown by maximizing
        would stop the window being restored, and its game would follow a smaller tile down
        anyway.
        """
        slots = self._occupied_slots()
        if not slots:
            self.setMinimumSize(0, 0)
            return
        family = self._rung_family_in_use()
        if family is not None:
            self.setMinimumSize(*self._rung_floor(slots, family))
            return
        # A launching window is measured at the resolution its launch opens it at: a
        # profile's own, which the global one need not be anywhere near. Everything
        # detached, or a launch that did not say, leaves no size to read; the setting is
        # what those windows were launched at, and what a re-attach brings back.
        largest = self._largest_size() or self._settings.game_resolution
        self.setMinimumSize(*required_game_area(largest, self._tile_sizes(slots), self.devicePixelRatio() or 1.0))

    def _rung_floor(self, slots: list[int], family: Sequence[tuple[int, int]]) -> tuple[int, int]:
        """The equal grid at the family's smallest rung, beside another; a lone client's launch size.

        Beside another, every game can follow its tile down to the smallest rung, so that is
        all the window needs, and a sidebar can widen as far as the games can shrink (#14). A
        launch is still judged as the equal grid at its launch size, by the launch fit. A lone
        client never resizes, so its floor is the size it launched at.
        """
        count, dpr = len(slots), self.devicePixelRatio() or 1.0
        if count > 1:
            return required_game_area(family[0], count, dpr)
        launches = [size for size in map(self._launch_size_at, slots) if size is not None]
        launches = launches or [self._settings.game_resolution]
        largest = max(size[0] for size in launches), max(size[1] for size in launches)
        return required_game_area(largest, count, dpr)

    def _pending_resolutions(self) -> list[tuple[int, int]]:
        """The resolution each launching window will open at, where its launch said."""
        return [tile.resolution for tile in self._pending_tiles.values() if tile.resolution is not None]

    def _detach_client(self, idx: int) -> None:
        """Save hwnd, create a DetachedTile, and unembed the ClientTile."""
        if idx not in self._tiles:
            return
        hwnd = self._tiles[idx].hwnd
        label = self._tiles[idx].label
        self._detached_hwnds[idx] = hwnd
        self._create_detached_tile(idx, label)
        # Carried across rather than waiting for the next sync, so the pill does not
        # flash gray for a tick on the way out.
        self._detached_tiles[idx].set_color_key(self._tiles[idx].color_key)
        # Unembed the ClientTile without clearing the DetachedTile
        tile = self._tiles.pop(idx)
        self._asked.pop(hwnd, None)
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()
        self.window_detached.emit(hwnd)
        self._recompute_geometry()
        self._update_minimum_size()
        self._set_embed_state(bool(self._tiles))
        self._update_overlay()

    def _create_detached_tile(self, idx: int, label: str) -> None:
        dtile = DetachedTile(label, parent=self)
        dtile.set_wizard_name(self._wizard_name_for(self._player_for(idx)))
        dtile.reattach_requested.connect(lambda i=idx: self.reattach_client(i))
        dtile.show()
        self._detached_tiles[idx] = dtile

    def _remove_detached_tile(self, idx: int) -> None:
        dtile = self._detached_tiles.pop(idx, None)
        if dtile is not None:
            dtile.hide()
            dtile.setParent(None)  # type: ignore[arg-type]
            dtile.deleteLater()

    def _update_overlay(self) -> None:
        if self._tiles:
            self._overlay_btn.setIcon(svg_icon("detach", 22))
            self._overlay_btn.setToolTip("Detach all windows")
            self._overlay_btn.setVisible(True)
            self._overlay_btn.raise_()
        elif any(not isinstance(tile, ScaledTile) for tile in self._detached_tiles.values()):
            # A scaled window cannot be reattached, so it alone offers no Re-attach all.
            self._overlay_btn.setIcon(svg_icon("attach", 22))
            self._overlay_btn.setToolTip("Re-attach all windows")
            self._overlay_btn.setVisible(True)
            self._overlay_btn.raise_()
        else:
            self._overlay_btn.setVisible(False)

    def _on_overlay_clicked(self) -> None:
        if self._detached_hwnds:
            self.reattach_all()
        else:
            self.detach_all()

    # ------------------------------------------------------------------
    # Focus
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            widget = obj if isinstance(obj, QWidget) else None
            while widget is not None:
                if isinstance(widget, ClientTile):
                    return False  # click is on or inside a game tile; the tile grants focus itself
                if widget in self._keeps_keyboard:
                    return False  # see keep_keyboard_over
                widget = widget.parent() if isinstance(widget, QWidget) else None
            # Click landed on a non-game Qt widget; revoke game focus if any tile has it.
            for tile in self._tiles.values():
                if tile.is_game_focused():
                    _user32.SetFocus(int(self.window().winId()))
                    break
        return False

    def _poll_focus(self) -> None:
        # Borders only. Granting focus is the tile's job, on the click itself (see
        # ClientTile.nativeEvent): a poll grants it too late to survive the app being
        # brought to the front by that click, and matched clicks through windows stacked
        # over a tile (#93).
        focused = None
        for tile in self._tiles.values():
            tile.watch_game()
            has_focus = tile.is_game_focused()
            tile.set_focused(has_focus)
            if has_focus:
                focused = tile.hwnd
        # A game thread keeps its focus when the user switches to another application, so
        # an embedded tile only counts while the app itself is in front. A detached game
        # window is a top-level window of its own, and is in front or not by itself.
        foreground = _user32.GetForegroundWindow()
        if focused is not None and foreground != int(self.window().winId()):
            focused = None
        if foreground and foreground in self._detached_hwnds.values():
            focused = foreground
            # A detached window comes to the front only by the player's hand, so it counts as
            # theirs. An embedded game is theirs only by a click, never by the poll, which
            # would also see the game a refit left the keyboard on.
            self._note_player_focus(foreground)
        if focused is not None:
            self._last_focused_hwnd = focused
        if focused != self._focused_hwnd:
            self._focused_hwnd = focused
            self.game_focus_changed.emit(focused)

    @property
    def focused_hwnd(self) -> int | None:
        """The game window holding the keyboard right now, or None. See game_focus_changed."""
        return self._focused_hwnd

    @property
    def last_focused_hwnd(self) -> int | None:
        """The game window that most recently held the keyboard, or None if none ever has.

        The source for XYZ sync and TP to player. *Last*, not current: pressing their button
        is a click on the app, which takes the focus away from the game first.
        """
        return self._last_focused_hwnd

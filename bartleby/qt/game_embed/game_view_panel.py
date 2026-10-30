"""GamePanel: GameView wrapped with a status title bar, Bar A, whose buttons each product lists.

Bar A is the strip above the game windows. Its left caption says what is connected, its
right caption what the product is doing with it, and between them sits an
:class:`ActionBar`: a product's actions about the clients, as 24px icon buttons in groups
with a divider between groups. Each product hands its own list of :class:`BarAction`; the
bar knows nothing of what the buttons do, and reports a press by the action's id.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartleby.qt.game_embed.game_view import GameView
from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.icon_button import IconButton, IconTint


def client_summary(connected: int, profile: str | None) -> str:
    """The caption for the left of Bar A: how many clients, launched from what.

    Pure, so the wording is testable without a ``QApplication`` — the same reasoning
    as ``client_refresh_interval()``.  The profile is named only when there is one
    *and* something is connected: "0 clients · Four Wizards" reads as a claim that
    the profile is running.
    """
    noun = "client" if connected == 1 else "clients"
    text = f"{connected} {noun} connected" if connected else "No clients connected"
    if connected and profile:
        text = f"{text} · {profile}"
    return text


@dataclass(frozen=True)
class BarAction:
    """One button on Bar A: what it is called by, its glyph, and what it says on hover.

    Icons, not labels: Bar A's height is a term in the window-sizing arithmetic
    (``game_sizing.GAME_PANEL_CHROME_PX``), and a labeled button is taller than the caption
    row it would sit in. *tooltip* is what the button says until the product's first
    :meth:`ActionBar.set_state`, which is where a key and a state belong.

    A *checkable* action is a toggle whose state lives elsewhere, in what the product runs:
    the button never flips itself on a press, and draws its glyph in the accent while the
    product reports it on. A *menu* action opens a menu under the button instead of
    reporting a press, from the builder :meth:`ActionBar.set_menu` was handed.
    """

    id: str
    icon: str
    tooltip: str
    checkable: bool = False
    menu: bool = False


# The divider's box: the rule is drawn centered in it, so the width beyond its 1px is the
# padding either side, added to the row's spacing. Shorter than the 24px buttons, so it
# reads as a break in the row rather than a wall.
_DIVIDER_SIZE = (9, 16)


class ActionBar(ThemedMixin, QWidget):
    """A product's actions as icon buttons, in the middle of Bar A (BartleBot #81, #85).

    Bar A, because the actions are about the clients, not about what the product does
    with them. A toggle that is on draws its glyph in the accent, the same way the node
    browser's favorite star reports its state. The buttons sit in the groups handed in,
    with a divider between groups.
    """

    action_requested = pyqtSignal(str)  # a BarAction's id; never a menu action's

    def __init__(self, groups: Sequence[Sequence[BarAction]], parent=None) -> None:
        super().__init__(parent)
        self._actions: dict[str, BarAction] = {}
        self._buttons: dict[str, IconButton] = {}
        self._checked: dict[str, bool] = {}
        self._menus: dict[str, Callable[[QMenu], None]] = {}
        self._dividers: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for group in groups:
            if self._buttons:
                divider = QFrame(self)
                divider.setFrameShape(QFrame.Shape.VLine)
                divider.setFixedSize(*_DIVIDER_SIZE)
                layout.addWidget(divider, alignment=Qt.AlignmentFlag.AlignVCenter)
                self._dividers.append(divider)
            for action in group:
                btn = IconButton(action.icon, tooltip=action.tooltip, size=24, icon_size=18, parent=self)
                btn.clicked.connect(lambda _checked=False, a=action: self._pressed(a))
                layout.addWidget(btn)
                self._actions[action.id] = action
                self._buttons[action.id] = btn
                self._checked[action.id] = False
        self.refresh_theme_styles()

    def button(self, action_id: str) -> IconButton:
        return self._buttons[action_id]

    @property
    def action_ids(self) -> list[str]:
        """Every action's id, in bar order."""
        return list(self._buttons)

    @property
    def dividers(self) -> list[QFrame]:
        return list(self._dividers)

    def is_checked(self, action_id: str) -> bool:
        """Whether the product last reported this toggle on."""
        return self._checked[action_id]

    def apply_theme_styles(self) -> None:
        for divider in self._dividers:
            divider.setStyleSheet(f"color: {THEME.border};")

    def set_state(
        self,
        action_id: str,
        *,
        enabled: bool | None = None,
        checked: bool | None = None,
        tooltip: str | None = None,
    ) -> None:
        """Show what the product reports of one action. What is left None stays as it was."""
        btn = self._buttons[action_id]
        if enabled is not None:
            btn.setEnabled(enabled)
        if checked is not None:
            if not self._actions[action_id].checkable:
                raise ValueError(f"{action_id} is not a toggle")
            self._checked[action_id] = checked
            btn.set_tint(IconTint.ACCENT if checked else IconTint.DEFAULT)
        if tooltip is not None:
            btn.setToolTip(tooltip)

    def set_menu(self, action_id: str, build: Callable[[QMenu], None]) -> None:
        """What a menu action's menu holds: *build* fills a fresh menu on every press."""
        if not self._actions[action_id].menu:
            raise ValueError(f"{action_id} is not a menu action")
        self._menus[action_id] = build

    def menu_for(self, action_id: str) -> QMenu:
        """The menu a press on *action_id* would open, built and not yet shown."""
        menu = popup_menu(self._buttons[action_id])
        build = self._menus.get(action_id)
        if build is not None:
            build(menu)
        return menu

    def _pressed(self, action: BarAction) -> None:
        if not action.menu:
            self.action_requested.emit(action.id)
            return
        btn = self._buttons[action.id]
        # Under the button, as a toolbar's drop-down opens.
        self.menu_for(action.id).popup(btn.mapToGlobal(QPoint(0, btn.height())))


def _side_policy() -> QSizePolicy:
    """The size policy of Bar A's two captions, which is what keeps the buttons centered.

    Equal shares of the row regardless of text: ``Ignored`` so a long node title clips at
    its half rather than pushing the buttons left, and space kept while hidden so the node
    title appearing when a run starts does not move them either -- a hidden widget
    otherwise gives its share to the other caption.
    """
    policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    policy.setRetainSizeWhenHidden(True)
    return policy


class GamePanel(ThemedMixin, QWidget):
    """Middle panel: a title bar above the embedded game view.

    Title bar layout (left → right):
        [N clients connected · Profile]   [the product's actions]   [what it is doing]

    The bar sits above the *game windows*, so it says what the clients are doing.
    BartleBot's bot name and run status used to live here and moved to Bar B above the
    graph — they belong to the bot, and here they vanished with the game view
    whenever it was hidden.
    """

    launch_account_requested = pyqtSignal(str, str)  # forwarded from GameView
    launch_profile_requested = pyqtSignal(str)  # forwarded from GameView
    edit_account_requested = pyqtSignal(str)  # forwarded from GameView
    delete_account_requested = pyqtSignal(str)  # forwarded from GameView
    edit_profile_requested = pyqtSignal(str)  # forwarded from GameView
    duplicate_profile_requested = pyqtSignal(str)  # forwarded from GameView
    delete_profile_requested = pyqtSignal(str)  # forwarded from GameView
    add_account_requested = pyqtSignal()  # forwarded from GameView
    add_profile_requested = pyqtSignal()  # forwarded from GameView
    reorder_accounts_requested = pyqtSignal(list)  # forwarded from GameView
    reorder_profiles_requested = pyqtSignal(list)  # forwarded from GameView
    manage_accounts_requested = pyqtSignal()  # forwarded from GameView
    dismiss_launch_requested = pyqtSignal(str)  # forwarded from GameView
    action_requested = pyqtSignal(str)  # a BarAction's id, from the ActionBar

    def __init__(self, game_view: GameView, action_bar: ActionBar, parent=None) -> None:
        super().__init__(parent)
        self.game_view = game_view
        self.action_bar = action_bar
        self._connected = 0
        self._profile: str | None = None
        self._build_ui()

        # Forward placeholder signals
        self.game_view.launch_account_requested.connect(self.launch_account_requested)
        self.game_view.launch_profile_requested.connect(self.launch_profile_requested)
        self.game_view.edit_account_requested.connect(self.edit_account_requested)
        self.game_view.delete_account_requested.connect(self.delete_account_requested)
        self.game_view.edit_profile_requested.connect(self.edit_profile_requested)
        self.game_view.duplicate_profile_requested.connect(self.duplicate_profile_requested)
        self.game_view.delete_profile_requested.connect(self.delete_profile_requested)
        self.game_view.add_account_requested.connect(self.add_account_requested)
        self.game_view.add_profile_requested.connect(self.add_profile_requested)
        self.game_view.reorder_accounts_requested.connect(self.reorder_accounts_requested)
        self.game_view.reorder_profiles_requested.connect(self.reorder_profiles_requested)
        self.game_view.manage_accounts_requested.connect(self.manage_accounts_requested)
        self.game_view.dismiss_launch_requested.connect(self.dismiss_launch_requested)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch lists — see GameView.refresh_launch_lists."""
        self.game_view.refresh_launch_lists()

    def set_roster_source(self, source) -> None:
        """Supply what the account cards ask for their characters."""
        self.game_view.set_roster_source(source)

    def repaint_tile_labels(self) -> None:
        """Repaint the tile captions — see GameView.repaint_tile_labels."""
        self.game_view.repaint_tile_labels()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = self._make_header()
        outer.addWidget(self.header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        outer.addWidget(self.game_view, stretch=1)

        self.refresh_theme_styles()

    def _make_header(self) -> QWidget:
        bar = QWidget()

        # ── Left: what is connected, and what launched it ────────────
        self._clients_lbl = QLabel(client_summary(0, None))
        self._clients_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._clients_lbl.setSizePolicy(_side_policy())

        # ── Right: what the product is doing (BartleBot's active node) ───
        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state_lbl.setSizePolicy(_side_policy())
        self._state_lbl.setVisible(False)

        layout = QHBoxLayout(bar)
        # 2px, not 4, above and below: the action buttons set the row's height, and the
        # whole strip has to stay inside GAME_PANEL_CHROME_PX.
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)
        # ── Middle: the product's actions (BartleBot #81) ────────────
        self.action_bar.action_requested.connect(self.action_requested)

        # The buttons stay dead center, whatever the two captions say: each side gets the
        # same stretch and neither one's text width counts (see _side_policy).
        layout.addWidget(self._clients_lbl, stretch=1)
        layout.addWidget(self.action_bar)
        layout.addWidget(self._state_lbl, stretch=1)

        return bar

    # ------------------------------------------------------------------
    # Public API — called by the product's window
    # ------------------------------------------------------------------

    def set_client_summary(self, connected: int, profile: str | None = None) -> None:
        """How many clients are hooked, and the launch profile that produced them.

        *profile* is remembered when omitted, because the client count changes on
        every detection tick while the profile changes only on a launch.
        """
        self._connected = connected
        if profile is not None:
            self._profile = profile
        self._clients_lbl.setText(client_summary(self._connected, self._profile))

    def set_launch_profile(self, profile: str | None) -> None:
        """Name the launch profile most recently used, or clear it."""
        self._profile = profile
        self._clients_lbl.setText(client_summary(self._connected, self._profile))

    def set_active_node(self, state_label: str) -> None:
        """Show what the product is doing right now (BartleBot's node), or nothing when it is idle."""
        self._state_lbl.setText(state_label)
        # Its half of the bar can be narrower than a long title, which then clips.
        self._state_lbl.setToolTip(state_label)
        self._state_lbl.setVisible(bool(state_label))

    @property
    def client_text(self) -> str:
        """What the left of the bar currently reads."""
        return self._clients_lbl.text()

    @property
    def node_text(self) -> str:
        """What the right of the bar currently reads."""
        return self._state_lbl.text()

    def apply_theme_styles(self) -> None:
        self._clients_lbl.setStyleSheet(f"color: {THEME.text};")
        self._state_lbl.setStyleSheet(f"color: {THEME.text_muted};")

"""``CombatStrategyDialog`` — the one surface where combat strategies are edited.

WHY A DIALOG, AND WHY ONLY ONE
------------------------------

BartleBot's Combat tab was permanently in an edit mode it never declared: no Edit
button, no Save button, and a 500ms autosave straight into a shipped file. Every
other surface in that app keys its editability off ``BotMode.EDIT``, but combat
is the one thing most users *will* need to change — it depends on which wizards
they own — while never touching a bot graph. So combat editing gets its own
surface with an explicit Save/Cancel boundary, and that boundary is what retires
the autosave.

There used to be two overlapping windows doing most of this job with different
vocabularies (a "Strategy Library" and a "Strategy Picker"). This is both, in
two modes, one per way in (#118, #123):

``LIBRARY``  ``[Cancel] [Save]`` — a product's own entry to the library, as BartleBot's
             **File → Combat Strategies** is. The Duel, Player and Deck tabs, library
             entries only.
``PICK``     ``[Use Default Duel Strategies] [Cancel] [Use this]`` — choosing a strategy
             for something. Only the tab asked for, and **select-only**: no New, Duplicate
             or Delete, and the detail pane is read-only. Before #123 Pick edited
             everything, which made BartleBot's 🔍 and ✏ the same button. The first button
             names the level below the caller's, and is not offered where there is none to
             fall to.

BartleBot's window, a subclass, has a third: ``BOT_STRATEGY``, the Duel and Player tabs with
a Bot Combat Strategy's own strategies in a section at the top ("What a product adds").

One rule covers the edge: **Cancel discards every edit; Save and Use both commit
every edit**, and Use additionally returns the selection. In Pick there is nothing
to commit but the selection.

WHY MASTER-DETAIL RATHER THAN A POPUP
-------------------------------------

``PickerPopup`` is 400×420 and right for a one-line payload — a tag, a fight
name. A DuelCombatStrategy's payload is seven to nine mostly-long lines: a
description, a combat order, and one row per player assignment naming a strategy
and a note. Eliding those to fit a popup would hide exactly the part the user
opened it to compare. So: a compact :class:`StrategyCard` list on the left with
a filter, the full detail on the right, unelided, with a usage count — the shape
``_AddOptionDialog`` already uses for the same reason.

WHAT IT IS A VIEW OF
--------------------

A :class:`~bartleby.runtime.combat.strategy_workset.StrategyWorkingSet` — deep copies, edited
freely, written only by ``commit()``. Nothing below this dialog decides when a
file is written any more. See that module for why dirtiness is derived rather
than reported.

ONE STEP OF A FLOW (#272)
-------------------------

BartleBot's Combat tab's add-card chains this dialog into a second, which asks for the fights.
Opened that way it is handed a :class:`FlowStep`: a title saying what is being done, a
"Step 1 of 2" heading over the tabs, and a commit naming where it goes next. Opened to
make a new strategy (``start_new``), it opens on one rather than an empty pane: a copy of
``start_from`` under New's name, or New's own entry, selected with its name ready to type
over. That starting entry is forgotten at Save if another was chosen and it was never
touched, so looking around does not leave an unused copy behind.

WHAT A PRODUCT ADDS
-------------------

A product opens this window with its settings, a :class:`StrategySettings`, and nothing else
is required: BartleBuddy's is this class as it is. What a product can hand in, it can leave
out, and then the window has no trace of it: a ``deck_link`` for the Deck tab's Import and
Apply, an ``archmastery_default``, the accounts behind the player slots.

What only one product has is a subclass, which plugs in here:

- :class:`CombatStrategyDialog`: ``_prepare_window``, ``_tabs_shown`` (which tabs a mode has), ``_make_page`` (the
  page class), ``_result_page`` (whose selection a commit returns), ``_owner_open`` and
  ``_owner_label`` (what the Save confirmation says of an owner).
- :class:`KindPage`: ``own_source`` and ``_filter_source``, ``_sections`` over
  ``_library_sections``, ``_on_empty_section`` with ``_add_section_note``, ``_favorable``,
  ``_new_card``, ``card_menu``, ``_make_about`` and ``_set_own_notes``, ``creates_own`` and
  ``_create_block``.
- :class:`AboutSection`: ``_build_origin_extras``, beside the line saying where the entry lives.

BartleBot's subclass draws its Bot Combat Strategy's own strategies with them: a working
set's :class:`~bartleby.runtime.combat.strategy_workset.StrategyOwner` and what it keeps. The
page knows an owner's entry from the library's, since the working set does, but has no word
for one.

TAGS, FILTERS AND FAVORITES (#125)
----------------------------------

Each tab has a :class:`~bartleby.qt.combat.filter_bar.StrategyFilterBar`
across its top: facet pickers and the search box, with the selection as chips below.
:mod:`bartleby.runtime.combat.strategy_tags` holds the vocabulary and the matching rule.

Favorites are the one thing here that is *not* inside the Save/Cancel boundary. A
star is saved to the product's settings on the click, because it is a preference about
the library rather than a change to it. A favorite moves its card into a "★
Favorites" section. Each entry is in exactly one section, never two: selection is
restored by id, so an entry drawn twice would make it ambiguous which row is
selected. The headers
only appear once there is more than one section, and a section that the filter
empties hides its header.

Moved from BartleBot's ``gui/combat_strategies/dialog.py``, whose Bot Combat Strategy mode
stayed there as a subclass (BartleBuddy #31, Stage 6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartleby.qt.combat.deck_editor import DeckPresetEditor
from bartleby.qt.combat.deck_link import DeckClientLink
from bartleby.qt.combat.dsl_checks import DeferredDslChecks
from bartleby.qt.combat.duel_editor import DuelConfigEditor
from bartleby.qt.combat.filter_bar import KIND_NOUNS, StrategyFilterBar
from bartleby.qt.combat.player_editor import PlayerStrategyEditor
from bartleby.qt.combat.strategy_settings import StrategySettings
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.cards.strategy_card import StrategyCard
from bartleby.qt.widgets.labels.elided_label import apply_muted_color
from bartleby.qt.widgets.labels.rule_heading import RuleHeading
from bartleby.qt.widgets.labels.step_heading import Step, StepHeading
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.qt.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from bartleby.qt.widgets.pickers.tag_picker import STRATEGY_VOCABULARY, TagPickerButton
from bartleby.runtime.combat.strategy_summary import (
    StrategySummary,
    summarize_deck,
    summarize_duel,
    summarize_player,
)
from bartleby.runtime.combat.strategy_tags import (
    KIND_GROUPS,
    STORED_GROUPS,
    StrategyFacets,
    facets_match,
    normalize_strategy_tags,
    tag_groups,
)
from bartleby.runtime.combat.strategy_workset import OWN_KINDS, Conflict, SharedUse, StrategyOwner, StrategyWorkingSet
from bartleby.shared import product
from bartleby.shared.store_files import SOURCE_LABELS, Origin, held_listings
from bartleby.shared.strategy_library import StrategyKind

logger = logging.getLogger(__name__)


class DialogMode(Enum):
    """Which way in the dialog was opened by — see the module docstring for each."""

    LIBRARY = "library"
    PICK = "pick"


#: Tab order, and the label each kind carries everywhere in this dialog. Duel first:
#: it is what a bot references, and what the Change… button opens onto.
KIND_LABELS = {
    StrategyKind.DUEL: "Duel Strategies",
    StrategyKind.PLAYER: "Player Strategies",
    StrategyKind.DECK: "Deck Presets",
}


def tabs_for(mode: DialogMode, kind: StrategyKind) -> tuple[StrategyKind, ...]:
    """The tabs a way in shows, in order — and no tab its row in the table doesn't list.

    The library view has all three. Pick has only the kind it was asked for: a Player
    Strategy tab while picking a duel is a place to edit things, and Pick edits nothing.
    """
    if mode is DialogMode.PICK:
        return (kind,)
    return tuple(KIND_LABELS)


#: What ``+ New`` names an entry before the user renames it.
NEW_NAMES = {
    StrategyKind.DUEL: "New Duel Strategy",
    StrategyKind.PLAYER: "New Player Strategy",
    StrategyKind.DECK: "New Deck Preset",
}


@dataclass(frozen=True)
class FlowStep:
    """This dialog as one step of a longer flow (#272): its title, its heading, and its commit."""

    title: str
    step: Step
    commit_text: str
    commit_icon: str = "arrow_right"


#: Pick mode's way to choose no strategy, and what it says it does. Callers name the level
#: below their own; these are for one that doesn't.
NONE_LABEL = "no strategy here"
NONE_TOOLTIP = "Choose no duel strategy here, so these fights use the next level's"
#: What Delete says becomes of the links to an entry that is used elsewhere.
DELETE_USED_NOTE = "those links will point at nothing until they are changed"

#: Where a row keeps the text its filter query is matched against, so filtering
#: never has to re-summarize the entry behind it.
_HAYSTACK_ROLE = Qt.ItemDataRole.UserRole + 1

#: Where a section header row keeps its text. Not DisplayRole: the item would paint it
#: underneath the heading widget drawn over the row.
_HEADING_ROLE = Qt.ItemDataRole.UserRole + 2

#: The section headers, shown only once there is more than one section.
FAVORITES_HEADING = "★ Favorites"
LIBRARY_HEADING = "Library"
#: The keys of the two sections the library is drawn in.
FAVORITES_SECTION = "favorites"
LIBRARY_SECTION = "library"

#: Origin.SHIPPED_OVERRIDE means the shipped file reappears underneath, so the
#: honest verb is "revert" — see :class:`bartleby.shared.store_files.Origin`.
_REVERT_TEXT = "Revert to default"
_DELETE_TEXT = "Delete"
#: Why Delete is grayed out on an unedited Official one: removing only touches the user
#: root, so the entry would be back on the next open (#124).
_BUILT_IN_DELETE_TIP = "Official strategies can't be deleted. Duplicate one to make a copy of your own."
#: The About pane's note on an override whose Official strategy has changed since (#169),
#: beside the Revert button it points at.
OFFICIAL_UPDATED_NOTE = (
    "The Official version has changed since you made your copy. "
    "Revert to default to use the new one; your changes are discarded."
)
#: How far a tab's contents sit inside the tab pane, and how far *each* pane sits from
#: the splitter between them. Two numbers rather than one because the splitter handle is
#: already a few pixels of its own: the gap the eye sees beside a handle is the handle
#: plus this, while the gap beside the pane border is only this.
#:
#: The gutter is applied on **both** sides. Giving it to the detail pane alone left the
#: card list's filter box, its scrollbar and its button row all butted against the
#: divider — and a scrollbar pressed against a vertical line reads as part of the line.
_PAGE_INSET = 10
_SPLITTER_GUTTER = 12
#: The card list's per-row padding, in its stylesheet and in the width a row is fitted to.
_ROW_PAD = 1


def newer_format_note() -> str:
    """What an entry from a file in a newer format says, and why it can't be edited or deleted."""
    return f"Saved by a newer version. Update {product.current().name} to edit it."


def summary_haystack(summary: StrategySummary, own_source: str | None = None) -> str:
    """The text a filter query is matched against, lowercased.

    Description and subtitle are included deliberately: a user looking for "the
    storm one" is far more likely to have written that in the description than in
    the name. *own_source* is the Source word of an owner's own entry.

    Built once per card and cached on its list item, because ``subtitle_line()``
    is only cheap relative to the summary it comes from — producing that summary
    parses every DSL the entry references.
    """
    if summary.owner_name is not None:
        source = f"{own_source} {summary.owner_name}" if own_source else summary.owner_name
    else:
        source = SOURCE_LABELS[summary.source] if summary.source is not None else ""
    tags = " ".join(summary.tags)
    return f"{summary.name} {summary.description} {summary.subtitle_line()} {source} {tags}".lower()


def summary_facets(summary: StrategySummary, own_source: str | None = None) -> StrategyFacets:
    """What the facet filter asks of an entry: its stored tags plus its derived source, *own_source* for an owner's."""
    tags = set(summary.tags)
    if summary.owner_name is not None:
        if own_source:
            tags.add(own_source)
    elif summary.source is not None:
        tags.add(SOURCE_LABELS[summary.source])
    return StrategyFacets(tags=frozenset(tags), slot_schools=summary.slot_schools, player_range=summary.player_range)


def others_heading(kind: StrategyKind) -> str:
    """The header over the non-favorites: "Other duels", "Other strategies", "Other presets"."""
    return f"Other {KIND_NOUNS[kind][1]}"


def matches_query(haystack: str, query: str) -> bool:
    """Whether a cached haystack passes ``query``. An empty query passes everything."""
    needle = query.strip().lower()
    return not needle or needle in haystack


def matches_filter(summary: StrategySummary, query: str) -> str | None:
    """``None`` if ``summary`` should be hidden, else the haystack that matched.

    Pulled out as a plain function so the filter rule can be tested without a
    ``QApplication``, and so all three tabs cannot drift apart on what "search"
    means.
    """
    haystack = summary_haystack(summary)
    return haystack if matches_query(haystack, query) else None


def shared_edit_message(uses: list[SharedUse], *, other: bool) -> str:
    """The Save confirmation's text: every shared use, then what saving means for them."""
    return "\n\n".join(use.sentence(other=other) for use in uses) + "\n\nYour changes will apply there too."


class AboutSection(QWidget):
    """What a library entry *is*: its name, its description, and what depends on it.

    One widget for all three tabs. The three editors each used to draw a name field and a
    description field in an arrangement of its own — one with the label above, one with
    it beside, one with the description on the ``DuelConfig`` rather than on the entry —
    and the usage and origin lines sat above them in a fourth arrangement belonging to
    the page. That is four ways of saying the same two things on one dialog.

    Model-agnostic on purpose: every strategy kind has a ``name`` and a ``description``,
    which is all this touches, so the kind never has to be branched on. What differs
    between tabs is what sits *below* it, which is the editor's business.

    The two muted lines under the fields are things the card list has no room for and the
    old Strategy Library never said at all: what breaks if this goes, and where the entry
    lives — whether "delete" here means delete or means revert, or that it belongs to one
    owner alone. A subclass adds what it offers beside that line in :meth:`_build_origin_extras`.

    *tag_groups* are the stored tag groups this kind is tagged with — Role, on Player
    Strategies and Deck Presets — one picker row each (#125). Duels pass none.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *, tag_groups: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self._entry = None
        self._suppress = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(RuleHeading("About", self))

        # A two-column grid rather than labels stacked above their fields: these are two
        # short one-line values, and stacking them costs two rows of height at the top of
        # every tab for no gain in legibility.
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Name:", self), 0, 0)
        self._name_edit = QLineEdit(self)
        self._name_edit.textChanged.connect(self._on_changed)
        grid.addWidget(self._name_edit, 0, 1)
        grid.addWidget(QLabel("Description:", self), 1, 0)
        self._description_edit = QLineEdit(self)
        self._description_edit.setPlaceholderText("What this is for and why (optional)")
        self._description_edit.textChanged.connect(self._on_changed)
        grid.addWidget(self._description_edit, 1, 1)
        self._tag_pickers: list[TagPickerButton] = []
        for row, group in enumerate(tag_groups, start=2):
            grid.addWidget(QLabel(f"{group}:", self), row, 0)
            picker = TagPickerButton(
                title=group, placeholder="None", group=group, vocabulary=STRATEGY_VOCABULARY, lists_selection=True
            )
            picker.setToolTip(f"What this is for — shown on its card, and filterable by {group}")
            picker.selection_changed.connect(self._on_changed)
            # Sized to its selection at the left of the cell, not stretched across it.
            grid.addWidget(picker, row, 1, Qt.AlignmentFlag.AlignLeft)
            self._tag_pickers.append(picker)
        layout.addLayout(grid)

        self._usage_lbl = QLabel(self)
        self._usage_lbl.setWordWrap(True)
        apply_muted_color(self._usage_lbl)
        layout.addWidget(self._usage_lbl)

        origin_row = QHBoxLayout()
        origin_row.setContentsMargins(0, 0, 0, 0)
        origin_row.setSpacing(6)
        self._origin_lbl = QLabel(self)
        self._origin_lbl.setWordWrap(True)
        apply_muted_color(self._origin_lbl)
        origin_row.addWidget(self._origin_lbl, 1)
        self._build_origin_extras(origin_row)
        layout.addLayout(origin_row)

    def _build_origin_extras(self, row: QHBoxLayout) -> None:
        """Add what a subclass draws beside the line saying where the entry lives."""

    def load(self, entry) -> None:
        self._entry = entry
        self._suppress = True
        self._name_edit.setText(entry.name)
        self._description_edit.setText(entry.description)
        tags = getattr(entry, "tags", [])
        for picker in self._tag_pickers:
            picker.set_selection([t for t in tags if picker.vocabulary.group_of(t) == picker.group])
        self._suppress = False

    def clear(self) -> None:
        self._entry = None
        self._suppress = True
        self._name_edit.clear()
        self._description_edit.clear()
        for picker in self._tag_pickers:
            picker.set_selection([])
        self._usage_lbl.clear()
        self._origin_lbl.clear()
        self._origin_lbl.setVisible(False)
        self._suppress = False

    def set_editable(self, editable: bool) -> None:
        """Read-only in Pick, which selects and edits nothing (#123)."""
        self._name_edit.setReadOnly(not editable)
        self._description_edit.setReadOnly(not editable)
        for picker in self._tag_pickers:
            picker.setEnabled(editable)

    def set_notes(self, usage: str, origin: str) -> None:
        """The two muted lines — what uses this entry, and where it lives."""
        self._usage_lbl.setText(usage)
        self._origin_lbl.setText(origin)
        self._origin_lbl.setVisible(bool(origin))

    @property
    def tag_pickers(self) -> list[TagPickerButton]:
        """One picker per stored tag group. Public so a test can tag an entry as a user would."""
        return list(self._tag_pickers)

    @property
    def usage_text(self) -> str:
        """What the usage line reads. Public so a test can assert what was said."""
        return self._usage_lbl.text()

    @property
    def origin_text(self) -> str:
        """What the origin line reads — empty for an entry with nothing to say."""
        return self._origin_lbl.text()

    @property
    def name_edit(self) -> QLineEdit:
        """The name field. Public so a test can check it is read-only in Pick."""
        return self._name_edit

    def _on_changed(self) -> None:
        if self._suppress or self._entry is None:
            return
        # The last non-empty name stands, so an entry can never become a nameless card
        # with no way back to it.
        self._entry.name = self._name_edit.text().strip() or self._entry.name
        self._entry.description = self._description_edit.text()
        if self._tag_pickers:
            self._entry.tags = normalize_strategy_tags([t for p in self._tag_pickers for t in p.tags()])
        self.changed.emit()


class KindPage(QWidget):
    """One tab: a filtered card list on the left, the detail editor on the right.

    Every kind gets the same page. What differs is which editor sits in the
    detail pane and how an entry is summarized, and both are parameters. What a product
    adds to it is a subclass, through the hooks the module docstring lists.

    *editable* is False in Pick: the button row is gone and the detail pane is
    read-only (#123). *settings* keeps the favorites, and *archmastery_default* is the Deck
    tab's, as :class:`DeckPresetEditor` takes it.
    """

    #: The list's structure changed (created, duplicated, removed, moved) — as opposed
    #: to a field edit, which only ever redraws one card.
    structure_changed = pyqtSignal()
    promote_requested = pyqtSignal(object)
    #: The Duel tab's pencil asked to edit a Player Strategy, by id.
    open_pcs_requested = pyqtSignal(str)

    #: The Source word of the open owner's own entries, in a product that has one.
    own_source: str | None = None

    def __init__(
        self,
        kind: StrategyKind,
        ws: StrategyWorkingSet,
        settings: StrategySettings,
        parent=None,
        *,
        editable: bool = True,
        archmastery_default: Callable[[], bool | None] = lambda: None,
    ):
        super().__init__(parent)
        self._kind = kind
        self._ws = ws
        self._settings = settings
        self._editable = editable
        self._archmastery_default = archmastery_default
        #: The vocabulary the filter's chips are matched by: the library's, with the owner's word first in Source.
        self._tag_groups = tag_groups(self.own_source)
        self._selected_id: str | None = None
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []
        #: Per entry id: what the facet filter asks of it, and which section it is in.
        self._facets: dict[str, StrategyFacets] = {}
        self._section_of: dict[str, str] = {}
        self._favorite_ids: set[str] = set()
        #: Section header rows, top to bottom, each with the section it heads.
        self._headers: list[tuple[QListWidgetItem, str]] = []
        #: The note row of each section that has one, by the section's key.
        self._notes: dict[str, tuple[QListWidgetItem, QWidget]] = {}
        #: Each card's health is drawn from the DSL checks already known; the rest are
        #: checked off the GUI thread, and a card that turns out broken is redrawn.
        self._checks = DeferredDslChecks(self, self._redraw_cards)

        layout = QVBoxLayout(self)
        # A real inset on all four sides. The page used to run edge to edge inside the tab
        # pane, so the filter box, the cards and the fields all sat directly against its
        # border — which is most of what made a screen of otherwise ordinary controls read
        # as crowded. A line with nothing beside it reads as a wall.
        layout.setContentsMargins(_PAGE_INSET, _PAGE_INSET, _PAGE_INSET, _PAGE_INSET)
        layout.setSpacing(6)

        # Across the whole tab, not over the card list alone: four pickers and a search
        # box need more width than the list has, and the chips need a row of their own.
        self._filter_bar = StrategyFilterBar(kind, self, own_source=self._filter_source())
        # Filtering hides rows; it never rebuilds them. See _apply_filter.
        self._filter_bar.changed.connect(self._apply_filter)
        layout.addWidget(self._filter_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_master())
        splitter.addWidget(self._build_detail())
        # The detail side is where the long lines live; the card list only needs
        # enough width for a name and a subtitle.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        # Styled before the cards go in: a list's stylesheet re-polishes every row widget
        # it holds, so set afterwards it cost a pass over every card.
        self._apply_style()
        self.rebuild_list()

    # -- hooks ----------------------------------------------------------

    def _filter_source(self) -> str | None:
        """The owner's Source word the filter offers, on a page that shows an owner's entries."""
        return None

    def _make_about(self) -> AboutSection:
        """The detail pane's About section, tagged with the groups this kind stores."""
        return AboutSection(tag_groups=self._about_groups())

    def _about_groups(self) -> tuple[str, ...]:
        return tuple(g for g in KIND_GROUPS[self._kind] if g in STORED_GROUPS)

    def _on_empty_section(self, key: str) -> None:
        """A section was drawn with no entries in it. A subclass may say so with :meth:`_add_section_note`."""

    def _favorable(self, section: str) -> bool:
        """Whether the entries in *section* can be favorites: the library's can."""
        return True

    def _new_card(self, summary: StrategySummary, favorite: bool | None) -> StrategyCard:
        """The card one entry is drawn as."""
        return StrategyCard(summary, favorite=favorite)

    def _create_block(self) -> str:
        """Why New and Duplicate have nowhere to put an entry, or empty when they do."""
        return ""

    def _set_own_notes(self, usage: str) -> None:
        """Say where an entry of the owner's own lives. The name alone is enough: its section's heading names it too."""
        self._about.set_notes(usage, f"In '{self._ws.owner.name}' only")

    @property
    def creates_own(self) -> bool:
        """Whether New and Duplicate put their entry in the open owner rather than the library."""
        return False

    def card_menu(self, uuid: str) -> QMenu | None:
        """The menu a card shows on a right-click; None for no menu. A product's own verbs go here."""
        return None

    # -- construction ---------------------------------------------------

    def _build_master(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, _SPLITTER_GUTTER, 0)
        box.setSpacing(4)

        self._list = QListWidget()
        self._list.setSpacing(2)
        # Filtering changes how many cards there are, and so whether the list scrolls.
        reserve_scrollbar_gutter(self._list)
        # A column of cards scrolls one way. _fit_row keeps every row inside the
        # viewport, so this only says so; it never hides content that had a bar.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.currentItemChanged.connect(self._on_item_changed)
        # A product's own verbs on a card's right-click, as BartleBot's Export… (#170) is.
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_card_menu)
        # Rows are sized for the width the column actually has; see _fit_row.
        self._list.viewport().installEventFilter(self)
        box.addWidget(self._list, 1)

        # The same three buttons wherever the library is edited; what differs is where New
        # and Duplicate put their entry. None at all in Pick (#123).
        self._button_row = QWidget(panel)
        buttons = QHBoxLayout(self._button_row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        self._new_btn = ActionButton("plus", "New")
        self._new_btn.clicked.connect(self._on_new)
        buttons.addWidget(self._new_btn, 1)
        self._dup_btn = ActionButton("copy", "Duplicate")
        self._dup_btn.clicked.connect(self._on_duplicate)
        buttons.addWidget(self._dup_btn, 1)
        # Caution from the start, and downgraded to tertiary only once the origin says
        # this button is a Revert — see ``_update_labels``.
        self._del_btn = ActionButton("trash", _DELETE_TEXT, style=ActionStyle.CAUTION)
        self._del_btn.clicked.connect(self._on_delete)
        buttons.addWidget(self._del_btn, 1)
        box.addWidget(self._button_row)
        self._button_row.setVisible(self._editable)
        return panel

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(_SPLITTER_GUTTER, 0, 0, 0)
        box.setSpacing(6)

        self._about = self._make_about()
        self._about.changed.connect(self._on_edited)
        self._about.set_editable(self._editable)
        box.addWidget(self._about)

        if self._kind is StrategyKind.DUEL:
            # No wrapper around the config editor: a DuelCombatStrategy IS-A DuelConfig,
            # and the one field the library entry added — the name — is the About
            # section's now.
            self._editor = DuelConfigEditor()
            self._editor.promote_requested.connect(self.promote_requested)
            self._editor.open_pcs_requested.connect(self.open_pcs_requested)
        elif self._kind is StrategyKind.PLAYER:
            self._editor = PlayerStrategyEditor()
        else:
            self._editor = DeckPresetEditor(archmastery_default=self._archmastery_default)
        self._editor.changed.connect(self._on_edited)
        self._editor.set_editable(self._editable)

        # One outer scroll area, never a nested one: DuelConfigEditor grows its
        # rows to their full height on purpose and expects the page around them
        # to scroll.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # An assignment card folding open is what most often tips this pane into
        # scrolling, and without a reserved gutter every card narrows under the click.
        reserve_scrollbar_gutter(self._scroll)
        self._scroll.setWidget(self._editor)
        box.addWidget(self._scroll, 1)

        self._empty_lbl = QLabel(
            "Select a strategy on the left, or press + New." if self._editable else "Select a strategy on the left."
        )
        apply_muted_color(self._empty_lbl)
        box.addWidget(self._empty_lbl)
        return panel

    # -- reading --------------------------------------------------------

    def kind(self) -> StrategyKind:
        return self._kind

    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def editable(self) -> bool:
        """False in Pick, which selects and edits nothing."""
        return self._editable

    @property
    def new_button(self) -> ActionButton:
        return self._new_btn

    @property
    def duplicate_button(self) -> ActionButton:
        return self._dup_btn

    @property
    def delete_button(self) -> ActionButton:
        return self._del_btn

    @property
    def button_row(self) -> QWidget:
        """New, Duplicate and Delete. Public so a test can see Pick has none of them."""
        return self._button_row

    @property
    def editor(self):
        """The detail pane's editor. Public so a test can check it is read-only in Pick."""
        return self._editor

    def summarize(self, item) -> StrategySummary:
        if self._kind is StrategyKind.DUEL:
            # Resolved the way a run resolves them, the owner's own first — so one of its
            # duels names its own player strategies, and a library duel one only when
            # a hand-edited file broke the rule, which it then shows rather than hides.
            summary = summarize_duel(
                item, self._ws.in_reach(StrategyKind.PLAYER), name=item.name, validate=self._checks.validator(item.id)
            )
        elif self._kind is StrategyKind.PLAYER:
            summary = summarize_player(item, validate=self._checks.validator(item.id))
        else:
            summary = summarize_deck(item)
        if self._ws.is_own_entry(self._kind, item.id):
            return replace(summary, owner_name=self._ws.owner.name)
        return replace(
            summary,
            source=self._ws.status(self._kind, item.id),
            official_updated=self._ws.official_updated(self._kind, item.id),
        )

    @property
    def filter_bar(self) -> StrategyFilterBar:
        """The tab's facet pickers and search box."""
        return self._filter_bar

    def section_headings(self) -> list[str]:
        """The visible section headers, top to bottom. Empty when there is one section."""
        return [h.data(_HEADING_ROLE) for h, _key in self._headers if not h.isHidden()]

    def visible_ids(self) -> list[str]:
        """The ids of the cards the filter leaves showing, top to bottom."""
        return [i.data(Qt.ItemDataRole.UserRole) for i, _c in self._card_map if not i.isHidden()]

    def ids_in_section(self, section: str) -> list[str]:
        """The ids drawn in *section* (``FAVORITES_SECTION``, ``LIBRARY_SECTION``, or a subclass's), top to bottom."""
        return [
            i.data(Qt.ItemDataRole.UserRole)
            for i, _c in self._card_map
            if self._section_of.get(i.data(Qt.ItemDataRole.UserRole)) == section
        ]

    def section_note_text(self, section: str) -> str:
        """What *section*'s note row says, or empty when it has none."""
        item, widget = self._notes.get(section, (None, None))
        if item is None or item.isHidden():
            return ""
        label = widget.findChild(QLabel)
        return label.text() if label is not None else ""

    def card_for(self, uuid: str) -> StrategyCard | None:
        """The card drawn for ``uuid``, or None. Public so a test can read what a card says."""
        for item, card in self._card_map:
            if item.data(Qt.ItemDataRole.UserRole) == uuid:
                return card
        return None

    # -- the card list --------------------------------------------------

    def _sections(self) -> list[tuple[str, str, list]]:
        """``(key, heading, entries)`` per section, top to bottom; a heading may be empty."""
        return self._library_sections()

    def _library_sections(
        self, exclude: frozenset[str] | set[str] = frozenset(), heading: str = ""
    ) -> list[tuple[str, str, list]]:
        """★ Favorites, then the library under *heading*, without the ids *exclude* (#118)."""
        library = [e for e in self._ws.items(self._kind) if e.id not in exclude]
        saved = set(self._settings.favorite_strategies(self._kind.value))
        self._favorite_ids = {e.id for e in library if e.id in saved}
        favorites = [e for e in library if e.id in self._favorite_ids]
        others = [e for e in library if e.id not in self._favorite_ids]
        if favorites:
            return [
                (FAVORITES_SECTION, FAVORITES_HEADING, favorites),
                (LIBRARY_SECTION, others_heading(self._kind), others),
            ]
        return [(LIBRARY_SECTION, heading, others)]

    def rebuild_list(self) -> None:
        """Redraw every card. Selection is restored by id, not by row index.

        Inside :func:`held_listings`: each card asks where its file is, and without it
        every one of those questions listed and checked the strategy folders again.

        Every entry gets a row, filtered-out ones included — the filter only
        decides which rows are *visible*. Building a ``StrategyCard`` means
        summarizing the entry and building its widgets, so this is far too expensive
        to run per keystroke.
        Call it when the list's *contents* change; call ``_apply_filter`` when
        only the query does.
        """
        with held_listings():
            self._rebuild_list()

    def _rebuild_list(self) -> None:
        self._checks.reset()
        blocked = self._list.blockSignals(True)
        try:
            self._list.clear()
            self._card_map.clear()
            self._facets.clear()
            self._section_of.clear()
            self._headers.clear()
            self._notes.clear()
            keep: QListWidgetItem | None = None
            for key, heading, section in self._sections():
                if heading:
                    self._add_header(heading, key)
                if not section:
                    self._on_empty_section(key)
                for entry in section:
                    summary = self.summarize(entry)
                    card = self._make_card(entry.id, summary, favorable=self._favorable(key))
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, entry.id)
                    item.setData(_HAYSTACK_ROLE, summary_haystack(summary, self.own_source))
                    self._fit_row(item, card)
                    self._list.addItem(item)
                    self._list.setItemWidget(item, card)
                    self._card_map.append((item, card))
                    self._facets[entry.id] = summary_facets(summary, self.own_source)
                    self._section_of[entry.id] = key
                    if entry.id == self._selected_id:
                        keep = item
                        card.set_selected(True)
            # Reselected inside the blocked region: every item here is a new
            # QListWidgetItem, so Qt would report a selection change that did not
            # happen, and the slot would reload the editor mid-keystroke.
            if keep is not None:
                self._list.setCurrentItem(keep)
        finally:
            self._list.blockSignals(blocked)
        self._filter_bar.set_counts(self._facets.values())
        self._apply_filter()
        self._update_create_buttons()
        self._checks.request_unknown()
        if keep is None:
            # Actually gone — deleted, or never there. Detach the editor. An entry
            # that is merely filtered out of view stays loaded: typing in the
            # filter box must not empty the pane being edited.
            self._selected_id = None
            self._load_selection()

    def _apply_filter(self) -> None:
        """Show or hide each existing row against the query. No widgets are built.

        Signals stay blocked throughout: hiding the current row can make Qt move
        the current item, and that would reload the detail pane mid-keystroke —
        the defect ``tests/qt/combat/test_strategy_library_filter.py`` exists for. The
        selected row is restored afterwards even when the query hides it, so the
        editor keeps the entry it was given.
        """
        query = self._filter_bar.query()
        flt = self._filter_bar.selection()
        blocked = self._list.blockSignals(True)
        try:
            shown: set[str] = set()
            for item, _card in self._card_map:
                uuid = item.data(Qt.ItemDataRole.UserRole)
                facets = self._facets.get(uuid, StrategyFacets())
                visible = matches_query(item.data(_HAYSTACK_ROLE) or "", query) and facets_match(
                    facets, flt, self._tag_groups
                )
                item.setHidden(not visible)
                if visible:
                    shown.add(uuid)
            shown_sections = {self._section_of[uuid] for uuid in shown}
            for header, key in self._headers:
                # A section the filter has emptied loses its header too, or it would sit
                # over nothing — or, worse, appear to head the section below it. A section
                # empty to begin with keeps its header while its note row is under it.
                header.setHidden(key not in shown_sections and key not in self._notes)
            current = self._current_item()
            if current is not None and self._list.currentItem() is not current:
                self._list.setCurrentItem(current)
        finally:
            self._list.blockSignals(blocked)

    def _current_item(self) -> QListWidgetItem | None:
        """The row for ``_selected_id``, visible or not."""
        for item, _card in self._card_map:
            if item.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                return item
        return None

    def clear_filter(self) -> None:
        """Drop every facet and empty the search box, so every row is visible again."""
        self._filter_bar.clear()

    # -- sections and favorites -----------------------------------------

    def _add_header(self, text: str, key: str) -> None:
        """A section header row: not selectable, not focusable, skipped by the arrow keys."""
        item = QListWidgetItem()
        item.setData(_HEADING_ROLE, text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        heading = RuleHeading(text)
        item.setSizeHint(heading.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, heading)
        self._headers.append((item, key))

    def _add_section_note(self, key: str, row: QWidget) -> None:
        """Put *row* under the header just added, in place of the entries *key*'s section lacks."""
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._fit_row(item, row)
        self._list.addItem(item)
        self._list.setItemWidget(item, row)
        self._notes[key] = (item, row)

    def _fit_row(self, item: QListWidgetItem, card: QWidget) -> None:
        """Size *item* to what *card* needs at the column's real width.

        Not ``card.sizeHint()``: its height wraps the name at whatever width Qt's own
        guess for a word-wrapped label picks, not at the column's. A name that fits on
        one line was still given a two-line row, and the card spread the slack between
        its lines -- so an edit that turned the source pill into the wider "Official
        (modified)", really wrapping the name, visibly tightened the card's spacing.

        The width asked for is only the card's minimum: a list row fills the viewport
        anyway, and asking for more is what gives the list a horizontal scrollbar.

        And never more than the column, even when the minimum is wider. A list's rows
        are all as wide as its widest, so one card that could not fit -- a long name in a
        pill did it -- widened every row past the edge and clipped the stars
        off cards that fit perfectly well. Capped, a card that still cannot fit is
        clipped on its own. The card elides its text so that should not happen; this is
        what keeps it one card's problem if it does.

        A section's note row is fitted the same way: its sentence wraps too, and a row left
        at the height it had when the column was wider cuts it off.
        """
        width = self._list.viewport().width() - 2 * (self._list.spacing() + _ROW_PAD)
        height = card.heightForWidth(width)
        item.setSizeHint(
            QSize(min(card.minimumSizeHint().width(), width), height if height > 0 else card.sizeHint().height())
        )

    def _refit_rows(self) -> None:
        for item, card in self._card_map:
            self._fit_row(item, card)
        for item, row in self._notes.values():
            self._fit_row(item, row)
        # And move the cards to match. The view narrows its own row rectangles on a
        # resize, but a widget set into a row keeps its old geometry until a full items
        # layout: a tab first laid out while hidden, wider than its column turned out to
        # be, opened with every card overhanging the column, its pill and star cut off.
        self._list.doItemsLayout()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            if event.size().width() != event.oldSize().width():
                self._refit_rows()
        return super().eventFilter(obj, event)

    def _make_card(self, uuid: str, summary: StrategySummary, *, favorable: bool = True) -> StrategyCard:
        # No star on an entry of the owner's own: favorites are a preference about the library.
        card = self._new_card(summary, (uuid in self._favorite_ids) if favorable else None)
        card.favorite_toggled.connect(lambda wanted, uuid=uuid: self.set_favorite(uuid, wanted))
        return card

    def is_favorite(self, uuid: str) -> bool:
        return uuid in self._favorite_ids

    def set_favorite(self, uuid: str, wanted: bool) -> None:
        """Star or unstar ``uuid``, saving at once, and move its card to the right section.

        Saved now, not on Save: a favorite is a preference about the library, not a
        change to it, and so it is not something Cancel should take back.
        """
        if self._settings.is_favorite_strategy(self._kind.value, uuid) != wanted:
            self._settings.toggle_favorite_strategy(self._kind.value, uuid)
        # Safe inside the star's own click: clearing the list hands each card to
        # deleteLater, so the button being clicked outlives this call.
        self.rebuild_list()

    def focus_editor(self) -> None:
        """Move focus into the detail pane's main field, where the editor has one.

        Only the Player tab's does — its DSL box. The other editors have no single field
        a visitor has obviously come for, so focus stays where it is.
        """
        if isinstance(self._editor, PlayerStrategyEditor):
            self._editor.focus_dsl()

    def select(self, uuid: str | None) -> None:
        """Select ``uuid`` if it is in the list, loading it into the detail pane."""
        self._selected_id = uuid
        for item, card in self._card_map:
            chosen = item.data(Qt.ItemDataRole.UserRole) == uuid
            card.set_selected(chosen)
            if chosen:
                self._list.setCurrentItem(item)
        self._load_selection()

    def _on_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        # Whatever the card being left was last drawn from is checked now: a DSL typed and
        # left before the box's debounce fired would otherwise wait for the next rebuild.
        self._checks.request_unknown()
        for item, card in self._card_map:
            if item is previous:
                card.set_selected(False)
            elif item is current:
                card.set_selected(True)
        self._selected_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self._load_selection()

    def _refresh_selected_card(self) -> None:
        """Redraw just the selected card, so a rename shows without a full rebuild.

        A rebuild on every keystroke would be wasteful and would re-sort the list
        under the user mid-word; the alphabetical position catches up the next
        time the list is rebuilt for a reason of its own.

        Runs on every edit, DSL keystrokes included, so the card's health is drawn
        from the checks already known and the new text is not parsed here: the DSL
        box's own debounce checks it off the GUI thread, and :meth:`_redraw_cards`
        follows if it does not parse.
        """
        if self._selected_id is not None:
            self._refresh_card(self._selected_id)

    def _redraw_cards(self, uuids: set) -> None:
        """Redraw the cards drawn as healthy whose DSL has since turned out not to parse."""
        for uuid in uuids:
            self._refresh_card(uuid)

    def _refresh_card(self, uuid: str) -> None:
        """Redraw the card for *uuid* in place, if it is in the list."""
        with held_listings():
            self._refresh_card_now(uuid)

    def _refresh_card_now(self, uuid: str) -> None:
        entry = self._ws.get(self._kind, uuid)
        if entry is None:
            return
        for item, _card in list(self._card_map):
            if item.data(Qt.ItemDataRole.UserRole) != uuid:
                continue
            summary = self.summarize(entry)
            card = self._make_card(entry.id, summary, favorable=self._favorable(self._section_of.get(entry.id, "")))
            card.set_selected(uuid == self._selected_id)
            self._fit_row(item, card)
            item.setData(_HAYSTACK_ROLE, summary_haystack(summary, self.own_source))
            # The facets follow the edit, and so do the counts; whether the row still
            # passes the filter is left for the next filter change, as the name is.
            self._facets[entry.id] = summary_facets(summary, self.own_source)
            self._filter_bar.set_counts(self._facets.values())
            self._list.setItemWidget(item, card)
            self._card_map = [(i, card if i is item else c) for i, c in self._card_map]
            return

    def set_deck_link(self, link: DeckClientLink | None) -> None:
        """Give the Deck tab its way to the game clients; any other tab has none to give."""
        if isinstance(self._editor, DeckPresetEditor):
            self._editor.set_link(link)

    # -- the detail pane ------------------------------------------------

    def _load_selection(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            self._editor.clear()
            self._about.clear()
        else:
            self._about.load(entry)
            if self._kind is StrategyKind.DUEL:
                # What an assignment may link depends on where the duel lives: a library
                # duel may not use an owner's own player strategies (#118).
                self._editor.load(entry, self._ws.players_for_duel(entry.id))
            else:
                self._editor.load(entry)
        # Per entry, not per page: one saved by a newer version is read-only wherever it is.
        editable = self._editable and not self._ws.is_read_only(self._kind, self._selected_id)
        self._about.set_editable(editable)
        self._editor.set_editable(editable)
        self._about.setVisible(entry is not None)
        self._scroll.setVisible(entry is not None)
        self._empty_lbl.setVisible(entry is None)
        self._update_create_buttons()
        self._del_btn.setEnabled(entry is not None)
        if entry is not None:
            self._update_labels()

    def _update_create_buttons(self) -> None:
        """New needs somewhere to put its entry; Duplicate also needs something to copy."""
        block = self._create_block()
        self._new_btn.setEnabled(not block)
        # Not one saved by a newer version: the copy would be in that format too, and refused at Save.
        copyable = self._ws.get(self._kind, self._selected_id) is not None
        self._dup_btn.setEnabled(not block and copyable and not self._ws.is_read_only(self._kind, self._selected_id))
        self._new_btn.setToolTip(block)
        self._dup_btn.setToolTip(block)

    def _update_labels(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        usage = self._ws.usage(entry.id).phrase()
        if self._ws.is_own_entry(self._kind, entry.id):
            # Never built-in and never reverted: an own entry's Delete is a plain one.
            self._set_delete_button(revert=False, built_in=False)
            self._set_own_notes(usage)
            return
        # The working set's status, not the file's origin: an edit made a moment ago
        # already makes a built-in revertible, before anything is written (#124).
        status = self._ws.status(self._kind, entry.id)
        revert = status is Origin.SHIPPED_OVERRIDE
        built_in = status is Origin.SHIPPED
        self._set_delete_button(revert=revert, built_in=built_in)
        if self._ws.is_read_only(self._kind, entry.id):
            self._del_btn.setEnabled(False)
            note = newer_format_note()
            self._del_btn.setToolTip(note)
            self._about.set_notes(usage, note)
            return
        if revert and self._ws.official_updated(self._kind, entry.id):
            note = OFFICIAL_UPDATED_NOTE
        elif revert:
            note = f"Your copy of a strategy {product.current().name} ships — reverting restores the original."
        elif built_in:
            note = f"Ships with {product.current().name} — editing it saves your own copy."
        else:
            note = ""
        self._about.set_notes(usage, note)

    def _set_delete_button(self, *, revert: bool, built_in: bool) -> None:
        self._del_btn.setText(_REVERT_TEXT if revert else _DELETE_TEXT)
        self._del_btn.set_icon_name("revert" if revert else "trash")
        # A revert is not a caution: the shipped version comes back, so there is nothing
        # to lose — which is exactly the distinction the two verbs exist to draw.
        self._del_btn.set_action_style(ActionStyle.TERTIARY if revert else ActionStyle.CAUTION)
        self._del_btn.setEnabled(not built_in)
        self._del_btn.setToolTip(_BUILT_IN_DELETE_TIP if built_in else "")

    def _on_edited(self) -> None:
        self._refresh_selected_card()
        self._update_labels()

    # -- create / duplicate / remove / move -----------------------------

    def _on_new(self) -> None:
        self._show_made(self._ws.create(self._kind, NEW_NAMES[self._kind], own=self.creates_own))

    def start_new(self, copy_of: str | None = None):
        """Open on a new entry rather than an empty pane, and return it (#272).

        A copy of *copy_of* when it is here, under New's name and with no description,
        since both described the original: it is a new strategy that starts from that
        one's setup. Otherwise New's own entry. None where nothing can be made, in Pick.
        """
        if not self._editable:
            return None
        source = self._ws.get(self._kind, copy_of)
        if source is None:
            made = self._ws.create(self._kind, NEW_NAMES[self._kind], own=self.creates_own)
        else:
            made = self._ws.duplicate(self._kind, source.id, own=self.creates_own)
            made.name = self._ws.unique_name(self._kind, NEW_NAMES[self._kind])
            made.description = ""
        self._show_made(made)
        return made

    def _show_made(self, made) -> None:
        self._selected_id = made.id
        self.rebuild_list()
        self.select(made.id)
        self.structure_changed.emit()

    def _show_card_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        uuid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(uuid, str) or self._ws.get(self._kind, uuid) is None:
            return
        menu = self.card_menu(uuid)
        if menu is not None:
            menu.exec(self._list.viewport().mapToGlobal(pos))

    def _on_duplicate(self) -> None:
        if self._selected_id is None:
            return
        # Where a product's page makes its own, a copy always lands there, a library entry's
        # included: that is how one of BartleBot's fights gets something slightly different (#118).
        copy = self._ws.duplicate(self._kind, self._selected_id, own=self.creates_own)
        if copy is not None:
            self._show_made(copy)

    def _on_delete(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        status = self._ws.status(self._kind, entry.id)
        if status is Origin.SHIPPED:
            return  # nothing to delete or revert; the button is disabled for this
        revert = status is Origin.SHIPPED_OVERRIDE
        verb = "Revert" if revert else "Delete"
        if revert:
            # No usage warning: the id stays, so everything linking it keeps working.
            detail = f"“{entry.name}” will go back to the version {product.current().name} ships."
        else:
            detail = f"“{entry.name}” will be removed when you save."
            usage = self._ws.usage(entry.id)
            if usage.total:
                detail += f"\n\n{usage.phrase()} — {DELETE_USED_NOTE}."
        answer = QMessageBox.question(
            self,
            f"{verb} strategy?",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        if revert:
            # The shipped version replaces the live object here and now, so the entry
            # stays listed and selected; the editor must reload, since it holds the
            # object that was just replaced.
            self._ws.revert(self._kind, entry.id)
            self.rebuild_list()
            self.select(entry.id)
        else:
            self._ws.remove(self._kind, entry.id)
            self._selected_id = None
            self.rebuild_list()
        self.structure_changed.emit()

    # -- cross-tab refresh ----------------------------------------------

    def refresh_pcs_list(self) -> None:
        """Re-read the Player Strategies the duel editor offers. Duel tab only."""
        if self._kind is StrategyKind.DUEL:
            self._editor.set_pcs_list(self._ws.players_for_duel(self._selected_id))

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Color the assignment cards' slot pills. Duel tab only; a no-op elsewhere."""
        if self._kind is StrategyKind.DUEL:
            self._editor.set_player_color_keys(keys, labels)

    @property
    def about(self) -> AboutSection:
        """This tab's About section. Public because the entry's name and description are
        edited there now, and a caller checking what a tab says has to be able to ask.
        """
        return self._about

    # -- theme ----------------------------------------------------------

    def _apply_style(self) -> None:
        # No border on the list itself. It holds a column of cards, each of which draws
        # its own edge when it is selected, and the splitter handle already says where
        # this pane stops — a box around a column of boxes is one line too many.
        set_style_sheet(
            self._list,
            f"QListWidget {{ border: none; background: {THEME.bg}; }}"
            f"QListWidget::item {{ background: transparent; border: none; padding: {_ROW_PAD}px; }}"
            f"QListWidget::item:selected {{ background: transparent; border: none; }}"
            f"QListWidget::item:hover {{ background: transparent; border: none; }}",
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


class CombatStrategyDialog(QDialog):
    """The combat edit surface. See the module docstring for the modes, and for what a subclass adds."""

    def __init__(
        self,
        ws: StrategyWorkingSet,
        settings: StrategySettings,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.LIBRARY,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = NONE_LABEL,
        player_color_keys: list[int | None] | None = None,
        player_account_labels: list[str] | None = None,
        deck_link: DeckClientLink | None = None,
        parent=None,
        *,
        none_tooltip: str = NONE_TOOLTIP,
        team_size: int | None = None,
        flow: FlowStep | None = None,
        start_new: bool = False,
        start_from: str | None = None,
        archmastery_default: Callable[[], bool | None] = lambda: None,
    ):
        """*settings* keeps the favorites. *deck_link* is the Deck tab's way to the game clients:
        without one it has no Import and no Apply. *archmastery_default* is the product's own
        setting for a deck that defers to it, asked when it is drawn; None where it has none.

        *team_size* opens the Duel tab filtered to the duels a team of that size can run,
        as a chip the user can remove (BartleBot's Settings › Combat's Solo and Team cards).

        *flow* makes the dialog one step of a longer flow, and *start_new* opens it on a new
        entry of *kind*, a copy of *start_from* when that is given (#272; the module
        docstring has both). *start_new* does nothing in Pick, which makes nothing."""
        super().__init__(parent)
        self._ws = ws
        self._settings = settings
        self._mode = mode
        self._pick_kind = kind
        self._archmastery_default = archmastery_default
        # What falling through to nothing means is a property of the binding's level,
        # not of this dialog — the Default Duel Strategies on BartleBot's default card, the
        # bot's default on a fight — so the caller names it, and says it.
        self._allow_none = allow_none
        self._none_label = none_label
        self._none_tooltip = none_tooltip
        self._selected_id: str | None = current_id
        self._selected_was_created = False
        self._used_none = False
        self._flow = flow
        # The entry start_new opened on, and how it looked then: see _drop_unused_starter.
        self._starter: tuple[str, dict] | None = None
        self._focus_name_on_show = False
        if flow is not None:
            self.setWindowTitle(flow.title)
        else:
            self.setWindowTitle("Pick a Combat Strategy" if self.picks else "Combat Strategies")
        self._prepare_window()
        self.setMinimumSize(880, 560)
        # Wider than the minimum so the card sidebar isn't fighting the detail
        # pane for space by default — see issue #44 — and wide enough that a card
        # carrying the longest source pill, "Official (modified)", fits the column.
        self.resize(1200, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self._step_heading = StepHeading(flow.step, self) if flow is not None else None
        if self._step_heading is not None:
            layout.addWidget(self._step_heading)

        self._tabs = QTabWidget()
        self._pages: dict[StrategyKind, KindPage] = {}
        for k in self._tabs_shown():
            page = self._make_page(k)
            page.structure_changed.connect(self._on_structure_changed)
            page.promote_requested.connect(self._on_promote_requested)
            page.open_pcs_requested.connect(self._on_open_pcs_requested)
            self._pages[k] = page
            self._tabs.addTab(page, KIND_LABELS[k])
        # The Duel tab's pencil opens a Player Strategy on the Player tab; without one
        # (Pick) it has nowhere to go, so it is grayed rather than a dead button.
        duel = self._pages.get(StrategyKind.DUEL)
        if duel is not None:
            duel.editor.set_can_open_pcs(StrategyKind.PLAYER in self._pages)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        # Which account is behind each player slot, if anything is hooked. Resolved by the
        # caller rather than read here: this dialog knows nothing about clients, and what
        # opens it already has the client manager in reach. Empty is the normal case — a
        # duel is usually authored with nothing running — and the pills simply go
        # uncolored. See qt/widgets/pills/player_pills.py.
        if StrategyKind.DUEL in self._pages:
            self._pages[StrategyKind.DUEL].set_player_color_keys(player_color_keys or [], player_account_labels or [])

        # Likewise the clients a deck can be read from or put on: the caller owns them.
        deck_page = self._pages.get(StrategyKind.DECK)
        if deck_page is not None:
            deck_page.set_deck_link(deck_link)

        start = self._pages.get(kind) or next(iter(self._pages.values()))
        self._tabs.setCurrentWidget(start)
        if team_size is not None and StrategyKind.DUEL in self._pages:
            self._pages[StrategyKind.DUEL].filter_bar.set_team_size(team_size)
        start.select(current_id)
        if start_new and kind in self._pages:
            made = self._pages[kind].start_new(start_from)
            if made is not None:
                self._starter = (made.id, made.model_dump())
                self._focus_name_on_show = True

        layout.addLayout(self._build_buttons())

    # -- hooks ----------------------------------------------------------

    def _prepare_window(self) -> None:
        """Set the window itself up, titled and before anything is in it: its flags, its help."""

    def _tabs_shown(self) -> tuple[StrategyKind, ...]:
        """The tabs this way in shows, in order."""
        return tabs_for(self._mode, self._pick_kind)

    def _make_page(self, kind: StrategyKind) -> KindPage:
        """The page of one tab."""
        return KindPage(
            kind,
            self._ws,
            self._settings,
            editable=not self.picks,
            archmastery_default=self._archmastery_default,
        )

    def _result_page(self) -> KindPage | None:
        """The page whose selection a commit returns, outside Pick: the tab showing."""
        page = self._tabs.currentWidget()
        return page if isinstance(page, KindPage) else None

    def _owner_open(self) -> bool:
        """Whether the window is open on an owner, so the Save confirmation says *another* one."""
        return False

    def _owner_label(self, owner: StrategyOwner) -> str | None:
        """What the Save confirmation adds to an owner's name; None for the name alone."""
        return None

    # -- reading --------------------------------------------------------

    @property
    def mode(self):
        """The way in it was opened by."""
        return self._mode

    @property
    def picks(self) -> bool:
        """Whether it selects and edits nothing: Pick."""
        return self._mode is DialogMode.PICK

    @property
    def step_heading(self) -> StepHeading | None:
        """The "Step N of M" heading a flow gave this dialog, or None."""
        return self._step_heading

    @property
    def commit_button(self) -> ActionButton:
        return self._commit_btn

    def tab_kinds(self) -> list[StrategyKind]:
        """The kinds this dialog has a tab for, in tab order."""
        return list(self._pages)

    def page(self, kind: StrategyKind) -> KindPage | None:
        """The tab for *kind*, or None when this way in does not show it."""
        return self._pages.get(kind)

    # -- buttons --------------------------------------------------------

    def _build_buttons(self) -> QHBoxLayout:
        # This footer was the worked example the shared helper was drawn from, and it is
        # built through it now so there is exactly one implementation. The weights are
        # unchanged: the commit is what this dialog exists to reach, so it is the one
        # PRIMARY on the screen; choosing no strategy, so the level below answers, is a
        # real alternative to it and takes the outline; Cancel is neither. See #47/#89.
        extras: list[ActionButton] = []
        if self.picks and self._allow_none:
            self._none_btn = ActionButton("revert", f"Use {self._none_label}", style=ActionStyle.SECONDARY, fill=False)
            self._none_btn.setToolTip(self._none_tooltip)
            self._none_btn.clicked.connect(self._on_use_none)
            extras.append(self._none_btn)
        # Both spellings of the same act: commit the working set. Use also
        # returns the selection — see the module docstring.
        if self._flow is not None:
            commit_text, commit_icon = self._flow.commit_text, self._flow.commit_icon
        else:
            commit_text, commit_icon = ("Use this" if self.picks else "Save"), "checkmark"
        footer = dialog_footer(
            self,
            commit_text=commit_text,
            commit_icon=commit_icon,
            on_commit=self._on_commit,
            extras=extras,
        )
        self._commit_btn = footer.commit
        return footer.layout

    def _on_use_none(self) -> None:
        self._used_none = True
        self._selected_id = None
        self._commit_and_accept()

    def _on_commit(self) -> None:
        if self.picks:
            source = self._pages[self._pick_kind]
            self._selected_id = source.selected_id()
            if self._selected_id is None and not self._allow_none:
                QMessageBox.information(
                    self,
                    "Nothing selected",
                    f"Choose a strategy on the {KIND_LABELS[self._pick_kind]} tab, or cancel.",
                )
                return
        else:
            page = self._result_page()
            if page is not None:
                self._selected_id = page.selected_id()
        self._commit_and_accept()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._focus_name_on_show:
            # On show, not in the constructor: a hidden window's focus does not stick.
            self._focus_name_on_show = False
            name = self._pages[self._pick_kind].about.name_edit
            name.setFocus()
            name.selectAll()

    def _drop_unused_starter(self, *, even_selected: bool = False) -> None:
        """Forget the entry ``start_new`` opened on, if it was never touched and not chosen.

        Otherwise looking around before choosing an existing strategy would save an unused
        copy, one more entry the user never asked for. Cancel
        forgets it *even_selected*, before asking whether anything will be lost: the dialog
        made it, not the user, so it is not a change of theirs to warn about.
        """
        if self._starter is None:
            return
        uuid, as_made = self._starter
        entry = self._ws.get(self._pick_kind, uuid)
        chosen = uuid == self._selected_id and not even_selected
        if entry is None or chosen or entry.model_dump() != as_made:
            return
        self._starter = None
        self._ws.remove(self._pick_kind, uuid)
        # Redrawn in case the dialog stays open, as it does when a write fails.
        self._pages[self._pick_kind].rebuild_list()

    def _commit_and_accept(self) -> None:
        if not self.picks:
            self._drop_unused_starter()
            uses = self._ws.shared_uses(self._owner_label)
            if uses and not self._confirm_shared_edits(uses):
                return
        # Asked before the commit, which baselines everything and so forgets what was new.
        self._selected_was_created = self._ws.was_created(self._pick_kind, self._selected_id)
        self._ws.commit()
        if self._ws.conflicts:
            self._resolve_conflicts(self._ws.conflicts)
            # Writes the copies the user chose to keep; a reloaded entry has nothing to write.
            self._ws.commit()
        if self._ws.is_dirty:
            # commit() logs and skips a failed write, leaving the entry dirty.
            # Closing anyway would lose it silently.
            QMessageBox.warning(
                self,
                "Some changes were not saved",
                "One or more strategies could not be written to disk. "
                "The dialog is staying open so nothing is lost — see the log for details.",
            )
            return
        self.accept()

    def _resolve_conflicts(self, conflicts: list[Conflict]) -> None:
        """Ask about each write Save refused, then redraw every tab on what was chosen.

        Another program (the other product, which shares this library) changed the file since
        this dialog loaded it, so writing would have lost its change. The user keeps
        theirs as a copy, the default because nothing is lost, or reloads the other
        program's. A file in a newer format, or a refused delete, can only be reloaded.
        """
        for conflict in conflicts:
            name = f"“{conflict.name}”"
            if conflict.newer_format:
                QMessageBox.information(
                    self,
                    "Saved by a newer version",
                    f"{name} was saved by a newer version, so your changes to it can't be saved. "
                    f"Update {product.current().name} to edit it.",
                )
                self._ws.reload(conflict.kind, conflict.uuid)
                continue
            if conflict.deleted:
                QMessageBox.information(
                    self,
                    "Changed by another program",
                    f"{name} was changed by another program while this window was open, so it was not "
                    "removed. Its new version is shown now.",
                )
                self._ws.reload(conflict.kind, conflict.uuid)
                continue
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Changed by another program")
            box.setText(
                f"{name} was changed by another program while this window was open, so your changes to it "
                "were not saved."
            )
            copy = box.addButton("Save mine as a copy", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Use theirs", QMessageBox.ButtonRole.DestructiveRole)
            box.setDefaultButton(copy)
            box.setEscapeButton(copy)
            box.exec()
            if box.clickedButton() is copy:
                self._ws.keep_as_copy(conflict.kind, conflict.uuid)
            else:
                self._ws.reload(conflict.kind, conflict.uuid)
        for page in self._pages.values():
            page.rebuild_list()
            # The editor may hold an object reload just replaced.
            page.select(page.selected_id())

    def _confirm_shared_edits(self, uses: list[SharedUse]) -> bool:
        """Ask once, at Save, before an edit reaches places the user can't see (#118).

        At Save rather than on the first keystroke: typing into a shared entry is not yet
        a decision, and a prompt per field would be one nobody reads.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Save changes to shared strategies?")
        box.setText(shared_edit_message(uses, other=self._owner_open()))
        save = box.addButton("Save anyway", QMessageBox.ButtonRole.AcceptRole)
        back = box.addButton("Go back", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
        box.exec()
        return box.clickedButton() is save

    # -- results --------------------------------------------------------

    def selected_id(self) -> str | None:
        """The picked id. ``None`` means no strategy here, so the level below answers."""
        return self._selected_id

    def selected_was_created(self) -> bool:
        """Whether the returned selection was made on this visit — by New, Duplicate or a
        promotion. What lets BartleBot's Combat tab link the card that opened its Bot Combat
        Strategy editor to a strategy made for it, and leave the card alone otherwise (#118)."""
        return self._selected_was_created

    def used_built_in_default(self) -> bool:
        """Whether the user chose no strategy, so the level below answers.

        Distinguishes "no strategy, deliberately" from ``selected_id() is None``
        after a Cancel, which the caller must not act on at all.
        """
        return self._used_none

    # -- cross-tab plumbing ---------------------------------------------

    def _on_structure_changed(self) -> None:
        # Another tab's list can change too: moving an owner's duel into the library takes
        # its player strategies with it.
        sender = self.sender()
        for page in self._pages.values():
            if page is not sender and page.kind() in OWN_KINDS:
                page.rebuild_list()
        if StrategyKind.DUEL in self._pages:
            self._pages[StrategyKind.DUEL].refresh_pcs_list()

    def _on_tab_changed(self, _index: int) -> None:
        # A Player Strategy renamed on its own tab has to show its new name in
        # every assignment selector, and this is the cheapest honest moment.
        duel = self._pages.get(StrategyKind.DUEL)
        if duel is not None:
            duel.refresh_pcs_list()
            duel.rebuild_list()

    def _on_promote_requested(self, row) -> None:
        """Turn one assignment row's custom DSL into a named Player Strategy.

        It lands where the duel lives: among the owner's own for one of its own, in the library
        for a library duel, which may only link library entries (#118).
        """
        assignment = row.assignment()
        dsl = assignment.dsl.strip()
        if not dsl:
            return
        duel = self._pages.get(StrategyKind.DUEL)
        own = duel is not None and self._ws.is_own_entry(StrategyKind.DUEL, duel.selected_id())
        suggested = self._ws.unique_name(StrategyKind.PLAYER, "New Player Strategy")
        name, ok = QInputDialog.getText(self, "Save as Player Strategy", "Name:", text=suggested)
        if not ok or not name.strip():
            return
        pcs = self._ws.create(StrategyKind.PLAYER, name.strip(), own=own)
        pcs.dsl = assignment.dsl
        pcs.schools = list(assignment.schools)
        if StrategyKind.PLAYER in self._pages:
            self._pages[StrategyKind.PLAYER].rebuild_list()
        if duel is not None:
            duel.refresh_pcs_list()
        row.link_to(pcs.id)

    def _on_open_pcs_requested(self, pcs_id: str) -> None:
        """Show one Player Strategy on its own tab — the Duel tab's pencil.

        The filter is cleared first: ``_apply_filter`` keeps a selected row selected
        even when the query hides it, so a stale query would load the strategy into the
        editor with no visible card saying which one it is. Nothing is saved or lost on
        the way — both tabs edit the one working set. Pick has no Player tab, so there
        the pencil has nowhere to go.

        Focus goes to the DSL box last: switching tabs hands it to the page's first
        focusable child, the filter, and the DSL is what the pencil promised.
        """
        page = self._pages.get(StrategyKind.PLAYER)
        if page is None:
            return
        page.clear_filter()
        self._tabs.setCurrentWidget(page)
        page.select(pcs_id)
        page.focus_editor()

    # -- closing --------------------------------------------------------

    def reject(self) -> None:
        """Cancel. Confirms first if there is anything to lose.

        There is nothing to roll back — dropping the working set *is* the whole
        of Cancel. The prompt exists only because the count is the one thing the
        user cannot see from the cards.
        """
        self._drop_unused_starter(even_selected=True)
        if self._ws.is_dirty:
            # An owner made here with nothing in it yet is still one change to lose.
            total = self._ws.changes().total or 1
            answer = QMessageBox.question(
                self,
                "Discard changes?",
                f"{total} change{'s' if total != 1 else ''} will be lost.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Discard:
                return
        super().reject()

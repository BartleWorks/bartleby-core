"""The tag picker, built on :mod:`picker_popup` and shared by every tagging surface.

An editor sets a thing's own tags; a library filters by them. Same vocabulary, same
grouping, same descriptions — so they share one adapter rather than each building
sections out of the groups and drifting apart.

The vocabulary is a parameter, a :class:`TagVocabulary`, always named by the caller, because
several things are tagged: the combat strategy dialog's is here
(:func:`strategy_vocabulary`, #125), and BartleBot's bots and subgraphs have theirs in its
``gui/widgets/pickers/tag_vocabularies.py``.

A library additionally passes ``counts``, which fills the popup's right-hand slot
with how many things carry each tag. That is the difference between guessing which tag
is worth clicking and being told, and it is meaningless in the editor, which is why
it is a parameter rather than something the widget works out for itself.

Moved from BartleBot's ``gui/widgets/pickers/tag_picker.py``, whose own vocabularies stayed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget

from bartleby.qt.art_signal import follow_art
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.cards.card_art_tile import art_icon
from bartleby.qt.widgets.pickers.picker_popup import PickerButton, PickerRow, Section
from bartleby.runtime.combat.strategy_tags import ROLE, SOURCE, tag_groups
from bartleby.runtime.game import card_art
from bartleby.shared.store_files import LISTED_ORIGINS, SOURCE_LABELS

#: Side of a tag's medallion wherever it is drawn. 16px was hard to read; the medallions
#: are 128px (two are 64px) game art, and 20px is the smallest that stays legible.
TAG_ICON_PX = 20


def tag_icon(tag: str) -> QIcon | None:
    """A tag's extracted medallion at :data:`TAG_ICON_PX`, or None -- the ordinary case.

    None on every install that has not run the extractor, which is most of them. Every
    caller keeps its text form for that case.

    Cached per :func:`card_art.cache_generation`, so ``card_art.forget_cache`` is what
    makes new art visible here too. The lookup stats the file twice, and BartleBot's library
    asks once per tag per card -- a few hundred stats a listing, which on Windows was a
    visible share of a keystroke in the search box.
    """
    return _tag_icon(tag, card_art.cache_generation())


@lru_cache(maxsize=64)
def _tag_icon(tag: str, _generation: int) -> QIcon | None:
    path = card_art.tag_icon_path(tag)
    return None if path is None else art_icon(path)


def group_accent(group: str, accents: Mapping[str, str]) -> str:
    """Resolve a group's dot color against the live theme."""
    return getattr(THEME, accents.get(group, "text_muted"))


def count_label(n: int, noun: tuple[str, str]) -> str:
    """The right-hand meta text for a tag used by *n* things, *noun* being one's name and several's.

    Carries the unit: "49" beside a name reads as ambiguous until you work out what
    it counts, and the row has room for the word.
    """
    return f"{n} {noun[0] if n == 1 else noun[1]}"


@dataclass(frozen=True)
class TagVocabulary:
    """Everything a picker, pill or chip needs to know about one set of tags.

    ``groups`` is the ordered vocabulary, ``accents`` names the ``THEME`` attribute
    each group is colored with (resolved at paint time, so a theme switch reaches
    it), and ``noun`` is what a count counts, singular and plural.
    """

    groups: Mapping[str, tuple[str, ...]]
    accents: Mapping[str, str]
    noun: tuple[str, str]

    @property
    def tags(self) -> tuple[str, ...]:
        """Every tag, in presentation order."""
        return tuple(t for group in self.groups.values() for t in group)

    def group_of(self, tag: str) -> str:
        """The group *tag* belongs to, or an empty string."""
        for group, tags in self.groups.items():
            if tag in tags:
                return group
        return ""

    def accent(self, tag: str) -> str:
        """*tag*'s color against the live theme."""
        return group_accent(self.group_of(tag), self.accents)


def strategy_vocabulary(own_source: str | None = None) -> TagVocabulary:
    """The combat strategy dialog's vocabulary (#125), with *own_source* first in Source when there is one.

    Source is gray, the color of the source pill on each card, so a Source chip reads as the
    pill it matches; Role is the blue-gray, a muted tone no school wears: a role is not a
    school, and the purple it had was Storm's. The noun is replaced per tab — duels,
    strategies, presets. *own_source* is the open owner's word, as ``tag_groups`` takes it.
    """
    return TagVocabulary(
        tag_groups(own_source),
        {SOURCE: "pill_neutral_bg", ROLE: "chip_neutral_bg"},
        ("strategy", "strategies"),
    )


#: The strategy vocabulary of a library with no owner open.
STRATEGY_VOCABULARY = strategy_vocabulary()


def source_vocabulary(noun: tuple[str, str]) -> TagVocabulary:
    """The Source filter on a list (#149): Official, Official (modified), Custom, counting *noun*.

    The strategy dialog's Source group with the same words and color, minus an owner's,
    which only a strategy can have. *noun* is the list's: ``("bot", "bots")``.
    """
    return TagVocabulary({SOURCE: tuple(SOURCE_LABELS[o] for o in LISTED_ORIGINS)}, {SOURCE: "pill_neutral_bg"}, noun)


def tag_sections(
    vocabulary: TagVocabulary,
    counts: dict[str, int] | None = None,
    group: str | None = None,
) -> list[Section]:
    """Build the popup's sections from the vocabulary, or from one *group* of it.

    A tag with a zero count is still listed, dimmed. In an editor every tag must be
    offerable regardless of what the library happens to hold today, and in a
    library a dimmed "0 bots" is a clearer answer than a row that is missing.

    No descriptions: the names already say what the tags mean, and a second line
    under each one doubled the row height for nothing.

    *group* narrows the popup to one group's tags. A library opens one button per
    group, so that each is labeled with the facet it filters — BartleBot's "Rewards",
    "Activities" — and the vocabulary is legible without opening anything. An editor passes
    None and gets the whole thing, because tagging one thing is one decision across them.
    """
    sections: list[Section] = []
    for name, tags in vocabulary.groups.items():
        if group is not None and name != group:
            continue
        rows = [
            PickerRow(
                key=tag,
                label=tag,
                meta=("" if counts is None else count_label(counts.get(tag, 0), vocabulary.noun)),
                accent=group_accent(name, vocabulary.accents),
                dim=(counts is not None and counts.get(tag, 0) == 0),
                icon=tag_icon(tag),
            )
            for tag in tags
        ]
        sections.append((name, rows))
    return sections


class TagPickerButton(PickerButton):
    """A :class:`PickerButton` preloaded with the tag vocabulary *vocabulary*.

    Pass *group* to offer only that group's tags; the default offers all of them.

    *caption* pins the button's text. A filter button says what is selected, because
    the selection *is* the filter and there is nowhere else to read it. An editor
    button sits beside chips that already show the selection, so it can stay the bare
    group name — "Rewards" rather than "Rewards (2)".

    *lists_selection* makes a grouped button name what is selected instead, the way an
    ungrouped one does. For a field with no chips beside it — the strategy dialog's Role
    field (#125) — where the button is the only place the value can be read.
    """

    #: Class-level default so ``PickerButton.__init__`` can call ``format_text``
    #: before the instance attribute is assigned.
    _group: str | None = None
    _caption: str | None = None
    _lists_selection: bool = False

    def __init__(
        self,
        *,
        vocabulary: TagVocabulary,
        title: str = "Tags",
        placeholder: str = "—",
        counts: dict[str, int] | None = None,
        group: str | None = None,
        caption: str | None = None,
        lists_selection: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            placeholder=placeholder,
            search_placeholder="Filter tags…",
            parent=parent,
        )
        self._vocabulary = vocabulary
        self._lists_selection = lists_selection
        self._group = group
        self._caption = caption
        if caption is not None:
            self.setText(caption)
        self._counts = counts
        self.set_counts(counts)
        # The rows hold their medallions, so new art means new rows.
        follow_art(self, lambda: self.set_counts(self._counts))

    @property
    def group(self) -> str | None:
        """The tag group this button offers, or None for the whole vocabulary."""
        return self._group

    def format_text(self, labels: list[str]) -> str:
        """Keep a grouped button labeled with its group, plus a count when active.

        A grouped picker is one of a row of facets, so its name has to stay legible
        whether or not anything is selected — that name is the only thing advertising
        the vocabulary to someone who has not opened it. The count is what says a
        filter is on without making the user read the chip bar.
        """
        if self._caption is not None:
            return self._caption
        if self._group is None or self._lists_selection:
            return super().format_text(labels)
        return self._group if not labels else f"{self._group} ({len(labels)})"

    @property
    def vocabulary(self) -> TagVocabulary:
        return self._vocabulary

    def set_counts(self, counts: dict[str, int] | None) -> None:
        """Rebuild the rows against fresh per-tag counts."""
        self._counts = counts
        self.set_sections(tag_sections(self._vocabulary, counts, self._group))

    def tags(self) -> list[str]:
        """Selected tags, in vocabulary order."""
        chosen = set(self.selection())
        return [t for t in self._vocabulary.tags if t in chosen]

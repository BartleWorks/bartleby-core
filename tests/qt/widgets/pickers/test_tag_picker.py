"""Tests for the tag picker shared by every tagging surface, over a made-up vocabulary."""

from dataclasses import replace

from PyQt6.QtCore import QEvent

from bartleby.qt.testing.picker_helpers import pick
from bartleby.qt.widgets.pickers.picker_popup import _RowWidget
from bartleby.qt.widgets.pickers.tag_picker import (
    STRATEGY_VOCABULARY,
    TagPickerButton,
    TagVocabulary,
    count_label,
    group_accent,
    source_vocabulary,
    strategy_vocabulary,
    tag_sections,
)
from bartleby.qt.widgets.pills.tag_pill import TagChip, TagPill
from bartleby.runtime.combat.strategy_tags import STRATEGY_TAG_GROUPS

#: Two groups of a few tags each, as a product's own vocabulary is.
TAG_GROUPS = {"Rewards": ("Gear", "Jewels", "Mounts", "Gold"), "Activities": ("Dungeon", "Boss")}
TAGS = ("Gear", "Jewels", "Mounts", "Gold", "Dungeon", "Boss")
VOCABULARY = TagVocabulary(TAG_GROUPS, {"Rewards": "accent", "Activities": "pill_activity_bg"}, ("bot", "bots"))

# ---------------------------------------------------------------------------
# Section building (pure)
# ---------------------------------------------------------------------------


def test_sections_mirror_the_vocabulary():
    sections = tag_sections(VOCABULARY)

    assert [title for title, _ in sections] == list(TAG_GROUPS)
    assert [r.key for _, rows in sections for r in rows] == list(TAGS)


def test_rows_carry_no_description():
    """Names say enough; a second line doubled the row height and read as an underline."""
    assert all(not r.description for _, rows in tag_sections(VOCABULARY) for r in rows)


def test_counts_are_omitted_when_not_supplied():
    """The editor has no library to count, and '0' there would be a lie."""
    assert all(r.meta == "" for _, rows in tag_sections(VOCABULARY) for r in rows)


def test_counts_carry_their_unit():
    """A bare "49" beside a name reads as ambiguous until you work out what it counts."""
    rows = {r.key: r.meta for _, rows in tag_sections(VOCABULARY, {"Gear": 49}) for r in rows}

    assert rows["Gear"] == "49 bots"


def test_a_count_of_one_is_singular():
    assert count_label(1, ("bot", "bots")) == "1 bot"
    assert count_label(0, ("bot", "bots")) == "0 bots"


def test_a_tag_with_no_bots_is_listed_but_dimmed():
    """A missing row reads as a bug; a dimmed "0 bots" is an answer."""
    rows = {r.key: r for _, rows in tag_sections(VOCABULARY, {"Gear": 49}) for r in rows}

    assert rows["Mounts"].meta == "0 bots"
    assert rows["Mounts"].dim
    assert not rows["Gear"].dim


def test_nothing_is_dimmed_when_counts_are_unknown():
    """The editor has no counts, so nothing there is "empty"."""
    assert not any(r.dim for _, rows in tag_sections(VOCABULARY) for r in rows)


def test_the_two_groups_get_different_dots():
    assert VOCABULARY.accent("Gear") != VOCABULARY.accent("Boss")
    assert VOCABULARY.accent("Gear") == group_accent("Rewards", VOCABULARY.accents)


def test_a_vocabulary_finds_the_owning_group():
    assert VOCABULARY.group_of("Gear") == "Rewards"
    assert VOCABULARY.group_of("Dungeon") == "Activities"
    assert VOCABULARY.group_of("Nonsense") == ""


# ---------------------------------------------------------------------------
# The button
# ---------------------------------------------------------------------------


def test_tags_come_back_in_vocabulary_order_not_click_order(qapp):
    btn = TagPickerButton(vocabulary=VOCABULARY)
    btn._open()

    btn._popup._on_row_toggled("Dungeon", True)
    btn._popup._on_row_toggled("Gear", True)

    assert btn.tags() == ["Gear", "Dungeon"]
    btn._popup.close()


def test_the_popup_offers_every_tag(qapp):
    btn = TagPickerButton(vocabulary=VOCABULARY)
    btn._open()

    assert len(btn._popup.findChildren(_RowWidget)) == len(TAGS)
    btn._popup.close()


def test_constructing_the_button_does_not_recurse(qapp):
    """This widget styles itself, and setStyleSheet feeds StyleChange back in.
    Without the re-entrancy guard the app died at startup with no traceback at
    all — a 0xC0000409 stack overflow inside window construction."""
    btn = TagPickerButton(vocabulary=VOCABULARY, placeholder="Filter by tag")

    assert btn.text() == "Filter by tag"


def test_a_theme_change_reapplies_the_stylesheet_without_recursing(qapp):
    btn = TagPickerButton(vocabulary=VOCABULARY)
    btn.setStyleSheet("")

    qapp.sendEvent(btn, QEvent(QEvent.Type.StyleChange))

    assert "QToolButton" in btn.styleSheet()


# ---------------------------------------------------------------------------
# One button per group
# ---------------------------------------------------------------------------


def test_a_grouped_button_offers_only_its_own_group(qapp):
    btn = TagPickerButton(vocabulary=VOCABULARY, group="Activities")
    btn._open()

    assert len(btn._popup.findChildren(_RowWidget)) == len(TAG_GROUPS["Activities"])
    btn._popup.close()


def test_a_grouped_button_keeps_its_group_name_when_nothing_is_selected(qapp):
    """The name is the only thing advertising the vocabulary to someone who has
    not opened the popup, so it must not be replaced by a placeholder dash."""
    assert TagPickerButton(vocabulary=VOCABULARY, group="Rewards", placeholder="Rewards").text() == "Rewards"


def test_a_grouped_button_counts_its_selection_rather_than_listing_it(qapp):
    btn = TagPickerButton(vocabulary=VOCABULARY, group="Rewards", placeholder="Rewards")
    btn._open()

    btn._popup._on_row_toggled("Gear", True)
    btn._popup._on_row_toggled("Jewels", True)

    assert btn.text() == "Rewards (2)"
    btn._popup.close()


def test_an_ungrouped_button_still_lists_its_selection(qapp):
    """The library's ungrouped picker names what is selected rather than counting."""
    btn = TagPickerButton(vocabulary=VOCABULARY)
    btn._open()

    btn._popup._on_row_toggled("Gear", True)

    assert btn.text() == "Gear"
    btn._popup.close()


def test_tag_sections_for_a_group_drops_the_others():
    sections = tag_sections(VOCABULARY, group="Activities")

    assert [name for name, _ in sections] == ["Activities"]


# ---------------------------------------------------------------------------
# The vocabulary is a parameter (#125)
# ---------------------------------------------------------------------------


def test_sections_follow_the_vocabulary_they_are_given():
    sections = tag_sections(STRATEGY_VOCABULARY)
    assert [name for name, _rows in sections] == list(STRATEGY_TAG_GROUPS)


def test_an_owners_word_is_first_in_source_and_colored_as_source():
    vocabulary = strategy_vocabulary("This bot")
    assert vocabulary.groups["Source"] == ("This bot", *STRATEGY_VOCABULARY.groups["Source"])
    assert vocabulary.accent("This bot") == STRATEGY_VOCABULARY.accent("Custom")
    assert "This bot" not in STRATEGY_VOCABULARY.tags


def test_a_source_filter_counts_the_lists_own_noun():
    vocabulary = source_vocabulary(("bot", "bots"))
    [(name, rows)] = tag_sections(vocabulary, {"Official": 1})
    assert name == "Source"
    assert [(r.key, r.meta) for r in rows] == [
        ("Official", "1 bot"),
        ("Official (modified)", "0 bots"),
        ("Custom", "0 bots"),
    ]


def test_counts_use_the_vocabularys_noun():
    vocab = replace(STRATEGY_VOCABULARY, noun=("preset", "presets"))
    [(_name, rows)] = tag_sections(vocab, {"Hitter": 1, "Healer": 2}, group="Role")
    assert [r.meta for r in rows] == ["1 preset", "0 presets", "2 presets", "0 presets"]


def test_a_picker_returns_tags_in_its_own_vocabularys_order(qapp):
    btn = TagPickerButton(title="Role", group="Role", vocabulary=STRATEGY_VOCABULARY)
    pick(btn, "Healer", "Hitter")
    assert btn.tags() == ["Hitter", "Healer"]


def test_a_strategy_chip_and_pill_take_their_color_from_the_strategy_vocabulary(qapp):
    chip = TagChip("Custom", vocabulary=STRATEGY_VOCABULARY)
    pill = TagPill("Hitter", vocabulary=STRATEGY_VOCABULARY)
    assert STRATEGY_VOCABULARY.accent("Custom") in chip.styleSheet()
    assert STRATEGY_VOCABULARY.accent("Hitter") in pill.styleSheet()
    assert STRATEGY_VOCABULARY.accent("Custom") != STRATEGY_VOCABULARY.accent("Hitter")


def test_new_art_rebuilds_the_rows_keeping_their_counts(qapp):
    """The rows hold their medallions, so art extracted after the button was made needs new rows (#239)."""
    from bartleby.qt.art_signal import announce_art_changed  # noqa: PLC0415
    from bartleby.qt.testing.extracted_art import write_extracted_art  # noqa: PLC0415

    button = TagPickerButton(vocabulary=VOCABULARY, counts={"Gold": 3}, group="Rewards")

    def gold_row():
        return next(r for _title, rows in button._sections for r in rows if r.key == "Gold")

    assert gold_row().icon is None

    write_extracted_art(icons=["tag_gold"])
    announce_art_changed()

    assert gold_row().icon is not None
    assert gold_row().meta == "3 bots"

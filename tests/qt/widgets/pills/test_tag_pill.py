"""Tests for the tag pill and chip, with a medallion extracted and without one, which is the ordinary install."""

from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from bartleby.qt.widgets.pickers.tag_picker import TagVocabulary
from bartleby.runtime.game import card_art
from bartleby.runtime.game.card_art import tag_icon_name

VOCABULARY = TagVocabulary(
    {"Rewards": ("Gear",), "Activities": ("Dungeon", "Boss", "Mobs")},
    {"Rewards": "accent", "Activities": "pill_activity_bg"},
    ("bot", "bots"),
)


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(*tags: str) -> None:
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    icons = {}
    for tag in tags:
        key = tag_icon_name(tag)
        Image.new("RGBA", (128, 128), (10, 200, 10, 255)).save(card_art.icons_dir() / f"{key}.png")
        icons[key] = f"{key}.png"
    card_art.index_path().write_text(json.dumps({"icons": icons}), encoding="utf-8")


def test_a_tag_with_an_extracted_medallion_resolves(art_home):
    _extract("Gear")

    assert card_art.tag_icon_path("Gear") == card_art.icons_dir() / "tag_gear.png"
    assert card_art.tag_icon_path("Boss") is None


def test_a_tag_icon_is_looked_up_once_until_the_cache_is_forgotten(qapp, art_home, monkeypatch):
    from bartleby.qt.widgets.pickers.tag_picker import tag_icon

    _extract("Gear")
    calls = []
    real = card_art.tag_icon_path
    monkeypatch.setattr(card_art, "tag_icon_path", lambda tag: calls.append(tag) or real(tag))

    assert tag_icon("Gear") is not None
    assert tag_icon("Gear") is not None
    assert calls == ["Gear"]

    card_art.forget_cache()
    tag_icon("Gear")
    assert calls == ["Gear", "Gear"]


def test_forgetting_the_cache_lets_new_art_appear(qapp, art_home):
    from bartleby.qt.widgets.pickers.tag_picker import tag_icon

    assert tag_icon("Gear") is None

    _extract("Gear")
    card_art.forget_cache()

    assert tag_icon("Gear") is not None


def test_the_pill_is_led_by_the_medallion_and_keeps_its_name(qapp, art_home):
    from bartleby.qt.widgets.pickers.tag_picker import TAG_ICON_PX
    from bartleby.qt.widgets.pills.tag_pill import TagPill

    _extract("Gear")
    pill = TagPill("Gear", vocabulary=VOCABULARY)

    assert pill.has_icon
    assert pill.text() == "Gear"
    assert pill.height() == TAG_ICON_PX
    assert pill.width() > TAG_ICON_PX + 10


def test_the_medallion_is_the_pills_round_end(qapp, art_home):
    """The ring and the pill's left end are one circle: the corner outside the medallion
    stays clear, the medallion covers the left, and the body runs on to a round right end."""
    from PyQt6.QtGui import QColor

    from bartleby.qt.widgets.pickers.tag_picker import TAG_ICON_PX
    from bartleby.qt.widgets.pills.tag_pill import TagPill

    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    disc = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 127, 127), fill=(10, 200, 10, 255))
    disc.save(card_art.icons_dir() / "tag_gear.png")
    card_art.index_path().write_text(json.dumps({"icons": {"tag_gear": "tag_gear.png"}}), encoding="utf-8")
    pill = TagPill("Gear", vocabulary=VOCABULARY)
    image = pill.grab().toImage()
    ratio = image.devicePixelRatio()

    def at(x, y):
        return image.pixelColor(round(x * ratio), round(y * ratio))

    h = TAG_ICON_PX
    assert at(0, 0).alpha() == 0, "no fill peeks past the ring's corner"
    assert at(h / 2, h / 2).green() > 150, "the medallion sits in the left end"
    fill = QColor(VOCABULARY.accent("Gear"))
    body = at(pill.width() - h / 2, 1)
    assert (body.red(), body.green(), body.blue()) == (fill.red(), fill.green(), fill.blue())
    assert at(pill.width() - 1, 0).alpha() == 0, "the right end is round"


def test_the_pill_keeps_its_name_without_art(qapp, art_home):
    from bartleby.qt.widgets.pills.tag_pill import TagPill

    _extract("Gear")
    pill = TagPill("Boss", vocabulary=VOCABULARY)  # this install extracted only Gear

    assert not pill.has_icon
    assert pill.text() == "Boss"
    assert VOCABULARY.accent("Boss") in pill.styleSheet()


def test_the_chip_takes_the_pills_medallion_shape(qapp, art_home):
    from bartleby.qt.widgets.pickers.tag_picker import TAG_ICON_PX
    from bartleby.qt.widgets.pills.tag_pill import TagChip

    _extract("Boss")
    with_art, without = TagChip("Boss", vocabulary=VOCABULARY), TagChip("Mobs", vocabulary=VOCABULARY)

    assert with_art.has_icon
    assert with_art.height() == TAG_ICON_PX
    assert "Boss" in with_art.text()
    assert not without.has_icon


def test_picker_rows_carry_the_medallion_where_there_is_one(qapp, art_home):
    from bartleby.qt.widgets.pickers.tag_picker import tag_sections

    _extract("Dungeon")
    rows = {row.key: row for _title, section in tag_sections(VOCABULARY) for row in section}

    assert rows["Dungeon"].icon is not None
    assert rows["Boss"].icon is None
    assert rows["Boss"].accent, "a row with no medallion keeps its group dot"

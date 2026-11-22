"""Tests for bartleby.runtime.game.card_art — finding extracted game art, and not finding it.

Every test points ``APPDATA`` at a tmp directory, because that is what
``shared/vendor.vendor_dir`` reads and so what decides where the cache is. The
module caches the index, so the fixture clears it either side.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bartleby.runtime.game import card_art
from bartleby.shared import product
from bartleby.shared.vendor import vendor_dir


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _write_index(images: dict[str, str], *, files: tuple[str, ...] = ()) -> None:
    card_art.spells_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"images": images}), encoding="utf-8")
    for name in files:
        (card_art.spells_dir() / name).write_bytes(b"not really a png, only its presence is read")


def test_nothing_extracted_reads_as_no_art(art_home):
    assert not card_art.is_available()
    assert card_art.art_path("Tempest") is None


def test_a_template_in_the_index_finds_its_file(art_home):
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    path = card_art.art_path("Tempest")

    assert path is not None
    assert path.name == "Spell_Storm_Tempest.png"
    assert card_art.is_available()


def test_templates_sharing_one_image_both_resolve(art_home):
    # 18,000 templates share ~1,300 images: a tiered variant uses its base spell's
    # texture, so the index maps many names onto one file rather than copying it.
    _write_index(
        {"Storm Lord": "Spell_Storm_StormLord.png", "Storm Lord - T05 - A": "Spell_Storm_StormLord.png"},
        files=("Spell_Storm_StormLord.png",),
    )

    assert card_art.art_path("Storm Lord") == card_art.art_path("Storm Lord - T05 - A")


def test_a_template_the_index_does_not_name_reads_as_missing(art_home):
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    assert card_art.art_path("Some Card With No Art") is None


def test_an_index_entry_whose_file_is_gone_reads_as_missing(art_home):
    # The index is written once; the directory beside it is an ordinary cache the user
    # can delete part of. A name in the index is not a promise the file is still there.
    _write_index({"Tempest": "Spell_Storm_Tempest.png"})

    assert card_art.art_path("Tempest") is None


def test_an_unreadable_index_reads_as_no_art(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text("{not json at all", encoding="utf-8")

    assert not card_art.is_available()
    assert card_art.art_path("Tempest") is None


def test_an_index_with_no_images_map_reads_as_no_art(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"generated": "2026-09-15"}), encoding="utf-8")

    assert not card_art.is_available()


def test_an_icon_in_the_index_finds_its_file(art_home):
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"archmastery": "archmastery.png"}}), encoding="utf-8")
    (card_art.icons_dir() / "archmastery.png").write_bytes(b"only its presence is read")

    path = card_art.icon_path("archmastery")

    assert path is not None
    assert path.name == "archmastery.png"


def test_an_art_cache_written_before_icons_existed_reads_as_no_icons(art_home):
    """The spell art still resolves: a missing ``icons`` map is an older cache, not a
    broken one, and the surfaces using icons keep the text they replaced."""
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    assert card_art.art_path("Tempest") is not None
    assert card_art.icon_path("archmastery") is None


def test_an_icon_whose_file_is_gone_reads_as_missing(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"archmastery": "archmastery.png"}}), encoding="utf-8")

    assert card_art.icon_path("archmastery") is None


def test_forgetting_the_cache_picks_up_a_later_extraction(art_home):
    assert not card_art.is_available()

    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))
    assert not card_art.is_available(), "the empty answer should still be cached"

    card_art.forget_cache()
    assert card_art.is_available()


# -- one extraction, read by every product -----------------------------------


def _extracted_by(name: str, template: str = "Tempest", *, age: int = 0) -> Path:
    """Art another product, *name*, extracted: one card's, and its index made *age* seconds ago."""
    art = vendor_dir() / name / card_art.ART_DIRNAME
    (art / card_art.SPELLS_DIRNAME).mkdir(parents=True)
    (art / card_art.ICONS_DIRNAME).mkdir()
    (art / card_art.SPELLS_DIRNAME / "card.png").write_bytes(b"only its presence is read")
    (art / card_art.ICONS_DIRNAME / "icon.png").write_bytes(b"only its presence is read")
    index = art / card_art.INDEX_FILENAME
    index.write_text(
        json.dumps({"images": {template: "card.png"}, "icons": {"school_fire": "icon.png"}}), encoding="utf-8"
    )
    stamp = index.stat().st_mtime - age
    os.utime(index, (stamp, stamp))
    return art


def test_another_products_art_is_read_when_this_one_has_none(art_home):
    """BartleBuddy extracts nothing: it draws what BartleBot did."""
    art = _extracted_by("Other")

    assert card_art.is_available()
    assert card_art.art_path("Tempest") == art / "spells" / "card.png"
    assert card_art.icon_path("school_fire") == art / "icons" / "icon.png"
    assert card_art.art_dir() == art


def test_the_products_own_art_is_read_before_anothers(art_home):
    _extracted_by("Other")
    own = _extracted_by(product.current().name, age=60)

    assert card_art.art_path("Tempest") == own / "spells" / "card.png"


def test_the_products_own_art_answers_for_every_card_even_one_it_lacks(art_home):
    """One extraction at a time: the index and the files it names are always the same run's."""
    _extracted_by("Other", "Only In The Other")
    _extracted_by(product.current().name)

    assert card_art.art_path("Only In The Other") is None


def test_the_newest_of_several_other_products_answers(art_home):
    _extracted_by("Older", age=60)
    newer = _extracted_by("Newer")

    assert card_art.art_dir() == newer


def test_an_unreadable_index_falls_to_another_products(art_home):
    card_art.own_art_dir().mkdir(parents=True)
    (card_art.own_art_dir() / card_art.INDEX_FILENAME).write_text("{not json", encoding="utf-8")
    other = _extracted_by("Other")

    assert card_art.art_dir() == other


def test_a_folder_of_the_vendors_with_no_index_is_no_products_art(art_home):
    (vendor_dir() / "strategies" / "duel").mkdir(parents=True)
    (vendor_dir() / "Half" / card_art.ART_DIRNAME).mkdir(parents=True)
    (vendor_dir() / "accounts.json").write_text("{}", encoding="utf-8")

    assert not card_art.is_available()
    assert card_art.art_dir() == card_art.own_art_dir()


def test_the_extractors_folder_is_the_products_own_whatever_is_read(art_home):
    _extracted_by("Other")

    assert card_art.own_art_dir() == vendor_dir() / product.current().name / "art"


def test_art_another_product_extracts_meanwhile_is_read_once_the_cache_is_forgotten(art_home):
    assert card_art.art_path("Tempest") is None

    _extracted_by("Other")
    assert card_art.art_path("Tempest") is None, "the empty answer should still be cached"

    card_art.forget_cache()
    assert card_art.art_path("Tempest") is not None


def test_a_tag_key_is_a_tidy_filename():
    assert card_art.tag_icon_name("Team-Up") == "tag_team_up"
    assert card_art.tag_icon_name("Treasure Cards") == "tag_treasure_cards"


def test_a_slot_key_is_its_number():
    assert card_art.duel_slot_icon_name(1) == "duel_slot_1"


def test_every_slot_has_a_symbol_in_the_games_own_order():
    """The order is the game's, read off its own dialog, and is not alphabetical or
    derivable from anything. Spelled out so a reorder has to be deliberate."""
    assert [card_art.duel_slot_symbol(s) for s in range(1, card_art.DUEL_SLOTS + 1)] == ["Sun", "Eye", "Star", "Moon"]
    assert card_art.duel_slot_symbol(9) == ""

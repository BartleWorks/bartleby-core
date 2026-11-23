"""Deck sets: which deck each wizard puts on, and the file two products write an entry of at a time."""

from __future__ import annotations

import json

import pytest

from bartleby.shared.deck_names import DeckName
from bartleby.shared.deck_sets import FILENAME, DeckSet, DeckSetChanged, DeckSetStore
from bartleby.shared.vendor import vendor_dir

MAIN, BOSS, PIERCING = (DeckName(type_key=f"Type_{n}") for n in (1, 2, 9))
P1, P2 = 101, 102


@pytest.fixture
def store(tmp_path):
    return DeckSetStore(tmp_path)


def _file(store) -> dict:
    return json.loads(store.path.read_text(encoding="utf-8"))


# -- a wizard's deck in a set -----------------------------------------------------------------


def test_everyone_puts_on_the_set_s_deck():
    assert DeckSet(everyone=BOSS).deck_for(P1) == BOSS


def test_an_exception_wins_over_everyone_s_deck():
    deck_set = DeckSet(everyone=BOSS, exceptions={P2: PIERCING})

    assert deck_set.deck_for(P2) == PIERCING
    assert deck_set.deck_for(P1) == BOSS


def test_a_wizard_that_keeps_its_deck_has_none_in_the_set():
    assert DeckSet(everyone=BOSS, exceptions={P2: None}).deck_for(P2) is None


def test_a_set_with_neither_names_no_deck():
    assert DeckSet().deck_for(P1) is None


def test_an_exception_alone_is_one_wizard_s_set():
    deck_set = DeckSet(exceptions={P2: PIERCING})

    assert (deck_set.deck_for(P1), deck_set.deck_for(P2)) == (None, PIERCING)


# -- the file ---------------------------------------------------------------------------------


def test_it_is_the_vendor_s_file_whichever_product_reads_it():
    assert DeckSetStore().path == vendor_dir() / FILENAME


def test_a_set_round_trips_through_a_second_store(store):
    deck_set = DeckSet(name="Boss decks", everyone=BOSS, exceptions={P2: PIERCING, P1: None})

    store.save(deck_set, expected=None)

    assert DeckSetStore(store.path.parent).sets() == (deck_set,)


def test_the_file_round_trips_byte_identical(store):
    store.save(DeckSet(name="Boss decks", everyone=BOSS, exceptions={P2: PIERCING, P1: None}), expected=None)
    store.save(DeckSet(name="Mob décks"), expected=None)
    before = store.path.read_bytes()

    for deck_set in store.sets():
        store.save(deck_set, expected=store.fingerprint(deck_set.id))

    assert store.path.read_bytes() == before


def test_a_field_is_left_out_of_the_file_while_it_is_empty(store):
    deck_set = DeckSet(id="a", name="Boss decks")

    store.save(deck_set, expected=None)
    assert _file(store) == {"version": 1, "sets": [{"id": "a", "name": "Boss decks"}]}

    print_ = store.fingerprint("a")
    print_ = store.save(deck_set.model_copy(update={"everyone": BOSS}), expected=print_)
    assert _file(store)["sets"][0]["everyone"] == {"type_key": "Type_2"}

    store.save(deck_set, expected=print_)
    assert _file(store)["sets"] == [{"id": "a", "name": "Boss decks"}]


def test_a_newer_version_s_fields_and_sets_survive_a_write_by_this_one(store):
    newer = {"id": "b", "everyone": {"outfit": 3}}
    store.path.write_text(
        json.dumps({"version": 2, "later": 1, "sets": [{"id": "a", "name": "A", "gear": [7]}, newer]}),
        encoding="utf-8",
    )
    ours = store.get("a")

    store.save(ours.model_copy(update={"name": "Boss decks"}), expected=store.fingerprint("a"))

    assert [s.id for s in store.sets()] == ["a"], "a set this version can't read is not offered"
    assert _file(store) == {
        "version": 2,
        "later": 1,
        "sets": [{"id": "a", "name": "Boss decks", "gear": [7]}, newer],
    }


def test_a_file_that_is_absent_or_not_json_holds_no_sets(store):
    assert store.sets() == () and store.get("a") is None and store.fingerprint("a") is None

    store.path.write_text("not json", encoding="utf-8")
    assert store.sets() == ()


def test_the_other_product_s_write_is_seen_at_the_next_read(store):
    other = DeckSetStore(store.path.parent)
    assert store.sets() == ()

    other.save(DeckSet(id="a", name="A"), expected=None)
    assert [s.name for s in store.sets()] == ["A"]

    other.save(DeckSet(id="a", name="B"), expected=other.fingerprint("a"))
    assert [s.name for s in store.sets()] == ["B"]


def test_a_read_refused_keeps_the_sets_as_they_were_and_is_tried_again(store, monkeypatch):
    other = DeckSetStore(store.path.parent)
    other.save(DeckSet(id="a", name="A"), expected=None)
    assert [s.name for s in store.sets()] == ["A"]
    other.save(DeckSet(id="a", name="Bee"), expected=other.fingerprint("a"))

    with monkeypatch.context() as refused:
        refused.setattr(type(store.path), "read_text", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError()))
        assert [s.name for s in store.sets()] == ["A"]

    assert [s.name for s in store.sets()] == ["Bee"]


def test_an_unchanged_file_is_not_parsed_again(store, monkeypatch):
    store.save(DeckSet(id="a", name="A"), expected=None)
    store.sets()
    monkeypatch.setattr(json, "loads", lambda *_: pytest.fail("parsed again"))

    assert store.get("a").name == "A"


# -- two products at once ---------------------------------------------------------------------


def test_two_products_writing_different_sets_both_keep_theirs(store):
    other = DeckSetStore(store.path.parent)
    store.save(DeckSet(id="a", name="A"), expected=None)
    other.save(DeckSet(id="b", name="B"), expected=None)

    store.save(DeckSet(id="a", name="A2"), expected=store.fingerprint("a"))

    assert [s.name for s in other.sets()] == ["A2", "B"]


def test_a_set_the_other_product_changed_is_not_overwritten(store):
    other = DeckSetStore(store.path.parent)
    store.save(DeckSet(id="a", name="A"), expected=None)
    read = store.fingerprint("a")
    other.save(DeckSet(id="a", name="Theirs"), expected=read)
    before = store.path.read_bytes()

    with pytest.raises(DeckSetChanged):
        store.save(DeckSet(id="a", name="Mine"), expected=read)
    with pytest.raises(DeckSetChanged):
        store.delete("a", expected=read)

    assert store.path.read_bytes() == before


def test_a_set_the_other_product_deleted_is_not_written_back(store):
    store.save(DeckSet(id="a", name="A"), expected=None)
    read = store.fingerprint("a")
    DeckSetStore(store.path.parent).delete("a", expected=read)

    with pytest.raises(DeckSetChanged):
        store.save(DeckSet(id="a", name="Mine"), expected=read)

    assert store.sets() == ()


def test_the_fingerprint_a_save_returns_is_the_next_one_s(store):
    deck_set = DeckSet(id="a", name="A")
    read = store.save(deck_set, expected=None)
    read = store.save(deck_set.model_copy(update={"name": "B"}), expected=read)

    assert read == store.fingerprint("a")
    store.delete("a", expected=read)
    assert store.sets() == ()


def test_a_duplicate_is_a_set_of_its_own_under_a_free_name(store):
    store.save(DeckSet(id="a", name="Boss decks", everyone=BOSS, exceptions={P2: PIERCING}), expected=None)

    copy = store.duplicate("a")

    assert copy.id != "a" and copy.name == "Boss decks (2)"
    assert copy.everyone == BOSS and copy.exceptions == {P2: PIERCING}
    assert [s.id for s in store.sets()] == ["a", copy.id]
    assert store.duplicate("gone") is None

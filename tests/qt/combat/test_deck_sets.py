"""Deck sets in Settings: the cards, the editor, the wizards a set can except, and two products at once."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialog, QFrame, QMessageBox

from bartleby.qt import streamer_mode
from bartleby.qt.combat import deck_sets
from bartleby.qt.combat.deck_sets import (
    ADD_TITLE,
    EDIT_TITLE,
    EVERYONE_NONE,
    NO_WIZARDS,
    UNKNOWN_WIZARD,
    DeckSetDialog,
    DeckSetList,
    DeckWizards,
    known_wizards,
)
from bartleby.runtime.views import DeckWizard
from bartleby.shared.deck_names import DeckName
from bartleby.shared.deck_sets import DeckSet, DeckSetStore

MAIN, BOSS, PIERCING = (DeckName(type_key=f"Type_{n}") for n in (1, 2, 24))
P1 = DeckWizard(101, wizard_name="Luke SkullHunter", slot=1, account_label="Storm", decks=(MAIN, BOSS))
P2 = DeckWizard(102, wizard_name="Wolf Deathblade", slot=2, account_label="Ice", decks=(PIERCING,))
#: Two accounts whose rosters hold the team's two and one wizard more.
ACCOUNTS = [{"username": "one"}, {"username": "two"}]
ROSTERS = {
    "one": [{"gid": 101, "name": "Luke SkullHunter"}, {"gid": 103, "name": "Amber Frost"}],
    "two": [{"gid": 102, "name": "Wolf Deathblade"}],
}


@pytest.fixture(autouse=True)
def rosters(monkeypatch):
    monkeypatch.setattr(deck_sets.character_roster, "load", lambda username: ROSTERS.get(username, []))
    monkeypatch.setattr(deck_sets, "account_label", lambda username: f"Account {username}")
    streamer_mode.configure(lambda: False)


@pytest.fixture
def store(tmp_path):
    return DeckSetStore(tmp_path)


@pytest.fixture
def wizards(qapp):
    return DeckWizards()


@pytest.fixture
def answers(monkeypatch):
    """Press a message box's button by its words, in place of its exec(); every box shown, by its text."""
    shown: list[str] = []
    wanted = {"button": "Cancel"}

    def press(box):
        shown.append(box.text())
        next(b for b in box.buttons() if b.text().replace("&", "") == wanted["button"]).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", press)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: shown.append(a[2]))

    def choose(button: str) -> list[str]:
        wanted["button"] = button
        return shown

    return choose


def _dialog(store, wizards, deck_set=None, fingerprint=None) -> DeckSetDialog:
    return DeckSetDialog(store, deck_set or DeckSet(), fingerprint, wizards=wizards, accounts=ACCOUNTS)


# -- the wizards a set can except -------------------------------------------------------------


def test_the_wizards_known_are_the_team_s_then_every_other_in_the_accounts_rosters():
    known = known_wizards([P2, P1], ACCOUNTS)

    assert [(w.character_id, w.slot) for w in known] == [(101, 1), (102, 2), (103, None)]
    assert known[0].decks == (MAIN, BOSS), "a wizard of the team keeps what was read of it"


def test_a_wizard_of_the_team_takes_its_name_from_its_roster_until_the_game_s_is_read():
    unnamed = DeckWizard(101, slot=1)

    assert known_wizards([unnamed], ACCOUNTS)[0].wizard_name == "Luke SkullHunter"


def test_streamer_mode_hides_every_wizard_s_name(qapp, store, wizards):
    streamer_mode.configure(lambda: True)
    store.save(DeckSet(id="a", name="Boss decks", exceptions={103: PIERCING}), expected=None)
    wizards.show([P1])
    dialog = _dialog(store, wizards, store.get("a"), store.fingerprint("a"))
    sets = DeckSetList(store, wizards, lambda: ACCOUNTS)

    shown = [dialog.rows[0].who_text, *[a.text() for a in dialog.add_menu().actions()], *sets.cards[0].texts]
    assert not any("Amber" in text or "Luke" in text or "Wolf" in text for text in shown)
    assert dialog.rows[0].who_text == "Character 2 (Account one)"


# -- the editor -------------------------------------------------------------------------------


def test_the_editor_asks_for_the_team_s_decks_as_it_opens_and_shows_them_when_they_come(store, wizards):
    asked = []
    wizards.requested.connect(lambda: asked.append(True))
    dialog = _dialog(store, wizards, DeckSet(name="A", exceptions={102: PIERCING}))

    assert asked and not dialog.everyone.owned_button.isEnabled()
    assert dialog.rows[0].who_text == "Wolf Deathblade (Account two)"

    wizards.show([P1, P2])
    assert dialog.everyone.owned_button.isEnabled()
    assert dialog.rows[0].who_text == "Wolf Deathblade (Ice)"
    assert [a.text() for a in dialog.rows[0].picker.owned_menu().actions()] == ["Piercing Deck\tP2"], (
        "an exception's list is that wizard's own decks"
    )


def test_a_new_set_is_written_as_the_editor_has_it(store, wizards):
    wizards.show([P1, P2])
    dialog = _dialog(store, wizards)
    assert dialog.windowTitle() == ADD_TITLE

    dialog.name_edit.setText("  Boss decks ")
    dialog.everyone.set_name(BOSS)
    dialog.add_wizard(next(w for w in dialog.addable() if w.character_id == 102))
    dialog.rows[0].picker.set_name(PIERCING)
    dialog.add_wizard(next(w for w in dialog.addable() if w.character_id == 103))
    dialog.save()

    assert dialog.result() == QDialog.DialogCode.Accepted
    (saved,) = store.sets()
    assert (saved.name, saved.everyone) == ("Boss decks", BOSS)
    assert saved.exceptions == {102: PIERCING, 103: None}, "empty wheels keep the wizard's deck"


def test_an_edit_keeps_the_set_s_id_and_takes_an_exception_out(store, wizards):
    store.save(DeckSet(id="a", name="A", everyone=BOSS, exceptions={102: PIERCING, 103: None}), expected=None)
    dialog = _dialog(store, wizards, store.get("a"), store.fingerprint("a"))
    assert dialog.windowTitle() == EDIT_TITLE

    dialog.rows[0].remove_button.click()
    dialog.everyone.set_name(None)
    dialog.save()

    assert store.sets() == (DeckSet(id="a", name="A", exceptions={103: None}),)


def test_a_wizard_already_listed_is_not_offered_again(store, wizards):
    wizards.show([P1])
    dialog = _dialog(store, wizards, DeckSet(name="A", exceptions={101: BOSS}))
    assert [w.character_id for w in dialog.addable()] == [103, 102]

    for wizard in dialog.addable():
        dialog.add_wizard(wizard)
    assert not dialog.add_button.isEnabled() and dialog.add_button.toolTip() == NO_WIZARDS

    dialog.rows[0].remove_button.click()
    assert [w.character_id for w in dialog.addable()] == [101] and dialog.add_button.isEnabled()


def test_an_exception_of_a_wizard_nobody_knows_is_kept_and_named_nothing(store, wizards):
    store.save(DeckSet(id="a", name="A", exceptions={999: PIERCING}), expected=None)
    dialog = _dialog(store, wizards, store.get("a"), store.fingerprint("a"))

    assert dialog.rows[0].who_text == UNKNOWN_WIZARD
    dialog.save()
    assert store.get("a").exceptions == {999: PIERCING}


def test_a_set_with_no_name_is_not_saved(store, wizards, answers):
    shown = answers("Cancel")
    dialog = _dialog(store, wizards)
    dialog.name_edit.setText("   ")

    dialog.save()

    assert shown == ["Enter a name for the deck set."]
    assert store.sets() == () and dialog.result() != QDialog.DialogCode.Accepted


# -- two products at once ------------------------------------------------------------------------


@pytest.fixture
def overtaken(store, wizards):
    """The editor open on a set that the other product has since changed."""
    store.save(DeckSet(id="a", name="A", everyone=MAIN), expected=None)
    dialog = _dialog(store, wizards, store.get("a"), store.fingerprint("a"))
    dialog.everyone.set_name(BOSS)
    other = DeckSetStore(store.path.parent)
    other.save(DeckSet(id="a", name="Theirs", everyone=PIERCING), expected=other.fingerprint("a"))
    return dialog


def test_a_set_changed_meanwhile_is_kept_as_a_copy(store, overtaken, answers):
    answers("Save as copy")
    overtaken.save()

    theirs, mine = store.sets()
    assert (theirs.id, theirs.name, theirs.everyone) == ("a", "Theirs", PIERCING)
    assert mine.id != "a" and (mine.name, mine.everyone) == ("A", BOSS)


@pytest.mark.parametrize(("button", "result"), [("Discard", QDialog.DialogCode.Rejected), ("Cancel", None)])
def test_a_set_changed_meanwhile_is_never_overwritten(store, overtaken, answers, button, result):
    overtaken.show()
    answers(button)
    overtaken.save()

    assert [s.name for s in store.sets()] == ["Theirs"]
    assert overtaken.isVisible() == (button == "Cancel"), "canceled, the editor stays open with the edit"


def test_a_file_that_can_t_be_written_says_so_and_keeps_the_editor_open(store, wizards, answers, monkeypatch):
    shown = answers("Cancel")
    dialog = _dialog(store, wizards)
    dialog.name_edit.setText("A")

    def refuse(*_a, **_k):
        raise PermissionError("no")

    monkeypatch.setattr(store, "save", refuse)
    dialog.save()

    assert shown == ["The deck set couldn't be saved. The log has the details."]
    assert dialog.result() != QDialog.DialogCode.Accepted


# -- the cards ----------------------------------------------------------------------------------


@pytest.fixture
def cards(store, wizards):
    store.save(DeckSet(id="a", name="Mob decks", everyone=MAIN), expected=None)
    store.save(DeckSet(id="b", name="Boss decks", everyone=BOSS, exceptions={102: PIERCING, 103: None}), expected=None)
    wizards.show([P1, P2])
    return DeckSetList(store, wizards, lambda: ACCOUNTS)


def test_each_card_reads_its_set(cards):
    assert [card.texts for card in cards.cards] == [
        ["Mob decks", "Main Deck"],
        ["Boss decks", "Boss Deck", "P2 Piercing Deck, Amber Frost keeps its deck"],
    ]


def test_a_set_that_names_nothing_for_everyone_says_so(store, wizards):
    store.save(DeckSet(id="a", name="A"), expected=None)

    sets = DeckSetList(store, wizards, lambda: ACCOUNTS)

    assert sets.cards[0].texts == ["A", EVERYONE_NONE]


def test_a_duplicate_is_written_at_once_and_drawn(cards, store):
    heard = []
    cards.changed.connect(lambda: heard.append(True))

    cards.cards[0].duplicate_requested.emit("a")

    assert [c.texts[0] for c in cards.cards] == ["Mob decks", "Boss decks", "Mob decks (2)"]
    assert heard and len(DeckSetStore(store.path.parent).sets()) == 3


@pytest.mark.parametrize(("button", "left"), [("Delete", ["b"]), ("Cancel", ["a", "b"])])
def test_a_delete_is_asked_about_first(cards, store, answers, button, left):
    shown = answers(button)

    cards.cards[0].delete_requested.emit("a")

    assert shown == ["Delete deck set 'Mob decks'?"]
    assert [s.id for s in store.sets()] == left == [c.deck_set.id for c in cards.cards]


def test_a_click_on_a_card_opens_its_set_and_the_add_card_a_new_one(cards, store, monkeypatch):
    opened = []

    def stand_in(dialog):
        opened.append((dialog.windowTitle(), dialog.name_edit.text()))
        dialog.name_edit.setText("Changed")
        dialog.save()
        return 1

    monkeypatch.setattr(DeckSetDialog, "exec", stand_in)

    cards.cards[1].activated.emit()
    cards.add_card.add_requested.emit()

    assert opened == [(EDIT_TITLE, "Boss decks"), (ADD_TITLE, "")]
    assert [c.texts[0] for c in cards.cards] == ["Mob decks", "Changed", "Changed"]


def test_the_other_product_s_change_is_drawn_and_asked_about_again(cards, store, answers, monkeypatch):
    shown = answers("Delete")
    other = DeckSetStore(store.path.parent)
    real = deck_sets.confirm_delete

    def meanwhile(*a):
        other.save(DeckSet(id="a", name="Theirs"), expected=other.fingerprint("a"))
        return real(*a)

    monkeypatch.setattr(deck_sets, "confirm_delete", meanwhile)
    cards.cards[0].delete_requested.emit("a")

    assert "Another app changed this deck set" in shown[-1]
    assert [c.texts[0] for c in cards.cards] == ["Theirs", "Boss decks"]


def test_the_card_that_adds_one_starts_at_the_edge_the_sets_do(cards):
    cards.resize(600, 400)
    cards.show()

    assert cards.cards[0].frame.x() == cards.add_card.findChild(QFrame).x() == 0


def test_nothing_is_shown_before_it_has_a_parent_as_a_set_is_saved_and_drawn(
    store, wizards, parentless_shows, monkeypatch
):
    """A label shown before it was laid out flashed as a window of its own (bartleby/qt/CLAUDE.md)."""
    wizards.show([P1, P2])
    sets = DeckSetList(store, wizards, lambda: ACCOUNTS)
    sets.resize(600, 400)
    sets.show()
    parentless_shows.clear()  # the list itself, shown on purpose

    def save_one(dialog):
        dialog.name_edit.setText("Boss decks")
        dialog.everyone.set_name(BOSS)
        for wizard in dialog.addable()[:2]:
            dialog.add_wizard(wizard)
        dialog.rows[0].picker.set_name(PIERCING)
        dialog.save()
        return 1

    monkeypatch.setattr(DeckSetDialog, "exec", save_one)
    sets.add_card.add_requested.emit()

    assert [c.texts[0] for c in sets.cards] == ["Boss decks"]
    assert parentless_shows == []


def test_the_editor_shows_nothing_before_it_has_a_parent(store, wizards, parentless_shows):
    wizards.show([P1, P2])
    dialog = _dialog(store, wizards, DeckSet(name="A", everyone=BOSS, exceptions={102: PIERCING, 999: None}))
    dialog.show()
    parentless_shows.clear()  # the dialog itself, shown on purpose

    dialog.add_wizard(next(w for w in dialog.addable() if w.character_id == 101))
    dialog.rows[0].remove_button.click()
    wizards.show([P1])

    assert parentless_shows == []

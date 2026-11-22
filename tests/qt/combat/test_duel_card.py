"""The duel card of a product's settings: the duel in use, its two verbs, and what it says.

Read against a library of the test's own. The window Change… opens is a mock: what is under
test is what the card asks it for and does with the answer.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bartleby.qt.art_signal import announce_art_changed
from bartleby.qt.combat.dialog import DialogMode
from bartleby.qt.combat.duel_card import CHOSEN_NOTE, DuelCard, DuelCardText, Unset
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.combat_defaults import TeamSize
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DuelCombatStrategy
from tests.qt.combat import library

TEXT = DuelCardText(
    title="Team",
    summary="With two to four wizards.",
    change_tooltip="Pick the Duel Strategy",
    unset="Use default",
    unset_tooltip="Use the default Duel Strategy",
    unreadable="No Duel Strategy could be read.",
)
DEFAULT = DuelCombatStrategy(id="default", name="Default Duel", max_players=4)
MINE = DuelCombatStrategy(id="mine", name="My Duel", max_players=4)


@pytest.fixture
def roots(tmp_path):
    roots = library.library_roots(tmp_path)
    library.save(DEFAULT, roots[0])
    library.save(MINE, roots[1])
    return roots


@pytest.fixture
def make(qtbot, roots):
    """Builds a card over the library, with nothing chosen unless the test says so."""

    def build(chosen: str | None = None, **kw) -> DuelCard:
        window = kw.pop("window", None) or MagicMock()
        card = DuelCard(
            kw.pop("size", TeamSize.TEAM),
            TEXT,
            chosen,
            library=lambda: StrategyWorkingSet.from_store(roots),
            unset=kw.pop("unset", lambda _ws: Unset(DEFAULT.id, "The default one.")),
            notes=kw.pop("notes", lambda _ws, _choice: []),
            window=window,
            **kw,
        )
        qtbot.addWidget(card)
        return card

    return build


def _window(picked: str | None) -> MagicMock:
    window = MagicMock()
    window.return_value.exec.return_value = picked is not None
    window.return_value.selected_id.return_value = picked
    return window


def _name(card: DuelCard) -> str:
    return card.strategy_card._name_lbl.text()


def test_with_nothing_chosen_the_duel_that_answers_is_shown_with_its_note(make):
    card = make()

    assert card.choice is None
    assert card.shown_id == DEFAULT.id
    assert _name(card) == "Default Duel"
    assert card.strategy_card.source_text == "Official"
    assert card.note_text == "The default one."
    assert not card.unset_button.isEnabled()


def test_a_pick_is_shown_as_the_users_and_heard_once(make):
    card = make(window=_window(MINE.id))
    heard: list = []
    card.changed.connect(heard.append)

    card.change_button.click()
    card.change_button.click()

    assert heard == [MINE.id], "picking the one chosen already is no change"
    assert card.choice == card.shown_id == MINE.id
    assert card.note_text == CHOSEN_NOTE
    assert card.unset_button.isEnabled()


def test_the_unset_verb_goes_back_to_no_choice(make):
    card = make(MINE.id)
    heard: list = []
    card.changed.connect(heard.append)

    card.unset_button.click()

    assert heard == [None]
    assert card.choice is None
    assert _name(card) == "Default Duel"


@pytest.mark.parametrize("chosen", [None, MINE.id])
@pytest.mark.parametrize(("size", "team_size"), [(TeamSize.SOLO, 1), (TeamSize.TEAM, 2)])
def test_change_opens_pick_on_the_duel_in_use_filtered_to_the_cards_team_size(make, size, team_size, chosen):
    window = _window(None)
    card = make(chosen, size=size, window=window)

    card.change_button.click()

    kwargs = window.call_args.kwargs
    assert isinstance(window.call_args.args[0], StrategyWorkingSet)
    assert kwargs["kind"] is StrategyKind.DUEL
    assert kwargs["mode"] is DialogMode.PICK
    assert kwargs["current_id"] == (chosen or DEFAULT.id)
    assert kwargs["allow_none"] is False
    assert kwargs["team_size"] == team_size
    assert kwargs["parent"] is card
    assert card.choice == chosen, "a canceled pick changes nothing"


def test_a_duel_that_counts_as_no_choice_is_none(make):
    card = make(DEFAULT.id, same_as_unset=DEFAULT.id)
    assert card.choice is None

    card.set_choice(MINE.id)
    card.set_choice(DEFAULT.id)
    assert card.choice is None


def test_without_one_the_default_picked_by_name_is_a_choice(make):
    """BartleBuddy's: what answers with no choice may change, and the player picked this one."""
    card = make()

    card.set_choice(DEFAULT.id)

    assert card.choice == DEFAULT.id
    assert card.note_text == CHOSEN_NOTE


def test_a_choice_gone_from_the_library_shows_what_answers_instead_and_the_notes(make):
    card = make("gone", notes=lambda _ws, choice: [f"{choice} is missing."])

    assert card.choice == "gone"
    assert card.shown_id == DEFAULT.id
    assert card.note_text == f"{CHOSEN_NOTE} gone is missing."


def test_with_nothing_readable_the_card_says_so(make):
    card = make(unset=lambda _ws: Unset("gone", "The default one."))

    assert card.strategy_card is None
    assert card.shown_id is None
    assert card.note_text == TEXT.unreadable


def test_a_redraw_follows_what_answers_with_no_choice(make):
    """A card that answers as another does is told when the other changed."""
    answers = [DEFAULT.id]
    card = make(unset=lambda _ws: Unset(answers[0], "Same as the other."))

    answers[0] = MINE.id
    card.redraw()

    assert card.choice is None
    assert _name(card) == "My Duel"


def test_new_art_redraws_the_strategy(make):
    card = make()
    before = card.strategy_card

    announce_art_changed()

    assert card.strategy_card is not before
    assert _name(card) == "Default Duel"

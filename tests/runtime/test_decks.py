"""A wizard's decks read from recorded memory, and one equipped by its name through the backpack.

The memory is laid out as the game's and read by wizwalker's own classes, and the backpack
behaves as four live clients' did on 2026-11-21 (``runtime/testing/sim_backpack.py``).
"""

import asyncio
import json

import pytest
from wizwalker.errors import MemoryReadError

from bartleby.runtime import decks
from bartleby.runtime.actions import live_character, node_helpers
from bartleby.runtime.arbiter import Arbiter, Owner, Preempted
from bartleby.runtime.clients import game_keys
from bartleby.runtime.decks import DecksUnreadable, EquipOutcome, equip_deck, owned_decks
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.testing.sim_backpack import SimBackpack, SimDeck
from bartleby.runtime.testing.sim_client import SimClient
from bartleby.shared.deck_names import DeckName
from bartleby.shared.locales import LOCALES

MAIN, BOSS, DUNGEON = (DeckName(type_key=f"Type_{n}") for n in (1, 2, 20))
STORM_MAIN_2 = DeckName(class_key="Class_7", type_key="Type_1", num_key="Num_2")
# en-US shows both as "Healer".
HEALER, OTHER_HEALER = DeckName(type_key="Type_12"), DeckName(type_key="Type_18")

# The end of three live decks' BasicDeckBehaviors (r814353, 2026-11-21), from +0xB8 to +0x100,
# the bytes of the heap beside the name included.
RECORDED_BLADE = bytes.fromhex(
    "b336f80430003000a82318dcf77f0000000000000000000050a306949c02000020dff437de000000"
    "0a000000000000001f000000000000000000000028000000000000003e000000"
)
RECORDED_FIRE_VANGUARD_1 = bytes.fromhex(
    "b336f80430003000a82318dcf77f00000000000000000000209832b52a02000090e12feffa000000"
    "14000000000000001f000000000000000300000025000000010000003e000000"
)
RECORDED_NEVER_NAMED = bytes.fromhex(
    "b336f80430003000a82318dcf77f0000000000000000000020a606949c02000063006f006c006f00"
    "1a000000000000001f0000000000000000000000000000000000000000000000"
)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """The cadences, and the fixed settles, which the double needs none of. No ceiling."""
    for name in ("BACKPACK_POLL", "DECK_EQUIP_POLL"):
        monkeypatch.setattr(decks, name, 0.001)
    for name in ("BACKPACK_TAB_SETTLE", "BACKPACK_ROW_SETTLE"):
        monkeypatch.setattr(decks, name, 0)
    monkeypatch.setattr(node_helpers, "WINDOW_CLOSE_RETRY_INTERVAL", 0.005)
    monkeypatch.setattr(node_helpers, "WINDOW_CLOSE_CONFIRM_INTERVAL", 0.001)


def _ceilings(monkeypatch, *names: str) -> None:
    """Shorten the waits a test is about running out of."""
    for name in names:
        monkeypatch.setattr(decks, name, 0.1)


def _three() -> tuple[SimClient, SimBackpack]:
    """A wizard in its Main deck, with a Boss and a Dungeon deck."""
    sim = SimClient()
    return sim, SimBackpack(sim, [SimDeck(MAIN), SimDeck(BOSS), SimDeck(DUNGEON)])


def _nothing_pressed(sim: SimClient, backpack: SimBackpack) -> bool:
    return not sim.inputs and not backpack.clicks


# -- the decks, from memory ---------------------------------------------------------------


async def test_every_deck_is_listed_by_name_with_the_equipped_one_marked():
    sim, backpack = _three()

    owned = await owned_decks(sim)

    assert [(d.name, d.equipped) for d in owned] == [(MAIN, True), (BOSS, False), (DUNGEON, False)]
    assert [d.global_id for d in owned] == [d.global_id for d in backpack.decks]


async def test_decks_recorded_from_live_clients_read_as_they_were_named():
    sim = SimClient()
    SimBackpack(
        sim,
        [
            SimDeck(recorded=RECORDED_FIRE_VANGUARD_1),
            SimDeck(recorded=RECORDED_BLADE),
            SimDeck(recorded=RECORDED_NEVER_NAMED),
        ],
    )

    owned = await owned_decks(sim)

    assert [d.name and d.name.text() for d in owned] == ["Fire Vanguard Deck 1", "Blade Deck", None]


async def test_the_equipped_deck_is_found_in_the_equipment_alone():
    sim, backpack = _three()
    backpack.equip(backpack.decks[1])

    assert [d.name for d in await owned_decks(sim)] == [BOSS, MAIN, DUNGEON]
    assert len(await backpack.equipment_behavior().item_list()) == 2  # the deck, and a hat


async def test_a_wizard_with_no_deck_on_lists_its_others():
    sim, backpack = _three()
    backpack.equip(None)

    assert [(d.name, d.equipped) for d in await owned_decks(sim)] == [(MAIN, False), (BOSS, False), (DUNGEON, False)]


async def test_a_deck_is_empty_when_its_spell_list_holds_no_cards():
    sim = SimClient()
    SimBackpack(sim, [SimDeck(MAIN, cards=64), SimDeck(BOSS, cards=1), SimDeck(DUNGEON, cards=0)])

    owned = await owned_decks(sim)

    assert [d.cards for d in owned] == [64, 1, 0]
    assert [d.empty for d in owned] == [False, False, True]


async def test_a_deck_never_named_has_no_name():
    sim = SimClient()
    SimBackpack(sim, [SimDeck(item="Headmistress' Dynamic Deck")])

    assert [d.name for d in await owned_decks(sim)] == [None]


async def test_decks_with_no_character_loaded_are_unreadable():
    with pytest.raises(DecksUnreadable):
        await owned_decks(SimClient())


def _items_fail(monkeypatch, backpack: SimBackpack, failing: SimDeck) -> None:
    """Make one deck item's memory move under every read of it."""
    read = decks._read_deck

    async def read_deck(item, equipped):
        if await item.global_id_full() == failing.global_id:
            raise MemoryReadError(0x1234)
        return await read(item, equipped)

    monkeypatch.setattr(decks, "_read_deck", read_deck)


async def test_an_item_that_cannot_be_read_is_left_out(monkeypatch):
    sim, backpack = _three()
    _items_fail(monkeypatch, backpack, backpack.decks[1])

    assert [d.name for d in await owned_decks(sim)] == [MAIN, DUNGEON]


# -- equipping one ---------------------------------------------------------------------------


async def test_a_deck_is_equipped_by_its_name_and_the_backpack_closed():
    sim, backpack = _three()

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.EQUIPPED and result.ok
    assert (result.on.name, result.on.equipped) == (BOSS, True)
    assert backpack.equipped is backpack.decks[1]
    assert backpack.clicks == ["Tab_Deck", "Item_2", "Equip_Item", "Close_Button"]
    assert sim.presses(GameAction.BACKPACK) == 1
    assert not backpack.is_open and not result.left_open


async def test_the_equipment_is_polled_until_it_shows_the_deck():
    """It shows it 0.0 to 0.6 s after the click."""
    sim, backpack = _three()
    backpack.equip_lag = 0.05

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.EQUIPPED
    assert backpack.clicks.count("Equip_Item") == 1


async def test_the_backpack_is_waited_for():
    sim, backpack = _three()
    backpack.open_lag = 0.05

    assert (await equip_deck(sim, BOSS)).ok
    assert sim.presses(GameAction.BACKPACK) == 1


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_row_is_found_in_whichever_locale_the_client_shows(locale):
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(BOSS), SimDeck(STORM_MAIN_2)], locale=locale)

    assert (await equip_deck(sim, STORM_MAIN_2)).ok
    assert backpack.equipped is backpack.decks[2]


async def test_equipping_works_on_a_walking_wizard():
    sim, _ = _three()
    await sim.key_sender.down(ord("W"))

    assert (await equip_deck(sim, BOSS)).ok
    assert ord("W") in sim.held


async def test_four_wizards_equip_at_once():
    wizards = [_three() for _ in range(4)]

    results = await asyncio.gather(*(equip_deck(sim, BOSS) for sim, _ in wizards))

    assert [r.outcome for r in results] == [EquipOutcome.EQUIPPED] * 4
    assert not any(backpack.is_open for _, backpack in wizards)


# -- what is a result, with nothing pressed -----------------------------------------------------


async def test_the_deck_already_on_presses_nothing():
    sim, backpack = _three()

    result = await equip_deck(sim, MAIN)

    assert result.outcome is EquipOutcome.ALREADY_ON and result.ok
    assert result.on.name == MAIN
    assert _nothing_pressed(sim, backpack)


async def test_a_deck_not_owned_presses_nothing():
    sim, backpack = _three()

    result = await equip_deck(sim, STORM_MAIN_2)

    assert result.outcome is EquipOutcome.NOT_OWNED and not result.ok
    assert result.on.name == MAIN
    assert _nothing_pressed(sim, backpack)


async def test_a_name_is_its_keys_and_not_its_text():
    """A wizard whose Healer deck is the wheel's other Healer does not own this one."""
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(OTHER_HEALER)])

    assert (await equip_deck(sim, HEALER)).outcome is EquipOutcome.NOT_OWNED
    assert _nothing_pressed(sim, backpack)


async def test_a_name_owned_twice_presses_nothing():
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(BOSS), SimDeck(BOSS)])

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.OWNED_TWICE and not result.ok
    assert result.on.name == MAIN
    assert _nothing_pressed(sim, backpack)


async def test_a_name_owned_twice_is_so_even_with_one_of_them_on():
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(BOSS), SimDeck(BOSS)])

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.OWNED_TWICE
    assert _nothing_pressed(sim, backpack)


async def test_an_empty_deck_presses_nothing():
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(BOSS, cards=0)])

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.EMPTY and not result.ok
    assert result.on.name == MAIN
    assert _nothing_pressed(sim, backpack)


async def test_a_fight_refuses():
    sim, backpack = _three()
    sim.battle = True

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.IN_FIGHT
    assert _nothing_pressed(sim, backpack)


async def test_a_loading_screen_refuses():
    sim, backpack = _three()
    sim.loading = True

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.BACKPACK_CLOSED
    assert _nothing_pressed(sim, backpack)


async def test_character_select_refuses(monkeypatch):
    sim, backpack = _three()

    async def at_character_select(_client):
        return True

    monkeypatch.setattr(live_character, "at_character_select", at_character_select)
    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.BACKPACK_CLOSED
    assert _nothing_pressed(sim, backpack)


@pytest.mark.parametrize("up", ["chat", "other_box"])
async def test_the_chat_line_or_a_message_box_refuses(up):
    """The key would type in the one, and the other takes every click behind it."""
    sim, backpack = _three()
    setattr(backpack, up, True)

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.BACKPACK_CLOSED and result.reason
    assert _nothing_pressed(sim, backpack)


async def test_a_deck_box_the_player_raised_is_left_to_the_player():
    sim, backpack = _three()
    backpack.asking = backpack.decks[2]

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.BACKPACK_CLOSED
    assert backpack.asking is backpack.decks[2]


async def test_a_deck_that_could_not_be_read_is_not_called_unowned(monkeypatch):
    sim, backpack = _three()
    _items_fail(monkeypatch, backpack, backpack.decks[1])

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.FAILED
    assert _nothing_pressed(sim, backpack)


async def test_unreadable_decks_are_a_failure():
    sim = SimClient()

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.FAILED
    assert not sim.inputs


# -- the backpack that does not open, or was open -------------------------------------------------


async def test_a_backpack_that_does_not_open_is_said_so(monkeypatch):
    _ceilings(monkeypatch, "BACKPACK_OPEN_TIMEOUT")
    sim, backpack = _three()
    backpack.opens = False

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.BACKPACK_CLOSED and result.reason
    assert sim.presses(GameAction.BACKPACK) == 1
    assert backpack.clicks == []


async def test_a_backpack_key_no_press_can_use_is_said_so(monkeypatch):
    sim, backpack = _three()

    async def press(*_):
        raise game_keys.KeyNotPressable("Backpack is on Ctrl+B")

    monkeypatch.setattr(game_keys, "press", press)
    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.BACKPACK_CLOSED
    assert _nothing_pressed(sim, backpack)


async def test_a_backpack_the_player_has_open_is_used_and_left_open():
    sim, backpack = _three()
    backpack.open("Tab_Hat")

    assert (await equip_deck(sim, BOSS)).ok
    assert backpack.is_open
    assert sim.presses(GameAction.BACKPACK) == 0
    assert backpack.clicks == ["Tab_Deck", "Item_2", "Equip_Item"]


async def test_a_spellbook_open_on_another_page_is_turned_to_the_backpack_and_left_open():
    sim, backpack = _three()
    backpack.other_page = True

    assert (await equip_deck(sim, BOSS)).ok
    assert backpack.page_shown
    assert "Close_Button" not in backpack.clicks


async def test_rows_ordered_before_another_deck_went_on_still_equip():
    """The order is fixed when the backpack opens, so the deck on is not the first row any more."""
    sim, backpack = _three()
    backpack.open()
    backpack.equip(backpack.decks[2])

    assert (await equip_deck(sim, MAIN)).ok
    assert backpack.equipped is backpack.decks[0]
    assert "Item_1" in backpack.clicks


async def test_a_page_with_no_decks_tab_is_a_failure():
    sim, backpack = _three()
    backpack.root.place(decks.BACKPACK_DECKS_TAB_PATH, there=lambda: False)

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.FAILED and "Decks tab" in result.reason
    assert not backpack.is_open


# -- pages -------------------------------------------------------------------------------------


def _many(count: int = 10) -> tuple[SimClient, SimBackpack, SimDeck]:
    """A wizard with *count* decks never named, listed before its Storm Main Deck 2."""
    sim = SimClient()
    wanted = SimDeck(STORM_MAIN_2)
    others = [SimDeck(item=f"A Deck {n}") for n in range(count)]
    return sim, SimBackpack(sim, [*others, wanted]), wanted


async def test_a_deck_on_the_second_page_is_found():
    sim, backpack, wanted = _many()

    assert (await equip_deck(sim, STORM_MAIN_2)).ok
    assert backpack.equipped is wanted
    assert backpack.clicks == ["Tab_Deck", "rightscroll", "Item_3", "Equip_Item", "Close_Button"]


async def test_the_list_is_walked_from_its_first_page():
    sim = SimClient()
    decks_owned = [SimDeck(item=f"Z Deck {n}") for n in range(10)] + [SimDeck(BOSS)]
    backpack = SimBackpack(sim, decks_owned)
    backpack.open("Tab_Deck", page=2)

    assert (await equip_deck(sim, BOSS)).ok
    assert backpack.clicks[:2] == ["Tab_Deck", "leftscroll"]


async def test_an_arrow_that_does_not_turn_the_page_ends_the_walk(monkeypatch):
    _ceilings(monkeypatch, "BACKPACK_PAGE_TIMEOUT")
    sim, backpack, _ = _many()
    backpack.drops["rightscroll"] = 99

    result = await equip_deck(sim, STORM_MAIN_2)

    assert result.outcome is EquipOutcome.FAILED and result.reason
    assert backpack.clicks.count("rightscroll") == decks.DECK_EQUIP_ATTEMPTS
    assert not backpack.is_open


async def test_the_walk_is_bounded_by_the_decks_owned(monkeypatch):
    """A list that always has another page, and never the deck, is walked no further than the decks reach."""
    sim, backpack, wanted = _many()
    monkeypatch.setattr(wanted, "text", lambda _locale: "Something Else")
    monkeypatch.setattr(SimBackpack, "pages", property(lambda _self: 1000))

    result = await equip_deck(sim, STORM_MAIN_2)

    assert result.outcome is EquipOutcome.FAILED
    # 11 decks fill two pages, and one more is allowed.
    assert backpack.clicks.count("rightscroll") == 2 * decks.DECK_EQUIP_ATTEMPTS
    assert not backpack.is_open


# -- clicks the game drops ----------------------------------------------------------------------


@pytest.mark.parametrize("dropped", ["Tab_Deck", "Item_2", "Equip_Item", "Close_Button"])
async def test_one_dropped_click_is_made_up_for(monkeypatch, dropped):
    _ceilings(monkeypatch, "BACKPACK_BUTTON_TIMEOUT", "DECK_EQUIP_TIMEOUT")
    sim, backpack = _three()
    backpack.drops[dropped] = 1

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.EQUIPPED
    assert backpack.equipped is backpack.decks[1]
    assert not backpack.is_open


async def test_a_deck_that_never_goes_on_is_a_failure_with_the_backpack_closed(monkeypatch):
    _ceilings(monkeypatch, "DECK_EQUIP_TIMEOUT")
    sim, backpack = _three()
    backpack.drops["Equip_Item"] = 99

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.FAILED and result.reason
    assert result.on.name == MAIN
    assert backpack.clicks.count("Equip_Item") == decks.DECK_EQUIP_ATTEMPTS
    assert not backpack.is_open


async def test_a_row_left_selected_by_the_player_is_not_equipped_for_good(monkeypatch):
    """The row's click dropped and another row selected: the wrong deck goes on, and the row is tried again."""
    sim, backpack = _three()
    backpack.open("Tab_Deck")
    backpack.drops = {"Item_2": 1}
    monkeypatch.setattr(backpack, "_tab", lambda: None)
    backpack.root.place(decks.BACKPACK_DECKS_TAB_PATH)
    backpack.selected = backpack.decks[2]

    assert (await equip_deck(sim, BOSS)).ok
    assert backpack.equipped is backpack.decks[1]


async def test_a_row_that_reads_otherwise_once_selected_is_not_equipped():
    """The list changed between the row's click and Equip's."""
    sim, backpack = _three()
    backpack.on_click = lambda name: name == "Item_2" and setattr(backpack.decks[1], "name", STORM_MAIN_2)

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.FAILED
    assert "Equip_Item" not in backpack.clicks


async def test_a_backpack_that_will_not_close_is_said_so(monkeypatch):
    monkeypatch.setattr(node_helpers, "WINDOW_CLOSE_TIMEOUT", 0.05)
    sim, backpack = _three()
    backpack.closes = False

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.EQUIPPED
    assert result.left_open


async def test_a_read_that_fails_partway_is_a_failure_with_the_backpack_closed(monkeypatch):
    sim, backpack = _three()

    async def walk(_self):
        raise MemoryReadError(0x1234)

    monkeypatch.setattr(decks._Visit, "_walk", walk)
    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.FAILED and "MemoryReadError" in result.reason
    assert not backpack.is_open


# -- names that read alike --------------------------------------------------------------------


@pytest.mark.parametrize("wanted", [1, 2])
async def test_the_right_deck_of_two_that_read_alike_goes_on(wanted):
    """The rows read the same, so the deck that goes on is told by its id, and the next row tried."""
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(HEALER), SimDeck(OTHER_HEALER)])

    result = await equip_deck(sim, backpack.decks[wanted].name)

    assert result.outcome is EquipOutcome.EQUIPPED
    assert backpack.equipped is backpack.decks[wanted]
    assert result.on.global_id == backpack.decks[wanted].global_id


@pytest.mark.parametrize("on", [0, 1])
async def test_the_deck_on_is_never_clicked_for_one_that_reads_alike(on):
    """Equip on the deck that is on would take it off."""
    sim = SimClient()
    owned = [SimDeck(HEALER), SimDeck(OTHER_HEALER)]
    backpack = SimBackpack(sim, owned, equipped=owned[on])

    assert (await equip_deck(sim, owned[1 - on].name)).ok
    assert backpack.equipped is owned[1 - on]
    assert backpack.clicks.count("Equip_Item") == 1


async def test_a_deck_that_reads_alike_left_on_is_reported(monkeypatch):
    _ceilings(monkeypatch, "DECK_EQUIP_TIMEOUT")
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(OTHER_HEALER), SimDeck(HEALER)])
    # The first Equip puts the other Healer on, and the game drops every one after it.
    monkeypatch.setattr(
        backpack,
        "on_click",
        lambda name: (
            name == "Equip_Item" and backpack.equipped is not backpack.decks[0] and backpack.drops.update(Equip_Item=1)
        ),
    )

    result = await equip_deck(sim, HEALER)

    assert result.outcome is EquipOutcome.FAILED and not result.ok
    assert result.on.name == OTHER_HEALER
    assert not backpack.is_open


async def test_an_empty_deck_that_reads_alike_is_declined_and_passed_over():
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(OTHER_HEALER, cards=0), SimDeck(HEALER)])

    assert (await equip_deck(sim, HEALER)).outcome is EquipOutcome.EQUIPPED
    assert backpack.equipped is backpack.decks[2]
    assert not backpack.copied


# -- the game's question on an empty deck -----------------------------------------------------------


def _called_empty(**kwargs) -> tuple[SimClient, SimBackpack]:
    """A wizard whose Boss deck reads one card, and which the game still calls empty."""
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(MAIN), SimDeck(BOSS, cards=1)], **kwargs)
    backpack.called_empty = [backpack.decks[1]]
    return sim, backpack


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_games_question_is_answered_no_and_the_deck_reported_empty(locale):
    sim, backpack = _called_empty(locale=locale)

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.EMPTY and not result.ok
    assert result.on.name == BOSS  # No equips it as it is, and the result says so
    assert not backpack.copied
    assert backpack.asking is None and not backpack.is_open


async def test_a_deck_put_on_a_moment_after_the_no_is_waited_for():
    sim, backpack = _called_empty()
    backpack.equip_lag = 0.05

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.EMPTY
    assert result.on.name == BOSS


async def test_a_dropped_no_is_clicked_again():
    sim, backpack = _called_empty()
    backpack.drops["rightButton"] = 2

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.EMPTY
    assert backpack.asking is None and not backpack.is_open


async def test_a_question_this_build_does_not_know_is_still_answered_no():
    sim, backpack = _called_empty()
    backpack.question = "A question reworded by a game update?"

    await equip_deck(sim, BOSS)

    assert not backpack.copied
    assert backpack.asking is None and not backpack.is_open


async def test_a_question_that_will_not_go_fails_and_is_said_so(monkeypatch):
    monkeypatch.setattr(node_helpers, "WINDOW_CLOSE_TIMEOUT", 0.05)
    sim, backpack = _called_empty()
    backpack.drops["rightButton"] = 10_000

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.FAILED
    assert result.left_open
    assert backpack.clicks.count("Equip_Item") == 1


async def test_a_disconnect_is_left_alone():
    sim, backpack = _called_empty()
    captions = json.loads(dialog_captions.CAPTIONS_PATH.read_text(encoding="utf-8"))["captions"]
    backpack.question = captions[dialog_captions.DISCONNECTED_LOST]["en-US"]

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.FAILED
    assert "rightButton" not in backpack.clicks


# -- pulled into a fight ----------------------------------------------------------------------------


@pytest.mark.parametrize("window", ["Tab_Deck", "Item_2", "Equip_Item"])
async def test_a_fight_that_starts_meanwhile_ends_it(monkeypatch, window):
    _ceilings(monkeypatch, "DECK_EQUIP_TIMEOUT", "BACKPACK_BUTTON_TIMEOUT")
    sim, backpack = _three()
    backpack.fight_on = window

    result = await equip_deck(sim, BOSS)

    assert result.outcome is EquipOutcome.IN_FIGHT
    assert result.on.name == MAIN
    assert not backpack.is_open


async def test_a_fight_before_the_backpack_opens_is_the_reason_it_did_not(monkeypatch):
    _ceilings(monkeypatch, "BACKPACK_OPEN_TIMEOUT")
    sim, backpack = _three()
    sim.on_press = lambda _action: setattr(sim, "battle", True)
    backpack.opens = False

    assert (await equip_deck(sim, BOSS)).outcome is EquipOutcome.IN_FIGHT


# -- a cancel, anywhere ----------------------------------------------------------------------------


async def _cancel_when(equip: asyncio.Task, condition) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0)
    equip.cancel()
    with pytest.raises(asyncio.CancelledError):
        await equip


async def test_a_cancel_before_the_backpack_shows_still_closes_it():
    """The key has gone to the game: it opens a moment later, behind whoever took the client."""
    sim, backpack = _three()
    backpack.open_lag = 0.1

    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: sim.presses(GameAction.BACKPACK))

    assert backpack.clicks == ["Close_Button"]
    assert not backpack.is_open


@pytest.mark.parametrize("window", ["Tab_Deck", "Item_2"])
async def test_a_cancel_on_the_way_closes_the_backpack(window):
    sim, backpack = _three()

    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: window in backpack.clicks)

    assert not backpack.is_open


async def test_a_cancel_while_the_deck_goes_on_closes_the_backpack():
    sim, backpack = _three()
    backpack.equip_lag = 0.1

    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: "Equip_Item" in backpack.clicks)

    assert backpack.equipped is backpack.decks[1]
    assert not backpack.is_open


async def test_a_cancel_just_before_the_games_question_answers_it_and_closes_the_backpack():
    sim, backpack = _called_empty()
    backpack.equip_lag = 0.1

    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: "Equip_Item" in backpack.clicks)

    assert backpack.asking is None and not backpack.copied
    assert not backpack.is_open


async def test_a_cancel_leaves_a_backpack_the_player_had_open():
    sim, backpack = _three()
    backpack.open()

    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: "Item_2" in backpack.clicks)

    assert backpack.is_open


async def test_a_lease_preempted_by_combat_closes_the_backpack_before_it_is_given_up():
    sim, backpack = _three()
    arbiter = Arbiter()

    async def hotkey():
        async with arbiter.acquire(sim, Owner.HOTKEY, detail="decks") as lease:
            await equip_deck(lease.client, BOSS)

    task = asyncio.create_task(hotkey())
    async with asyncio.timeout(10):
        while "Tab_Deck" not in backpack.clicks:
            await asyncio.sleep(0)
    async with arbiter.acquire(sim, Owner.COMBAT):
        assert not backpack.is_open
    with pytest.raises(Preempted):
        await task


async def test_a_cleanup_that_fails_does_not_hide_the_cancel(monkeypatch):
    sim, _ = _three()

    async def close_spellbook(_client):
        raise MemoryReadError(0x1234)

    monkeypatch.setattr(node_helpers, "close_spellbook", close_spellbook)
    await _cancel_when(asyncio.create_task(equip_deck(sim, BOSS)), lambda: sim.backpack.clicks)


# -- the deck on, alone ---------------------------------------------------------------------


async def test_the_deck_on_is_read_by_its_id_and_name():
    sim, backpack = _three()

    worn = await decks.worn_deck(sim)
    assert (worn.global_id, worn.name) == (backpack.decks[0].global_id, MAIN)

    backpack.equip(backpack.decks[2])
    assert (await decks.worn_deck(sim)).name == DUNGEON


async def test_a_worn_deck_never_named_has_no_name_and_no_deck_on_is_none():
    sim = SimClient()
    backpack = SimBackpack(sim, [SimDeck(item="Headmistress' Dynamic Deck")])

    assert (await decks.worn_deck(sim)).name is None

    backpack.equip(None)
    assert await decks.worn_deck(sim) is None


async def test_the_deck_on_cannot_be_read_with_no_character():
    with pytest.raises(DecksUnreadable):
        await decks.worn_deck(SimClient())

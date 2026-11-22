"""A wizard's decks, and equipping one by its name.

:func:`owned_decks` reads every deck item a wizard has: the inventory's, and the equipped one,
which is only in the equipment. Reads alone, so it needs no lease.

:func:`equip_deck` puts the deck of a :class:`~bartleby.shared.deck_names.DeckName` on, by the
quickest route Stage 1 of BartleBuddy's Auto-combat epic measured: the backpack's Decks tab,
the row whose text is the name, then Equip (``docs/WIZARD101.md``, *Decks and the backpack*).
It took 1.7 to 2.1 s a wizard by hand-driven probe, and it works while the wizard walks, so
nothing here waits for a wizard to stand still. Everything it presses is input, so in a
product it is called with a lease's client (:mod:`bartleby.runtime.arbiter`).

A row's text is not a deck's identity, since two names can share theirs. So the deck that goes
on is told by its global id, in the equipment, and a row that turns out to be another deck is
passed over for the next that reads the same.

Nothing here logs a wizard, and neither does it know which player a client is: the caller logs
the :class:`EquipResult` by the player's number.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import re
from dataclasses import dataclass
from enum import StrEnum

from wizwalker.memory.memory_objects.deck_behavior import ClientDeckBehavior

from bartleby.runtime.actions import node_helpers
from bartleby.runtime.actions.live_character import live_character_id
from bartleby.runtime.clients import game_keys
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.game.wizard_names import NO_CHARACTER
from bartleby.runtime.paths import (
    BACKPACK_DECKS_TAB_PATH,
    BACKPACK_EQUIP_BUTTON_PATH,
    BACKPACK_NEXT_PAGE_PATH,
    BACKPACK_PAGE_NUMBER_PATH,
    BACKPACK_PAGE_PATH,
    BACKPACK_PREVIOUS_PAGE_PATH,
    BACKPACK_ROWS,
    CHAT_EDIT_PATH,
    WORLD_MESSAGE_BOX_CAPTION_PATH,
    WORLD_MESSAGE_BOX_PATH,
    WORLD_MESSAGE_BOX_RIGHT,
    backpack_row_equipped_path,
    backpack_row_path,
)
from bartleby.runtime.timing import (
    BACKPACK_BUTTON_TIMEOUT,
    BACKPACK_LATE_OPEN,
    BACKPACK_OPEN_TIMEOUT,
    BACKPACK_PAGE_TIMEOUT,
    BACKPACK_POLL,
    BACKPACK_ROW_SETTLE,
    BACKPACK_TAB_SETTLE,
    DECK_EQUIP_ATTEMPTS,
    DECK_EQUIP_POLL,
    DECK_EQUIP_TIMEOUT,
)
from bartleby.shared import deck_names
from bartleby.shared.deck_names import DeckName

logger = logging.getLogger(__name__)

#: The name of the behavior a deck item has, and no other item.
DECK_BEHAVIOR = "BasicDeckBehavior"

_PAGE_NUMBER = re.compile(r"(\d+)\s*/\s*(\d+)")


@dataclass(frozen=True)
class OwnedDeck:
    """One deck item a wizard has."""

    global_id: int
    #: Its name, or None for a deck never named, which the game shows by its item's own name.
    name: DeckName | None
    equipped: bool
    #: How many cards its spell list holds. Whether item cards count among them, or treasure
    #: cards, is not known: a deck holding nothing else may read 0.
    cards: int

    @property
    def empty(self) -> bool:
        return self.cards == 0


class DecksUnreadable(RuntimeError):
    """A wizard's decks could not be read: no character is loaded, or the memory moved under the read."""


class EquipOutcome(StrEnum):
    """What :func:`equip_deck` came to."""

    EQUIPPED = "equipped"
    #: It was on before: nothing was pressed.
    ALREADY_ON = "already on"
    #: No deck of the wizard's has the name. Nothing was pressed.
    NOT_OWNED = "not owned"
    #: Two or more have it, and nothing says which is meant. Nothing was pressed.
    OWNED_TWICE = "owned twice"
    #: The deck holds no cards. Nothing was pressed, unless the game was first to say so: then
    #: its offer to copy the old deck's cards was declined, and :attr:`EquipResult.on` is what
    #: the wizard was left wearing.
    EMPTY = "empty"
    #: The wizard is in a fight, where no deck can be changed, or was pulled into one first.
    IN_FIGHT = "in a fight"
    #: The backpack can't open here, or something else has the screen.
    BACKPACK_CLOSED = "the backpack can't open"
    FAILED = "failed"


@dataclass(frozen=True)
class EquipResult:
    """What :func:`equip_deck` did."""

    outcome: EquipOutcome
    name: DeckName
    #: The deck on when it ended, or None where that wasn't read. After EQUIPPED and ALREADY_ON
    #: it is the deck named; after anything else, usually the deck on before.
    on: OwnedDeck | None = None
    #: Why, in a few words, for a BACKPACK_CLOSED or FAILED result's log line. Names no wizard.
    reason: str = ""
    #: Whether the backpack could not be closed again, so it takes every click meant for the game.
    left_open: bool = False

    @property
    def ok(self) -> bool:
        """Whether the deck named is on, ready for a fight."""
        return self.outcome in (EquipOutcome.EQUIPPED, EquipOutcome.ALREADY_ON)


# -- the wizard's decks -------------------------------------------------------------------


async def owned_decks(client) -> list[OwnedDeck]:
    """Every deck item the wizard has: the equipped one first, then the inventory's in its own order.

    Walks the whole inventory, some 150 items and their behaviors, so it belongs behind an
    action, never a poll. An item whose memory moved under the read is left out. Raises
    :class:`DecksUnreadable` where there is no inventory to read, as on a loading screen.
    """
    decks, _ = await _read_decks(client)
    return decks


async def _read_decks(client) -> tuple[list[OwnedDeck], int]:
    """:func:`owned_decks`, and how many items could not be read."""
    try:
        equipment = await client.client_object.try_get_equipment_behavior()
        inventory = await client.client_object.try_get_inventory_behavior()
        if equipment is None or inventory is None:
            raise DecksUnreadable("no character is loaded")
        items = [(item, True) for item in await equipment.item_list()]
        items += [(item, False) for item in await inventory.item_list()]
    except DecksUnreadable:
        raise
    except Exception as e:
        raise DecksUnreadable("the inventory could not be read") from e
    decks: list[OwnedDeck] = []
    unread = 0
    for item, equipped in items:
        try:
            if (deck := await _read_deck(item, equipped)) is not None:
                decks.append(deck)
        except Exception:
            logger.debug("Could not read one item of a wizard's", exc_info=True)
            unread += 1
        # Reads are synchronous underneath: without this the walk holds the loop throughout.
        await asyncio.sleep(0)
    return decks, unread


async def _read_deck(item, equipped: bool) -> OwnedDeck | None:
    """*item* as a deck, or None when it is no deck."""
    behavior = await item.search_behavior_by_name(DECK_BEHAVIOR)
    if behavior is None:
        return None
    deck = ClientDeckBehavior(behavior.hook_handler, await behavior.read_base_address())
    return OwnedDeck(
        global_id=await item.global_id_full(),
        name=DeckName.from_indices(*await deck.name_indices()),
        equipped=equipped,
        cards=sum([await spell.quantity() for spell in await deck.spell_list()]),
    )


async def _equipped_id(client) -> int | None:
    """The global id of the deck on, or None: none is on, or it can't be read just now."""
    try:
        equipment = await client.client_object.try_get_equipment_behavior()
        for item in await equipment.item_list() if equipment is not None else []:
            if await item.search_behavior_by_name(DECK_BEHAVIOR) is not None:
                return await item.global_id_full()
    except Exception:
        logger.debug("Could not read the equipped deck", exc_info=True)
    return None


# -- equipping one ----------------------------------------------------------------------------


async def equip_deck(client, name: DeckName) -> EquipResult:
    """Equip the wizard's deck named *name*. *client* is a lease's: this presses and clicks.

    Nothing is pressed for a deck already on, not owned, owned twice or empty, in a fight, on a
    loading screen or at character select, or with a message box or the chat line up. Each is
    a result, not an error, and the wizard keeps its deck.

    **Empty** is the spell list reading 0 cards. What a deck holding only item cards reads is
    not known yet; one that reads 0 is refused as empty here, which changes nothing in the game.

    **The backpack** is closed again however this ends, a cancel included. A spellbook the
    player already had open is used as it is and left open, on its Decks tab: the order of its
    rows is fixed when it opens, and nothing here depends on it being fresh.

    **A cancel** (a lease preempted) can land anywhere. After the Equip click the deck may
    still go on; running this again reads what is on and starts over.

    Raises nothing for what goes wrong in the game: a click that does nothing, or a read that
    fails, is FAILED, with its reason.
    """
    if await live_character_id(client) == NO_CHARACTER:
        return EquipResult(EquipOutcome.BACKPACK_CLOSED, name, reason="no character is in the world")
    if await client.in_battle():
        return EquipResult(EquipOutcome.IN_FIGHT, name)
    try:
        decks, unread = await _read_decks(client)
    except DecksUnreadable as e:
        return EquipResult(EquipOutcome.FAILED, name, reason=str(e))

    on = next((deck for deck in decks if deck.equipped), None)
    owned = [deck for deck in decks if deck.name == name]
    if not owned and unread:
        return EquipResult(EquipOutcome.FAILED, name, on, "not every deck could be read")
    if not owned:
        return EquipResult(EquipOutcome.NOT_OWNED, name, on)
    if len(owned) > 1:
        return EquipResult(EquipOutcome.OWNED_TWICE, name, on)
    wanted = owned[0]
    if wanted.equipped:
        return EquipResult(EquipOutcome.ALREADY_ON, name, on)
    if wanted.empty:
        return EquipResult(EquipOutcome.EMPTY, name, on)
    if reason := await _something_up(client):
        return EquipResult(EquipOutcome.BACKPACK_CLOSED, name, on, reason)

    visit = _Visit(client, wanted, len(decks))
    try:
        outcome, reason = await visit.equip()
    except Exception as e:
        logger.debug("Equipping a deck failed", exc_info=True)
        outcome, reason = EquipOutcome.FAILED, f"a read or a click failed ({type(e).__name__})"
    finally:
        closed = await visit.leave()
    now = await _equipped_id(client)
    left_on = next((dataclasses.replace(d, equipped=True) for d in decks if d.global_id == now), None)
    return EquipResult(outcome, name, left_on, reason, left_open=not closed)


async def _something_up(client) -> str:
    """What would take the backpack's key or its clicks, or "" when nothing is."""
    if await node_helpers.modal_showing(client) or await _box_up(client):
        return "a message box is up"
    if await node_helpers.is_visible_by_path(client, CHAT_EDIT_PATH):
        return "the chat line is open"
    return ""


async def _box_up(client) -> bool:
    """Whether a message box is up where equipping an empty deck puts one."""
    return await node_helpers.is_visible_by_path(client, WORLD_MESSAGE_BOX_PATH)


class _Visit:
    """One visit to the backpack, to equip *wanted*. ``equip`` then ``leave``, whatever ``equip`` did."""

    def __init__(self, client, wanted: OwnedDeck, owned: int) -> None:
        self._client = client
        self._wanted = wanted
        # A page more than the decks fill, for decks gained since they were read.
        self._max_pages = math.ceil(owned / BACKPACK_ROWS) + 1
        #: Whether this visit pressed the backpack's key, and so has it to close.
        self._pressed = False
        #: Whether the key's effect was waited out: the backpack showed, or it never did.
        self._waited = False
        #: Whether any row was clicked for the deck's, and whether the game called the last one empty.
        self._found = False
        self._asked = False
        #: The deck on before an Equip click whose effect has not been waited out, as (id,).
        self._unsettled: tuple[int | None] | None = None

    async def equip(self) -> tuple[EquipOutcome, str]:
        """Put the deck on. The outcome, and why for one that needs saying."""
        if not await self._open():
            if await self._client.in_battle():
                return EquipOutcome.IN_FIGHT, ""
            return EquipOutcome.BACKPACK_CLOSED, "the backpack did not open"
        reason = "no row of the backpack's decks reads its name"
        for _ in range(DECK_EQUIP_ATTEMPTS):
            if not await node_helpers.click_by_path(self._client, BACKPACK_DECKS_TAB_PATH):
                reason = "the backpack's Decks tab is missing or grayed"
                break
            await asyncio.sleep(BACKPACK_TAB_SETTLE)
            if await self._walk():
                return (EquipOutcome.EMPTY, "") if self._asked else (EquipOutcome.EQUIPPED, "")
            if self._found or await self._client.in_battle():
                break
        if await self._client.in_battle():
            return EquipOutcome.IN_FIGHT, ""
        if self._found:
            reason = "its row was clicked and the deck did not go on"
        return EquipOutcome.FAILED, reason

    async def leave(self) -> bool:
        """Put the backpack back as it was. True once it is. Never raises but for a cancel.

        Runs on a cancel too, which may have come between the key and the backpack showing, or
        with the game's question up, under which nothing closes.
        """
        try:
            if self._pressed and not self._waited:
                await node_helpers.poll_until(self._page_open, BACKPACK_LATE_OPEN, BACKPACK_POLL)
            if self._unsettled is not None:
                await node_helpers.poll_until(self._settled, BACKPACK_LATE_OPEN, BACKPACK_POLL)
                if await _box_up(self._client) and not await self._answer_box():
                    return False
            return not self._pressed or await node_helpers.close_spellbook(self._client)
        except Exception:
            logger.debug("Could not close the backpack", exc_info=True)
            return False

    # -- opening it, and its pages ------------------------------------------------------------

    async def _page_open(self) -> bool:
        return await node_helpers.is_visible_by_path(self._client, BACKPACK_PAGE_PATH)

    async def _open(self) -> bool:
        """Show the backpack: already there, or by its key. From another spellbook page the key turns to it."""
        if await self._page_open():
            return True
        # A spellbook the player has open on another page is theirs to close.
        self._pressed = not await node_helpers.is_spellbook_open(self._client)
        try:
            await game_keys.press(self._client, GameAction.BACKPACK, 0.1)
        except game_keys.KeyNotPressable as e:
            logger.info("The backpack can't be opened: %s", e)
            self._pressed = False
            return False
        # Once, since the key is a toggle: a second press would close one that opened late.
        opened = await node_helpers.poll_until(self._page_open, BACKPACK_OPEN_TIMEOUT, BACKPACK_POLL)
        self._waited = True
        return opened

    async def _page(self) -> tuple[int, int]:
        """The list's page and how many it has, (1, 1) where the number can't be read."""
        text = node_helpers.strip_html(await node_helpers.get_text_by_path(self._client, BACKPACK_PAGE_NUMBER_PATH))
        found = _PAGE_NUMBER.search(text)
        return (int(found.group(1)), int(found.group(2))) if found else (1, 1)

    async def _turn(self, arrow: list[str], to: int) -> bool:
        """Click a page arrow. True once the page number reads *to*."""

        async def turned() -> bool:
            return (await self._page())[0] == to

        if not await node_helpers.click_by_path(self._client, arrow):
            return False
        if not await node_helpers.poll_until(turned, BACKPACK_PAGE_TIMEOUT, BACKPACK_POLL):
            return False
        await asyncio.sleep(BACKPACK_ROW_SETTLE)
        return True

    async def _walk(self) -> bool:
        """Try every row of the list that reads the deck's name, from the first page. True once it is on.

        Bounded: each turn must reach the next page, and there are no more pages than the
        wizard's decks fill, whatever the page number says.
        """
        page, pages = await self._page()
        for before in range(min(page, self._max_pages) - 1, 0, -1):
            if not await self._turn(BACKPACK_PREVIOUS_PAGE_PATH, before):
                break
        page, pages = await self._page()
        while True:
            for row in range(1, BACKPACK_ROWS + 1):
                if await self._reads_name(row):
                    if await self._equip_row(row):
                        return True
                    if await self._client.in_battle() or await _box_up(self._client):
                        return False
            if page >= min(pages, self._max_pages) or not await self._turn(BACKPACK_NEXT_PAGE_PATH, page + 1):
                return False
            page += 1

    # -- a row --------------------------------------------------------------------------------

    async def _reads_name(self, row: int) -> bool:
        """Whether *row* shows a deck reading the wanted name, in whichever locale the client runs."""
        path = backpack_row_path(row)
        if not await node_helpers.is_visible_by_path(self._client, path):
            return False
        text = await node_helpers.get_text_by_path(self._client, path)
        return deck_names.matches(self._wanted.name, text)

    async def _worn(self, row: int) -> bool:
        """Whether *row* is the deck on, which Equip must not be clicked for: it is another of the same text."""
        return await node_helpers.is_visible_by_path(self._client, backpack_row_equipped_path(row))

    async def _equip_row(self, row: int) -> bool:
        """Select *row* and click Equip, again if the game dropped it. True once the wanted deck is on.

        False with the row worn means it was another deck of the same text.
        """
        for _ in range(DECK_EQUIP_ATTEMPTS):
            before = await _equipped_id(self._client)
            if before == self._wanted.global_id:
                return True
            if await self._client.in_battle() or not await self._reads_name(row) or await self._worn(row):
                return False
            self._found = True
            await node_helpers.click_by_path(self._client, backpack_row_path(row))
            await asyncio.sleep(BACKPACK_ROW_SETTLE)
            if not await self._reads_name(row) or not await node_helpers.poll_until(
                self._equip_offered, BACKPACK_BUTTON_TIMEOUT, BACKPACK_POLL
            ):
                continue
            self._unsettled = (before,)
            await node_helpers.click_by_path(self._client, BACKPACK_EQUIP_BUTTON_PATH)
            if await self._await_change(before) == self._wanted.global_id:
                return True
            if self._asked or await _box_up(self._client):
                return False
        return False

    async def _equip_offered(self) -> bool:
        return await node_helpers.is_visible_by_path(
            self._client, BACKPACK_EQUIP_BUTTON_PATH
        ) and await node_helpers.is_enabled_by_path(self._client, BACKPACK_EQUIP_BUTTON_PATH)

    async def _await_change(self, before: int | None) -> int | None:
        """The deck on once it is no longer *before*, or *before* when nothing changes in time.

        The game's question about an empty deck is answered No on the way, which equips the
        deck as it is.
        """
        self._asked = False
        await node_helpers.poll_until(self._settled, DECK_EQUIP_TIMEOUT, DECK_EQUIP_POLL)
        if await _box_up(self._client) and await self._answer_box():
            await node_helpers.poll_until(self._changed, DECK_EQUIP_TIMEOUT, DECK_EQUIP_POLL)
        if not await _box_up(self._client):
            self._unsettled = None
        now = await _equipped_id(self._client)
        return before if now is None else now

    async def _changed(self) -> bool:
        """Whether another deck is on than before the last Equip click."""
        now = await _equipped_id(self._client)
        return now is not None and (now,) != self._unsettled

    async def _settled(self) -> bool:
        """Whether the last Equip click has done something: a deck went on, or the game asks first."""
        return await _box_up(self._client) or await self._changed()

    async def _answer_box(self) -> bool:
        """Answer the message box Equip raised with its right button, No, until it is gone. True once it is.

        No is safe: it declines to copy one deck's cards into another. A box this build does
        not recognize gets it too, since none was up before Equip was clicked, and a question
        the game has reworded would keep the backpack open. A disconnect is left alone, since
        nothing on it helps.
        """
        caption = await node_helpers.get_text_by_path(self._client, WORLD_MESSAGE_BOX_CAPTION_PATH)
        key = dialog_captions.identify_caption(caption)
        if key in dialog_captions.DISCONNECTS:
            return False
        self._asked = key == dialog_captions.EQUIP_EMPTY_DECK
        logger.info("Declining the game's question on equipping a deck (%s)", key and dialog_captions.describe(key))
        return await node_helpers.click_until_gone(
            self._client, WORLD_MESSAGE_BOX_RIGHT, is_open=lambda: _box_up(self._client)
        )

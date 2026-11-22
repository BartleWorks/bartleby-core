"""A simulated client's decks and backpack, for what reads and equips them (``runtime/decks.py``).

``SimBackpack(sim, decks)`` gives a :class:`~bartleby.runtime.testing.sim_client.SimClient` two
things a real client has, as Stage 1 of BartleBuddy's Auto-combat epic measured them
(``docs/WIZARD101.md``, *Decks and the backpack*):

* **The deck items in memory**, laid out as the game's and read by wizwalker's own classes:
  the equipped one in the equipment's item list, every other in the inventory's, among items
  that are no deck.
* **The backpack's windows**, behind the client's ``root_window`` and ``mouse_handler``: its
  Backpack key, the Decks tab, rows, pages, Equip, and the question on equipping an empty deck.

What it does not do is guess: a click on the list's other tabs, or a double-click, does
nothing here. Every switch that makes the game misbehave (``opens``, ``drops``, ``lag``) is a
field, and ``clicks`` is what was clicked, in order.
"""

from __future__ import annotations

import itertools
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace

import pymem.exception
from wizwalker.memory.memory_objects.equipment_behavior import ClientWizEquipmentBehavior
from wizwalker.memory.memory_objects.inventory_behavior import ClientWizInventoryBehavior

from bartleby.runtime.game import dialog_captions
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    BACKPACK_DECKS_TAB_PATH,
    BACKPACK_EQUIP_BUTTON_PATH,
    BACKPACK_NEXT_PAGE_PATH,
    BACKPACK_PAGE_NUMBER_PATH,
    BACKPACK_PAGE_PATH,
    BACKPACK_PREVIOUS_PAGE_PATH,
    BACKPACK_ROWS,
    CHAT_EDIT_PATH,
    DECK_CONFIGURATION_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    WORLD_MESSAGE_BOX_CAPTION_PATH,
    WORLD_MESSAGE_BOX_PATH,
    WORLD_MESSAGE_BOX_RIGHT,
    backpack_row_equipped_path,
    backpack_row_path,
)
from bartleby.runtime.testing.sim_client import SimClient
from bartleby.shared.deck_names import DeckName
from bartleby.shared.locales import DEFAULT_LOCALE

_ids = itertools.count(0x7000_0000_0000)

#: The question's Yes, which nothing of ours clicks: it copies the old deck's cards.
_YES = [*WORLD_MESSAGE_BOX_RIGHT[:-1], "centerButton"]

#: Where in a BasicDeckBehavior a :attr:`SimDeck.recorded` starts: its archmastery school.
_RECORDED_AT = 0xB8


@dataclass(eq=False)
class SimDeck:
    """One deck item. *name* is its wheels, and None a deck never named, shown as *item*."""

    name: DeckName | None = None
    cards: int = 10
    item: str = "Plain Deck"
    #: The bytes its behavior ends in, from its archmastery school at +0xB8 on, as recorded from
    #: a live client, the name's wheels included. In memory they stand for *name*.
    recorded: bytes | None = None
    global_id: int = field(default_factory=lambda: next(_ids))

    def text(self, locale: str) -> str:
        """What the backpack shows it as."""
        return self.name.text(locale) if self.name is not None else self.item


class _Memory:
    """A process of bytes laid out by hand, as ``pymem.Pymem`` answers a read of it."""

    _BASE = 0x3000_0000

    def __init__(self) -> None:
        self.data = bytearray()
        # This process, for wizwalker's check of whether the game is still running.
        self.process_handle = -1

    def alloc(self, size: int) -> int:
        address = self._BASE + len(self.data)
        self.data += bytes((size + 0xF) & ~0xF)
        return address

    def put(self, address: int, fmt: str, *values) -> None:
        struct.pack_into("<" + fmt, self.data, address - self._BASE, *values)

    def put_bytes(self, address: int, raw: bytes) -> None:
        self.data[address - self._BASE : address - self._BASE + len(raw)] = raw

    def put_string(self, address: int, text: str) -> None:
        """An MSVC std::string on the heap."""
        raw = text.encode()
        heap = self.alloc(len(raw) + 1)
        self.put_bytes(heap, raw)
        self.put(address, "QQQQ", heap, 0, len(raw), max(len(raw), 16))

    def put_list(self, at: int, pointers: list[int]) -> None:
        """A std::list<shared_ptr>: its sentinel at *at*, its count after, a node per pointer."""
        nodes = [self.alloc(0x20) for _ in range(len(pointers) + 1)]
        for i, node in enumerate(nodes):
            self.put(node, "QQ", nodes[(i + 1) % len(nodes)], nodes[i - 1])
        for node, pointer in zip(nodes[1:], pointers):
            self.put(node + 0x10, "QQ", pointer, 0)
        self.put(at, "Qi", nodes[0], len(pointers))

    def put_vector(self, at: int, pointers: list[int]) -> None:
        """A std::vector<shared_ptr>: where its elements begin and end, 16 bytes each."""
        begin = self.alloc(16 * len(pointers))
        for i, pointer in enumerate(pointers):
            self.put(begin + 16 * i, "QQ", pointer, 0)
        self.put(at, "QQ", begin, begin + 16 * len(pointers))

    def read_bytes(self, address: int, size: int) -> bytes:
        start = address - self._BASE
        if not 0 <= start <= len(self.data) - size:
            raise pymem.exception.MemoryReadError(address, size)
        return bytes(self.data[start : start + size])


class SimWindow:
    """One window of the tree: its name, its children, and what it answers just now."""

    def __init__(
        self,
        name: str,
        *,
        shown: Callable[[], bool] = lambda: True,
        there: Callable[[], bool] = lambda: True,
        text: Callable[[], str] = lambda: "",
        grayed: Callable[[], bool] = lambda: False,
        click: Callable[[], None] = lambda: None,
    ) -> None:
        self._name = name
        self._shown, self._text, self._grayed = shown, text, grayed
        self.there = there
        self.clicked = click
        self.below: list[SimWindow] = []

    async def name(self) -> str:
        return self._name

    async def children(self) -> list[SimWindow]:
        return [window for window in self.below if window.there()]

    async def is_visible(self) -> bool:
        return self._shown()

    async def maybe_text(self) -> str:
        return self._text()

    async def is_control_grayed(self) -> bool:
        return self._grayed()

    def place(self, path: list[str], **answers) -> SimWindow:
        """The window at *path* under this one, given *answers*, made with every window on the way."""
        window = self
        for depth, name in enumerate(path, start=1):
            found = next((w for w in window.below if w._name == name), None)
            if found is None or depth == len(path):
                made = SimWindow(name, **(answers if depth == len(path) else {}))
                if found is not None:
                    made.below = found.below
                    window.below[window.below.index(found)] = made
                else:
                    window.below.append(made)
                found = made
            window = found
        return window


class _Mouse:
    """A client's mouse handler: a click reaches the window's, unless a message box takes it."""

    def __init__(self, backpack: SimBackpack) -> None:
        self._backpack = backpack

    async def __aenter__(self) -> _Mouse:
        return self

    async def __aexit__(self, *_) -> None:
        pass

    async def click_window(self, window: SimWindow) -> None:
        await self._backpack.click(window)


class SimBackpack:
    """*decks* on *sim*, the first one equipped, behind a backpack in *locale*."""

    def __init__(
        self, sim: SimClient, decks: list[SimDeck], *, locale: str = DEFAULT_LOCALE, equipped: SimDeck | None = None
    ) -> None:
        self.sim = sim
        self.decks = list(decks)
        self.locale = locale
        self.equipped: SimDeck | None = equipped or (decks[0] if decks else None)
        #: How many items of the inventory are no deck.
        self.other_items = 5
        #: What was clicked, by each window's own name.
        self.clicks: list[str] = []
        #: How many clicks on a window, by its name, the game drops from here.
        self.drops: dict[str, int] = {}
        #: Whether the Backpack key opens it, and whether its close button closes it.
        self.opens = True
        self.closes = True
        #: Seconds between the key, or Equip, and what it does.
        self.open_lag = 0.0
        self.equip_lag = 0.0
        #: A fight that starts as the window of this name is clicked.
        self.fight_on: str | None = None
        #: Called with each window's name as it is clicked.
        self.on_click: Callable[[str], None] | None = None
        #: The decks the game itself calls empty, whatever their cards read: Equip asks about them.
        self.called_empty: list[SimDeck] = [deck for deck in decks if deck.cards == 0]
        #: Whether the chat line is open, and a message box of another kind up.
        self.chat = False
        self.other_box = False
        #: Whether the question's Yes was clicked, which copies one deck's cards into another.
        self.copied = False
        #: The question's own text, for a build whose wording the snapshot doesn't know.
        self.question = _question(locale)

        self.page_shown = False
        #: Another page of the spellbook, open when the player has one.
        self.other_page = False
        self.tab = "Tab_All"
        self.page = 1
        self.selected: SimDeck | None = None
        self.asking: SimDeck | None = None
        self._rows: list[SimDeck] = []
        self._due: list[tuple[float, Callable[[], None]]] = []
        self._memory: _Memory | None = None

        self.root = self._windows()
        sim.root_window = self.root
        sim.mouse_handler = _Mouse(self)
        sim.backpack = self

    # -- what the player, or the game, does ---------------------------------------------------

    def open(self, tab: str = "Tab_All", page: int = 1) -> None:
        """Open the backpack as the player would, on *tab* and *page*."""
        self._show()
        self.tab, self.page = tab, page

    def equip(self, deck: SimDeck | None) -> None:
        """Put *deck* on, or take the one on off with None."""
        self.equipped = deck
        self._memory = None

    @property
    def is_open(self) -> bool:
        """Whether the spellbook is open, on any page."""
        self.advance()
        return self.page_shown or self.other_page

    def advance(self) -> None:
        """Do what is due by now."""
        now = self.sim.clock()
        due = [act for at, act in self._due if at <= now]
        self._due = [(at, act) for at, act in self._due if at > now]
        for act in due:
            act()

    def _after(self, seconds: float, act: Callable[[], None]) -> None:
        if seconds > 0:
            self._due.append((self.sim.clock() + seconds, act))
        else:
            act()

    # -- the windows ----------------------------------------------------------------------------

    def pressed(self, action: GameAction | int) -> None:
        """What the client does on a key: its Backpack key is a toggle, and turns another page to it."""
        if action != GameAction.BACKPACK or not self.opens or self.sim.battle:
            return
        if self.page_shown:
            self._after(self.open_lag, self._hide)
        else:
            self._after(self.open_lag, self._show)

    def _show(self) -> None:
        # The order is set now and stays until it is reopened: the deck on, then the rest by name.
        rest = sorted((d for d in self.decks if d is not self.equipped), key=lambda d: d.text(self.locale))
        self._rows = [*([self.equipped] if self.equipped in self.decks else []), *rest]
        self.page_shown, self.other_page = True, False
        self.tab, self.page, self.selected = "Tab_All", 1, None

    def _hide(self) -> None:
        self.page_shown = self.other_page = False

    @property
    def pages(self) -> int:
        return max(1, -(-len(self._rows) // BACKPACK_ROWS))

    def _row(self, row: int) -> SimDeck | None:
        """The deck on *row*, with the Decks tab showing."""
        index = (self.page - 1) * BACKPACK_ROWS + row - 1
        return self._rows[index] if self.tab == "Tab_Deck" and index < len(self._rows) else None

    def _row_text(self, row: int) -> str:
        if self.tab != "Tab_Deck":
            return f"Some Hat {row}"
        deck = self._row(row)
        return deck.text(self.locale) if deck is not None else ""

    async def click(self, window: SimWindow) -> None:
        name = await window.name()
        self.advance()
        self.clicks.append(name)
        if self.on_click is not None:
            self.on_click(name)
        if self.fight_on == name:
            self.sim.battle = True
        if self.drops.get(name, 0) > 0:
            self.drops[name] -= 1
            return
        # Until the question is answered nothing behind it takes a click.
        if self.asking is None or name in (WORLD_MESSAGE_BOX_RIGHT[-1], _YES[-1]):
            window.clicked()

    def _tab(self) -> None:
        self.tab, self.selected = "Tab_Deck", None

    def _turn(self, by: int) -> None:
        self.page = min(max(self.page + by, 1), self.pages)

    def _select(self, row: int) -> None:
        self.selected = self._row(row)

    def _equip_selected(self) -> None:
        deck = self.selected
        if deck is None or self.sim.battle:
            return
        if deck is self.equipped:
            self._after(self.equip_lag, lambda: self.equip(None))
        elif deck in self.called_empty:
            self._after(self.equip_lag, lambda: self._ask(deck))
        else:
            self._after(self.equip_lag, lambda: self.equip(deck))

    def _ask(self, deck: SimDeck) -> None:
        self.asking = deck

    def _answer(self, *, copy: bool) -> None:
        deck, self.asking = self.asking, None
        self.copied = self.copied or copy
        self._after(self.equip_lag, lambda: self.equip(deck))

    def _close(self) -> None:
        if self.closes:
            self._hide()

    def _windows(self) -> SimWindow:
        root = SimWindow("Root")

        def shown() -> bool:
            self.advance()
            return self.page_shown

        def asking() -> bool:
            self.advance()
            return self.asking is not None

        place = root.place
        place(DECK_CONFIGURATION_PATH, there=lambda: self.is_open)
        # A control's own flag stays set whether or not its window is open, as in the game.
        place(BACKPACK_CLOSE_BUTTON_PATH, click=self._close)
        place(BACKPACK_PAGE_PATH, shown=shown)
        place(BACKPACK_DECKS_TAB_PATH, click=self._tab)
        place(BACKPACK_EQUIP_BUTTON_PATH, grayed=lambda: self.selected is None, click=self._equip_selected)
        place(BACKPACK_NEXT_PAGE_PATH, click=lambda: self._turn(1))
        place(BACKPACK_PREVIOUS_PAGE_PATH, click=lambda: self._turn(-1))
        place(BACKPACK_PAGE_NUMBER_PATH, text=lambda: f"<center>{self.page}/{self.pages}</center>")
        for row in range(1, BACKPACK_ROWS + 1):
            place(
                backpack_row_path(row),
                shown=lambda row=row: bool(self._row_text(row)),
                text=lambda row=row: self._row_text(row),
                click=lambda row=row: self._select(row),
            )
            place(backpack_row_equipped_path(row), shown=lambda row=row: self._row(row) is self.equipped)
        place(CHAT_EDIT_PATH, shown=lambda: self.chat)
        place(MESSAGE_BOX_CAPTION_PATH, text=lambda: "You have been invited" if self.other_box else "")
        place(WORLD_MESSAGE_BOX_PATH, shown=asking)
        place(WORLD_MESSAGE_BOX_CAPTION_PATH, text=lambda: self.question)
        place(WORLD_MESSAGE_BOX_RIGHT, click=lambda: self._answer(copy=False))
        place(_YES, click=lambda: self._answer(copy=True))
        return root

    # -- the memory -------------------------------------------------------------------------------

    def inventory_behavior(self) -> ClientWizInventoryBehavior:
        handler, _, inventory = self._placed()
        return ClientWizInventoryBehavior(handler, inventory)

    def equipment_behavior(self) -> ClientWizEquipmentBehavior:
        handler, equipment, _ = self._placed()
        return ClientWizEquipmentBehavior(handler, equipment)

    def _placed(self) -> tuple[SimpleNamespace, int, int]:
        """The memory's handler, and where the equipment's behavior and the inventory's are in it."""
        self.advance()
        if self._memory is None:
            self._memory = memory = _Memory()
            worn = [_place_item(memory), *(_place_deck(memory, d) for d in self.decks if d is self.equipped)]
            carried = [_place_item(memory) for _ in range(self.other_items)]
            carried += [_place_deck(memory, deck) for deck in self.decks if deck is not self.equipped]
            self._equipment, self._inventory = memory.alloc(0x100), memory.alloc(0x100)
            memory.put_list(self._equipment + 120, worn)
            memory.put_list(self._inventory + 112, carried)
        return SimpleNamespace(process=self._memory), self._equipment, self._inventory


def _question(locale: str) -> str:
    """The game's question on equipping an empty deck, in *locale*."""
    captions = json.loads(dialog_captions.CAPTIONS_PATH.read_text(encoding="utf-8"))["captions"]
    return captions[dialog_captions.EQUIP_EMPTY_DECK][locale]


def _place_behavior(memory: _Memory, name: str, size: int = 0x100) -> int:
    template = memory.alloc(0x80)
    memory.put_string(template + 72, name)
    behavior = memory.alloc(size)
    memory.put(behavior + 0x58, "Q", template)
    return behavior


def _place_item(memory: _Memory, *behaviors: int, global_id: int = 1) -> int:
    """An item with *behaviors*, after those every item has."""
    item = memory.alloc(0x180)
    memory.put(item + 72, "Q", global_id)
    own = [_place_behavior(memory, "RenderBehavior"), _place_behavior(memory, "JewelSocketBehavior")]
    memory.put_vector(item + 224, [*own, *behaviors])
    return item


def _place_deck(memory: _Memory, deck: SimDeck) -> int:
    behavior = _place_behavior(memory, "BasicDeckBehavior")
    # Two entries of the spell list, where there are cards for two: a card held more than once is one.
    copies = [n for n in (deck.cards - deck.cards // 2, deck.cards // 2) if n]
    spells = [memory.alloc(0x58) for _ in copies]
    for spell, count in zip(spells, copies):
        memory.put(spell + 80, "I", count)
    memory.put_list(behavior + 120, spells)
    if deck.recorded is not None:
        memory.put_bytes(behavior + _RECORDED_AT, deck.recorded)
    elif deck.name is not None:
        memory.put(behavior + 0xF0, "III", *deck.name.indices)
    return _place_item(memory, behavior, global_id=deck.global_id)

"""An in-memory working copy of the strategy library, with a Save/Cancel boundary.

WHY THIS EXISTS
---------------

Every surface that edited a strategy used to write it on a 500ms debounce, which
made three things impossible at once: Cancel meant nothing, a half-typed DSL was
persisted, and a partial flush could commit some fields of an edit and drop
others. The fix is not a better debounce — it is a boundary. The Combat Strategies
window opens onto *copies*, every editor mutates a copy, and nothing reaches disk
until Save.

That also makes the library's copy-on-write (:mod:`bartleby.shared.strategy_library`)
fall out for free: writes only ever land in the user root, so an edit to a shipped
strategy is already a user override by the time it is written, and Cancel leaves the
shipped file as untouched as it was before the window opened.

WHAT IS AND IS NOT IN HERE
--------------------------

The library's three kinds: player, duel, deck. They are read and written across the
pair of roots the working set is given, ``(shipped, user)``, as every library call
takes them.

Beside the library there may be **owners** (:class:`StrategyOwner`): things that keep
duel and player strategies of their own and name the library's. BartleBot's Bot Combat
Strategy is the one there is; BartleBuddy has none, so its working set is the library
alone. One owner may be **open**, as the Bot Combat Strategy of the bot loaded in
BartleBot is: its own entries are edited here like any library entry, inside the same
boundary, so it is held as an editable copy. Every other owner is read-only, there only
to be counted: a delete confirmation and the Save confirmation say what uses an entry.
Commit writes the library before the open owner, so moving an entry of its own into the
library leaves two copies if it is interrupted, never none. Lookups go to its own first,
then the library.

Everything is Qt-free and needs no ``QApplication``, matching
:mod:`bartleby.runtime.combat.strategy_summary` next door. It came from BartleBot
(BartleBuddy #31, Stage 5).

DIRTINESS IS DERIVED, NEVER REPORTED
------------------------------------

Nothing calls ``mark_dirty()``. An entry is modified when its current fields
differ from the snapshot taken at load, which means an editor that mutates its
model directly — as BartleBot's ``DuelConfigEditor`` has always done — needs no
changes to participate, and an edit typed and then typed back out again correctly
leaves the window clean. A dirty flag set by a ``changed`` signal cannot say that.

BUILT-IN, MODIFIED, CUSTOM — ALSO DERIVED
-----------------------------------------

Whether an entry is still the built-in one is asked of its *content*, against the
shipped version held from load, not of what is on disk (#124). Asking disk meant an
unsaved edit to a built-in still read as unedited, so the window offered Delete
where it meant Revert, and a revert took the entry out of the list until the window
was reopened, because disk was the only place the shipped version came back from.

So :meth:`StrategyWorkingSet.revert` puts the shipped version back *here*, and
:meth:`~StrategyWorkingSet.commit` removes the user copy of any built-in whose
content has come back to the shipped version, however it got there. A built-in is
never removed: there is nothing under the user root to delete, and the shipped file
would reappear on the next open anyway.

ANOTHER PROGRAM CAN CHANGE A FILE WHILE THE WINDOW IS OPEN
---------------------------------------------------------

The library is shared: BartleBot and BartleBuddy both read and write it. Each entry is
held with the fingerprint it was loaded with, and :meth:`StrategyWorkingSet.commit`
passes it to every write, so a file another program changed since is never
overwritten. Commit records it as a :class:`Conflict` instead and leaves the entry
dirty; the window then offers to :meth:`~StrategyWorkingSet.reload` it, discarding this
session's edit, or to :meth:`~StrategyWorkingSet.keep_as_copy`, which keeps the edit
under a new id. An entry from a file in a newer format is read-only
(:meth:`~StrategyWorkingSet.is_read_only`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from bartleby.runtime.combat.strategy_summary import UsageCount, find_references, name_list
from bartleby.shared import strategy_library as library
from bartleby.shared.content_hash import content_sha256, official_updated
from bartleby.shared.store_files import Origin, unique_name
from bartleby.shared.strategy_library import ChangedOnDisk, NewerFormat, StrategyKind, is_newer_format
from bartleby.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy

logger = logging.getLogger(__name__)

#: The three kinds the library holds, and so this module can edit.
EDITABLE_KINDS = (StrategyKind.PLAYER, StrategyKind.DUEL, StrategyKind.DECK)

#: The kinds an owner can hold strategies of. Deck presets stay library-only: a Bot
#: Combat Strategy's own decks are its ``player_decks``, one per slot, on BartleBot's Combat tab.
OWN_KINDS = (StrategyKind.PLAYER, StrategyKind.DUEL)


class StrategyOwner(Protocol):
    """Something beside the library that keeps duel and player strategies of its own, and names the library's.

    BartleBot's Bot Combat Strategy is the one there is: a bot's file holding duels and
    player strategies of its own, and naming library duels for its fights. What makes it
    one is only what is below, so the working set never reads its file's shape.

    The working set holds the open owner as a :meth:`copy`, edits its :meth:`own` lists in
    place, tells it changed by its :meth:`snapshot`, and writes it with :meth:`save`. Of
    every other owner it asks only :meth:`refers_to` and :meth:`own`, to count what uses an
    entry.
    """

    #: What one owner and several are called, as a usage sentence names them:
    #: ``("Bot Combat Strategy", "Bot Combat Strategies")``.
    nouns: tuple[str, str]
    #: The Source word on an own entry's pill and its filter chip: BartleBot's "This bot".
    source: str

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    def own(self, kind: StrategyKind) -> list:
        """Its own entries of *kind*, one of :data:`OWN_KINDS`: the live list, so appending to it adds one."""
        ...

    def refers_to(self, duel_id: str) -> bool:
        """Whether it names the library duel *duel_id* itself, as a Bot Combat Strategy's default or fight does."""
        ...

    def snapshot(self) -> str:
        """A stable string for every field it has now, own entries included."""
        ...

    def copy(self) -> StrategyOwner:
        """A deep copy, for the working set to edit while the original is still drawn."""
        ...

    def save(self) -> None:
        """Write it where it lives. Raises on a failed write, which commit logs."""
        ...


def _fresh_copy(name: str) -> dict:
    """The fields a copy of an entry gets anew: an id, a name, and no history or Official base.

    A copy is a new item, as a bot's Make my own copy is: what it was copied from is not
    its own past, and it overrides nothing (#169).
    """
    return {"id": str(uuid4()), "name": name, "history": [], "official_sha256": None, "sent_sha256": None}


_FACTORIES = {
    StrategyKind.PLAYER: lambda name: PlayerCombatStrategy(name=name),
    StrategyKind.DUEL: lambda name: DuelCombatStrategy(name=name),
    StrategyKind.DECK: lambda name: DeckPreset(name=name),
}


def _snapshot(item) -> str:
    """A stable string for "these fields, right now".

    ``model_dump`` rather than the ``exclude_defaults=True`` form ``save()``
    writes: a field explicitly set back to its default is not a change to the
    file, but it *is* a change the user made, and a window that called itself
    clean after one would lose it on Cancel without saying so.
    """
    return json.dumps(item.model_dump(), sort_keys=True, default=str)


def _merged(own_items: Iterable, library_items: Iterable) -> list:
    """Its own entries first, then every library entry they do not shadow."""
    own_items = list(own_items)
    shadowed = {item.id for item in own_items}
    return own_items + [item for item in library_items if item.id not in shadowed]


@dataclass(frozen=True)
class Changes:
    """What Save would do, as three lists of ids. Also what a close prompt counts."""

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted)

    def __bool__(self) -> bool:
        return self.total > 0


@dataclass(frozen=True)
class SharedUse:
    """A changed library entry, and the places outside the open owner that use it.

    What the Save confirmation lists (#118): an edit to a library entry reaches every
    owner and library duel that uses it, and the window only shows one. ``owners`` and
    ``duels`` are display names, already quoted, and ``nouns`` what one owner and
    several are called (:attr:`StrategyOwner.nouns`).
    """

    name: str
    owners: tuple[str, ...] = ()
    duels: tuple[str, ...] = ()
    nouns: tuple[str, str] = ("", "")

    def sentence(self, *, other: bool = True) -> str:
        """ "'Boss Burn' is also used by 2 other Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."

        *other* is False with no owner open, where "other" would have nothing to be other than.
        """
        parts = []
        if self.owners:
            noun = self.nouns[0] if len(self.owners) == 1 else self.nouns[1]
            parts.append(f"{len(self.owners)} {'other ' if other else ''}{noun}: {name_list(self.owners)}")
        if self.duels:
            noun = f"library duel{'s' if len(self.duels) != 1 else ''}"
            parts.append(f"{len(self.duels)} {noun}: {name_list(self.duels)}")
        return f"'{self.name}' is {'also ' if other else ''}used by {'; and '.join(parts)}."


@dataclass(frozen=True)
class Conflict:
    """A write :meth:`StrategyWorkingSet.commit` refused, so the entry is still unsaved."""

    kind: StrategyKind
    uuid: str
    name: str
    #: Its file is in a format newer than this build knows, rather than changed since load.
    newer_format: bool = False
    #: The refused write was a delete (or a revert), not a save.
    deleted: bool = False


@dataclass
class _Slot:
    """One kind's live items, plus what they looked like when they were loaded."""

    items: dict[str, object] = field(default_factory=dict)
    baselines: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    #: Built-in entries as they ship, by id. Empty in developer mode, where nothing is shipped.
    shipped: dict[str, object] = field(default_factory=dict)
    #: Built-ins reverted this session. Counted as a change even when the content
    #: matches the baseline: a user copy identical to the shipped one still shadows
    #: every future update to it, so reverting one is a real write.
    reverted: set[str] = field(default_factory=set)
    #: The fingerprint each entry was loaded with: what a write expects to find. Absent
    #: (or None) for an entry with no user file.
    fingerprints: dict[str, str | None] = field(default_factory=dict)


class StrategyWorkingSet:
    """Deep copies of the library, edited freely, written only by :meth:`commit`.

    *roots* is ``(shipped, user)``, as every :mod:`~bartleby.shared.strategy_library` call
    takes them, held from construction and reused by :meth:`commit`, so a test that loads
    from a sandbox writes back to the same sandbox. The same directory twice is one flat
    store with nothing shipped, as in developer mode.
    """

    def __init__(
        self,
        players: list[PlayerCombatStrategy],
        duels: list[DuelCombatStrategy],
        decks: list[DeckPreset],
        *,
        roots: tuple[Path, Path],
        shipped: dict[StrategyKind, list] | None = None,
        fingerprints: dict[StrategyKind, dict[str, str | None]] | None = None,
        owner: StrategyOwner | None = None,
        others: Iterable[StrategyOwner] = (),
        make_owner: Callable[[str], StrategyOwner] | None = None,
    ) -> None:
        """*owner* is the open owner, held editable; *others* every owner, there to be counted.

        The open owner may be among *others*: it is counted as its live copy instead.
        *make_owner* makes a new, empty owner by name, for a window that may make one while
        none is open, as BartleBot's does for a bot with no Bot Combat Strategy; None where
        none can be made.
        *fingerprints* are what each entry was loaded with (:meth:`from_store` passes the
        ones its load read). Without them, each is read from disk now.
        """
        self._shipped_root, self._user_root = Path(roots[0]), Path(roots[1])
        self._owner: StrategyOwner | None = None
        self._owner_baseline: str | None = None
        self._own_entry_baselines: dict[str, str] = {}
        if owner is not None:
            # A copy: the product is still drawing from the original.
            self._owner = owner.copy()
            self._owner_baseline = owner.snapshot()
            self._own_entry_baselines = {item.id: _snapshot(item) for kind in OWN_KINDS for item in owner.own(kind)}
        self._slots: dict[StrategyKind, _Slot] = {kind: _Slot() for kind in EDITABLE_KINDS}
        for kind, loaded in (
            (StrategyKind.PLAYER, players),
            (StrategyKind.DUEL, duels),
            (StrategyKind.DECK, decks),
        ):
            slot = self._slots[kind]
            given = (fingerprints or {}).get(kind)
            for item in loaded:
                # Deep copies: the caller's lists are what the product is still drawing
                # from, and an editor mutating those would change the cards behind the
                # window before anything was saved.
                slot.items[item.id] = item.model_copy(deep=True)
                slot.baselines[item.id] = _snapshot(item)
                slot.fingerprints[item.id] = (
                    given.get(item.id) if given is not None else library.fingerprint(self._user_root, kind, item.id)
                )
            for item in (shipped or {}).get(kind, []):
                slot.shipped[item.id] = item
        # Read-only, and not copied: nothing here edits one.
        self._others = list(others)
        self._make_owner = make_owner
        self._conflicts: list[Conflict] = []

    @classmethod
    def from_store(
        cls,
        roots: tuple[Path, Path],
        *,
        owner: StrategyOwner | None = None,
        others: Iterable[StrategyOwner] = (),
        make_owner: Callable[[str], StrategyOwner] | None = None,
    ) -> StrategyWorkingSet:
        """Load every kind from *roots*, ``(shipped, user)``: the window's own entry point.

        *owner*, *others* and *make_owner* are as for the constructor; leave them unset for
        the library alone.
        """
        shipped_root, user_root = Path(roots[0]), Path(roots[1])
        entries = {kind: library.entries(shipped_root, user_root, kind) for kind in EDITABLE_KINDS}
        return cls(
            players=[e.item for e in entries[StrategyKind.PLAYER].values()],
            duels=[e.item for e in entries[StrategyKind.DUEL].values()],
            decks=[e.item for e in entries[StrategyKind.DECK].values()],
            roots=(shipped_root, user_root),
            fingerprints={kind: {uuid: e.fingerprint for uuid, e in found.items()} for kind, found in entries.items()},
            shipped={kind: library.shipped_items(shipped_root, user_root, kind) for kind in EDITABLE_KINDS},
            owner=owner,
            others=others,
            make_owner=make_owner,
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def items(self, kind: StrategyKind) -> list:
        """Every live library entry of one kind, name-sorted — what the card list draws.

        Sorted here rather than at each call site so the list does not reorder
        under the user when a rename lands, and so ``+ New`` puts its entry where
        the alphabet says it goes rather than at the end.
        """
        return sorted(self._slots[kind].items.values(), key=lambda i: i.name.lower())

    def get(self, kind: StrategyKind, uuid: str | None):
        """The live entry for ``uuid``, or None. This is the object editors mutate.

        The open owner's own entries first, then the library's — the lookup order
        everything else uses.
        """
        if uuid is None:
            return None
        own = self._own_entry(kind, uuid)
        return own if own is not None else self._slots[kind].items.get(uuid)

    def in_reach(self, kind: StrategyKind) -> list:
        """Every entry of *kind* a strategy may link, the open owner's own first, then the library's."""
        return _merged(self.own_items(kind), self.items(kind))

    @property
    def players(self) -> list[PlayerCombatStrategy]:
        return self.items(StrategyKind.PLAYER)

    @property
    def duels(self) -> list[DuelCombatStrategy]:
        return self.items(StrategyKind.DUEL)

    @property
    def decks(self) -> list[DeckPreset]:
        return self.items(StrategyKind.DECK)

    @property
    def owners(self) -> list[StrategyOwner]:
        """Every owner, for counting what uses an entry — the open one as its live copy.

        The others are read-only; see the module docstring.
        """
        if self._owner is None:
            return list(self._others)
        return [self._owner, *(o for o in self._others if o.id != self._owner.id)]

    @property
    def roots(self) -> tuple[Path, Path]:
        """``(shipped, user)``: where this set was loaded from."""
        return self._shipped_root, self._user_root

    def has_unsaved(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* is new or changed since the set was loaded or last saved.

        What Export asks before it sends an entry: a share file carries what is saved.
        """
        changes = self.changes(kind)
        return uuid in changes.created or uuid in changes.modified

    # ------------------------------------------------------------------
    # The open owner (#118)
    # ------------------------------------------------------------------

    @property
    def owner(self) -> StrategyOwner | None:
        """The open owner's live copy, or None: the library alone, or an owner not made yet."""
        return self._owner

    @property
    def owner_created(self) -> bool:
        """Whether the open owner was made in this session, so Save writes it for the first time."""
        return self._owner is not None and self._owner_baseline is None

    @property
    def takes_owner(self) -> bool:
        """Whether this set has an owner open, or can make one: in BartleBot, whether a bot is loaded."""
        return self._owner is not None or self._make_owner is not None

    def create_owner(self, name: str) -> StrategyOwner:
        """A new, empty owner named *name*, held open until Save; Cancel discards it. Returns it.

        As a Bot Combat Strategy made in the window for a bot that had none. Raises
        ``ValueError`` when this set was given no ``make_owner``.
        """
        if self._make_owner is None:
            raise ValueError("This working set cannot make an owner")
        self._owner = self._make_owner(name)
        self._owner_baseline = None
        self._own_entry_baselines = {}
        return self._owner

    def own_items(self, kind: StrategyKind) -> list:
        """The open owner's own entries of *kind*, name-sorted; empty without one."""
        if self._owner is None or kind not in OWN_KINDS:
            return []
        return sorted(self._owner.own(kind), key=lambda i: i.name.lower())

    def is_own_entry(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* is one of the open owner's own entries rather than the library's."""
        return uuid is not None and self._own_entry(kind, uuid) is not None

    def _own_entry(self, kind: StrategyKind, uuid: str):
        if self._owner is None or kind not in OWN_KINDS:
            return None
        return next((i for i in self._owner.own(kind) if i.id == uuid), None)

    def own_players_of(self, duel: DuelCombatStrategy) -> list[PlayerCombatStrategy]:
        """The open owner's own player strategies *duel* links, in assignment order, each once.

        What has to move with one of its own duels into the library, since a library duel
        may not link an owner's own content.
        """
        if self._owner is None:
            return []
        own = {p.id: p for p in self._owner.own(StrategyKind.PLAYER)}
        return [own[pcs_id] for pcs_id in dict.fromkeys(duel.linked_pcs_ids()) if pcs_id in own]

    def players_for_duel(self, duel_id: str | None) -> list[PlayerCombatStrategy]:
        """The player strategies an assignment in duel *duel_id* may link.

        One of the owner's own duels may use its own and the library's, its own first. A
        library duel may use the library's only: every owner shares it, so it must not
        depend on one owner's own.
        """
        if self.is_own_entry(StrategyKind.DUEL, duel_id):
            return self.own_items(StrategyKind.PLAYER) + self.players
        return self.players

    def was_created(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* was made in this session — by New, Duplicate or a promotion.

        What lets BartleBot's Combat tab link the card that opened the window to a strategy
        made on this visit, and to nothing else. An entry of the owner's own moved into the
        library still counts only if it was new to begin with.
        """
        if uuid is None or self.get(kind, uuid) is None:
            return False
        return uuid not in self._slots[kind].baselines and uuid not in self._own_entry_baselines

    def move_to_library(self, kind: StrategyKind, uuid: str) -> list:
        """Move an entry of the owner's own into the library, keeping its id; return every entry moved.

        A duel takes the owner's own player strategies it uses along with it, since a
        library duel may not link an owner's own content — the caller confirms that first,
        with :meth:`own_players_of`. Like every edit it reaches disk at :meth:`commit` and
        not before, and Cancel undoes it.
        """
        entry = self._own_entry(kind, uuid)
        if entry is None:
            return []
        moving = [(kind, entry)]
        if kind is StrategyKind.DUEL:
            moving += [(StrategyKind.PLAYER, pcs) for pcs in self.own_players_of(entry)]
        for moved_kind, item in moving:
            own = self._owner.own(moved_kind)
            own[:] = [i for i in own if i.id != item.id]
            self._slots[moved_kind].items[item.id] = item
            # A tombstone for this id, left by deleting a library entry it once shadowed,
            # would otherwise delete the file this move is about to write.
            slot = self._slots[moved_kind]
            slot.deleted = [d for d in slot.deleted if d != item.id]
        return [item for _kind, item in moving]

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def create(self, kind: StrategyKind, base_name: str, *, own: bool = False):
        """A new, empty entry of ``kind``, named uniquely against what is here now.

        *own* puts it in the open owner rather than the library (#118).
        """
        item = _FACTORIES[kind](self.unique_name(kind, base_name))
        self._add(kind, item, own)
        return item

    def duplicate(self, kind: StrategyKind, uuid: str, *, own: bool = False):
        """A copy of ``uuid`` under a fresh id and the next free name, or None if it is gone.

        A fresh id, not the source's: an override shares its id with the shipped
        file it shadows, and duplicating one under that id would silently *become*
        the override rather than making a second entry. *own* puts the copy in the
        open owner, whichever side the source was on — which is how "this fight
        needs something slightly different" is done.
        """
        source = self.get(kind, uuid)
        if source is None:
            return None
        copy = source.model_copy(deep=True, update=_fresh_copy(self.unique_name(kind, source.name)))
        self._add(kind, copy, own)
        return copy

    def _add(self, kind: StrategyKind, item, own: bool) -> None:
        if own:
            if self._owner is None or kind not in OWN_KINDS:
                raise ValueError(f"No owner to hold a {kind.value} strategy")
            self._owner.own(kind).append(item)
        else:
            self._slots[kind].items[item.id] = item

    def remove(self, kind: StrategyKind, uuid: str) -> None:
        """Drop an entry. Written through as a delete by :meth:`commit`.

        An entry created in this same session is simply forgotten — there is no
        file to delete, and recording a tombstone for an id that was never
        written would make ``changes()`` overstate what Save is about to do. An
        entry of the owner's own is a plain delete: it leaves the owner when that is saved.
        """
        if self.is_own_entry(kind, uuid):
            own = self._owner.own(kind)
            own[:] = [i for i in own if i.id != uuid]
            return
        slot = self._slots[kind]
        if uuid in slot.shipped:
            # Removing only ever touches the user root, so a built-in would reappear on
            # the next open. The window does not offer it; this is the backstop.
            logger.warning("Refusing to remove built-in %s strategy %s; revert it instead", kind.value, uuid)
            return
        if slot.items.pop(uuid, None) is None:
            return
        if uuid in slot.baselines:
            slot.deleted.append(uuid)

    def origin(self, kind: StrategyKind, uuid: str) -> Origin:
        """Where ``uuid``'s file is, under this set's own roots — see :class:`Origin`.

        Offered here so a surface can ask whether removing an entry deletes it or
        merely reverts it without reaching for the roots the set was built with.
        An entry created in this session has no file under either root and reads
        as :attr:`Origin.MISSING`, which is the truth: there is nothing to revert
        to, and Save would be its first write.
        """
        return library.origin(self._shipped_root, self._user_root, kind, uuid)

    def status(self, kind: StrategyKind, uuid: str) -> Origin:
        """What this entry *is* right now, unsaved edits included — the source a surface shows.

        :meth:`origin` answers where the file is; this answers what Save would leave
        behind. A built-in edited a moment ago reads :attr:`Origin.SHIPPED_OVERRIDE`
        before anything is written, and a reverted one reads :attr:`Origin.SHIPPED`
        while its user copy is still on disk. Anything that is not a built-in, including
        an entry created this session, reads :attr:`Origin.USER`.
        """
        if self.is_own_entry(kind, uuid):
            # Never built-in: an entry of the owner's own is always the user's, and never reverts.
            return Origin.USER
        slot = self._slots[kind]
        item = slot.items.get(uuid)
        if item is None:
            return Origin.MISSING
        shipped = slot.shipped.get(uuid)
        if shipped is None:
            return Origin.USER
        if content_sha256(item) != content_sha256(shipped):
            return Origin.SHIPPED_OVERRIDE
        if uuid in slot.reverted or _snapshot(item) != slot.baselines.get(uuid):
            # Back to the shipped content this session; Save removes the user copy.
            return Origin.SHIPPED
        # Unchanged since load and identical to the shipped version: only disk can say
        # whether a user copy is shadowing it.
        return self.origin(kind, uuid)

    def revert(self, kind: StrategyKind, uuid: str):
        """Put the shipped version of ``uuid`` back in place and return it; None if not a built-in.

        The returned object replaces the live one, so a surface holding the old one
        must reload. The user copy, if there is one, goes on :meth:`commit`.
        """
        slot = self._slots[kind]
        shipped = slot.shipped.get(uuid)
        if shipped is None or uuid not in slot.items:
            return None
        restored = shipped.model_copy(deep=True)
        slot.items[uuid] = restored
        # Only a user copy on disk makes this a change. Reverting unsaved edits to a
        # built-in that was never saved over just undoes them, and leaves the set clean.
        if self.origin(kind, uuid) is Origin.SHIPPED_OVERRIDE:
            slot.reverted.add(uuid)
        return restored

    def _matches_shipped(self, kind: StrategyKind, uuid: str) -> bool:
        slot = self._slots[kind]
        shipped = slot.shipped.get(uuid)
        item = slot.items.get(uuid)
        # Content, not every field: an override's history and Official base are its record
        # of where it came from, and say nothing about whether it still differs (#169).
        return shipped is not None and item is not None and content_sha256(item) == content_sha256(shipped)

    def official_updated(self, kind: StrategyKind, uuid: str) -> bool:
        """Whether ``uuid`` overrides an Official strategy that has changed since the override was made (#169).

        Asked of the override as loaded: its ``official_sha256`` against the shipped one
        held from load. True only while the entry is still an override; Revert takes the
        new Official one, and the user's changes go with the override.
        """
        if self.is_own_entry(kind, uuid) or self.status(kind, uuid) is not Origin.SHIPPED_OVERRIDE:
            return False
        slot = self._slots[kind]
        return official_updated(slot.items.get(uuid), slot.shipped.get(uuid))

    def unique_name(self, kind: StrategyKind, base: str) -> str:
        """``base`` made unique against every live name of that kind, the owner's own
        included — see :func:`bartleby.shared.store_files.unique_name`."""
        names = [i.name for i in self._slots[kind].items.values()]
        names += [i.name for i in self.own_items(kind)]
        return unique_name(base, names)

    # ------------------------------------------------------------------
    # Dirtiness
    # ------------------------------------------------------------------

    def changes(self, kind: StrategyKind | None = None) -> Changes:
        """What Save would write. Derived by comparison — see the module docstring.

        Counted per entry, the open owner's own included. An entry moved into the library
        counts once, as created there, not also as deleted from its old home.
        """
        kinds = EDITABLE_KINDS if kind is None else (kind,)
        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        for k in kinds:
            slot = self._slots[k]
            for uuid, item in slot.items.items():
                baseline = slot.baselines.get(uuid)
                if baseline is None:
                    created.append(uuid)
                elif _snapshot(item) != baseline or uuid in slot.reverted:
                    modified.append(uuid)
            deleted.extend(slot.deleted)
            for item in self.own_items(k):
                baseline = self._own_entry_baselines.get(item.id)
                if baseline is None:
                    created.append(item.id)
                elif _snapshot(item) != baseline:
                    modified.append(item.id)
        if kind is None:
            live = {i.id for k in OWN_KINDS for i in self.own_items(k)}
            moved = {uuid for k in OWN_KINDS for uuid in self._slots[k].items}
            deleted.extend(uuid for uuid in self._own_entry_baselines if uuid not in live | moved)
        return Changes(created=created, modified=modified, deleted=deleted)

    @property
    def owner_dirty(self) -> bool:
        """Whether Save has to write the open owner: new, or changed in any way."""
        if self._owner is None:
            return False
        return self._owner_baseline is None or self._owner.snapshot() != self._owner_baseline

    @property
    def is_dirty(self) -> bool:
        return bool(self.changes()) or self.owner_dirty

    # ------------------------------------------------------------------
    # Committing
    # ------------------------------------------------------------------

    def commit(self) -> Changes:
        """Write every change to the user root and re-baseline. Returns what was written.

        Deletes run last. An entry removed and another created under the same
        name in one session is a rename by another route, and doing the delete
        first would be the only ordering where a failure mid-commit loses both.

        A built-in whose content is back to the shipped version — reverted, or edited
        back by hand — is not written: its user copy is deleted instead, so the
        shipped file shows through again and keeps receiving updates.

        A single failed write is logged and skipped rather than raised: the rest
        of the session's work is not forfeit because one file could not be
        written, and the entry keeps its old baseline, so it still reads as
        modified and Save can be tried again. A write refused because another
        program changed the file, or because it is in a newer format, is also
        listed in :attr:`conflicts`, for the window to ask about.

        The open owner is written after the library and before any library delete. If a
        library write failed, it is not written at all: the library may be the new home of
        an entry that just left it, and rewriting it without that entry would leave no
        copy anywhere.
        """
        written = self.changes()
        self._conflicts = []
        library_failed = False
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            for uuid in [*written.created, *written.modified]:
                item = slot.items.get(uuid)
                if item is None:
                    continue
                expected = slot.fingerprints.get(uuid)
                reverting = self._matches_shipped(kind, uuid)
                try:
                    if reverting:
                        library.delete(self._user_root, kind, uuid, item.name, expected=expected)
                        slot.fingerprints.pop(uuid, None)
                    else:
                        slot.fingerprints[uuid] = library.save(
                            self._user_root, kind, item, expected=expected, shipped=self._shipped_root
                        )
                except (ChangedOnDisk, NewerFormat) as refused:
                    self._refused(kind, uuid, item.name, refused, deleted=reverting)
                    library_failed = True
                    continue
                except Exception:
                    logger.exception("Failed to save %s strategy %s", kind.value, uuid)
                    library_failed = True
                    continue
                slot.baselines[uuid] = _snapshot(item)
                slot.reverted.discard(uuid)
        if self.owner_dirty:
            if library_failed:
                logger.warning("Not saving owner %s: a library strategy failed to save", self._owner.id)
            else:
                self._commit_owner()
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            kept: list[str] = []
            for uuid in slot.deleted:
                # The entry itself is gone from the set; its baseline still has its name.
                name = json.loads(slot.baselines.get(uuid, "{}")).get("name") or uuid
                try:
                    library.delete(self._user_root, kind, uuid, name, expected=slot.fingerprints.get(uuid))
                except (ChangedOnDisk, NewerFormat) as refused:
                    self._refused(kind, uuid, name, refused, deleted=True)
                    kept.append(uuid)
                    continue
                except Exception:
                    logger.exception("Failed to delete %s strategy %s", kind.value, uuid)
                    continue
                slot.baselines.pop(uuid, None)
                slot.fingerprints.pop(uuid, None)
            # A refused delete stays pending, so the set still reads dirty until it is resolved.
            slot.deleted = kept
        return written

    def _refused(self, kind: StrategyKind, uuid: str, name: str, refused: Exception, *, deleted: bool) -> None:
        logger.warning("Not saving %s strategy %s: %s", kind.value, uuid, refused)
        self._conflicts.append(
            Conflict(kind, uuid, name, newer_format=isinstance(refused, NewerFormat), deleted=deleted)
        )

    @property
    def conflicts(self) -> list[Conflict]:
        """The writes the last :meth:`commit` refused; see the module docstring."""
        return list(self._conflicts)

    def is_read_only(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether ``uuid`` came from a file in a newer format than this build knows, so it may not be edited."""
        if uuid is None or self.is_own_entry(kind, uuid):
            return False
        item = self._slots[kind].items.get(uuid)
        return item is not None and is_newer_format(item)

    def reload(self, kind: StrategyKind, uuid: str):
        """Replace ``uuid`` with what is on disk now, discarding this session's edit to it. Returns it.

        None when no root has it any more: another program deleted it, so it leaves the
        list too. A surface holding the old object must reload, as after :meth:`revert`.
        """
        slot = self._slots[kind]
        slot.deleted = [d for d in slot.deleted if d != uuid]
        slot.reverted.discard(uuid)
        self._conflicts = [c for c in self._conflicts if (c.kind, c.uuid) != (kind, uuid)]
        try:
            entry = library.load(self._shipped_root, self._user_root, kind, uuid)
        except FileNotFoundError:
            for held in (slot.items, slot.baselines, slot.fingerprints):
                held.pop(uuid, None)
            return None
        slot.items[uuid] = entry.item
        slot.baselines[uuid] = _snapshot(entry.item)
        slot.fingerprints[uuid] = entry.fingerprint
        return entry.item

    def keep_as_copy(self, kind: StrategyKind, uuid: str):
        """Keep this session's edit to ``uuid`` as a new entry, and reload ``uuid`` from disk. Returns the copy.

        The copy gets a fresh id and the next free name, as :meth:`duplicate` gives, so
        Save writes it as a new file and nothing the other program wrote is touched.
        """
        source = self._slots[kind].items.get(uuid)
        if source is None:
            return None
        copy = source.model_copy(deep=True, update=_fresh_copy(self.unique_name(kind, source.name)))
        self.reload(kind, uuid)
        self._add(kind, copy, own=False)
        return copy

    def _commit_owner(self) -> None:
        try:
            self._owner.save()
        except Exception:
            logger.exception("Failed to save owner %s", self._owner.id)
            return
        self._owner_baseline = self._owner.snapshot()
        self._own_entry_baselines = {item.id: _snapshot(item) for kind in OWN_KINDS for item in self._owner.own(kind)}

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    def usage(self, uuid: str) -> UsageCount:
        """How many things reference ``uuid``, counting unsaved edits.

        Counted against the working set rather than disk, which is the whole
        reason :func:`~bartleby.runtime.combat.strategy_summary.find_references` takes
        lists: a player strategy linked into a duel a minute ago has no file saying so
        yet, and a delete confirmation that missed it would be wrong in exactly
        the case the user most needs it to be right. The open owner is counted as its
        live copy, for the same reason.
        """
        return find_references(uuid, self.owners, self.duels)

    def shared_uses(self, label: Callable[[StrategyOwner], str | None] | None = None) -> list[SharedUse]:
        """Every changed library entry used somewhere the user does not have open.

        What the Save confirmation asks about (#118): an edit to a library entry reaches
        every owner and library duel using it, and the window shows at most one. "Not open"
        means any owner but the open one, plus any library duel that links a changed
        player strategy. With none open, every one counts.

        *label* gives an owner a second name, so one reads as ``'Solo' (Atlantea)``, as
        BartleBot names a Bot Combat Strategy's bot; without it, or when it has no
        answer, its name stands alone. Only edits count: a new entry has no uses yet,
        and a delete has a confirmation of its own.
        """
        open_id = self._owner.id if self._owner is not None else None
        others = [o for o in self._others if o.id != open_id]
        modified = set(self.changes().modified)
        uses: list[SharedUse] = []
        for kind in OWN_KINDS:
            for item in self.items(kind):
                if item.id not in modified:
                    continue
                users = [o for o in others if _uses(o, kind, item.id)]
                duels = []
                if kind is StrategyKind.PLAYER:
                    duels = [f"'{d.name}'" for d in self.duels if item.id in d.linked_pcs_ids()]
                if users or duels:
                    nouns = users[0].nouns if users else ("", "")
                    names = tuple(_owner_label(o, label) for o in users)
                    uses.append(SharedUse(item.name, names, tuple(duels), nouns))
        return uses


def _owner_label(owner: StrategyOwner, label: Callable[[StrategyOwner], str | None] | None) -> str:
    second = label(owner) if label is not None else None
    return f"'{owner.name}' ({second})" if second else f"'{owner.name}'"


def _uses(owner: StrategyOwner, kind: StrategyKind, uuid: str) -> bool:
    """Whether *owner* uses library entry *uuid*: a duel it names, or a player strategy in one of its own duels."""
    if kind is StrategyKind.DUEL:
        return owner.refers_to(uuid)
    return any(uuid in d.linked_pcs_ids() for d in owner.own(StrategyKind.DUEL))

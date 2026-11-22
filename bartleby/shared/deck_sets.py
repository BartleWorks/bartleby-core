"""Deck sets: which of its decks each wizard puts on, by the names the player gave them in the game.

A wizard owns several decks and wears one (``docs/WIZARD101.md``, *Decks and the backpack*). A
:class:`DeckSet` names one deck for everyone, and another for the wizards that differ: a team
that names its decks alike is a set with no exceptions, and a team whose second wizard runs a
Piercing Deck for bosses is one with an exception. It holds names and never cards: putting one
on equips a deck the wizard owns (``bartleby.runtime.decks``).

The sets belong to the user rather than to one product, so they are in the vendor's
``%APPDATA%/Bartleby/deck_sets.json``::

    {"version": 1, "sets": [{"id": "...", "name": "Boss decks",
                             "everyone": {"type_key": "Type_2"},
                             "exceptions": {"<character id>": {"type_key": "Type_9"},
                                            "<character id>": null}}]}

**An exception is kept by character id**, which lasts across relogs and relaunches, and never by
a wizard's name: names are real player data, and not unique (``runtime/clients/character_roster``
has where a name is kept). ``null`` is a wizard that keeps the deck it has on.

**What a product does with a set is its own settings'**: BartleBuddy names a mob set and a boss
set. A duel strategy never holds one, since duel strategies are shared and deck names are
personal (BartleBot #276).

Two products can be open on the file at once. Every write goes through
:func:`~bartleby.shared.json_update.update_json` and touches one set's entry, so the other sets,
those a newer version wrote among them, stay as they are. A set is authored, so a writer names
the fingerprint of the entry it read, and one that has changed since is refused
(:class:`DeckSetChanged`) rather than replaced, as the strategy library refuses. A field is left
out of the file while it is empty.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bartleby.shared.deck_names import DeckName
from bartleby.shared.json_update import update_json
from bartleby.shared.store_files import unique_name
from bartleby.shared.vendor import vendor_dir

logger = logging.getLogger(__name__)

#: Schema version of the stored record.
SCHEMA_VERSION = 1

#: The filename beneath the vendor directory.
FILENAME = "deck_sets.json"

_VERSION = "version"
_SETS = "sets"
#: The fields of an entry this version owns. Any other is a newer version's, and kept.
_FIELDS = ("id", "name", "everyone", "exceptions")


class DeckSetChanged(Exception):
    """Another product changed or removed the set since it was read, so nothing was written."""


class DeckSet(BaseModel):
    """One set. *everyone* is the deck every wizard puts on, or None for none.

    *exceptions* is by character id: another deck, or None for a wizard that keeps the one it
    has on.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    everyone: DeckName | None = None
    exceptions: dict[int, DeckName | None] = Field(default_factory=dict)

    def deck_for(self, character_id: int) -> DeckName | None:
        """The deck the wizard *character_id* puts on: its exception, else everyone's. None for none."""
        if character_id in self.exceptions:
            return self.exceptions[character_id]
        return self.everyone

    def entry(self) -> dict[str, Any]:
        """The set as the file holds it: no field that is empty."""
        body: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.everyone is not None:
            body["everyone"] = _name_body(self.everyone)
        if self.exceptions:
            body["exceptions"] = {
                str(character): None if name is None else _name_body(name)
                for character, name in self.exceptions.items()
            }
        return body


def _name_body(name: DeckName) -> dict[str, str]:
    return name.model_dump(exclude_defaults=True)


class DeckSetStore:
    """The vendor's deck sets, read from the file as it is now and written an entry at a time.

    Constructed with its directory, as ``AccountStore`` is. The file is parsed again only when
    it has changed, so a caller may ask several times a second.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = (directory or vendor_dir()) / FILENAME
        self._stamp: tuple[int, int] | None = None
        self._sets: tuple[DeckSet, ...] = ()
        self._prints: dict[str, str] = {}

    @property
    def path(self) -> Path:
        return self._path

    # -- reading --------------------------------------------------------------------------

    def sets(self) -> tuple[DeckSet, ...]:
        """Every set, in the file's order. An entry that can't be read is left out, and left alone."""
        self._refresh()
        return self._sets

    def get(self, set_id: str | None) -> DeckSet | None:
        return next((s for s in self.sets() if s.id == set_id), None) if set_id else None

    def fingerprint(self, set_id: str) -> str | None:
        """What *set_id*'s entry is now, to hand back to :meth:`save` or :meth:`delete`. None where there is none."""
        self._refresh()
        return self._prints.get(set_id)

    def _refresh(self) -> None:
        try:
            stat = self._path.stat()
        except OSError:
            self._stamp, self._sets, self._prints = None, (), {}
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return
        try:
            record = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A read refused while the other product renames is tried again at the next ask.
            logger.warning("The deck sets at %s are not readable: %s", self._path, exc)
            self._stamp = stamp if isinstance(exc, ValueError) else None
            self._sets, self._prints = (), {}
            return
        self._stamp = stamp
        sets, prints = [], {}
        for entry in _entries(record):
            try:
                found = DeckSet.model_validate(entry)
            except ValidationError:
                logger.warning("A deck set can't be read, and is left as it is")
                continue
            if found.id not in prints:
                sets.append(found)
                prints[found.id] = _digest(entry)
        self._sets, self._prints = tuple(sets), prints

    # -- writing ---------------------------------------------------------------------------

    def save(self, deck_set: DeckSet, *, expected: str | None) -> str:
        """Write *deck_set* where its entry still is what *expected* says. Returns its new fingerprint.

        *expected* is the fingerprint it was read with, and None a new set's, which joins the
        end. Raises :class:`DeckSetChanged`, having written nothing, and OSError as
        ``update_json`` does.
        """
        body = deck_set.entry()
        written: dict[str, Any] = {}

        def put(entries: list, at: int | None) -> None:
            nonlocal written
            # Over what is there, so that a field of a newer version's stays, in its place.
            before = {} if at is None else entries[at]
            written = {k: v for k, v in before.items() if k not in _FIELDS or k in body} | body
            if at is None:
                entries.append(written)
            else:
                entries[at] = written

        self._write(deck_set.id, expected, put)
        return _digest(written)

    def delete(self, set_id: str, *, expected: str | None) -> None:
        """Remove *set_id*, where its entry still is what *expected* says. Raises as :meth:`save` does."""

        def take(entries: list, at: int | None) -> None:
            if at is not None:
                del entries[at]

        self._write(set_id, expected, take)

    def duplicate(self, set_id: str) -> DeckSet | None:
        """Write a copy of *set_id* under a name of its own, and return it. None for a set that is gone."""
        original = self.get(set_id)
        if original is None:
            return None
        name = unique_name(original.name, [s.name for s in self.sets()])
        copy = original.model_copy(update={"id": str(uuid.uuid4()), "name": name})
        self.save(copy, expected=None)
        return copy

    def _write(self, set_id: str, expected: str | None, change: Callable[[list, int | None], None]) -> None:
        def mutate(record: dict[str, Any]) -> None:
            record.setdefault(_VERSION, SCHEMA_VERSION)
            entries = record.get(_SETS)
            if not isinstance(entries, list):
                entries = record[_SETS] = []
            at = next((i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("id") == set_id), None)
            if (None if at is None else _digest(entries[at])) != expected:
                raise DeckSetChanged(f"deck set {set_id} changed since it was read")
            change(entries, at)

        update_json(self._path, mutate)
        # A write in the file's own timestamp tick, of the same size, must still be read again.
        self._stamp = None


def _entries(record: Any) -> list[dict]:
    entries = record.get(_SETS) if isinstance(record, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _digest(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()

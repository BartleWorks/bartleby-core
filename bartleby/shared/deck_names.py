"""The names a player can give a deck, and how the game shows them in every locale.

A deck is named by three wheels in the game's rename window: a school or world
(``Class_N`` of ``DeckNames.lang``), a kind (``Type_N``: Main, Boss, Dungeon, Piercing) and
a number (``Num_N``, 1 to 7), each with an empty choice. In memory a deck's name is the three
N's (wizwalker's ``ClientDeckBehavior.name_indices``), so a :class:`DeckName` is the three
keys, and the same name in every language. ``docs/WIZARD101.md``, *Decks and the backpack*.

WHY A KEY, AND WHY EVERY LOCALE
    The backpack shows a deck by the text its locale composes from the keys: "Boss Deck" in
    en-US. So a name is stored by key, and a row of the backpack is matched against every
    locale's text (:func:`matches`).

TEXT IS NOT IDENTITY
    Two keys of one wheel can share their text: en-US shows ``Type_12`` and ``Type_18`` both
    as "Healer", and ``Type_13`` and ``Type_21`` both as "Tank". Two names whose keys differ
    are two names, whatever they read as (:func:`same_text`), and a picker tells such a pair
    apart by its labels (:func:`choices`).

PER LOCALE
    The wheels differ: en-US has ``Class_36`` (Ahmarra), which de does not yet, and pl stops
    at ``Type_39``. A locale's picker offers what that locale holds, and a name with a key
    the locale lacks shows that part in en-US.

A DECK NEVER NAMED
    reads (0, 0, 0), and the game shows it by its item's own name ("Headmistress' Dynamic
    Deck"). That is no :class:`DeckName`: it can't be made, stored or picked, and
    :meth:`DeckName.from_indices` answers None for it.

WHY A SNAPSHOT
    Nothing reads a WAD at run time (``docs/GAME_DATA.md``). ``tools/dump_deck_names.py``
    writes ``data/deck_names.json``; run it again when the game adds a world.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from enum import StrEnum
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from bartleby.shared.locales import DEFAULT_LOCALE

logger = logging.getLogger(__name__)

DECK_NAMES_PATH = Path(__file__).resolve().parent / "data" / "deck_names.json"

#: The key of the text a name is composed by, and of the wheels' empty choice.
COMPOSED_BY = "DeckName"
EMPTY_CHOICE = "None"


class Wheel(StrEnum):
    """The wheels a deck's name is picked from, in the order a name's parts are numbered."""

    CLASS = "Class"
    TYPE = "Type"
    NUM = "Num"

    def key(self, index: int) -> str:
        """The lang key of this wheel's choice *index*, or "" for its empty choice, 0."""
        return f"{self}_{index}" if index else ""

    def index(self, key: str) -> int:
        """The choice *key* is on this wheel, 0 being the empty one. ValueError for no key of it."""
        if not key:
            return 0
        wheel, _, number = key.partition("_")
        if wheel != self or not (number.isascii() and number.isdigit()):
            raise ValueError(f"{key!r} is not one of the {self} wheel's keys")
        return int(number)


class DeckName(BaseModel):
    """A deck's name, by its wheels' lang keys. Never empty: see *A deck never named* above.

    In a file, a wheel left empty is left out: ``model_dump(exclude_defaults=True)`` is the
    body, as for every library model. A key newer than the snapshot is kept as it is.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    class_key: str = ""
    type_key: str = ""
    num_key: str = ""

    @field_validator("class_key", "type_key", "num_key")
    @classmethod
    def _one_spelling(cls, value: str, info: ValidationInfo) -> str:
        # "Class_0" is "" and "Type_01" is "Type_1", so that equal names compare equal.
        wheel = {"class_key": Wheel.CLASS, "type_key": Wheel.TYPE, "num_key": Wheel.NUM}[info.field_name]
        return wheel.key(wheel.index(value))

    @model_validator(mode="after")
    def _named(self) -> DeckName:
        if not any(self.keys):
            raise ValueError("a deck name with every wheel empty names no deck")
        return self

    @classmethod
    def from_indices(cls, class_: int, type_: int, num: int) -> DeckName | None:
        """The name whose wheels read *class_*, *type_* and *num*, or None for a deck never named."""
        if not (class_ or type_ or num):
            return None
        return cls(class_key=Wheel.CLASS.key(class_), type_key=Wheel.TYPE.key(type_), num_key=Wheel.NUM.key(num))

    @property
    def keys(self) -> tuple[str, str, str]:
        """The class, type and number keys, "" for a wheel left empty."""
        return self.class_key, self.type_key, self.num_key

    @property
    def indices(self) -> tuple[int, int, int]:
        """The wheels as the game holds them: 0 for one left empty."""
        return Wheel.CLASS.index(self.class_key), Wheel.TYPE.index(self.type_key), Wheel.NUM.index(self.num_key)

    def text(self, locale: str = DEFAULT_LOCALE) -> str:
        """The name as *locale* shows it. A part the locale lacks is en-US's, or else its key."""
        return _compose(locale, [_part(locale, key) for key in self.keys])


class WheelChoice(BaseModel):
    """One choice on a wheel, as a picker offers it."""

    model_config = ConfigDict(frozen=True)

    #: What a :class:`DeckName` stores. "" is the wheel's empty choice.
    key: str
    #: What the choice reads as in the locale.
    text: str
    #: The text, numbered where an earlier choice reads the same: "Healer", "Healer (2)".
    #: The empty choice's is the game's own word for it, "(None)".
    label: str


def choices(wheel: Wheel, locale: str = DEFAULT_LOCALE) -> list[WheelChoice]:
    """*wheel*'s choices in *locale*: its empty one, then the rest in the game's own numbering.

    Each label is unique on the wheel, since two choices can share their text.
    """
    table = _table(locale)
    offered = [WheelChoice(key="", text="", label=_trim(_shown(table.get(EMPTY_CHOICE, ""))))]
    seen: dict[str, int] = {}
    for key in sorted((k for k in table if k.startswith(f"{wheel}_")), key=wheel.index):
        text = _trim(_shown(table[key]))
        seen[text] = seen.get(text, 0) + 1
        label = text if seen[text] == 1 else f"{text} ({seen[text]})"
        offered.append(WheelChoice(key=key, text=text, label=label))
    return offered


def locales() -> list[str]:
    """Every locale the snapshot holds."""
    return list(_snapshot())


def matches(name: DeckName, shown: str) -> bool:
    """Whether a backpack row showing *shown* reads as *name*, in whichever locale the client runs.

    Only a locale that holds every part of the name counts. Case and spacing are ignored: es
    and pl compose an empty wheel as a doubled space, and how the game trims it is not known.
    """
    wanted = _folded(shown)
    return bool(wanted) and wanted in _folded_texts(name)


def same_text(a: DeckName, b: DeckName) -> bool:
    """Whether *a* and *b* read alike in some locale, so that a row's text can't tell them apart."""
    return bool(_folded_texts(a) & _folded_texts(b))


# -- the snapshot, and what the game makes of its text -----------------------------------


@cache
def _snapshot() -> dict[str, dict[str, str]]:
    """{locale: {lang key: text}} from the checked-in snapshot."""
    try:
        return json.loads(DECK_NAMES_PATH.read_text(encoding="utf-8"))["locales"]
    except (OSError, ValueError, KeyError):
        logger.exception("Could not read deck names from %s; no deck can be named or found", DECK_NAMES_PATH)
        return {}


def _table(locale: str) -> dict[str, str]:
    return _snapshot().get(locale, {})


# The game's markup. Not "<[^>]*>", since some locales' text holds a bare "<".
_MARKUP = re.compile(r"<[^<>]*>")
# A verb the game conjugates: "#-1:$PLURALITY$#-1:{Übe[1]|Übt[p]|Übe}". The last form is the one
# under no condition. What the game shows was not measured: no deck seen had such a part.
_CONJUGATED = re.compile(r"#-?\d+:\$PLURALITY\$\s*#-?\d+:\{([^}]*)\}")
_SPACES = re.compile(r"\s+")


def _shown(text: str) -> str:
    """A label as the game shows it, with no markup. Its spaces stay: en-US's "Boss " ends in one."""
    return _CONJUGATED.sub(lambda m: m.group(1).rsplit("|", 1)[-1], _MARKUP.sub("", text))


def _trim(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _part(locale: str, key: str) -> str:
    """One wheel's part of a name in *locale*, or in en-US where the locale lacks it, or the key."""
    if not key:
        return ""
    for table in (_table(locale), _table(DEFAULT_LOCALE)):
        if key in table:
            return _shown(table[key])
    return key


def _compose(locale: str, parts: list[str]) -> str:
    """*parts* put together as *locale*'s ``DeckName`` does."""
    text = _table(locale).get(COMPOSED_BY) or _table(DEFAULT_LOCALE).get(COMPOSED_BY, "$PART0$ $PART1$ $PART2$")
    for i, part in enumerate(parts):
        text = text.replace(f"$PART{i}$", part)
    return _trim(text)


def _folded(text: str) -> str:
    return _SPACES.sub("", _MARKUP.sub("", text)).casefold()


@cache
def _folded_texts(name: DeckName) -> frozenset[str]:
    return frozenset(_folded(text) for text in _whole_texts(name))


def _whole_texts(name: DeckName) -> Iterator[str]:
    """*name* in each locale that holds every part of it."""
    for locale, table in _snapshot().items():
        if all(key in table for key in name.keys if key):
            yield name.text(locale)

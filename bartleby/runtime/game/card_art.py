"""Where extracted game art lives, and how a template name finds its image.

The game ships every spell's art as a DDS texture inside its own WADs. Nothing in
this repo may carry one: a committed game texture is the same exposure a committed wizard
name is (see "No game assets, either" in the root ``CLAUDE.md``). So the art is *extracted
on the user's own machine*, by BartleBot (its ``game/art_extraction.py``), into BartleBot's
machine-local product directory, gitignored by construction because it is not in the tree
at all.

This module is the read side, and it is deliberately the *only* thing that knows the
cache's shape. It is Qt-free so the lookup can be tested without a ``QApplication``;
turning a path into a pixmap is ``bartleby/qt/widgets/cards/card_art_tile.py``'s job. The
icon keys here are the ones shared surfaces draw: schools, archmastery, duel slots and tags.
A product's own keys, such as BartleBot's worlds, badges and drops, are built on
:func:`icon_path` in that product.

ONE EXTRACTION, READ BY EVERY PRODUCT
    The art is the game's, so it is the same whichever app extracted it. A product reads its
    own ``art/`` folder when it has one, and otherwise another Bartleby product's, found by
    looking through the vendor folder for an art folder with an index. The newest index
    answers when several have one. So BartleBuddy, which extracts nothing, draws the art
    BartleBot did. Writing is never shared: an extractor writes to :func:`own_art_dir`.

    Where the art is, is asked once and remembered with the index, until
    :func:`forget_cache`. Art another app extracts while this one is open is drawn from the
    next start.

THE LOOKUP KEY IS THE TEMPLATE NAME
    A saved deck is keyed by ``SpellTemplate.name()`` -- the internal name,
    "Storm Lord - T05 - A" and not "Storm Lord" -- and the extractor reads the art
    filename off that same template's ``m_imageName``. So a deck entry finds its art
    by exact dict lookup, with no normalization and no tier parsing. Do not look art
    up by display name: one display name can cover many templates.

EVERY LOOKUP MAY RETURN NONE, AND THAT IS THE ORDINARY CASE
    Art is optional: a user can turn it off in Custom setup, an extraction can fail,
    and an install from before setup asked has none until the user extracts it. So
    ``art_path`` returning None is not an error and must never be logged as one. Individual cards miss too: a handful of
    templates name an image the game's own files do not contain, and the snapshot can
    be older than the game. Callers render their text form instead.

Moved from BartleBot's ``game/card_art.py``, which read BartleBot's own folder alone.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

from bartleby.shared import product
from bartleby.shared.vendor import vendor_dir

logger = logging.getLogger(__name__)

#: The cache directory's name beneath the product directory.
ART_DIRNAME = "art"

#: Where the extractor writes the per-spell PNGs, relative to :func:`art_dir`.
SPELLS_DIRNAME = "spells"

#: Where the extractor writes the school and archmastery icons.
ICONS_DIRNAME = "icons"

#: Logical names for the two icons that are not a school, so that neither the
#: extractor nor the GUI spells a game texture filename.
ARCHMASTERY_ICON = "archmastery"
UNIVERSAL_ICON = "universal"

#: Maps template name -> PNG filename. Written by the extractor, read here.
INDEX_FILENAME = "index.json"


def own_art_dir() -> Path:
    """``%APPDATA%/Bartleby/<product>/art`` -- where the running product's own extractor writes.

    May not exist: art is optional, and an extraction can be declined or fail.
    """
    return product.current().data_dir / ART_DIRNAME


def art_dir() -> Path:
    """The art cache lookups read: the running product's own, else another product's.

    The running product's own when no product has one, so it may not exist. Looked for
    afresh, where a lookup remembers.
    """
    return _find()[0]


def index_path() -> Path:
    return art_dir() / INDEX_FILENAME


def spells_dir() -> Path:
    return art_dir() / SPELLS_DIRNAME


def icons_dir() -> Path:
    return art_dir() / ICONS_DIRNAME


def tag_icon_name(tag: str) -> str:
    """The icon key for a tag, from its vocabulary name ("Team-Up" -> "tag_team_up").

    The tag's text is the key rather than a second table of ids, because a vocabulary, such
    as BartleBot's ``bot_tags.BOT_TAGS``, is already the one list of them.
    """
    return "tag_" + icon_slug(tag)


def icon_slug(text: str) -> str:
    """*text* as part of an icon key: spaces and hyphens become underscores, so the key is also a tidy filename.

    Case-folded for the reason :func:`school_icon_name` is.
    """
    return text.casefold().replace(" ", "_").replace("-", "_")


#: How many players a duel has slots for, and so how many slot gems there are.
DUEL_SLOTS = 4

#: Duel slot -> the symbol the game marks it with, first position to last.
#:
#: The game's own vocabulary, and the only name a user has for these: the "Set Sigil Slot
#: Preference" dialog draws the gem and nothing else, so a tooltip saying "Sun" is what
#: connects this app's slot 1 to the diamond they picked in-game. Here rather than in the
#: extractor because it is read at run time, for tooltips, on machines that never ran one;
#: BartleBot's ``test_duel_slot_icons`` checks it against the extractor's texture table.
DUEL_SLOT_SYMBOLS: dict[int, str] = {1: "Sun", 2: "Eye", 3: "Star", 4: "Moon"}


def duel_slot_symbol(slot: int) -> str:
    """The symbol name for a duel slot ("Sun"), or "" for a slot the game does not have."""
    return DUEL_SLOT_SYMBOLS.get(slot, "")


def duel_slot_icon_name(slot: int) -> str:
    """The icon key for a duel slot's gem (1 -> "duel_slot_1").

    Keyed by the slot *number* rather than by the symbol on the gem, because the number
    is what every caller has: a combat order is a list of positions. Which symbol goes
    with which position is the extractor's table to state (``DUEL_SLOT_TEXTURES``).

    That indirection is load-bearing here and not merely tidy. Three of the four gems are
    named Sun, Star and Moon -- which are also three real schools, with their own
    ``school_`` medallions that look nothing like these. A caller that reached for
    ``school_sun`` to draw slot 1 would get the archmastery picker's ringed medallion
    instead of the gold diamond the duel screen draws, and it would look plausible.
    """
    return f"duel_slot_{slot}"


def duel_slot_icon_path(slot: int) -> Path | None:
    """The PNG for a duel slot's gem, or None if there is not one on this machine."""
    return icon_path(duel_slot_icon_name(slot))


def school_icon_name(school: str) -> str:
    """The icon key for a school, from the name a deck stores ("Fire" -> "school_fire").

    Case-folded because the deck's value comes from wizwalker's school table and the
    extractor's from :data:`~bartleby.runtime.game.spell_schools.SCHOOL_ORDER`; keying on the exact
    casing would make the two agree only by luck.
    """
    return f"school_{school.casefold()}"


def _read_index(folder: Path) -> dict | None:
    """The index in *folder*, or None when there is none to use.

    A missing file is the common case and says nothing; an unreadable one is worth a line in
    the log, since the user did run the extractor and is entitled to know why they see no art.
    """
    path = folder / INDEX_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read extracted game art index at %s", path)
        return None
    return raw if isinstance(raw, dict) else None


def _other_art_dirs() -> list[Path]:
    """Every other product's art folder with an index, the newest index first."""
    own = own_art_dir()
    found: list[tuple[float, Path]] = []
    try:
        for folder in vendor_dir().iterdir():
            art = folder / ART_DIRNAME
            if art == own:
                continue
            try:
                found.append(((art / INDEX_FILENAME).stat().st_mtime, art))
            except OSError:
                continue  # no art there, which is most of them
    except OSError:
        return []  # no vendor folder yet
    return [art for _mtime, art in sorted(found, key=lambda entry: (-entry[0], str(entry[1])))]


def _find() -> tuple[Path, dict]:
    """The art folder to read and its index, which is empty when no art has been extracted."""
    own = own_art_dir()
    index = _read_index(own)
    if index is not None:
        return own, index
    for other in _other_art_dirs():
        index = _read_index(other)
        if index is not None:
            return other, index
    return own, {}


@cache
def _located() -> tuple[Path, dict]:
    """:func:`_find`'s answer, for the lookups.

    Cached because it is asked once per card per redraw and the files only change when an
    extractor runs, which ends with :func:`forget_cache` to make the new ones visible.

    One read for both maps: the spell images and the icons are written by the same run and
    live in the same file, so reading it twice would only be two chances to disagree about
    what is in it. The folder is settled with it, so the names and the files they name are
    always the same extraction's.
    """
    return _find()


def _manifest() -> dict:
    """The whole index file, or empty when no art has been extracted."""
    return _located()[1]


def _section(key: str) -> dict[str, str]:
    """One name -> filename map out of the manifest, or empty if it is not usable."""
    value = _manifest().get(key)
    return value if isinstance(value, dict) else {}


def _index() -> dict[str, str]:
    """Template name -> PNG filename."""
    return _section("images")


def _icons() -> dict[str, str]:
    """Icon key -> PNG filename. Empty for a cache written before icons were extracted."""
    return _section("icons")


_generation = 0


def cache_generation() -> int:
    """A number that changes whenever :func:`forget_cache` runs.

    For a cache built on these lookups in a layer this module cannot import: keying on
    the generation makes :func:`forget_cache` reach it too. ``tag_picker.tag_icon`` is
    the case -- the library builds a pill per tag per card, and each lookup here stats
    the file.
    """
    return _generation


def forget_cache() -> None:
    """Drop the cached folder and index, so the next lookup finds and reads them again.

    For an extractor finishing inside a running app, and for tests pointing the
    product directory somewhere else.
    """
    global _generation
    _generation += 1
    _located.cache_clear()


def manifest() -> dict:
    """The extractor's index as it was written, or empty when no art has been extracted.

    For the surfaces that report on the art as a whole, such as when it was extracted.
    A copy, so a caller cannot change what the lookups read.
    """
    return dict(_manifest())


def is_available() -> bool:
    """Whether any game art has been extracted on this machine, by this product or another."""
    return bool(_index())


def art_path(template_name: str) -> Path | None:
    """The PNG for *template_name*, or None if there is not one on this machine.

    None covers every "no art" case on purpose -- nothing extracted, this card not in
    the index, or an index entry whose file has since been deleted -- because every
    caller does the same thing about all three.
    """
    filename = _index().get(template_name)
    if not filename:
        return None
    path = _located()[0] / SPELLS_DIRNAME / filename
    return path if path.exists() else None


def icon_path(name: str) -> Path | None:
    """The PNG for an icon key, or None if there is not one on this machine.

    Same three "no icon" cases as :func:`art_path`, and the same answer to all three.
    An art cache extracted before icons were added has no ``icons`` map at all, which
    lands here as None per key rather than as an error -- the surfaces using icons all
    keep the text form they replaced.
    """
    filename = _icons().get(name)
    if not filename:
        return None
    path = _located()[0] / ICONS_DIRNAME / filename
    return path if path.exists() else None


def tag_icon_path(tag: str) -> Path | None:
    """The PNG for a tag's medallion, or None if there is not one on this machine.

    None for a tag no medallion is extracted for, as a strategy's roles are, and on an
    install with no art — the ordinary case. The caller keeps its text pill for both.
    """
    return icon_path(tag_icon_name(tag))

"""Checking and parsing a combat DSL, the wizsprinter strategy text each player fights by.

A fight runs a DSL through wizsprinter's own provider (:func:`parse_strategies` parses each
one ahead of the fight). Everything else here answers what a surface asks about a DSL
before it runs: whether it parses, and where it does not (:func:`dsl_error`), and which of
its spell names no card has (:func:`dsl_warnings`). Both come from one parse, cached in
memory and on disk (:mod:`bartleby.runtime.combat.dsl_check_cache`), since a parse is the
most expensive thing a strategy dialog does.

Pure: no client, and no Qt. wizwalker and lark are imported where they are used, so
importing this module pulls in neither. Moved from BartleBot's ``nodes/combat_runner.py``
(BartleBuddy #31, Stage 2).
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import NamedTuple

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _lark_parser():
    """Build the wizsprinter combat grammar parser once and reuse it for checking DSLs.

    Building one compiles the grammar (~77 ms). ``validate_dsl`` parses with it so it
    keeps the tree, which the name warnings read too. A fight's provider
    (``CombatConfigProvider.from_string``) parses with wizsprinter's own parser, cached
    the same way since e82212b. Lark parsers are reusable across ``parse()`` calls, and
    one shared instance is safe across threads too: the GUI's background checks
    (BartleBot's ``gui/combat_strategies/dsl_checks.py``) parse on a worker while the event loop may
    check another. lark's Earley parser sets its state when it is built and keeps each
    parse's in locals.
    """
    from lark import Lark  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        get_sprinty_grammar,
    )

    return Lark(get_sprinty_grammar())


def _combat_provider(dsl: str, cast_time: float = 0.2):
    """wizsprinter's combat config provider over a DSL string, as a fight runs one.

    Imported here, not at the top, so importing this module does not pull in wizwalker.
    """
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )

    return CombatConfigProvider.from_string(dsl, cast_time)


async def parse_strategies(dsls: list[str]) -> None:
    """Parse each distinct DSL in a worker thread, before a fight makes its providers from them.

    wizsprinter keeps each text's parse, so the provider each player's combat makes then
    costs nothing, in this fight and every later one. Left to those providers, a long
    strategy was parsed once per player on the event loop: Preceptor of Order's held it
    2.3 s at the start of every fight, and the players started 0.8 s apart. In a thread
    the loop keeps running, sharing the interpreter lock with the parse.
    Never raises: a DSL that does not parse fails in the fight's own provider, as before.
    """
    for dsl in dict.fromkeys(dsls):
        try:
            await asyncio.to_thread(_combat_provider, dsl)
        except Exception:
            logger.debug("A strategy did not parse ahead of its fight", exc_info=True)


class DslError(NamedTuple):
    """Why a DSL does not parse, and where.

    ``line`` and ``column`` are 1-based, and 0 when the failure has no position — an
    unexpected end of input, or a transform error in a rule that holds no token.
    """

    message: str
    line: int = 0
    column: int = 0


def validate_dsl(dsl: str) -> str | None:
    """Return a one-line parse error for ``dsl``, or None if it is valid. See :func:`dsl_error`."""
    error = dsl_error(dsl)
    return error.message if error else None


def dsl_error(dsl: str) -> DslError | None:
    """Return why ``dsl`` does not parse, and where, or None if it is valid.

    The position is what the DSL editor underlines; everything else wants only the
    message, through :func:`validate_dsl`.

    An empty or whitespace-only DSL is reported as valid: ``resolve_dsl_for_player``
    returns None for it and the product's resolution substitutes the built-in default, so
    it is never executed.  A newly created preset starts empty by design and must
    not be flagged.

    Pure — no client, no Qt.  Used to surface syntax errors in the pre-run warning
    dialog instead of mid-fight.  Answered by :func:`dsl_check`, which caches it.
    """
    return dsl_check(dsl).error


def _first_token(tree):  # noqa: ANN001, ANN202 - a Lark Tree, imported lazily
    from lark import Token, Tree  # noqa: PLC0415

    if not isinstance(tree, Tree):
        return None
    return next(tree.scan_values(lambda value: isinstance(value, Token)), None)


class DslWarning(NamedTuple):
    """A spell name in a DSL that parses, but that no card is named.

    ``line`` and ``column`` are 1-based, as in :class:`DslError`; ``length`` is how many
    characters of that line the name covers.
    """

    message: str
    line: int
    column: int
    length: int


#: Names ``SprintyCombat.try_get_spell`` answers itself, before it looks for a card.
_RESERVED_SPELL_NAMES = frozenset({"pass", "none", "willcast", "discard"})
#: Grammar nodes whose name is a card's: the move, its enchants, ``spell(…)``'s list, and
#: a name excluded inside ``any<…>`` (``!feint``). Not ``target_named`` — that name is a
#: combatant's.
_CARD_NAME_NODES = frozenset({"spell", "enchant", "second_enchant", "target_spell", "neg_name"})


class DslCheck(NamedTuple):
    """Everything checking a DSL says: why it does not parse, or the names in it no card has."""

    error: DslError | None
    warnings: tuple[DslWarning, ...] = ()


_BLANK = DslCheck(None)


def dsl_warnings(dsl: str) -> tuple[DslWarning, ...]:
    """Every spell name in ``dsl`` that will never find a card, in the order written.

    A name that matches nothing is not a parse error: the move just never casts, and the
    fight moves on to the next one without saying why. ``spell_names.name_problem``
    decides what counts, against the snapshot of every card name.

    Empty for a DSL that does not parse — :func:`dsl_error` has the one thing to fix.
    Answered by :func:`dsl_check`, from the same parse as the error.
    """
    return dsl_check(dsl).warnings


@lru_cache(maxsize=512)
def dsl_check(dsl: str) -> DslCheck:
    """Check ``dsl`` once: the parse error, or the spell names no card has.

    Cached twice over, because a check is the most expensive thing the GUI does. A parse
    is an Earley parse and grows faster than the text does — 4 ms for one move, 300 ms
    for a typical shipped player strategy, 800 ms for the longest — and the strategy
    dialog asks about every entry it lists. In memory, for the life of the process;
    ``maxsize`` is generous on purpose, since typing in the DSL editor mints a new string
    per pause and evicting the saved strategies to hold those would defeat the point.
    And on disk (:mod:`bartleby.runtime.combat.dsl_check_cache`), so the next session does not
    parse the same strategies again: that was most of the first open of the strategy
    dialog.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    cache = _disk_cache()
    stored = cache.get(dsl) if cache is not None else None
    if stored is not None:
        try:
            return _decoded(stored)
        except (TypeError, ValueError, KeyError):
            pass  # written by a different shape of this code; check it again
    result = _checked(dsl)
    if cache is not None:
        cache.put(dsl, _encoded(result))
    return result


def known_dsl_check(dsl: str) -> DslCheck | None:
    """The check for ``dsl`` if it is already known, in memory or on disk; never a parse.

    None means checking it would parse it. Every answer :func:`dsl_check` gives is
    stored in the disk cache's table, which is read once and kept in memory, so this is
    a hash and a lookup -- what a surface asks while drawing, before deciding whether to
    check the rest somewhere the user is not waiting on.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    stored = _disk_cache().get(dsl)
    if stored is None:
        return None
    try:
        return _decoded(stored)
    except (TypeError, ValueError, KeyError):
        return None


def store_dsl_check(dsl: str, check: DslCheck) -> None:
    """Record *check*, worked out elsewhere, as the answer for ``dsl``.

    For a check run in another process (BartleBot's ``gui/combat_strategies/dsl_checks.py``): once
    stored, :func:`known_dsl_check` and :func:`dsl_check` answer it without parsing.
    """
    if dsl and dsl.strip():
        _disk_cache().put(dsl, _encoded(check))


def check_dsl_uncached(dsl: str) -> DslCheck:
    """Check ``dsl`` from scratch: a parse, and no cache read or written.

    What a separate process runs, leaving the caches to the process that asked. Anything
    else wants :func:`dsl_check`.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    return _checked(dsl)


def _checked(dsl: str) -> DslCheck:
    """Parse ``dsl`` once and answer both questions from that parse."""
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )

    try:
        tree = _lark_parser().parse(dsl)
        # The transform is where a fight's provider refuses what parses but can never
        # hold: an unknown attribute, a % on a count, a negated filter.
        TreeToConfig().transform(tree)
    except Exception as exc:
        return DslCheck(_error_from(exc))
    return DslCheck(None, _warnings_in(tree))


def _error_from(exc: Exception) -> DslError:
    # An error raised while transforming arrives wrapped in Lark's VisitError, whose
    # own first line only names the rule. The wrapped error says what is wrong.
    message = str(getattr(exc, "orig_exc", exc)).strip()
    # Lark's UnexpectedInput carries both; an unexpected end of input sets them to -1.
    # A transform error carries the rule's tree instead, so it is placed at the rule's
    # first token: the attribute in ``?(boss.hp > 0)``, the kind in ``gambit(chrams)``.
    line, column = getattr(exc, "line", 0), getattr(exc, "column", 0)
    token = _first_token(getattr(exc, "obj", None))
    if token is not None:
        line, column = token.line, token.column
    positioned = isinstance(line, int) and isinstance(column, int) and line > 0 and column > 0
    return DslError(
        message.splitlines()[0] if message else exc.__class__.__name__,
        line if positioned else 0,
        column if positioned else 0,
    )


def _warnings_in(tree) -> tuple[DslWarning, ...]:  # noqa: ANN001 - a Lark Tree, imported lazily
    """The spell names in a parsed DSL that no card has.

    Names come from the parse tree rather than the text, because the two differ: the
    grammar reads a number in an unquoted name a digit at a time, so ``Detonate All 200``
    reaches the card lookup as ``Detonate All 2 0 0``.
    """
    from lark import Token, Tree  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )

    from bartleby.runtime.game import spell_names  # noqa: PLC0415

    transformer = TreeToConfig()
    warnings = []
    for node in tree.iter_subtrees():
        if node.data not in _CARD_NAME_NODES:
            continue
        for child in node.children:
            if not isinstance(child, Tree) or child.data not in ("words", "string"):
                continue
            tokens = list(child.scan_values(lambda value: isinstance(value, Token)))
            if not tokens:
                continue
            literal = child.data == "string"
            name = transformer.transform(child)
            name = name[1:-1] if literal else name
            if name in _RESERVED_SPELL_NAMES:
                continue
            problem = spell_names.name_problem(name, literal=literal)
            if problem is None:
                continue
            first, last = tokens[0], tokens[-1]
            end = last.end_column if last.line == first.line else first.end_column
            warnings.append(DslWarning(problem, first.line, first.column, end - first.column))
    return tuple(sorted(warnings, key=lambda warning: (warning.line, warning.column)))


def _encoded(check: DslCheck) -> dict:
    return {
        "error": list(check.error) if check.error is not None else None,
        "warnings": [list(warning) for warning in check.warnings],
    }


def _decoded(stored: dict) -> DslCheck:
    error = stored["error"]
    return DslCheck(
        DslError(*error) if error is not None else None,
        tuple(DslWarning(*warning) for warning in stored["warnings"]),
    )


def _disk_cache():  # noqa: ANN202 - imported lazily
    """The on-disk cache for this build of the checker."""
    from bartleby.runtime.combat import dsl_check_cache  # noqa: PLC0415

    return dsl_check_cache.shared_cache(_check_fingerprint())


@lru_cache(maxsize=1)
def _check_fingerprint() -> str:
    """A hash of everything an answer depends on besides the DSL itself.

    The grammar and the transform (wizsprinter's parser and backend modules), the
    spell-name snapshot and the code that reads it, and this module. A change to any of
    them discards the whole on-disk cache, which is cheaper than being clever about
    which answers it could have changed.
    """
    import hashlib  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from wizwalker.extensions.wizsprinter.combat_backends import (  # noqa: PLC0415
        combat_config_parser,
        config_backend,
    )

    from bartleby.runtime.game import spell_names  # noqa: PLC0415

    digest = hashlib.sha256(b"dsl-check-v1")
    for path in (
        combat_config_parser.__file__,
        config_backend.__file__,
        spell_names.__file__,
        spell_names.NAMES_PATH,
        __file__,
    ):
        try:
            digest.update(Path(path).read_bytes())
        except OSError:
            digest.update(b"missing:" + str(path).encode())
    return digest.hexdigest()


def dsl_warning_text(warnings: tuple[DslWarning, ...]) -> str:
    """The warnings as lines of text for a label, each message once."""
    return "\n".join(dict.fromkeys(warning.message for warning in warnings))

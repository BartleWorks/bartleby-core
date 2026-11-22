"""The shipped default duel strategies, and the DSL a fight runs when no file can be read.

Every product by this vendor fights, and the defaults are shipped content they share
(``docs/EPICS.md``, *Combat defaults*), so the ids and the last-resort DSL live here and import
nothing product-specific.

The three duels are Official items in ``strategies/duel/``, found by id:

- :data:`SHIPPED_SOLO_DUEL_ID`: one player, who runs Default.
- :data:`SHIPPED_TEAM_DUEL_ID`: P1 runs Default Team Hitter, P2 to P4 run Default
  Support, which buffs P1 and hits only in round 1 of a mob fight or once P1 is dead.
  Default Guardian Support, which shields and heals P1 first, ships beside it, linked by
  no duel.
- :data:`SHIPPED_EVERYONE_ATTACKS_DUEL_ID`: every player runs Default Team Hitter.

Default Team Hitter is Default plus a round-1 line that opens a mob fight with AoE, which
belongs to team fights only: a solo wizard buffs first.

:func:`default_duel` is levels 3 and 4 of a fight's resolution: the user's Solo or Team duel
when one is set and fits the team, and the shipped one otherwise. BartleBuddy calls the same
function, so a default set in either product is the default in both.

A fight no node started, an ambient one, gets the same defaults. Its players fill the seats
in whatever order they arrive, but the fight runner hands wizsprinter the duel's combat order
(:mod:`bartleby.runtime.combat.fight`, "The team's order"), so Default Support's blade on
``ally(last)`` lands on P1 however they sat. Until BartleBuddy #31's Stage 4 it got Everyone
Attacks instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartleby.shared.preferences import PreferenceStore
    from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy

_SEPARATOR = " | "

#: Fuse enchants into the cards they fit ahead of time, freeing hand slots so a big deck
#: cycles. The slot is held until the round's card is known, and runs only before a card
#: that does not attack, and only while at least two cards are left to draw: a deck the
#: hand already holds gains nothing from a freed slot.
_ENCHANT_AHEAD = " & ".join(
    [
        "?(deck.remaining >= 2) any<mod_damage> @ spell(any<damage & aoe>)",
        "any<mod_damage> @ spell(any<damage>)",
        "sharp @ spell(any<blade>)",
        "sharp @ spell(any<blade>)",
        "potent @ spell(any<trap>)",
        "potent @ spell(any<trap>)",
    ]
)

#: The hitter's ladder, first line that can cast wins (``docs/COMBAT_CONFIG.md``, "What the
#: hitter does"): heal below 30%; a shield while none is up, for a deck that carries one;
#: Mass Feint; Feint on the boss and blades and an aura on
#: itself while one adds to the next hit; then the hit, AoE or single-target at the boss;
#: then fillers, the buffs that won't stack, and a pass. Short of pips for a hit, it keeps
#: its buffs. Generic to any school.
_PRIORITY = [
    _ENCHANT_AHEAD,
    "?(self.health < 30%) any<heal> @ self",
    "?(self.beneficial_wards == 0) any<ward> @ self",
    '"Feint Mass - Amulet"',
    "any<trap & !aoe & stacks>[potent] @ boss",
    "any<blade & stacks>[sharp] @ self",
    "?(self.beneficial_auras == 0) any<aura & out_damage>",
    "any<damage & aoe>[any<mod_damage>]",
    "any<damage>[any<mod_damage>] @ boss",
    "any<global>",
    "any<shadow>",
    "any<trap & !aoe>[potent] @ boss",
    "any<blade>[sharp] @ self",
    "pass",
]

#: The generic single-wizard DSL. The shipped Default player strategy carries the same
#: text, and it is what a fight runs when the shipped files cannot be read at all.
LAST_RESORT_DSL = _SEPARATOR.join(_PRIORITY)

#: Default Solo Duel: one player.
SHIPPED_SOLO_DUEL_ID = "530aa34e-1567-4492-b507-12529a41f0db"
#: Default Team Duel: P1 hits, P2 to P4 support and hit only in round 1 of a mob fight
#: or once P1 is dead.
SHIPPED_TEAM_DUEL_ID = "9594ae6a-2cdc-4990-9ee7-3c43bb715121"
#: Default Team Duel (Everyone Attacks): every player runs Default Team Hitter.
SHIPPED_EVERYONE_ATTACKS_DUEL_ID = "f51b5e89-ba2b-40da-81ef-301e26ca5dd5"
#: The Default player strategy, which the Solo duel links; its DSL is LAST_RESORT_DSL.
SHIPPED_DEFAULT_PLAYER_ID = "a6ddb3e8-5a97-42de-a8d6-174a8353a589"
#: The Default Team Hitter player strategy, which the two Team duels link.
SHIPPED_TEAM_HITTER_ID = "dc7b81c3-c4cf-4ef8-8d4c-7a15d7982c47"
#: The Default Support player strategy, which only buffs.
SHIPPED_DEFAULT_SUPPORT_ID = "d2496f8b-85c5-44f5-98da-13fb0c7ddea5"
#: The Default Guardian Support player strategy: it protects P1 first, then buffs. No
#: shipped duel links it; a user picks it for a slot.
SHIPPED_GUARDIAN_SUPPORT_ID = "fb26e474-00fc-4548-9d79-520acd050844"


class TeamSize(Enum):
    """The two sizes a default is chosen for."""

    SOLO = "Solo"
    TEAM = "Team"


class DefaultSource(Enum):
    """Whose default answered: the user's own, or the one that ships."""

    YOURS = "yours"
    SHIPPED = "shipped"


def team_size(clients: int) -> TeamSize:
    """Solo for one connected client, Team for two or more."""
    return TeamSize.SOLO if clients <= 1 else TeamSize.TEAM


#: The key in ``preferences.json`` that holds the user's duel id for each size. Absent
#: means "recommended": the shipped duel, so a later fix to it reaches everyone who kept it.
PREFERENCE_KEYS = {
    TeamSize.SOLO: "combat.default_duel.solo",
    TeamSize.TEAM: "combat.default_duel.team",
}

SHIPPED_DUEL_IDS = {
    TeamSize.SOLO: SHIPPED_SOLO_DUEL_ID,
    TeamSize.TEAM: SHIPPED_TEAM_DUEL_ID,
}


def players_text(duel: DuelCombatStrategy) -> str:
    """``1 player``, ``4 players`` or ``2 to 4 players``: how many *duel* takes."""
    low, high = duel.min_players, duel.max_players
    noun = "player" if high == 1 else "players"
    return f"{low} {noun}" if low == high else f"{low} to {high} {noun}"


def shortfall_text(duel: DuelCombatStrategy, clients: int) -> str:
    """``'Boss' takes 4 players, and 2 are connected``: why *duel* does not accept the team."""
    verb = "is" if clients == 1 else "are"
    return f"'{duel.name}' takes {players_text(duel)}, and {clients} {verb} connected"


@dataclass(frozen=True)
class DefaultDuel:
    """What :func:`default_duel` found.

    ``duel`` is None only when the shipped default could not be read either, and ``source``
    is then None too. ``reasons`` say why the user's default was passed over, in the user's
    words, and are empty when it answered or none was set.
    """

    size: TeamSize
    duel: DuelCombatStrategy | None
    source: DefaultSource | None
    reasons: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """One phrase for the log: ``duel strategy 'Default Solo Duel' (the shipped Solo default)``.

        With no duel, the last-resort DSL is what runs, and the phrase says so. Either way any
        reasons follow, so the log says why the user's default was passed over.
        """
        if self.duel is None:
            return "; ".join(["the built-in default (no strategy file could be read)", *self.reasons])
        whose = "your" if self.source is DefaultSource.YOURS else "the shipped"
        where = "; ".join([f"{whose} {self.size.value} default", *self.reasons])
        return f"duel strategy '{self.duel.name}' ({where})"


def slot_dsls(
    duel: DuelCombatStrategy | None, player: Callable[[str], PlayerCombatStrategy | None], players: int
) -> list[str]:
    """The DSL each of *players* player slots runs in *duel*, P1 first.

    A slot's assignment's own DSL, or its linked player strategy's, found by *player*, which
    returns None for one it cannot read. A slot with neither, and every slot with no duel,
    runs :data:`LAST_RESORT_DSL`: a fight always has something to run.
    """
    if duel is None:
        return [LAST_RESORT_DSL] * players
    linked: dict[str, str] = {}
    for strategy_id in duel.linked_pcs_ids():
        if strategy_id not in linked:
            found = player(strategy_id)
            linked[strategy_id] = found.dsl if found is not None else LAST_RESORT_DSL
    return [duel.resolve_dsl_for_player(slot, linked) or LAST_RESORT_DSL for slot in range(1, players + 1)]


def chosen_default(prefs: PreferenceStore, size: TeamSize) -> str | None:
    """The duel id the user chose for *size*, or None for the recommended one.

    The shipped duel stored by its id is the recommended one too, as ``set_default_choice``
    treats it: BartleBuddy or a hand edit can store it, and it reads as no choice, so the
    fight's source says the shipped default, as it is.
    """
    stored = prefs.get(PREFERENCE_KEYS[size])
    if not isinstance(stored, str) or not stored or stored == SHIPPED_DUEL_IDS[size]:
        return None
    return stored


def set_default_choice(prefs: PreferenceStore, size: TeamSize, duel_id: str | None) -> bool:
    """Store the user's *size* default, and return whether the write reached disk.

    None, or the shipped duel itself, is the recommended one and is stored as no key, so a
    later fix to the shipped duel reaches everyone who kept it. Writes one key, as every
    write to ``preferences.json`` does.
    """
    if duel_id is None or duel_id == SHIPPED_DUEL_IDS[size]:
        return prefs.clear(PREFERENCE_KEYS[size])
    return prefs.set(PREFERENCE_KEYS[size], duel_id)


def default_duel(
    clients: int,
    prefs: PreferenceStore,
    library: Callable[[str], DuelCombatStrategy | None],
) -> DefaultDuel:
    """The default duel strategy for *clients* connected clients.

    The user's duel for the team size when one is stored, readable, and accepts *clients*;
    otherwise the shipped one, for a fight a node started and an ambient one alike. The
    shipped duel is never checked against *clients*: it was written for its size, and the alternative to it is the
    last-resort DSL.

    *library* finds a duel by id and returns None for one it cannot. Nothing is written.
    """
    size = team_size(clients)
    reasons: list[str] = []
    stored = chosen_default(prefs, size)
    if stored:
        mine = library(stored)
        if mine is None:
            reasons.append(f"your {size.value} default is not in the library")
        elif not mine.accepts(clients):
            reasons.append(f"your {size.value} default {shortfall_text(mine, clients)}")
        else:
            return DefaultDuel(size, mine, DefaultSource.YOURS)
    shipped = library(SHIPPED_DUEL_IDS[size])
    if shipped is None:
        return DefaultDuel(size, None, None, tuple(reasons))
    return DefaultDuel(size, shipped, DefaultSource.SHIPPED, tuple(reasons))

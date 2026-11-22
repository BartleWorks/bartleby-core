"""Tests for an open owner inside the working set (BartleBot #118), on a stand-in owner.

Qt-free, like ``test_strategy_workset.py`` beside it: an owner's own strategies sit inside
the same Save/Cancel boundary as the library, and every rule about them (where New lands,
what a move takes with it, the order Save writes in) is checkable without a widget.
BartleBot's Bot Combat Strategy, the real owner, has its adapter tested there.
"""

from pathlib import Path

import pytest

from bartleby.runtime.combat.strategy_workset import SharedUse, StrategyWorkingSet
from bartleby.shared import strategy_library as library
from bartleby.shared.store_files import Origin
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy
from tests.runtime.combat.owners import FakeOwner


def _save(kind: StrategyKind, item, root: Path) -> None:
    library.save(root, kind, item, expected=library.fingerprint(root, kind, item.id))


def _list(kind: StrategyKind, root: Path) -> list:
    return [e.item for e in library.entries(root, root, kind).values()]


def _own_ws(tmp_path: Path, owner: FakeOwner | None, **kw) -> StrategyWorkingSet:
    for d in kw.get("duels", []):
        _save(StrategyKind.DUEL, d, tmp_path)
    for p in kw.get("players", []):
        _save(StrategyKind.PLAYER, p, tmp_path)
    others = kw.get("others", [])
    return StrategyWorkingSet.from_store(
        (tmp_path, tmp_path),
        owner=owner,
        others=[*([owner] if owner is not None else []), *others],
        make_owner=kw.get("make_owner"),
    )


def _linking(name: str, *pcs: PlayerCombatStrategy) -> DuelCombatStrategy:
    return DuelCombatStrategy(
        name=name,
        player_assignments=[
            PlayerAssignment(players=[i + 1], player_combat_strategy_id=p.id) for i, p in enumerate(pcs)
        ],
    )


# ---------------------------------------------------------------------------
# Held, edited, written
# ---------------------------------------------------------------------------


def test_the_open_owner_is_held_as_an_editable_copy(tmp_path: Path):
    own = DuelCombatStrategy(name="Own")
    owner = FakeOwner(duels=[own])
    ws = _own_ws(tmp_path, owner)

    ws.get(StrategyKind.DUEL, own.id).name = "Edited"

    assert owner.duels[0].name == "Own"
    assert ws.is_dirty and ws.owner_dirty
    ws.commit()
    assert len(owner.saved) == 1 and "Edited" in owner.saved[0]
    assert not ws.is_dirty


def test_get_looks_in_the_owner_before_the_library(tmp_path: Path):
    in_library = DuelCombatStrategy(name="Library")
    own = in_library.model_copy(update={"name": "Own"})
    ws = _own_ws(tmp_path, FakeOwner(duels=[own]), duels=[in_library])

    assert ws.get(StrategyKind.DUEL, in_library.id).name == "Own"
    assert ws.is_own_entry(StrategyKind.DUEL, in_library.id)


def test_new_in_the_owner_lands_in_the_owner_not_the_library(tmp_path: Path):
    ws = _own_ws(tmp_path, FakeOwner())

    made = ws.create(StrategyKind.DUEL, "New Duel Strategy", own=True)
    ws.commit()

    assert [d.id for d in ws.owner.own(StrategyKind.DUEL)] == [made.id]
    assert _list(StrategyKind.DUEL, tmp_path) == []
    assert ws.status(StrategyKind.DUEL, made.id) is Origin.USER


def test_a_deck_cannot_be_made_in_the_owner(tmp_path: Path):
    ws = _own_ws(tmp_path, FakeOwner())

    with pytest.raises(ValueError):
        ws.create(StrategyKind.DECK, "Deck", own=True)


def test_duplicating_a_library_entry_into_the_owner_leaves_the_library_alone(tmp_path: Path):
    in_library = PlayerCombatStrategy(name="Blade", dsl="any<blade>")
    ws = _own_ws(tmp_path, FakeOwner(), players=[in_library])

    copy = ws.duplicate(StrategyKind.PLAYER, in_library.id, own=True)

    assert copy.id != in_library.id
    assert copy.dsl == "any<blade>"
    assert ws.is_own_entry(StrategyKind.PLAYER, copy.id)
    assert [p.id for p in ws.players] == [in_library.id]
    assert ws.changes().created == [copy.id]


def test_in_reach_puts_the_owners_own_first(tmp_path: Path):
    in_library = PlayerCombatStrategy(name="A library one")
    shadowed = PlayerCombatStrategy(name="Shadowed")
    own = [PlayerCombatStrategy(name="Own"), shadowed.model_copy(update={"name": "Mine"})]
    ws = _own_ws(tmp_path, FakeOwner(players=own), players=[in_library, shadowed])

    assert [p.name for p in ws.in_reach(StrategyKind.PLAYER)] == ["Mine", "Own", "A library one"]


def test_a_name_is_made_unique_across_the_owner_and_the_library(tmp_path: Path):
    ws = _own_ws(tmp_path, FakeOwner(duels=[DuelCombatStrategy(name="Boss")]))

    assert ws.create(StrategyKind.DUEL, "Boss").name == "Boss (2)"


def test_removing_an_own_entry_is_a_plain_delete(tmp_path: Path):
    own = PlayerCombatStrategy(name="Own")
    ws = _own_ws(tmp_path, FakeOwner(players=[own]))

    ws.remove(StrategyKind.PLAYER, own.id)

    assert ws.changes().deleted == [own.id]
    ws.commit()
    assert ws.owner.own(StrategyKind.PLAYER) == []
    assert not ws.is_dirty


def test_was_created_means_made_on_this_visit(tmp_path: Path):
    old = DuelCombatStrategy(name="Old")
    ws = _own_ws(tmp_path, FakeOwner(duels=[old]))
    new = ws.create(StrategyKind.DUEL, "New", own=True)

    assert ws.was_created(StrategyKind.DUEL, new.id)
    assert not ws.was_created(StrategyKind.DUEL, old.id)


def test_an_own_entry_is_never_read_only_or_out_of_date(tmp_path: Path):
    own = PlayerCombatStrategy(name="Own")
    ws = _own_ws(tmp_path, FakeOwner(players=[own]))

    assert not ws.is_read_only(StrategyKind.PLAYER, own.id)
    assert not ws.official_updated(StrategyKind.PLAYER, own.id)


# ---------------------------------------------------------------------------
# No owner open yet
# ---------------------------------------------------------------------------


def test_with_no_owner_and_no_way_to_make_one_none_is_taken(tmp_path: Path):
    ws = _own_ws(tmp_path, None)

    assert ws.owner is None and not ws.takes_owner
    with pytest.raises(ValueError):
        ws.create_owner("Solo")


def test_an_owner_can_be_made_and_cancel_discards_it(tmp_path: Path):
    made: list[FakeOwner] = []

    def make(name: str) -> FakeOwner:
        made.append(FakeOwner(name=name))
        return made[-1]

    ws = _own_ws(tmp_path, None, make_owner=make)
    assert ws.takes_owner and ws.owner is None

    owner = ws.create_owner("Solo")

    assert ws.owner is owner and owner.name == "Solo"
    assert ws.owner_created and ws.is_dirty
    del ws  # Cancel
    assert made[0].saved == []


def test_an_owner_made_in_the_window_is_written_on_save(tmp_path: Path):
    ws = _own_ws(tmp_path, None, make_owner=lambda name: FakeOwner(name=name))
    owner = ws.create_owner("Solo")
    ws.create(StrategyKind.DUEL, "Own", own=True)

    ws.commit()

    assert len(owner.saved) == 1 and '"Own"' in owner.saved[0]
    assert not ws.owner_created
    assert not ws.is_dirty


# ---------------------------------------------------------------------------
# Add to library
# ---------------------------------------------------------------------------


def _move_fixture(tmp_path: Path):
    frost = PlayerCombatStrategy(name="Frost Heal")
    blade = PlayerCombatStrategy(name="Blade Stack")
    library_pcs = PlayerCombatStrategy(name="Library P")
    boss = _linking("Boss Burn", frost, blade, library_pcs)
    owner = FakeOwner(duels=[boss], players=[frost, blade], names={boss.id})
    ws = _own_ws(tmp_path, owner, players=[library_pcs])
    return ws, owner, boss, frost, blade


def test_own_players_of_a_duel_are_its_own_links_in_order(tmp_path: Path):
    ws, _owner, boss, frost, blade = _move_fixture(tmp_path)

    assert ws.own_players_of(boss) == [frost, blade]
    assert _own_ws(tmp_path / "other", None).own_players_of(boss) == []


def test_moving_a_duel_to_the_library_takes_its_own_player_strategies(tmp_path: Path):
    ws, _owner, boss, frost, blade = _move_fixture(tmp_path)

    moved = ws.move_to_library(StrategyKind.DUEL, boss.id)

    assert [m.id for m in moved] == [boss.id, frost.id, blade.id]
    assert ws.own_items(StrategyKind.DUEL) == []
    assert ws.own_items(StrategyKind.PLAYER) == []
    assert ws.get(StrategyKind.DUEL, boss.id) is not None
    assert not ws.is_own_entry(StrategyKind.DUEL, boss.id)


def test_a_moved_entry_keeps_its_id_and_reaches_the_library_on_save(tmp_path: Path):
    ws, owner, boss, frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)

    ws.commit()

    assert '"duels": []' in owner.saved[-1]
    assert {d.id for d in _list(StrategyKind.DUEL, tmp_path)} == {boss.id}
    assert frost.id in {p.id for p in _list(StrategyKind.PLAYER, tmp_path)}


def test_a_move_counts_once_as_a_change(tmp_path: Path):
    ws, _owner, boss, frost, blade = _move_fixture(tmp_path)

    ws.move_to_library(StrategyKind.DUEL, boss.id)

    changes = ws.changes()
    assert sorted(changes.created) == sorted([boss.id, frost.id, blade.id])
    assert changes.deleted == []


def test_cancelling_a_move_leaves_disk_as_it_was(tmp_path: Path):
    ws, owner, boss, _frost, _blade = _move_fixture(tmp_path)

    ws.move_to_library(StrategyKind.DUEL, boss.id)
    del ws  # Cancel

    assert _list(StrategyKind.DUEL, tmp_path) == []
    assert owner.saved == []
    assert [d.id for d in owner.duels] == [boss.id]


def test_commit_writes_the_library_before_the_owner(tmp_path: Path, monkeypatch):
    """Interrupted, a move leaves two copies, never none — save_by_id's rule for a rename."""
    ws, owner, boss, _frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)
    order: list[str] = []
    real_save = library.save

    def _save_in_library(user, kind, item, **kw):
        order.append(kind.value)
        return real_save(user, kind, item, **kw)

    monkeypatch.setattr(library, "save", _save_in_library)
    real_owner_save = type(ws.owner).save
    monkeypatch.setattr(type(ws.owner), "save", lambda self: (order.append("owner"), real_owner_save(self)))

    ws.commit()

    assert order[-1] == "owner" and "duel" in order


def test_a_failed_library_write_holds_the_owner_back(tmp_path: Path, monkeypatch):
    ws, owner, boss, _frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)

    def _fail(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(library, "save", _fail)

    ws.commit()

    # The owner still holds the duel, so there is one copy of it on disk.
    assert owner.saved == []
    assert ws.is_dirty


def test_a_failed_owner_write_leaves_it_dirty(tmp_path: Path):
    own = PlayerCombatStrategy(name="Own")
    ws = _own_ws(tmp_path, FakeOwner(players=[own], fail=True))
    ws.get(StrategyKind.PLAYER, own.id).dsl = "edited"

    ws.commit()

    assert ws.owner_dirty


# ---------------------------------------------------------------------------
# What an assignment may link
# ---------------------------------------------------------------------------


def test_an_own_duel_may_link_own_and_library_player_strategies(tmp_path: Path):
    own_pcs = PlayerCombatStrategy(name="Own")
    library_pcs = PlayerCombatStrategy(name="Library")
    own = DuelCombatStrategy(name="Own duel")
    ws = _own_ws(tmp_path, FakeOwner(duels=[own], players=[own_pcs]), players=[library_pcs])

    assert [p.name for p in ws.players_for_duel(own.id)] == ["Own", "Library"]


def test_a_library_duel_may_link_library_player_strategies_only(tmp_path: Path):
    own_pcs = PlayerCombatStrategy(name="Own")
    library_pcs = PlayerCombatStrategy(name="Library")
    library_duel = DuelCombatStrategy(name="Library duel")
    ws = _own_ws(tmp_path, FakeOwner(players=[own_pcs]), duels=[library_duel], players=[library_pcs])

    assert [p.name for p in ws.players_for_duel(library_duel.id)] == ["Library"]


def test_usage_counts_the_live_owner(tmp_path: Path):
    library_pcs = PlayerCombatStrategy(name="Blade")
    ws = _own_ws(tmp_path, FakeOwner(), players=[library_pcs])

    own = ws.create(StrategyKind.DUEL, "Own", own=True)
    own.player_assignments.append(PlayerAssignment(players=[1], player_combat_strategy_id=library_pcs.id))

    assert ws.usage(library_pcs.id).total == 1


def test_the_open_owner_is_counted_once_as_its_live_copy(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss")
    owner = FakeOwner(name="Mine")
    ws = _own_ws(tmp_path, owner, duels=[duel])

    ws.owner.names.add(duel.id)

    assert [o.name for o in ws.owners] == ["Mine"]
    assert ws.usage(duel.id).owner_names == ("'Mine'",)


# ---------------------------------------------------------------------------
# The Save confirmation
# ---------------------------------------------------------------------------


def test_shared_uses_list_other_owners_and_library_duels_never_the_open_one(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal")
    duel = _linking("Boss Burn", pcs)
    team = FakeOwner(name="Team", names={duel.id})
    ws = _own_ws(tmp_path, FakeOwner(name="Mine", names={duel.id}), duels=[duel], players=[pcs], others=[team])

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"
    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    uses = {u.name: u for u in ws.shared_uses(lambda o: {team.id: "Darkmoor"}.get(o.id))}
    assert uses["Boss Burn"].owners == ("'Team' (Darkmoor)",)
    assert uses["Boss Burn"].nouns == FakeOwner.nouns
    assert uses["Frost Heal"].duels == ("'Boss Burn'",)
    assert all("Mine" not in p for u in uses.values() for p in u.owners)


def test_another_owners_own_duel_counts_as_a_use_of_a_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal")
    team = FakeOwner(name="Team", duels=[_linking("Theirs", pcs)])
    ws = _own_ws(tmp_path, FakeOwner(name="Mine"), players=[pcs], others=[team])

    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    assert [u.owners for u in ws.shared_uses()] == [("'Team'",)]


def test_shared_uses_ignore_an_entry_only_the_open_owner_uses(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    ws = _own_ws(tmp_path, FakeOwner(names={duel.id}), duels=[duel])

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"

    assert ws.shared_uses() == []


def test_shared_uses_ignore_an_unchanged_entry(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    ws = _own_ws(tmp_path, FakeOwner(), duels=[duel], others=[FakeOwner(name="Team", names={duel.id})])

    assert ws.shared_uses() == []


def test_with_no_owner_open_every_owner_counts(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    ws = _own_ws(tmp_path, None, duels=[duel], others=[FakeOwner(name="Solo", names={duel.id})])

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"

    assert [u.owners for u in ws.shared_uses()] == [("'Solo'",)]


def test_with_no_owner_at_all_a_library_duel_is_still_a_shared_use(tmp_path: Path):
    """BartleBuddy's working set: the library alone."""
    pcs = PlayerCombatStrategy(name="Frost Heal")
    ws = _own_ws(tmp_path, None, duels=[_linking("Boss Burn", pcs)], players=[pcs])

    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    (use,) = ws.shared_uses()
    assert (use.owners, use.duels) == ((), ("'Boss Burn'",))
    assert use.sentence(other=False) == "'Frost Heal' is used by 1 library duel: 'Boss Burn'."


def test_the_shared_use_sentence_reads_as_the_plan_has_it():
    use = SharedUse("Boss Burn", owners=("'Solo' (Atlantea)", "'Team' (Darkmoor)"), nouns=FakeOwner.nouns)

    assert use.sentence() == (
        "'Boss Burn' is also used by 2 other Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."
    )
    assert use.sentence(other=False) == (
        "'Boss Burn' is used by 2 Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."
    )
    one = SharedUse("Boss Burn", owners=("'Solo' (Atlantea)",), nouns=FakeOwner.nouns)
    assert one.sentence() == "'Boss Burn' is also used by 1 other Bot Combat Strategy: 'Solo' (Atlantea)."

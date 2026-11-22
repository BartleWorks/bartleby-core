"""The render sheets' sample library: the window opens on every entry it names, and nothing in it is broken."""

from __future__ import annotations

from bartleby.qt.testing import sample_library
from bartleby.runtime.combat.dsl import check_dsl_uncached
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.store_files import Origin
from bartleby.shared.strategy_library import StrategyKind


def _workset(tmp_path) -> StrategyWorkingSet:
    roots = tmp_path / "shipped", tmp_path / "user"
    sample_library.write_sample_library(*roots)
    return StrategyWorkingSet.from_store(roots)


def test_every_entry_a_sheet_opens_on_is_there_under_every_source(tmp_path):
    ws = _workset(tmp_path)

    assert ws.status(StrategyKind.DUEL, sample_library.TEAM_DUEL_ID) is Origin.SHIPPED
    assert ws.status(StrategyKind.DUEL, sample_library.SOLO_DUEL_ID) is Origin.SHIPPED_OVERRIDE
    assert ws.status(StrategyKind.DUEL, sample_library.OWN_DUEL_ID) is Origin.USER
    for kind, uuid in (
        (StrategyKind.PLAYER, sample_library.HITTER_ID),
        (StrategyKind.PLAYER, sample_library.SUPPORT_ID),
        (StrategyKind.PLAYER, sample_library.HEALER_ID),
        (StrategyKind.DECK, sample_library.DECK_ID),
    ):
        assert ws.get(kind, uuid) is not None


def test_every_dsl_in_it_parses_and_names_real_spells():
    for dsl in (sample_library.HIT, sample_library.BUFF, sample_library.HEAL):
        check = check_dsl_uncached(dsl)
        assert check.error is None and not check.warnings

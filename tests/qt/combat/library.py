"""A strategy library in two folders for the Combat Strategies window's tests, and the window open on it."""

from __future__ import annotations

from pathlib import Path

from bartleby.qt.combat.dialog import CombatStrategyDialog
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared import strategy_library
from bartleby.shared.store_files import save_by_id
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy

KINDS = {
    PlayerCombatStrategy: StrategyKind.PLAYER,
    DuelCombatStrategy: StrategyKind.DUEL,
    DeckPreset: StrategyKind.DECK,
}


class SampleStrategySettings:
    """A product's settings as the window reads them: the favorites, in memory."""

    def __init__(self) -> None:
        self.favorites: dict[str, list[str]] = {}

    def favorite_strategies(self, kind: str) -> list[str]:
        return list(self.favorites.get(kind, []))

    def is_favorite_strategy(self, kind: str, strategy_id: str) -> bool:
        return strategy_id in self.favorite_strategies(kind)

    def toggle_favorite_strategy(self, kind: str, strategy_id: str) -> bool:
        ids = self.favorites.setdefault(kind, [])
        ids.remove(strategy_id) if strategy_id in ids else ids.append(strategy_id)
        return strategy_id in ids


def library_roots(tmp_path: Path) -> tuple[Path, Path]:
    """A separate shipped and user root, so overrides and reverts are reachable."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for root in (shipped, user):
        for kind in StrategyKind:
            strategy_library.kind_dir(root, kind).mkdir(parents=True)
    return shipped, user


def save(item, root: Path) -> None:
    """Write *item* under *root*, the shipped one or the user's."""
    save_by_id(strategy_library.kind_dir(root, KINDS[type(item)]), item.id, item.name, item.save)


def stored(kind: StrategyKind, roots: tuple[Path, Path]) -> list:
    """Every entry of *kind* as it is on disk, the user's copy of a shipped one in its place."""
    return [entry.item for entry in strategy_library.entries(*roots, kind).values()]


def open_dialog(roots: tuple[Path, Path], **kw) -> CombatStrategyDialog:
    """The window over the library in *roots*, with settings of its own."""
    return CombatStrategyDialog(StrategyWorkingSet.from_store(roots), SampleStrategySettings(), **kw)

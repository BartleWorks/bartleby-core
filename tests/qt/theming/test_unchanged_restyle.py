"""A style change that leaves the theme as it was writes no stylesheet anywhere.

Every ``setStyleSheet`` re-polishes the widget and everything under it, and sends each of
them a ``StyleChange`` of its own. On the strategy dialog a page restyling its card list
with the very rule it already had cost a pass over every card, three times per page per
open. ``themed.set_style_sheet`` compares first; these surfaces answer a ``StyleChange``
through it, and this checks they still do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from bartleby.qt.combat.combat_order_widget import CombatOrderWidget
from bartleby.qt.combat.dialog import KindPage
from bartleby.qt.combat.duel_editor import DuelConfigEditor
from bartleby.qt.combat.player_editor import PlayerStrategyEditor
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy
from tests.qt.combat.library import SampleStrategySettings


def _strategy_page() -> QWidget:
    ws = StrategyWorkingSet(
        players=[PlayerCombatStrategy(name="A", dsl="pass")], duels=[], decks=[], roots=(Path("shipped"), Path("user"))
    )
    return KindPage(StrategyKind.PLAYER, ws, SampleStrategySettings())


def _player_editor() -> QWidget:
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl="pass"))
    return editor


def _assignment_card() -> QWidget:
    editor = DuelConfigEditor()
    editor.load(DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="pass")]), [])
    return editor.cards[0]


def _combat_order() -> QWidget:
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    return widget


@pytest.mark.parametrize(
    "build",
    [_strategy_page, _player_editor, _assignment_card, _combat_order],
    ids=lambda b: b.__name__,
)
def test_a_style_change_with_the_theme_unchanged_writes_no_stylesheet(qapp, build):
    root = build()
    written: list[QWidget] = []
    for widget in [root, *root.findChildren(QWidget)]:
        real = widget.setStyleSheet
        widget.setStyleSheet = lambda sheet, widget=widget, real=real: (written.append(widget), real(sheet))

    QApplication.sendEvent(root, QEvent(QEvent.Type.StyleChange))

    assert written == []

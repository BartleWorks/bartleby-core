"""Bar A: a product's actions in groups, centered whatever the captions say, and the panel around them.

The actions are a sample product's, so nothing here knows what BartleBot's or
BartleBuddy's buttons do; each product's own tests hold its list.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QPushButton

from bartleby.qt.game_embed.game_view_panel import ActionBar, BarAction, GamePanel, client_summary
from bartleby.qt.widgets.buttons.icon_button import IconTint
from bartleby.runtime.game.game_sizing import GAME_PANEL_CHROME_PX
from tests.qt.game_embed.views import make_game_view

GROUPS = (
    (BarAction("move", "teleport", "Move"), BarAction("port", "friend_teleport", "Port")),
    (BarAction("speed", "speed", "Speed", checkable=True), BarAction("talk", "dialogue", "Talk", checkable=True)),
    (BarAction("pick", "gear", "Pick one", menu=True),),
)


def _panel(groups=GROUPS) -> GamePanel:
    return GamePanel(make_game_view(), ActionBar(groups))


def _bar_center_offset(panel: GamePanel) -> int:
    """How far the buttons' center sits from the panel's, in pixels."""
    panel.layout().activate()
    panel.action_bar.parentWidget().layout().activate()
    bar = panel.action_bar
    center = bar.mapTo(panel, bar.rect().center()).x()
    return center - panel.width() // 2


def test_the_client_summary_counts_and_names_the_profile():
    assert client_summary(4, "Four Wizards") == "4 clients connected · Four Wizards"
    assert client_summary(1, None) == "1 client connected"


def test_the_client_summary_names_no_profile_while_nothing_is_connected():
    assert client_summary(0, "Four Wizards") == "No clients connected"


@pytest.mark.parametrize(
    "node_title",
    ["", "Fight", "A node title long enough to be wider than its half of the bar, and then some more"],
)
def test_the_buttons_are_centered_with_or_without_a_node_title(qapp, node_title):
    panel = _panel()
    panel.resize(900, 400)
    panel.show()
    panel.set_client_summary(4, "Four Wizards")
    panel.set_active_node(node_title)
    qapp.processEvents()

    assert abs(_bar_center_offset(panel)) <= 2
    panel.close()


def test_a_run_starting_does_not_move_the_buttons(qapp):
    panel = _panel()
    panel.resize(900, 400)
    panel.show()
    qapp.processEvents()
    idle = panel.action_bar.mapTo(panel, panel.action_bar.rect().topLeft()).x()

    panel.set_active_node("Go to zone")
    qapp.processEvents()
    panel.layout().activate()
    running = panel.action_bar.mapTo(panel, panel.action_bar.rect().topLeft()).x()

    assert running == idle
    panel.close()


def test_the_bar_s_height_is_the_buttons_whatever_the_list(qapp):
    """Every product's Bar A is as tall as every other's, and inside the sizing's chrome term."""
    panels = [_panel(groups) for groups in (GROUPS, GROUPS[:1], ((BarAction("one", "stop", "One"),),))]
    heights = {panel.header.sizeHint().height() for panel in panels}
    assert len(heights) == 1
    assert panels[0].header.sizeHint().height() <= GAME_PANEL_CHROME_PX
    assert panels[0].action_bar.sizeHint().height() == 24


def test_every_button_is_an_icon_and_none_is_a_text_button(qapp):
    bar = ActionBar(GROUPS)
    buttons = bar.findChildren(QPushButton)
    assert len(buttons) == 5
    assert all(not b.text() for b in buttons)
    assert bar.action_ids == ["move", "port", "speed", "talk", "pick"]


def test_a_divider_sits_between_each_group_and_nowhere_else(qapp):
    bar = ActionBar(GROUPS)
    bar.resize(bar.sizeHint())
    bar.layout().activate()

    def left(w):
        return w.geometry().left()

    def right(w):
        return w.geometry().right()

    groups = [[bar.button(a.id) for a in group] for group in GROUPS]
    dividers = bar.dividers
    assert len(dividers) == len(GROUPS) - 1
    for before, divider, after in zip(groups, dividers, groups[1:]):
        assert right(before[-1]) < left(divider) and right(divider) < left(after[0])

    within = left(groups[1][1]) - right(groups[1][0])
    across = left(groups[1][0]) - right(groups[0][-1])
    assert across > within


def test_a_press_reports_the_action_s_id(qtbot):
    bar = ActionBar(GROUPS)
    qtbot.addWidget(bar)
    with qtbot.waitSignal(bar.action_requested) as pressed:
        bar.button("port").click()
    assert pressed.args == ["port"]


def test_a_toggle_shows_what_the_product_reports_never_what_the_press_left(qtbot):
    bar = ActionBar(GROUPS)
    qtbot.addWidget(bar)
    bar.button("speed").click()
    assert not bar.is_checked("speed")
    assert not bar.button("speed").isCheckable()

    bar.set_state("speed", checked=True)
    assert bar.is_checked("speed")
    assert bar.button("speed")._tint is IconTint.ACCENT
    bar.set_state("speed", checked=False)
    assert bar.button("speed")._tint is IconTint.DEFAULT


def test_set_state_changes_only_what_it_is_given(qtbot):
    bar = ActionBar(GROUPS)
    qtbot.addWidget(bar)
    assert bar.button("move").toolTip() == "Move"

    bar.set_state("move", enabled=False, tooltip="Move [F1] — somewhere")
    bar.set_state("move", checked=None)
    assert not bar.button("move").isEnabled()
    assert bar.button("move").toolTip() == "Move [F1] — somewhere"


def test_only_a_toggle_takes_a_checked_state_and_only_a_menu_action_a_menu(qtbot):
    bar = ActionBar(GROUPS)
    qtbot.addWidget(bar)
    with pytest.raises(ValueError):
        bar.set_state("move", checked=True)
    with pytest.raises(ValueError):
        bar.set_menu("move", lambda menu: None)


def test_a_menu_action_opens_its_menu_under_the_button_and_reports_no_press(qtbot, monkeypatch):
    bar = ActionBar(GROUPS)
    qtbot.addWidget(bar)
    bar.set_menu("pick", lambda menu: [menu.addAction(name) for name in ("First", "Second")])
    assert [a.text() for a in bar.menu_for("pick").actions()] == ["First", "Second"]

    shown = []
    monkeypatch.setattr("PyQt6.QtWidgets.QMenu.popup", lambda menu, point, *_: shown.append(menu))
    pressed = []
    bar.action_requested.connect(pressed.append)
    bar.button("pick").click()

    assert [a.text() for a in shown[0].actions()] == ["First", "Second"]
    assert pressed == []


def test_the_panel_forwards_a_press_and_the_launch_screen_s_requests(qtbot):
    panel = _panel()
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.action_requested) as pressed:
        panel.action_bar.button("move").click()
    assert pressed.args == ["move"]
    with qtbot.waitSignal(panel.launch_profile_requested) as launched:
        panel.game_view.launch_profile_requested.emit("Farm Team")
    assert launched.args == ["Farm Team"]


def test_the_profile_is_remembered_while_the_count_changes(qtbot):
    panel = _panel()
    qtbot.addWidget(panel)
    panel.set_client_summary(2, "Farm Team")
    panel.set_client_summary(3)
    assert panel.client_text == "3 clients connected · Farm Team"
    panel.set_launch_profile(None)
    assert panel.client_text == "3 clients connected"

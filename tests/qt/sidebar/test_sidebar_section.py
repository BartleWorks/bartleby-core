"""The sidebar section's caption factory, and the field it labels.

``SidebarField``, BartleBot's player card and its options section had no THEME reference
of their own. They were stale because ``field_label`` built a styled label and handed it
back, leaving nothing able to restyle the instance. Fixing the factory fixed all three
without touching them, which is why these are about the factory's ``ThemedTextLabel``.
"""

import pytest
from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout

import bartleby.qt.theming.theme as theme_mod
from bartleby.qt.sidebar.sidebar_section import HeadingAction, SidebarField, SidebarSection, field_label
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    """Deliver StyleChange to one widget."""
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


def _switch_app(qapp, theme, mode: str) -> None:
    """What an app's theme switch does: broadcast to every widget.

    Needed wherever the styling is owned by a *child* — sendEvent reaches one widget,
    while setStyleSheet on the QApplication reaches the whole tree, which is how a
    ThemedTextLabel nested in a plain parent gets restyled in the real app.
    """
    theme(mode)
    qapp.setStyleSheet(THEME.global_stylesheet())


def test_field_label_returns_something_that_follows_the_theme(qapp, theme):
    theme("light")
    lbl = field_label("Zone")
    light = THEME.text_muted

    _switch(qapp, lbl, theme, "dark")

    assert light not in lbl.styleSheet()
    assert "font-weight: 600" in lbl.styleSheet()


def test_field_label_still_reads_as_a_caption(qapp, theme):
    """The colon and the fixed width are the caption, not decoration.

    Every caller passes a bare word, so the colon has to come from here; and the
    fixed horizontal policy is what holds the label column steady in a player card's
    rows, whose value labels are added with stretch. Both were dropped when this
    factory stopped styling a bare QLabel, and nothing asserted either.
    """
    theme("light")
    lbl = field_label("Zone")

    assert lbl.text() == "Zone:"
    assert lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    assert lbl.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Preferred


def test_a_sidebar_field_recolors_its_caption(qapp, theme):
    """SidebarField never referenced THEME — it was stale purely through field_label."""
    theme("light")
    field = SidebarField("Zone", ThemedTextLabel("value"))
    caption = field.findChild(ThemedTextLabel)
    assert caption is not None
    before = caption.styleSheet()

    _switch_app(qapp, theme, "dark")

    assert caption.styleSheet() != before


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def _section() -> SidebarSection:
    section = SidebarSection("Players")
    body = QVBoxLayout()
    body.addWidget(QLabel("a row"))
    section.set_content_layout(body)
    return section


def test_folding_hides_the_body_and_caps_the_section_at_its_heading(qapp):
    section = _section()
    section.show()

    section.set_expanded(False)

    assert not section.expanded
    assert not section.body.isVisible()
    assert section.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert section.maximumHeight() < 100


def test_unfolding_gives_the_body_back_its_room(qapp):
    section = _section()
    section.show()
    section.set_expanded(False)

    section.set_expanded(True)

    assert section.body.isVisible()
    assert section.maximumHeight() > 10_000


def test_setting_the_state_it_is_in_emits_nothing(qapp):
    """So a remembered fold, restored on open, is not taken for the user folding it."""
    section = _section()
    toggles = []
    section._header.toggled.connect(toggles.append)

    section.set_expanded(True)
    section.set_expanded(False)

    assert toggles == [False]


def test_a_heading_action_is_drawn_only_while_the_heading_is_hovered(qapp):
    section = _section()
    action = HeadingAction("plus", tooltip="Add")
    section.add_header_action(action)

    assert section.header_actions == [action]
    assert action.icon().isNull()
    point = QPointF(5, 5)
    section._header.enterEvent(QEnterEvent(point, point, point))
    assert not action.icon().isNull()
    section._header.leaveEvent(QEvent(QEvent.Type.Leave))
    assert action.icon().isNull()


def test_a_folded_section_takes_its_heading_actions_away(qapp):
    """They act on the body, which is not there to act on."""
    section = _section()
    action = HeadingAction("plus", tooltip="Add")
    section.add_header_action(action)
    section.show()

    section.set_expanded(False)

    assert not action.isVisible()

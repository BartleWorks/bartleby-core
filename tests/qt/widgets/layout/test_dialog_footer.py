"""``dialog_footer()``, the row of buttons at the foot of every dialog (BartleBot #89).

``QDialogButtonBox`` was not only chrome: it owned the platform button order, the Esc
wiring and — the one that bites — the default button. Qt hands "default" to the first
``autoDefault`` button *constructed*, so replacing the box by hand can leave Enter on
whichever button a dialog happened to build first, with nothing visible to show for it.
That is the regression the helper exists to prevent. The keyboard tests over each real
dialog that uses it are each product's: BartleBot's are in
``tests/gui/widgets/layout/test_dialog_footer.py``. What is here is the helper itself.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout

from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer

# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def _footer_texts(footer) -> list[str]:
    """The button labels in the order they sit in the row, with the stretch marked."""
    row = footer.layout
    out = []
    for i in range(row.count()):
        item = row.itemAt(i)
        widget = item.widget()
        out.append(widget.text() if widget is not None else "<stretch>")
    return out


def test_the_row_reads_extras_stretch_cancel_commit(qapp):
    """The order ``QDialogButtonBox`` used to supply per platform, written out once."""
    dialog = QDialog()
    extra = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY)

    footer = dialog_footer(dialog, commit_text="Save", extras=[extra])

    assert _footer_texts(footer) == ["Use default", "<stretch>", "Cancel", "Save"]


def test_an_extra_sits_left_of_the_stretch(qapp):
    """An extra is a second way forward, not a second commit: it stays on the far side."""
    dialog = QDialog()
    footer = dialog_footer(dialog, extras=[ActionButton("revert", "Use default")])

    texts = _footer_texts(footer)

    assert texts.index("Use default") < texts.index("<stretch>")


def test_every_button_in_the_footer_declines_to_fill(qapp):
    """An expanding button placed after a stretch eats the stretch and spreads the pair
    across the whole footer — see ``action_button.py``."""
    dialog = QDialog()
    extra = ActionButton("revert", "Use default")
    assert extra.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding, "built filling"

    footer = dialog_footer(dialog, extras=[extra])

    for button in (footer.commit, footer.cancel, *footer.extras):
        assert button.sizePolicy().horizontalPolicy() is not QSizePolicy.Policy.Expanding


def test_exactly_one_button_is_the_default(qapp):
    dialog = QDialog()
    footer = dialog_footer(dialog, extras=[ActionButton("revert", "Use default")])

    defaults = [b for b in (footer.commit, footer.cancel, *footer.extras) if b.isDefault()]

    assert defaults == [footer.commit]


def test_the_commit_is_the_one_primary(qapp):
    """At most one ``PRIMARY`` per surface — the standing rule in ``gui/CLAUDE.md``."""
    footer = dialog_footer(QDialog())

    assert footer.commit.action_style is ActionStyle.PRIMARY
    assert footer.cancel.action_style is ActionStyle.TERTIARY


def test_the_cancel_slot_can_carry_the_caution_weight(qapp):
    """The slot is "the button that does not commit", not "the quiet one" — the recovery
    dialog's Discard All permanently drops every recovered file."""
    footer = dialog_footer(QDialog(), cancel_text="Discard All", cancel_style=ActionStyle.CAUTION)

    assert footer.cancel.action_style is ActionStyle.CAUTION


def test_the_commit_calls_accept_and_the_cancel_reject(qapp):
    dialog = QDialog()
    footer = dialog_footer(dialog)

    footer.cancel.click()
    assert dialog.result() == QDialog.DialogCode.Rejected

    footer.commit.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_either_side_can_be_given_a_slot_of_its_own(qapp):
    """``recovery_dialog`` rejects *and* unchecks; ``settings_dialog`` validates first."""
    dialog = QDialog()
    seen: list[str] = []
    footer = dialog_footer(dialog, on_commit=lambda: seen.append("commit"), on_cancel=lambda: seen.append("cancel"))

    footer.commit.click()
    footer.cancel.click()

    assert seen == ["commit", "cancel"]
    assert dialog.result() == QDialog.DialogCode.Rejected, "untouched: neither slot closed it"


def test_a_button_built_before_the_footer_gives_up_its_claim_on_enter(qapp):
    """``profile_editor``'s Add button is the real case: built first, it held Enter, so
    Enter opened a popup on top of the dialog instead of accepting it."""
    dialog = QDialog()
    add = QPushButton("Add", dialog)
    assert add.autoDefault(), "Qt's own default for a button in a dialog"

    footer = dialog_footer(dialog)

    assert not add.autoDefault()
    assert not add.isDefault()
    assert footer.commit.isDefault()


def test_the_claim_is_renewed_when_the_dialog_is_shown(qapp):
    """The half a build-time sweep alone gets wrong. A widget in a free-standing layout
    has no parent until that layout is installed, so at footer-build time most of a
    dialog's buttons are not its children yet — the region color picker's six swatches
    are none of them. Worse, ``autoDefault`` is *acquired* on reparenting: Qt reports it
    for any button whose parent is a dialog, so a button the sweep could not see does not
    merely keep an old answer, it turns into a rival for Enter afterwards."""
    dialog = QDialog()
    orphan_row = QHBoxLayout()
    swatch = QPushButton("swatch")
    orphan_row.addWidget(swatch)

    footer = dialog_footer(dialog)

    layout = QVBoxLayout(dialog)
    layout.addLayout(orphan_row)
    layout.addLayout(footer.layout)
    assert swatch.autoDefault(), "acquired on reparenting, after the build-time sweep ran"

    dialog.show()

    assert not swatch.autoDefault()
    assert footer.commit.isDefault()

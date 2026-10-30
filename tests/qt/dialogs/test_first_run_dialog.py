"""Setup's frame and shared pages, as a product hands them its own list.

BartleBot's suite drives its whole setup through these, with its own pages after them
(``tests/gui/dialogs/test_first_run_dialog.py`` there). These are the parts core decides:
the walk with and without Quick or Custom, ``needs_asking`` over a product's page types,
the product's name in the texts, and what a product may say on the shared pages.
"""

from __future__ import annotations

from bartleby.qt.dialogs.first_run_dialog import (
    ACCOUNTS_EMPTY,
    ACCOUNTS_INTRO,
    MODE_HEADING,
    TITLE,
    AccountsPage,
    FirstRunDialog,
    FirstRunPage,
    LaunchProfilePage,
    SetupMode,
    named,
    needs_asking,
)
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.shared.preferences import PreferenceStore


class _Page(FirstRunPage):
    """A page with a stored answer of its own."""

    def __init__(self, heading: str, *, answered: bool = False, custom_only: bool = False) -> None:
        super().__init__()
        self.heading = heading
        self._answered = answered
        self._custom_only = custom_only

    def answered(self) -> bool:
        return self._answered

    def applies(self, mode: SetupMode) -> bool:
        return mode is SetupMode.CUSTOM or not self._custom_only

    def record(self) -> bool:
        self._answered = True
        return True


class _Asks(FirstRunPage):
    @classmethod
    def asks_first_run(cls, prefs: PreferenceStore) -> bool:
        return True


def _headings(dialog: FirstRunDialog) -> list[str]:
    return [page.heading for page in dialog.walk]


def test_the_title_names_the_product(qapp, sample_product):
    dialog = FirstRunDialog([_Page("One?")])

    assert dialog.windowTitle() == TITLE.format(product=sample_product.name) == named(TITLE)


def test_it_opens_on_quick_or_custom_by_default(qapp):
    dialog = FirstRunDialog([_Page("One?")])

    assert _headings(dialog) == [MODE_HEADING, "One?"]
    assert dialog.current is dialog.mode_page


def test_without_the_mode_question_it_opens_on_the_first_page_and_walks_quicks(qapp):
    dialog = FirstRunDialog([_Page("One?"), _Page("Custom only?", custom_only=True), _Page("Two?")], ask_mode=False)

    assert dialog.mode_page is None
    assert dialog.mode is SetupMode.QUICK
    assert _headings(dialog) == ["One?", "Two?"]
    assert dialog.heading_text == "One?"
    assert dialog.back_button.isHidden()


def test_without_the_mode_question_finish_on_the_last_page_accepts(qapp):
    first, last = _Page("One?"), _Page("Two?")
    dialog = FirstRunDialog([first, last], ask_mode=False)

    dialog.commit_button.click()
    dialog.commit_button.click()

    assert dialog.result() == dialog.DialogCode.Accepted
    assert first.answered() and last.answered()


def test_needs_asking_asks_the_products_own_page_types(qapp):
    prefs = PreferenceStore()

    assert not needs_asking(prefs, [FirstRunPage])
    assert needs_asking(prefs, [FirstRunPage, _Asks])


def test_the_accounts_page_says_cores_texts_with_the_products_name(qapp, sample_product):
    page = AccountsPage(PreferenceStore())
    texts = [label.text() for label in page.findChildren(ThemedTextLabel)]

    assert ACCOUNTS_INTRO.format(product=sample_product.name) in texts
    assert sample_product.name in ACCOUNTS_INTRO.format(product=sample_product.name)


def test_a_product_says_more_on_the_accounts_page(qapp):
    page = AccountsPage(PreferenceStore(), intro="{product} also recovers.", empty="Nothing yet.")
    texts = [label.text() for label in page.findChildren(ThemedTextLabel)]

    assert "Sample also recovers." in texts
    assert named(ACCOUNTS_EMPTY) not in texts


def test_the_launch_profile_page_judges_a_resolution_in_the_products_setup_area(qapp, monkeypatch):
    from bartleby.qt import screen_fit  # noqa: PLC0415

    asked = []
    monkeypatch.setattr(
        screen_fit, "largest_fitting_resolution", lambda count, widget, area: asked.append(area) or (800, 600)
    )
    page = LaunchProfilePage(PreferenceStore(), setup_area=lambda _widget: (1234, 567))

    assert page.largest_fitting(2) == (800, 600)
    assert asked == [(1234, 567)]


def test_with_no_setup_area_the_page_judges_at_the_products_panes(qapp, monkeypatch):
    from bartleby.qt import screen_fit  # noqa: PLC0415

    asked = []
    monkeypatch.setattr(
        screen_fit, "largest_fitting_resolution", lambda count, widget, area: asked.append(area) or None
    )

    LaunchProfilePage(PreferenceStore()).largest_fitting(2)

    assert asked == [None]

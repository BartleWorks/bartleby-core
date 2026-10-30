"""What a product says to core's settings rows: its folding groups, and its settings."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QCheckBox

from bartleby.qt.settings import rows
from bartleby.qt.settings.rows import SettingGroup, SettingRow, SettingsForm, declare_folds


@pytest.fixture(autouse=True)
def _folds(monkeypatch):
    monkeypatch.setattr(rows, "SETTINGS_FOLD_DEFAULTS", {})


def _group(group_id: str) -> SettingGroup:
    return SettingGroup(group_id, SettingRow("A switch", "What it does.", QCheckBox(), default=True), [])


def test_a_group_the_product_declared_is_built_collapsed_as_declared(qapp):
    declare_folds({"autosave": True, "grid": False})

    assert _group("autosave").is_collapsed
    assert not _group("grid").is_collapsed


def test_a_group_no_product_declared_is_refused(qapp):
    with pytest.raises(KeyError, match="SETTINGS_FOLD_DEFAULTS"):
        _group("quicksell")


def test_whether_descriptions_are_shown_is_read_from_the_products_settings(qapp, sample_store):
    sample_store.show_setting_descriptions = True
    form = SettingsForm()
    row = form.add_row(SettingRow("A setting", "What it does.", QCheckBox(), default=True, details="More."))
    form.finish()

    assert row.details_shown


def test_showing_descriptions_is_written_and_saved_at_once(qapp, sample_store):
    form = SettingsForm()
    form.add_row(SettingRow("A setting", "What it does.", QCheckBox(), default=True, details="More."))
    form.finish()

    form._on_switches(True)

    assert sample_store.show_setting_descriptions is True
    assert sample_store.saves == 1

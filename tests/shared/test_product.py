"""The running app as one value: its folders follow ``APPDATA``, and shared code reads its name."""

from __future__ import annotations

from dataclasses import replace

import pytest

from bartleby.runtime.game.key_bindings import Binding, KeyMap
from bartleby.shared import product
from bartleby.shared.product import CHECKOUT, Product


def test_the_data_folder_is_the_products_own_under_the_vendors(sample_product, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert sample_product.data_dir == tmp_path / "Bartleby" / "Sample"


def test_session_logs_go_under_the_data_folder_unless_the_product_says_where(sample_product, tmp_path):
    assert sample_product.log_dir == sample_product.data_dir / "logs"
    assert replace(sample_product, log_root=tmp_path).log_dir == tmp_path


def test_the_slug_is_the_name_in_lower_case(sample_product):
    assert sample_product.slug == "sample"


@pytest.mark.parametrize(("kind", "packaged"), [(CHECKOUT, False), ("packaged (PyInstaller)", True)])
def test_anything_but_a_checkout_is_packaged(sample_product, kind, packaged):
    assert replace(sample_product, build_kind=kind).packaged is packaged


def test_use_makes_a_product_the_current_one(monkeypatch):
    other = Product(name="Other", version="0.0.1", accent="#000000", accent_dark="#ffffff", logo_background="#808080")
    monkeypatch.setattr(product, "_current", None)

    product.use(other)

    assert product.current() is other


def test_with_no_product_in_use_current_says_what_was_missed(monkeypatch):
    monkeypatch.setattr(product, "_current", None)

    with pytest.raises(LookupError, match=r"product\.use\(\)"):
        product.current()


def test_a_message_from_deep_in_core_names_the_running_product():
    """Said to the user before a run: an input this app cannot press. The name is the product's, never a literal."""
    choice = KeyMap([Binding("Interact", "joystick.button1")], source="memory").choose("Interact")

    assert choice.problem == "is on an input Sample can't press"

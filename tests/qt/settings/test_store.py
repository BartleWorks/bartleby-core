"""The product's settings, as the shared settings surfaces reach them."""

from __future__ import annotations

import pytest

from bartleby.qt.settings import store


def test_the_store_is_whatever_the_product_handed_over_at_each_read(monkeypatch):
    """A getter, not an object: a product whose tests replace its settings is read afresh."""
    current = ["first"]
    store.configure(lambda: current[0])

    assert store.store() == "first"
    current[0] = "second"
    assert store.store() == "second"


def test_an_unconfigured_store_says_so(monkeypatch):
    monkeypatch.setattr(store, "_store", None)

    with pytest.raises(RuntimeError, match="configure"):
        store.store()

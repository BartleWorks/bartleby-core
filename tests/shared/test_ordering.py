"""apply_order: a drag's permutation applied, or refused when it no longer fits."""

import pytest

from bartleby.shared.ordering import apply_order


def test_the_permutation_is_applied():
    assert apply_order(["a", "b", "c"], [1, 0, 2]) == ["b", "a", "c"]


def test_the_identity_permutation_gives_the_same_list():
    assert apply_order(["a", "b"], [0, 1]) == ["a", "b"]


@pytest.mark.parametrize("order", [[0, 1], [0, 1, 2, 3], [0, 0, 1], [1, 2, 3]])
def test_a_permutation_of_another_list_is_refused(order):
    """Built from a list read earlier: applying it could drop an item or repeat one."""
    assert apply_order(["a", "b", "c"], order) is None

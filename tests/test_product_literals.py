"""No shared module names a product or a version literal (Stage 5 of BartleBot #167).

Each comes from the running app's ``Product`` instead (``bartleby/shared/product.py``).
"""

from pathlib import Path

from bartleby.runtime.testing.guards import product_literals

PACKAGE = Path(__file__).resolve().parents[1] / "bartleby"

#: Every app of the family, past, present and planned.
PRODUCTS = ("BartleBot", "BartleBuddy", "BartleBuilder")


def test_no_shared_module_names_a_product_or_a_version():
    result = product_literals(PACKAGE, PRODUCTS)

    assert not result, result.message()

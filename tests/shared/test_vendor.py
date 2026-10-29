"""One vendor name, defined once, everywhere it reaches the user's machine.

Three keyring services and one ``%APPDATA%`` directory were once named after BartleBot,
while the data in all four is about the *account*: the password, the characters on it, its
identity fingerprint, the launch profiles that bring it. A second product by the same
publisher needs every one of them, and a user cannot be asked to enter the same accounts
twice. Moved from BartleBot's ``tests/shared/test_vendor_identity.py``, which keeps the
checks that read BartleBot's own source.
"""

from __future__ import annotations

from pathlib import Path

from bartleby.runtime.clients import account_identity, character_roster, credentials
from bartleby.shared.vendor import VENDOR, keyring_service, product_dir, vendor_dir

SHARED = Path(__file__).resolve().parents[2] / "bartleby" / "shared"


def test_the_vendor_is_not_the_product():
    """Two names answering two questions; see shared/vendor.py.

    The organization (``BartleWorks``) is developer-facing and lives in URLs; the
    vendor is user-facing and lives on the user's disk. Collapsing them back into one
    string is what this guards against.
    """
    assert VENDOR == "Bartleby"


def test_every_keyring_service_carries_the_vendor_prefix():
    services = [credentials._SERVICE, character_roster._SERVICE, account_identity._SERVICE]

    assert services == [VENDOR, f"{VENDOR}-roster", f"{VENDOR}-identity"]
    assert len(set(services)) == 3, "a shared namespace would put a roster where a password is expected"


def test_the_product_directory_sits_under_the_vendor_one(monkeypatch, tmp_path):
    """Per-product data beneath the vendor root; shared data at the root itself."""
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert vendor_dir() == tmp_path / "Bartleby"
    assert product_dir("BartleBot") == tmp_path / "Bartleby" / "BartleBot"
    assert product_dir("BartleBuddy").parent == vendor_dir()


def test_keyring_service_builds_a_namespace_from_a_suffix():
    assert keyring_service() == VENDOR
    assert keyring_service("roster") == f"{VENDOR}-roster"


def test_the_shared_layer_is_reachable_without_qt():
    """Imported by a module list rather than by a running app, so a missing Qt is fatal.

    ``bartleby.shared`` is what a live-play tool would depend on, and it must not pull a
    widget toolkit in behind it. The import guard above is the static half of this; this
    is the half that catches a transitive one.
    """
    modules = sorted(p.stem for p in SHARED.glob("*.py") if p.stem != "__init__")

    assert modules, "the shared layer has modules"
    for name in modules:
        __import__(f"bartleby.shared.{name}")


def test_the_shared_layer_has_no_stray_files():
    """Every module here is one a second product would want. A reminder, not a rule."""
    assert {p.name for p in SHARED.glob("*.py")} == {
        "__init__.py",
        "account_setup.py",
        "account_slots.py",
        "account_store.py",
        "combat_defaults.py",
        "content_hash.py",
        "file_types.py",
        "first_run.py",
        "game_dpi.py",
        "game_install.py",
        "install_key.py",
        "json_update.py",
        "keyring_backend.py",
        "launch_profiles.py",
        "listeners.py",
        "official_set.py",
        "ordering.py",
        "preferences.py",
        "product.py",
        "share_file.py",
        "share_match.py",
        "store_files.py",
        "strategy_library.py",
        "strategy_models.py",
        "vendor.py",
    }

"""Isolation every test gets: its own %APPDATA%, and no write to the machine's registry or game."""

import itertools
import os
import sys

import pytest

#: Where the install key, the file types and the compatibility layers go in a test that does
#: not choose a key of its own: one throwaway key per worker, never the vendor's.
UNGUARDED_REGISTRY_KEY = rf"Software\Bartleby-test-unguarded-{os.getpid()}"

_APPDATA_SERIAL = itertools.count()


@pytest.fixture(scope="session")
def _appdata_root(tmp_path_factory):
    """The parent of every test's private ``%APPDATA%``, made once per worker."""
    return tmp_path_factory.getbasetemp() / "appdata"


@pytest.fixture(scope="session", autouse=True)
def _remove_unguarded_registry_key():
    """Remove whatever a test wrote under :data:`UNGUARDED_REGISTRY_KEY`, at the end of the session."""
    yield
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        from bartleby.shared.install_key import delete_tree  # noqa: PLC0415

        delete_tree(winreg.HKEY_CURRENT_USER, UNGUARDED_REGISTRY_KEY)


@pytest.fixture(autouse=True)
def isolated_appdata(_appdata_root, monkeypatch):
    """Point %APPDATA% at a per-test directory, named but not created, as BartleBot's suite does."""
    serial = next(_APPDATA_SERIAL)
    monkeypatch.setenv("APPDATA", str(_appdata_root / f"t{serial}"))
    monkeypatch.setenv("LOCALAPPDATA", str(_appdata_root / f"t{serial}-local"))
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    monkeypatch.delenv("BARTLEBUDDY_DEV", raising=False)

    from bartleby.shared import file_types, game_dpi, game_install, install_key, official_set  # noqa: PLC0415

    monkeypatch.setattr(official_set, "_registry", official_set.NoRegistry())
    official_set.reset_shipped_root()
    monkeypatch.setattr(install_key, "REGISTRY_KEY", UNGUARDED_REGISTRY_KEY)
    monkeypatch.setattr(file_types, "CLASSES_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Classes")
    monkeypatch.setattr(file_types, "notify", lambda: None)
    monkeypatch.setattr(game_dpi, "LAYERS_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Layers")
    monkeypatch.setattr(game_install, "_places", game_install.NoPlaces())

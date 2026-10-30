"""Which app is running: its name, version, look and folders, handed to the shared code as one value.

Every product built on this library is a :class:`Product`. Shared code that would
otherwise spell out a name or a version -- the session log's header, the single-instance
pipe, the ``%APPDATA%`` folder, a message that says which app cannot press a key -- takes
it from here instead. A literal in shared code is a value that drifts: BartleBot's font
sizes, written out at each use, "had drifted into six values doing four jobs", and a
product name written out in core would be wrong in every product but one.
``tests/test_product_literals.py`` keeps them out.

Two ways it arrives. The code an entry point calls at startup takes it as an argument
(``configure_logging(product, settings)``, ``SingleInstance(product)``). Code reached from
deep inside a run, such as a key-binding message, reads :func:`current`, which the entry
point sets with :func:`use` before anything else, so it needs no parameter threaded
through every caller.

Qt-free, though the accent and the logo are drawn by the Qt tier: ``bartleby.shared`` and
``bartleby.runtime`` need the name and the folders as much as the widgets need the color,
and the tiers import only upward. The tree is the family's, in ``bartleby.qt.logo``; a
product says only what color goes behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bartleby.shared.vendor import product_dir

#: :attr:`Product.build_kind` for a run from a source checkout. Anything else is a packaged build.
CHECKOUT = "checkout"


@dataclass(frozen=True)
class Product:
    """One app of the family. Built once, by the app's own code, and never changed."""

    #: As the user sees it: the window title, the ``%APPDATA%/Bartleby/`` folder, the log header.
    name: str
    #: This build's version, the one number the app's ``pyproject.toml`` holds.
    version: str
    #: The app's accent color in the light theme, and in the dark.
    accent: str
    accent_dark: str
    #: The color behind the family's tree, on the logo's square and its disc. The window icon has none.
    logo_background: str
    #: Developer mode: the app's own switch, for whatever its developers need (BartleBot's ``BARTLEBOT_DEV``).
    dev_mode: bool = False
    #: :data:`CHECKOUT`, or the kind of packaged build, for the log header. Never a path: logs get shared.
    build_kind: str = CHECKOUT
    #: Where session logs go, when not ``logs/`` under :attr:`data_dir`: BartleBot's checkout in developer mode.
    log_root: Path | None = None

    @property
    def slug(self) -> str:
        """The name in lower case: the app's import package, and the prefix of its session log files."""
        return self.name.lower()

    @property
    def data_dir(self) -> Path:
        """``%APPDATA%/Bartleby/<name>``: the app's own data. Read afresh, so it follows ``APPDATA``."""
        return product_dir(self.name)

    @property
    def log_dir(self) -> Path:
        """Where session logs go: :attr:`log_root`, else ``logs/`` under :attr:`data_dir`."""
        return self.log_root if self.log_root is not None else self.data_dir / "logs"

    @property
    def packaged(self) -> bool:
        return self.build_kind != CHECKOUT


_current: Product | None = None


def use(product: Product) -> None:
    """Make *product* the running app. The entry point's first call, before anything reads :func:`current`."""
    global _current
    _current = product


def current() -> Product:
    """The running app, as :func:`use` set it."""
    if _current is None:
        raise LookupError("No product is in use: the app's entry point calls bartleby.shared.product.use() first")
    return _current

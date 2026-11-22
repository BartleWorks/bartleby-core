"""The game's locales, by the names its ``Root.wad`` folders have.

Memory holds the text the game shows, already in the client's own language, so every table
matched against it holds each of these.
"""

from __future__ import annotations

# The game ships these seven. Kept in step with BartleBot's worlds.py and dialog_captions.py.
LOCALES: tuple[str, ...] = ("de", "el", "en-US", "es", "fr", "it", "pl")

#: The locale whose tables are whole, and what a text another locale lacks falls back to.
DEFAULT_LOCALE = "en-US"

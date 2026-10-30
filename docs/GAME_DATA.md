# Reading the game's own data

Moved here from BartleBot's `tools/CLAUDE.md`, where it was learned (2026-10-30). The tools
and snapshots it names (`typedump.py`, `dump_*.py`, `probe_*.py`, `bartlebot/game/data/`)
still live in BartleBot until a stage moves them; their file names do not change when it does.
The rules hold for every product.

Two sources answer most "what is this offset / where does this value come from"
questions without probing a live client. **Check both before writing a probe** --
several days of `probe_*.py` work was spent rediscovering things that were sitting in one
of these.

## Type dumps: `r<rev>_WizardDev.json`

A wiztype dump of every game class with the exact byte offset of each property —
about 7,000 classes and 49,000 properties. Query it with `tools/typedump.py`:

```bash
uv run python tools/typedump.py at ClientObject 448     # what lives at this offset?
uv run python tools/typedump.py show DeckListControl    # full layout, incl. inherited
uv run python tools/typedump.py prop "characterId"      # find a property everywhere
uv run python tools/typedump.py classes "SpellList"     # find classes by regex
uv run python tools/typedump.py gaps DeckListControl    # holes where raw members hide
uv run python tools/typedump.py annotate ../wizwalker/wizwalker/memory/memory_objects/client_object.py
uv run python tools/typedump.py diff <old.json> <new.json>
```

Two things to know before trusting it:

- **It covers reflected PropertyClass properties only.** Raw C++ members — child
  pointers, manager handles, the entry vectors `deck_io.py` reads — never appear.
  `gaps` is the workaround: it brackets those between named neighbors, which is
  how `deck_io`'s `0x300` entry vector shows up as a 40-byte hole between
  `m_treasureFrame` and `m_cardSize`.
- **A class name is not the runtime behavior template name**, and the difference
  is not derivable: `ClientWizPlayerNameBehavior` → `"WizPlayerNameBehavior"`,
  `ClientTreasureBookBehavior` → `"BasicTreasureBookBehavior"`,
  `ClientInventoryBehavior` → `"WizardInventoryBehavior"`. The authoritative list
  is `await client_object.list_behavior_names()` on a live client.

**A dump is captured with wiztype**, from one running client:
`uvx --from wiztype==0.1.13 python tools/game_update/capture_type_dump.py`. katsuba only
reads one. The tool finds the type registry itself when a patch breaks wiztype's own scan,
as r811339 did; `bartleby-core/docs/GAME_UPDATES.md` has when to run it and where the file goes.

**Dumps are committed, and old ones are never deleted.** A dump can only be
captured from a running client, so once a patch ships there is no way back to the
previous layout. The checked-in dumps are the only record of what the offsets were
when our code last worked; `typedump.py diff` against a fresh dump then says
exactly what moved. `.gitattributes` disables textual diffing for them — use the
tool, not `git diff`.

(BartleBot keeps the newest dump in `bartlebot/game/data/` and the older ones in `tools/`;
its `tools/CLAUDE.md` says why.)

## Game data: `Root.wad`

WizWalker can read the game's own archives, which hold the lookup tables the
client resolves IDs against. Many fields in memory are keys, not text —
`m_badgeTitle` reads `'Badges_00006184'`, and a wizard's name is three indices,
never characters — so a memory scan for display text will find nothing while the
answer sits in a WAD.

```python
from wizwalker.file_readers.wad import Wad

wad = Wad(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData\Root.wad")
names = await wad.names()                       # ~172,000 entries
data = await wad.get_file("CharacterNames.xml") # bytes
```

Conventions worth knowing:

- `<Thing>.xml` holds the **ordered key list**; `Locale/<loc>/<Thing>.lang` maps
  key → display text. IDs index **position in the XML list**, not the numeric
  suffix of the key (`First_Boy_102` is "Luke" but sits at index 100).
- `.lang` files are UTF-16LE with CRLF, in fixed three-line records: `key`,
  translator description, display text. The description reads as blank in most
  records, which makes the format look like `key / blank / value` — a parser that
  *requires* it blank silently drops every documented key rather than failing. Slice
  at a fixed stride; see `parse_lang` in `tools/dump_world_names.py`.
- Locales are `de el en-US es fr it pl`. Non-English tables can be shorter —
  later additions were not always translated.

Don't read a WAD at runtime: a 172k-entry archive read per lookup, plus a hard
dependency on the game being installed at a guessable path. Extract what you need
into a checked-in snapshot instead, as `tools/dump_world_names.py` does for
`bartlebot/game/data/world_names.json`, and regenerate it when the game adds entries.
## Reading a field out of a BINd file: use `katsuba`, not a byte scan

Every game object under `Root.wad` (`Spells/`, `ObjectData/`, etc.) is a `BINd`-
prefixed binary blob — a serialized `PropertyClass`, not text. Pulling one named
field out of it without a real deserializer means guessing at a marker in the raw
bytes, and that guess can silently read the wrong field: `dump_spell_names.py`
originally found a spell's display-name langcode by searching for the first
literal `b"Spells_"` and reading the length byte before it, which assumes
`m_displayName` is always the first localized string in the blob. It isn't —
field order in the serialized bytes isn't fixed, so for some templates
`m_description`'s langcode came first and got misread as the name (1457 spells
got their combat-log template text — `"$eA$ $dT_image$$Damage_image$..."` — as
their "display name" before this was caught).

[`katsuba`](https://pypi.org/project/katsuba/) is a real BINd deserializer — the
same one the wizdb/Kimerith-WindHammer Discord-bot toolchain uses — and it reads
straight off the newest wiztype dump, the one in `bartlebot/game/data/` that `typedump.py`
reads too, so no separate type dump is needed:

```python
from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive

from bartlebot.game.art_extraction import type_dump

archive = Archive.mmap(str(wad_path))
types = TypeList.open(str(type_dump()))
opts = SerializerOptions()
opts.flags = STATEFUL_FLAGS
opts.shallow = False
opts.skip_unknown_types = True
ser = Serializer(opts, types)

data = archive["Spells/Tiered Spells/Novus Storm - T05 - A.xml"]
if data.startswith(b"BINd"):
    data = data[4:]
obj = ser.deserialize(data)
obj.get("m_displayName")  # -> b"Spells_00003940", by name, not position
```

`tools/dump_spell_names.py` is the worked example. It's a dev-only dependency
(`uv add --group dev katsuba`) — nothing at runtime reads a WAD, per the rule
above — so reach for it whenever a new dump tool needs a specific field off a
`BINd` object rather than an ordered `<Thing>.xml` / `.lang` pair; save the
marker-scanning approach for cases `katsuba` genuinely can't cover.

**A spell's WAD path stem is its template name, but a template name is not unique.**
41 of them exist twice — once under `Spells/CastleMagic/` and once as the real spell,
so `Banshee` is both `CastleMagic` and `Death`. Both files name the same art, so it is
one card filed twice rather than two cards. A tool keyed by stem therefore has a
collision rule whether it writes one down or not, and "last one the archive handed
over" is not a rule. `dump_spell_schools.py` keeps whichever copy claims a real school,
because a card in a combat deck is never the castle-magic copy.
## What neither source answers: window layout

**UI window names are not in the type dump and not in any WAD.** The dump covers
reflected `PropertyClass` properties, and the archives hold game data; the window
layout is compiled into the client binary. Searching every UI-ish XML across
`Root.wad`, `GUI-WorldData.wad` and `_Shared-WorldData.wad` for names the app
already relies on — `DeckConfiguration`, `CardsInDeck`, `ItemSpells` — returns
zero content hits, and `GUI/` in `Root.wad` is textures.

So the rule above inverts for anything shaped like "what is this control called":
go straight to a live client. Either the in-app **UI Tree Browser**, or a probe
that dumps the subtree and diffs it across a change the user makes by hand —
`tools/probe_archmastery_ui.py` is the worked example of the second.

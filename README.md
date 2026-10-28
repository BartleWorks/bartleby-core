# bartleby-core

What every Bartleby product shares, in three tiers: `bartleby.shared` (accounts, launch
profiles, preferences, the strategy library), `bartleby.runtime` (game clients at run time:
launching, hooking, the state reader, the arbiter, movement and the helpers; no Qt) and
`bartleby.qt` (theming and the shared widgets). Each tier imports only from those before it;
`tests/test_layers.py` holds them to it.

Started 2026-10-28 from BartleBot `a0c51a71` with a fresh history (BartleBot #167). Modules
moved from BartleBot keep their file names, and their docstrings still describe BartleBot
where it is the only user so far.

```bash
uv sync --all-extras
uv run pytest
```

The rules BartleBot's CLAUDE.md sets hold here too: no real player data and no game assets,
ever; every timing constant in `bartleby/runtime/timing.py`; and every input to a client
through an arbiter lease (`bartleby/runtime/arbiter.py`).

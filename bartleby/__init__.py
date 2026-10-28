"""What every Bartleby product shares, in three tiers.

* ``bartleby.shared``: records about the user and their accounts -- the account store,
  launch profiles, preferences, the strategy library and its Official set. Stdlib and
  pydantic only.
* ``bartleby.runtime``: game clients at run time -- launching, hooking, the state reader,
  the arbiter that owns every input, and the actions that press keys. wizwalker and
  wizsprinter, never Qt (``pip install bartleby-core[runtime]``).
* ``bartleby.qt``: theming and the widgets the products share (``[qt]``).

A tier imports only from the tiers above it in this list. ``tests/test_layers.py`` is the guard.
"""

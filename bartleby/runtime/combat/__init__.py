"""Combat, as both products fight: running a fight, checking a DSL, holding the first cast.

``fight`` runs each player's combat and flees a fight that cannot be won, ``dsl`` checks
and parses a strategy's text, ``dsl_check_cache`` keeps those checks on disk, and ``hold``
holds a fighter's first cast for the players joining it. What picks a fight's strategy is
each product's own. No Qt, and no product (``tests/test_layers.py``).
"""

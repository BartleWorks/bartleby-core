"""What a second product by this publisher needs, and nothing this one does.

BartleBuddy (a live questing assistant) and BartleBuilder (a build simulator) are
planned beside BartleBot, and all three want the same accounts, the same launch
profiles and the same stored passwords. Asking a user to enter their accounts twice
is not a thing to ship, so the parts that are about the *account* rather than about
this application live here.

The constraint that makes that possible is a rule about imports: **nothing in this
subpackage imports from ``bartleby.runtime``, ``bartleby.qt`` or any product.** It began
as BartleBot's ``bartlebot/shared/`` under the same rule, which is what let it move into
this repo whole. ``tests/test_layers.py`` is the guard.

The seam that decides most cases: a *record* is shared, a *judgement about it made
against this app's window* is not. A launch profile's resolution override lives here;
``screen_fit``'s verdict on whether it fits, which measures this app's sidebars and bot
canvas, stays in ``gui/``. Same shape for account colors: the slot arithmetic is here
in ``account_slots``, resolving a slot against the live theme is the product's ``gui/theming/account_accent``.
"""

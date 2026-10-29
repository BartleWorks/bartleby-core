# Qt conventions

The family's GUI rules: for everything under `bartleby/qt/`, and for each product's GUI,
whose own `CLAUDE.md` imports this file. Each was learned in BartleBot and moved here word for
word (BartleBot #167, Stage 2), so "the app" and "here" in them mean BartleBot. Each holds for
every product.

A path is relative to `bartleby/qt/`, except one starting `bartleby/` or `docs/`, which is
from core's root. A path written as BartleBot's names a module still in BartleBot, under
`bartlebot/gui/` unless it says otherwise, until the stage that moves it updates the path.
The guards named here are in `testing/guards.py`, and each product runs them over its own
package.

## GUI theming
Use `THEME` singleton from `theming/theme.py`. No hardcoded colors in widget code.

### Font sizes come from a token, and are always px

`THEME.font_body_px` is the app font and the one number the rest hang off:
`font_heading_px`, `font_small_px`, `font_subtext_px` and `font_micro_px` are read
relative to it, so moving the body size moves all of them. Node text is the exception
(`font_node_px`, `font_node_sub_px`): it is drawn in scene units against fixed node
geometry, so it does not follow the body size.

A size no token names is an *offset* from one at the call site —
`f"font-size: {THEME.font_body_px + 1}px"` — never a literal, and never a
`_FONT_PX` constant in the module. `literal_font_sizes` (`testing/guards.py`) fails the build on any
of those, on a size in points, and on the two traps below. The sizes lived as literals
in about eighty places before, which is why they had drifted into six values doing
four jobs.

- **Never read a size back off a font.** `pointSize()` returns `-1` on a font sized in
  px, so `font.setPointSize(font.pointSize() + 1)` silently resolves to 0, and a
  `pointSizeF()` read into a `max(..., 6.0)` floor silently resolves to that floor.
  Both shipped here. Size from a token: `font.setPixelSize(THEME.font_body_px + 1)`.
- **A stylesheet `font-size` never reaches `QWidget.font()`.** A widget that measures
  its own text — anything eliding, sizing a slot, or growing to fit lines — must set
  the size on the font as well, or it measures at the app font and lays out for the
  wrong size.

Use `THEME.ui_font(px, bold=...)` to build one. For text drawn at a fractional scale
(the canvas region labels), `THEME.scaled_ui_font(px, scale)` keeps the fractional
size that `setPixelSize`'s whole pixels would step between.

There is deliberately **no user setting for font size**, so nothing re-reads these at
run time: a stylesheet built once in a constructor is fine.

Anything a widget builds from `THEME` is a snapshot of whichever theme was active at
the time, and Qt never recomputes it: `QApplication.setStyleSheet()` delivers
`StyleChange` to every widget, but a stylesheet the widget set on *itself* always
wins and is left exactly as it was. The widget has to re-derive it.

**Inherit `ThemedMixin` (`theming/themed.py`) and put the work in
`apply_theme_styles`.** It is enforced: `theme_refresh_offenders` (`testing/guards.py`) fails any
class that builds a `THEME`-derived value without either the mixin or a
`changeEvent` that handles `StyleChange`.

```python
class MyPanel(ThemedMixin, QWidget):          # mixin FIRST — see below
    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel("hello")
        self.refresh_theme_styles()           # call this; implement the other

    def apply_theme_styles(self) -> None:
        self._label.setStyleSheet(f"color: {THEME.text};")
```

Five things that are not obvious:

- **`refresh_theme_styles()` is what you call; `apply_theme_styles()` is what you
  write.** The first carries a re-entrancy guard, and `setStyleSheet` on `self`
  delivers `StyleChange` synchronously back into `changeEvent`. Calling the hook
  directly applies twice, or recurses until the app dies in its own constructor.
- **The mixin goes first in the base list.** `class P(QWidget, ThemedMixin)` does not
  merely skip the mixin — sending `StyleChange` to such a widget aborts the process
  inside `sendEvent`, with no exception. The guard test checks base order statically
  because nothing can catch it at runtime.
- **A bare `svg_icon(name)` is themed.** Its `tint` defaults to `THEME.icon_tint`, so
  an icon set once and kept goes stale with no stylesheet anywhere to grep for. Pass
  `tint=""` for an icon that should keep its own colors.
- **Write the rule through `set_style_sheet(widget, sheet)`, not `setStyleSheet`.**
  A re-assignment is never local: Qt re-parses the rule and delivers `StyleChange` to
  the whole subtree, every themed child answers by re-deriving *its* rule and setting
  it, and that broadcasts again. The re-entrancy guards stop it recursing forever;
  they do not stop it being quadratic in the depth of a card. Re-deriving from `THEME`
  almost always reproduces the string already there, so the helper compares first and
  writes only on a real change — 132 tests in BartleBot's `test_combat_tab.py` were paying 40,151
  `setStyleSheet` and 288,522 `changeEvent` calls for it. A genuine theme switch does
  change the string, so the cascade still runs the one time it has to. The guard test
  knows both spellings; if you add a third, teach `_direct_themed` about it or the
  rename silently empties the guard.
- **A factory cannot restyle what it returns.** `field_label(text)` hands a label to a
  caller that has nothing to re-apply, so the staleness lands on the *caller* — which
  is why the guard blames a themed helper's callers. Return a
  `ThemedTextLabel` (`widgets/labels/themed_label.py`) instead; it owns its own
  color, named by token so it is re-read rather than snapshotted.

Widgets that read `THEME` at paint time (e.g. `QGraphicsItem.paint()`, `_RowWidget`)
hold no baked value and need none of this.

**Never destroy a widget from inside `apply_theme_styles`.** A theme switch arrives as
`QApplication.setStyleSheet()`, which is iterating over every widget when it does, and
freeing a subtree mid-iteration aborts the process with no traceback. BartleBot's `_DetailPanel`
(`node_browser_dialog.py`) is the one place that rebuilds rather than restyles — its
`QScrollArea.setWidget()` deletes the old container outright — so it defers the rebuild
to the next event-loop turn via a `QTimer` parented to itself, which dies with the panel
instead of firing into a destroyed widget.

Two failure modes the guard cannot see, so check them by hand:

- **A color inside rich text.** The required-field marker in BartleBot's `param_panel` is a
  `<span>` carrying `THEME.error` in the label's own *text*, so restyling means
  rewriting the text. The guard looks for `setStyleSheet`.
- **A widget styled only on the next switch.** The guard checks that
  `refresh_theme_styles()` is called *somewhere* outside `changeEvent`, not that
  construction reaches it. BartleBot's `WikiImportDialog` passed while its only caller was
  `_set_status`, and opened unstyled.
- **A hand-written `changeEvent` that only restyles some of the widget.** The guard
  asks whether a class handles `StyleChange`, not whether it handles all of it, so a
  partial handler passes and reads as done. BartleBot's `_ParamPanel` restyled three of its ten
  themed widgets that way, leaving the node help button in the old palette. Prefer
  the mixin — one hook that restyles everything is easier to keep complete than a
  handler someone extended once.

## Popup menus

Build every `QMenu` through BartleBot's `gui/menu_utils.py` — `popup_menu(anchor)` for a
menu built per invocation, `anchor_menu_to_window(QMenu(btn))` for a long-lived one
handed to `QToolButton.setMenu()`.

Qt gives a popup the `QWindow` of its parent's nearest *native* ancestor as transient
parent. Embedding a game client makes `ClientTile` native, and its ancestors with it —
`GameView`, its panel, the splitter. A menu parented to a button inside one resolves
to a `QWidgetWindow` that is not top level, which Qt refuses outright: `... must be a
top level window`, leaving the popup with no transient parent at all.

Qt would also promote every *sibling* of those to a native window — the left sidebar,
the canvas — and BartleBot's `bartlebot.py` turns that off with `AA_DontCreateNativeWidgetSiblings`
(`native_widgets.py`). Leave it on. With promotion on, this helper's reparenting
made every child of the canvas's view native, and a native `ViewFrame` took every
click on the canvas (#126). A widget that has to draw over the tiles asks for its own
native window with `WA_NativeWindow`, as `GameView`'s overlay button does.

**A native widget sets a cursor of its own** (#258). With none, its window borrows its
parent window's, which can be a pointing hand left by a widget the pointer is nowhere
near. `ClientTile` gives itself and its ancestors an arrow through
`give_native_ancestors_a_cursor` (`native_widgets.py`, which has the mechanism), and
the overlay button sets its own pointer. For the same reason a disabled button unsets its
cursor rather than setting an arrow, and none calls `setCursor` when nothing changed, as
`IconButton.setEnabled` shows.

The helper swaps the menu onto its top-level window on `aboutToShow` and hands it back
on `aboutToHide`. The swap cannot happen at construction: a menu built in a widget's
`__init__` has no window yet, and `window()` would return the widget itself.

A menu with no parent, or one parented directly to a window or dialog, is already fine.

## The title bar is ours, not Windows'

The main window has no native caption. `TitleBar` (`title_bar/title_bar.py`) is the one row
above the panes, holding the logo, the menu bar, the title, the pane toggles and the
window buttons, and `NativeFrame` (`title_bar/native_frame.py`) is what makes it a title bar:
Qt's `ExpandedClientAreaHint` for the frame, plus the four things Qt leaves undone. Its
docstring has each one and the spike that found it. The rules:

- **A control in the bar is a `QAbstractButton` or the `QMenuBar`.** `is_control_at`
  decides by class, and everything else in the bar is caption: it drags the window, and a
  click on it never reaches the widget. A new kind of control needs adding there.
- **Never `menuBar()` or `setMenuBar()` on `MainWindow`.** The menu bar is the title bar's
  child, installed with `setMenuWidget`. `QMainWindow.menuBar()` would quietly create a
  second, empty one.
- **Every `WM_NCHITTEST` is answered by `NativeFrame`, never by Qt.** Qt's answer for the
  expanded area fakes a mouse press from inside the hit test; with a menu bar in the strip,
  File never opened. Outside the bar the answer is Windows' `DefWindowProc`.
- **A pane shown or hidden in code goes through its View action**, or the title bar's
  toggle and the menu's check mark go stale: `show_game_view`, `show_bot_canvas`, or
  `_sync_pane_actions` after changing several at once, as Maximize Game View does.
- **The window's edge is drawn once, and never by a status bar** (#271). Windows' 1px border
  wears `edge_color` (`title_bar/bottom_edge.py`), which is `border`. Where Windows draws
  none (maximized, full screen, before Windows 11), `BottomEdge` draws the bottom one as a
  splitter handle is drawn, so it reads as one more divider. Maximized, Qt's client area
  runs a physical pixel past the work area, so the rule's last pixel row can sit under the
  taskbar. **Never answer `WM_NCCALCSIZE` to fix that**: answering it with the work area
  left Qt's geometry at the restored size and froze the whole window, opened maximized on a
  4K screen at 125%, until it was dragged out. `native_frame.py`'s docstring has it.
- **Probing it from another process needs per-monitor DPI awareness.** A DPI-unaware sender
  has the coordinates it sends scaled by the display scale, so at 125% every answer lands
  1.25x inside where it should, and a posted click misses the window. Call
  `SetProcessDpiAwarenessContext(-4)` first.

## Buttons: `ActionButton` and its four weights

Use `ActionButton` (`widgets/buttons/action_button.py`), not a bare `QPushButton`. Fusion's
default chrome is a beveled 3D frame sized to its own label, which flattens SVG icons
against it and makes a row of buttons size each one to its own text.

`ActionStyle` says how much weight a button carries, on Bootstrap's model and for its
reason — a row of buttons cannot say which one it expects unless they are drawn at
different weights:

| style | look | for |
|---|---|---|
| `PRIMARY` | filled with `accent` | the one thing the surface expects you to do |
| `SECONDARY` | outlined in `accent` over a faint wash of it | a way forward that is not the commit |
| `TERTIARY` | no accent — **the default** | everything else |
| `CAUTION` | filled with `error` | a verb that costs something to undo |

Five rules that are easy to get wrong:

- **`TERTIARY` is the default, and a style is opted into.** Most rows here are
  equal-weight verbs beside a destructive one (`+ New` / `Duplicate` / `Delete`), and
  nothing in such a row should look more important than the dangerous button next to
  it. At most one `PRIMARY` per surface — but a `SECONDARY` does not imply a `PRIMARY`.
  Most of them sit on surfaces that have no commit at all (`assignment_card`'s *Save as
  Player Strategy…*, the toolbar's *New Bot*): the weight means "worth reaching for, but
  not the default thing to do here", which needs nothing beside it to be true.
- **A weight varies color and nothing else.** Every style shares the padding, the
  radius, the height and one disabled look. A style that also changed the shape would
  stop being a weight. That rule is why `SECONDARY` carries a wash rather than a 2px
  outline: drawn unfilled it read as a tinted `TERTIARY` on the light theme's near-white
  field, and thickening the border to fix it would have been a shape change.
- **A disabled button has no outline.** Its border is drawn in `surface_disabled`, the
  same color as its fill, because an enabled `TERTIARY` (`surface`) sits only ~3% off
  that fill — losing the outline is what actually separates off from on. It matters most
  on the inspector surfaces, where most buttons start disabled.
- **Derive, never hardcode.** `SECONDARY`'s wash is mixed from `THEME.accent` over
  `THEME.bg` at style time, and how much of it shows through is read off `bg`'s own
  lightness. A custom light theme is not required to be `#ffffff`, so a literal
  comparison there would silently give it the dark theme's wash.
- **`fill=False` for a trailing pair.** These expand by default so a row of them splits
  its width evenly. A dialog's `[Cancel] [Save]` sits *after* a stretch, and an
  expanding button there eats the stretch and spreads the pair across the whole footer.

BartleBot's issue #47 is the rollout; its `gui/combat_strategies/dialog.py` is the worked example.

`bare_buttons`, `extra_primaries` and `compound_widget_qss` (`testing/guards.py`) keep it
from eroding: they fail on a bare
`QPushButton` that is not on its allowlist, on a second `PRIMARY` in one module, and on
QSS that targets a compound widget's subcontrols. Every allowlist entry carries the
reason it is exempt, and the failure prints it — so adding one is writing down a
decision, not silencing a check.

### A dialog's footer comes from `dialog_footer()`

Never lay a `[Cancel] [Save]` row out by hand, and never reach for `QDialogButtonBox` —
none is left under BartleBot's `gui/`. `dialog_footer()` (BartleBot's `widgets/layout/dialog_footer.py`) builds
`[extras…] <stretch> [Cancel] [Commit]` and returns the buttons, so a dialog that
disables or retitles its commit still can.

What it is really for is the keyboard. `QDialogButtonBox` owned the platform button
order, the Esc wiring and the default button, and the last of those is a trap: Qt hands
"default" to the first `autoDefault` button *constructed*, and a button acquires
`autoDefault` when it is reparented into a dialog. So the helper sweeps every other
button in the dialog **on show**, not at build time — at build time most of a dialog's
widgets are still parentless, sitting in layouts nobody has installed yet.

Two things follow:

- **The reject slot is "the button that does not commit", not "the quiet one."**
  `recovery_dialog`'s Discard All takes that slot at `CAUTION`, because it permanently
  drops every recovered unsaved bot file.
- **`safety_warning_dialog`'s Run Bot is `TERTIARY` on purpose**, the one commit in the
  app that declines the accent: that dialog exists to make the user hesitate, and
  filling the button that dismisses a safety warning argues with its own purpose. It is
  still the default button — Enter runs. Raise it on issue #89 before changing it.

BartleBot's `tests/gui/widgets/layout/test_dialog_footer.py` presses Enter and Esc on every converted dialog.

### What is *not* an `ActionButton`

`ActionButton` is specifically *a labeled verb that expands to share a row*. Roughly a
seventh of this app's buttons are none of those, and forcing one into the action
vocabulary stretches a 22px affordance across a toolbar. Two other named classes cover
them, and both follow the same conventions — `ThemedMixin` first in the base list, all
`THEME` work in `apply_theme_styles()`:

| widget | look | for |
|---|---|---|
| `IconButton` (`widgets/buttons/icon_button.py`) | fixed square, glyph, no label | a remove `✕`, a close `×`, a help `?`, a favorite star |
| `LinkButton` (`widgets/buttons/link_button.py`) | accent text, no frame, underline on hover | a control sitting inline in prose or under a card |

Three things about them:

- **An `IconButton`'s tooltip is a required keyword argument.** It carries no label, so
  without one it is unusable to anyone who does not already recognize the glyph, and
  making it required is what stops that being an oversight rather than a decision.
- **`IconTint` varies color and nothing else**, the same rule the four weights follow:
  the size is the caller's to choose, because a toolbar's 44px and a flag row's 24px are
  both right. `DANGER` is what makes a remove read as one; `ACCENT_WHEN_ENABLED` is the
  Option Values gutter's revert, where *being* enabled is the signal.
- **A `LinkButton` underlines on hover, not always.** The accent alone says "clickable"
  while the pointer is elsewhere.

Four `QPushButton`s stay bare on purpose, and the reason is the same for all of them:
**their background is the data they display.** Any shared class writes `background`,
which paints over the one thing they exist to show. Those are BartleBot's color swatches in
`settings/theme_tab.py`, `canvas/param_panel.py` and `canvas/edit_scene.py`, and
`bot_canvas`'s `_warning_pill` — a status pill that happens to be clickable.

**`theme_tab.py`'s "Reset to System Default" is the last one, and it is the sharpest
case**: it keeps a hardcoded stylesheet so that it stays legible *whatever* the user has
done to the running theme. It is the escape hatch from a custom theme that has made the
app unreadable, and `ActionButton` derives everything from `THEME` — which is precisely
what would break it. Leave the literal colors and the comment above them alone;
BartleBot's `tests/gui/widgets/buttons/test_surface_button_weights.py` says so too.

## Section headings

Four, and they are not interchangeable:

| widget | look | for |
|---|---|---|
| `SidebarSection` | disclosure triangle + bold label | a region that **folds** |
| `OptionCard`'s band (BartleBot's `sidebar/option_card.py`) | bordered card, surface band, muted triangle + semibold title | a **group of options that folds**, inside a section |
| `CardColumn`'s heading | centered bold label + full-width rule | a **column of cards** |
| `RuleHeading` (BartleBot's `widgets/labels/rule_heading.py`) | left bold label + rule to the right | the regions of a **form** |

A triangle promises a fold, so do not use `SidebarSection` for a region that does not
have one; a centered heading over left-aligned fields reads as belonging to something
else. `RuleHeading` draws its rule from `THEME.border` rather than as a sunken `QFrame`
on purpose — Qt draws a sunken HLine from the palette's Mid/Light pair, which the dark
theme leaves all but invisible against `surface`.

It also carries its own leading gap, more above than below, so a surface using one gets
the rhythm without setting a spacer — and so the rule sits nearer what it introduces
than what it follows, which is what makes it read as a heading rather than a divider.

## Lines need room, or they read as walls

The commonest way a screen here looks crowded is not too many lines; it is lines with
nothing beside them. Two rules:

- **A line earns its place by separating things that are otherwise ambiguous.** Chrome
  *around* a group — a border on the list widget holding a column of cards, the box
  `QTabWidget::pane` used to draw around a whole page — separates nothing: the cards
  draw their own edges, and a splitter handle already says where a pane stops. The pane
  keeps only its `border-top`, which is load-bearing (it anchors the tab strip). Borders
  on the individual elements *stay* — a card, a chip, a field is a thing, and the outline
  is how it says so.
- **Inset the contents of anything bordered.** A tab page whose fields run edge to edge
  inside the pane reads as crowded however few lines are on it. `CombatStrategyDialog`
  is the worked example: a page inset on all four sides, and a wider gutter on the
  detail pane, because the gap the eye sees beside a splitter handle is the handle plus
  the margin while the gap beside a border is only the margin.

## Account colors, and what a colored thing may claim

A palette slot per account (`bartleby/shared/account_slots.py`), resolved to a color against the
live theme (`theming/account_accent.py`). It is a **match target** — find the card wearing
the same hue — never an identifier, and three rules keep it one:

- **Hold the slot, not the hex.** A widget that stores `account_accent(3)` repaints
  stale after a theme switch, with nothing in any stylesheet to grep for.
- **Outline and tint, not a saturated fill.** Four filled pills in a row make the row
  the loudest thing on the screen when the point of the surface is a *name*. See
  `AccountPill`.
- **No account known means no color, never a default one.** A color is a claim about
  which account is behind something; picking one for an unidentified client, or for an
  account that has been deleted, puts a hue on screen that matches nothing anywhere
  else. `account_stripe_keys` returns `None` for exactly these, and callers must keep it.

A **player slot** is colored but never renamed: `P1` stays `P1` on the pill and the
combat-order chip, with the account's name in the tooltip. A `DuelConfig` stores slots,
is authored offline and outlives whichever account is in slot 1 tonight — a pill reading
`Farmer` would be a claim the stored data does not make, and the same card would read
differently depending on what happened to be hooked. Issue #40 is the rollout.

On the Combat tab every `P#` is a `PlayerBadge` (BartleBot's `widgets/pills/player_pills.py`) — the deck
cards, each assignment line and the combat order — and each deck card carries the same
left stripe as the player card for its slot. Colors are held **per slot, not per
position**, so a `P3 → P1` order puts slot 3's hue first. The tab polls its account
source on a timer (`PLAYER_COLOR_REFRESH_INTERVAL`) only while it is visible, because an
identity arrives after its client and nothing announces it; a card built between ticks
takes the last answer at construction, since a tick with an unchanged answer repaints
nothing.

The Players tab's `P#` pill is the same `PlayerBadge`, keyed off the card's stripe. The
game view's tile caption is painted (`client_tile.tile_pill_colors`) in the same capsule
shape and colors at its original size, and a focused window's border is its account's
color, falling back to the accent. `GameView.sync_tiles` pushes the key from
`TileSlot.username` on every tick, alongside the captions.

## Never show a widget before it has a parent

`setVisible(True)` — or `show()` — on a widget with no parent makes that widget a
**top-level window**. Qt maps it there and then, and unmaps it again the moment the
next `addWidget` reparents it, so a list of rows assembled this way flashes one real
frame per row on screen. It is invisible in a test that only inspects the finished
tree, because by then nothing is a window any more.

Build first, then set visibility:

```python
row = build_gutter_row(bar, edit_btn, help_btn, body)   # this is what parents them
edit_btn.setVisible(self._editable)                     # only now
```

**A widget in a layout nothing has installed yet has no parent either.** `addWidget`
on a free-standing layout parents nothing; the widgets in it get a parent only when
that layout is set on a widget, or added to a layout that already is. So the builder
in the example above is only one way to get here. The other is three lines apart in
one constructor:

```python
text = QVBoxLayout()                 # free-standing
text.addWidget(self._blurb)          # still no parent
self._blurb.setVisible(bool(blurb))  # a window, once per card
row.addLayout(text)                  # too late
```

Attach the layout before putting anything in it (`row.addLayout(text)` first), or pass
the parent to the layout's constructor. The New Bot dialog flashed one window per
template card this way.

`setVisible(False)` is safe, and so is showing a child of a hidden parent — only the
parentless case opens a window. It bites hardest where a rebuild is on a hot path:
one click of a check box in Edit mode mapped and unmapped **eight** windows on a
shipped bot, because each row's description and Edit button were shown while still
loose. `test_building_a_row_never_shows_a_parentless_widget` is the guard, and the
way it catches it is the `parentless_shows` fixture in BartleBot's `tests/conftest.py` — an event
filter on the `QApplication` recording every `Show` delivered to a widget whose
`parent()` is `None`. **A new dialog, card or row builder gets a test that asks for
it**, builds the surface and asserts the list is empty; reading the code for this bug
has missed it more than once.

The same window opens on the way *out*. **Discard a widget with `discard_widget`
(BartleBot's `widgets/layout/discard.py`)**, never a bare `setParent(None)` + `deleteLater()`: a widget
shown earlier in the same event-loop turn still has that show to come, and once it is
unparented the show maps it as a window. Import all redrew each deck card once per slot
in one turn, and every art tile from the discarded redraws flashed in the corner (#120).
The helper hides first, which is the whole fix. `test_discard_widget.py` guards it.
When a flash appears that you can't place, run with `BARTLEBOT_DEV=1`:
BartleBot's `gui/debug/window_show_trace.py` logs each stray window's class and geometry, which usually
names it, plus the Python stack. A deferred show has no Python caller, so for one of those
the stack ends at the event loop. It also logs windows shown by the game processes.

The inverse trap sits one line away: **a hidden widget in a `QBoxLayout` reserves no
space at all.** The layout skips it outright rather than laying out a zero-size cell,
so a spacer or placeholder that is parented but never shown holds nothing open and
every widget after it lands a row early — with the layout, the size hints and the
geometry all internally consistent about the wrong answer, which is why it reads as an
arithmetic bug rather than a visibility one. `DragReorder._begin_drag` is the case:
the placeholder standing in for a lifted card has to be `show()`n by hand, after the
parent it was constructed with. (`setRetainSizeWhenHidden` is the other way, and is what
the launch cards' hover-revealed buttons use — but it only applies to a widget the layout
is still asked about.)


## Game art is optional, and missing is the ordinary case

Spell art is the game's own and is never in this repo: the app extracts it on the user's
machine (BartleBot's `game/art_extraction.py`, from setup's last page or Settings › General › Game
art), and `bartlebot/game/card_art.py` is the only thing that knows where it lands.
**Every lookup can return `None`** — a user can turn the art off in Custom setup, an
extraction can fail and setup still finishes, an install from before setup asked has none
until the user extracts it, a few templates name a texture the game's own files do not
contain, and the names snapshot can be older than the game. Setup offering the art made
none of this rarer to plan for. So a surface showing art needs a form that
works without it, and must never log the absence as an error.

`_DeckCard` is the worked example: the strip is hidden outright when nothing resolves,
the summary line carries the counts either way, and a single card with no art is
skipped rather than given a placeholder — a placeholder per missing card turns an
install with no art at all into a grid of gray squares.

**A deck is listed by `DeckCardList` (BartleBot's `widgets/cards/deck_card_list.py`), on both surfaces
that list one** — the sidebar card's unfolded rows and the dialog's Deck Presets page.
It was the sidebar's own rows against `QLabel(f"  {name} × {count}")` off the raw dict,
so the library page listed a deck unsorted, unheaded and unillustrated while the
sidebar three panels away did all three. The grouping and the order are
`deck_groups`' on both, never the surface's, and a row whose card has no art is the
row minus its tile — the count and the name still carry it.

Look art up by the **template name** (`deck_io`'s own key), never the display name: one
display name covers many templates, and a tiered variant draws its base spell's
texture, so two identical-looking tiles can be different cards. That is what the tile's
tooltip names the template for.

The same cache holds the school and archmastery icons, under `card_art.icon_path` and
logical keys rather than texture names. `ArchmasteryBadge` is the worked example, and
the fallback is the same shape: the deck card keeps its `Archmastery: Fire · always
set` line, on a row of its own, for every install that has not extracted them. Two
rules it is easy to lose:

- **Both icons or neither.** A lone archmastery spiral says the deck has an opinion
  without saying what it is, so `has_icons()` is false unless the mark *and* the
  medallion both resolved — which also covers an icon set missing the one school this
  deck happens to name.
- **The medallion names the school only when the school will actually be set.** A deck
  can carry a school it will never apply: the override resolves through an app setting
  whose shipped default is off. A Fire medallion on a card whose apply leaves
  archmastery alone is a claim about the wizard that the deck does not make, so the
  universal mark stands in. That means the badge asks `resolve_apply_archmastery`, not
  `deck.archmastery_school` alone.

The bot library's category list uses these too, under the same rule: each folder under
`bots/` is a game badge category (`bartlebot/content/bot_folders.py`), the row wears that
category's badge when one was extracted, and a row with none is its caption alone — at
the compact height, since only a row with an icon reserves room for one. Rows are looked
up by the folder name stored in `Qt.UserRole`, never by the caption, which comes from the
game's own wording.

Bot tags use them too (`card_art.tag_icon_path`, drawn at `TAG_ICON_PX` = 20 — 16 was
too small to read). Each surface degrades to what it was before: a picker row shows its
group dot, and a `TagChip` or `TagPill` its plain text form. With a medallion, both are
**drawn** by `paint_medallion_pill`: exactly `TAG_ICON_PX` tall, so the medallion *is* the
rounded left end, and the group-colored body starts at its center and carries the name.
An icon-only pill was tried and dropped, because the art alone was not enough to read a
card by. A stylesheet cannot do this shape: padding and border-radius will not put an icon
flush with a rounded end. The Type tags have no art, so they are always text, and a mixed
row is the normal case.

Drop categories use them in the Category column of both drop tables (Possible Drops and
the quicksell keep-list picker), through `drop_tree.drop_icon`, drawn at `DROP_ICON_PX` = 20
for the same reason as the tags. The icon sits beside the category text, never in place of
it, and an uncategorized row or an install with no art keeps the text alone. A pin row's
icon depends on its name (`card_art.drop_icon_name`), so pass the item name as well as the
category.

**The combat order is drawn right to left, and it is not a style choice.** A duel puts
four wizards around a circle and turns run clockwise, which puts a team's own players
right to left: slot 1 is the rightmost, and the game's own "Set Sigil Slot Preference"
dialog lists 4, 3, 2, 1 in that order with a gold gem on each. BartleBot's `duel_slot_gem.py` holds
the reasoning and the lookup; `CombatOrderWidget` (where an order is set) and
`PlayerBadgeRow(duel_order=True)` (where it is summarized) both mirror, and
`strategy_summary.format_combat_order` mirrors the text with them. **All four have to
agree** — a half-mirrored app is worse than the unmirrored one, because two surfaces then
disagree about which end enters first.

Only the *drawing* is mirrored. The value stays an ordinary first-to-last list everywhere
it is stored, passed or read, and the reversal happens once, at the point of layout. A
widget that also reversed its value would silently invert every bot file it saved.

Three things follow, and each has already been got wrong once in review. The gem names the
**position**, not the player in it, so on a reorder the gem stays put and the `P` number
moves. Hiding slots down to a duel's `max_players` hides them **by slot number**, not by
layout position — hiding "the last few" cells hides slots 1 and 2, the two a two-player
duel actually has. And the ordinal text stays beside the gem: the symbols are the *game's*
vocabulary, nothing on screen teaches them, and most installs have no gem at all.

**Art is a `QIcon` holding the full-size image, painted with `QIcon.paint`** — never a
`QPixmap` scaled to the logical size in advance. `card_art_tile.art_icon` is the one way to
load it. Users run any display scaling, and one machine can mix two (125% and 100% here), so
the resolution has to be chosen as each screen paints: `paint` asks for the logical size
times that device's pixel ratio and Qt scales down from the original, caching per ratio. A
20px pixmap is stretched to 25 physical pixels at 125% and drew soft with a jagged ring;
one pre-scaled for a single ratio is wrong on every other. So lay out by the size constant
(`TAG_ICON_PX`, `ROW_ICON_PX`, `SECTION_ICON_PX`), not by the image's own size, and where a
`QLabel` would have held the pixmap use `ArtIconView`.

An icon is artwork in its own colors, so a widget drawing one reads no `THEME` value
and needs neither `ThemedMixin` nor a `changeEvent` — the guard test agrees, and this is
the same exemption the card art tiles take.

### Art extracted while the app is open

Art can arrive while everything is on screen: setup's Game art page runs straight after an
install, with the bot library behind it, and Settings › General › Game art runs again after
a patch, with Settings open behind it. `extract_art` ends with `card_art.forget_cache()`, so
every *later* lookup sees the new files, but a widget that looked once and drew what it
found keeps it. So `ArtExtraction` announces **`art_changed`** once per run that wrote art
(`bartlebot/gui/art_signal.py`, #239), and a surface that draws art follows it with
`follow_art(self, redraw)`. A failed or stopped run announces nothing. `card_art` stays
free of Qt; the signal is in BartleBot's `gui/`.

- **The widget that chooses between art and text is the one that follows**, and it reruns
  its own fill method. A deck card shows its `CardArtStrip` or its summary line and its
  `ArchmasteryBadge` or its sentence, so `_DeckCard` follows and reruns `set_deck`; the strip
  and the badge do not follow on their own, or the card would keep showing the text form
  beside them. The same goes for a `TagPill` or `TagChip`, which is drawn in one form or the
  other when it is made: `TagsField` and the bot library rebuild theirs.
- **Rows that hold an icon are given it again.** `TagPickerButton` and `SchoolPickerButton`
  rebuild their sections, counts kept, and both drop tables re-icon each row in place
  (`drop_tree.follow_drop_icons`), so a sort, a selection and the keep list's ticks stay.
- **A surface built afresh each time it opens needs no connection.** The zone and Scales
  pickers resolve their section icons as each popup opens, and the New Bot, Templates and
  node browser dialogs look art up as they are built. The pickers and New Bot have a test
  each showing that a later open wears art extracted after the button or dialog was made.
- **Follow through `follow_art`, never by connecting to the signal directly.** It hangs the
  connection on a child `QObject`, so it ends when the widget is destroyed, and catches
  what the redraw raises, since a slot that raises aborts the process under PyQt6.

## A widget that wraps has to say so in its size policy

`FlowLayout` wraps correctly and the parent still reserves **one row**, because a
`QBoxLayout` asks the *size policy* whether an item's height depends on its width, and
a widget that has not said so answers no. The deck card's art strip laid out fourteen
tiles, drew seven and clipped the rest, in a card exactly as tall as a five-tile one —
with the layout, the size hints and the geometry all internally consistent about the
wrong answer, which is why it reads as a painting bug rather than a layout one.

Three things together, or none of them work: a `QSizePolicy` with
`setHeightForWidth(True)`, a `heightForWidth` that delegates to the layout, and a
`sizeHint` that answers for the width the widget *has* — `FlowLayout.sizeHint` is one
row of everything, which is what it would like rather than what it needs.
`test_the_strip_reports_a_taller_height_when_it_has_to_wrap` is the guard.

## A scroll area whose content grows under the user reserves its gutter

An as-needed vertical bar takes its width out of the viewport when the content
overflows and hands it back when it shrinks, so every card in the pane narrows and
widens by the bar's width — on a folding card, under the very click that folded it
(#112). A scroll area of cards, or of a form with sections that fold, calls
`reserve_scrollbar_gutter` (BartleBot's `widgets/layout/scroll_gutter.py`), the Qt stand-in for CSS
`scrollbar-gutter: stable`. Text editors, trees and tables keep the as-needed bar:
re-wrapping text costs nothing, and a permanent gutter there only costs width.

Do not "simplify" it into a viewport margin toggled with the bar's visibility. Qt
decides whether the bar is needed against the viewport *less its margins*, so the two
states measure different widths and content in the gap between them loops; the module
docstring has the detail.

## The first offscreen render costs 150-200ms

A widget with a `QGraphicsEffect` on it, and a `QWidget.grab()` or `render()` for a
`QDrag` pixmap, are all painted into an offscreen surface rather than into the window.
Qt flushes the whole top-level's withheld move and resize events before the first such
render — it holds them back from hidden widgets, so every unopened tab, menu and dialog
page built at startup is sitting on a layout that has never run, and they all run at
once. It is the same 150-200ms whether the target pixmap is full size or **1x1**: the
cost is the layout flush, not the paint. The second one is free.

Left alone it lands wherever the first offscreen render happens to be, which is a
gesture — the first card drag of a session stalled for a fifth of a second while the
shadow went on, and no later one did. Call `warm_offscreen_render(widget)`
(BartleBot's `gui/offscreen_warmup.py`) a turn after the surface is shown instead; it is per window,
because the flush is, so a dialog does not inherit the main window's.
`DragReorder.watch_for_show` is the pattern, and every card list that drags gets it. It is per window *and* once: the flag means the layouts outstanding at
that moment ran, not that the window is immune. Nothing warms the other offscreen sites
(`favorites_bar`, `combat_order_widget`, `duel_strategy_editor`) — if one ever stalls,
warm its window; do not add a call per widget class.

## qasync + Python 3.13 task re-entrancy rule

**Never call a blocking Qt method (e.g. `QDialog.exec()`, `QMessageBox.exec()`) from inside an `async` function that is running as an asyncio Task.**

Python 3.13 enforces that only one asyncio task may be executing at a time. `QDialog.exec()` re-enters Qt's event loop synchronously, but the calling task's frame remains on the Python call stack. While the dialog is open, any asyncio sleep or future that completes will attempt a `task_wakeup()` callback. Python 3.13 detects the calling task is still "being executed" and raises `RuntimeError: Cannot enter into task while another task is being executed`. qasync catches and logs this error, but the woken task is permanently stuck — its sleep Future is consumed and it never resumes.

**Rule:** If you need to show a blocking dialog before starting an async operation, do it in the synchronous Qt slot *before* calling `asyncio.create_task()`. Example pattern in BartleBot's `AppController._on_run_requested()` — the safety warning dialog runs there, in the slot, so no asyncio task exists yet when `.exec()` blocks.

A coroutine that has something to say afterwards defers it: `QTimer.singleShot(0, lambda: ...)`, a
queued signal, or `QMenu.popup` rather than `exec`. `test_no_coroutine_opens_a_modal_itself` reads
every `async def` and fails on a modal opened in its own body.

This bug caused the client section sidebar (zone/XYZ display) to freeze for the entire duration of any bot run that included a node with `safety_warning` set in its `NodeSpec`. The `SafetyWarningDialog.exec()` call inside the async `_start_bot()` task was killing the `_refresh_loop` and `_client_loop` tasks on every run.

## Background task lifetime in GUI widgets

Any `asyncio.Task` started inside a `QWidget` or `QDialog` must be explicitly cleaned up in `closeEvent` to prevent callbacks from firing on a destroyed widget.

Pattern for asyncio tasks:
```python
def __init__(self):
    self._my_task: asyncio.Task | None = None
    self._closed = False

def closeEvent(self, event):
    self._closed = True
    if self._my_task:
        self._my_task.cancel()
    super().closeEvent(event)

async def _some_async_method(self):
    if self._closed:
        return
    # ... do work ...
```

Blocking work goes off the GUI thread through `asyncio.to_thread` inside such a task, never a
`QThread`: Nuitka documents Qt threading as unsupported under PyQt6, and the packaged build
must not depend on it (BartleBot's `docs/RELEASING.md`). `test_no_qthread_remains_in_bartlebot` keeps it out.
Cancelling the task does not stop the thread; it only makes sure nothing it returns is shown.

## How big a game window is, and what will not fit

`docs/WINDOW_SIZING.md` is the file. The short form, because both halves are easy to
undo by accident:

- **Resolution and UI size are one coupled setting.** The HUD is fixed-pixel, so a
  resolution below 800x600 needs the game's own UI scale clamped down to match, and the
  pairing is `bartleby/runtime/game/game_sizing.py`'s ladder. `Resolution` is written to
  `preferences.xml`, `UISize` only to `config.xml`; neither file holds both. The user's
  own UI scale is captured **once** into `settings.json` and given back on quit — never
  re-read from `config.xml`, which after one session holds our clamp rather than their
  preference.
- **A configuration that will not fit cannot be launched**, and the entry that cannot
  launch is **disabled with the reason, never hidden**. `bartlebot/gui/screen_fit.py` is
  the one place that asks; the surfaces that must agree are `ProfileCard`/`AccountCard`,
  the Launch menu, `LauncherPopup`, and both resolution combos (`accounts_tab.py` and
  `profile_editor.py`). Disabled where it would *launch*, though (the Quick Launch
  screen, where a click selects and the Launch button or a double-click launches): the same cards
  draw the Game settings tab and the profile editor, where a click edits or does nothing
  at all and the refusal is a note instead. That is the card's `CardMode` and the
  `refuses_when_unfit` that reads it, never a fresh test of the fit result.
- **The user never scrolls to see a game window.** Screen space caps the game size
  (configured layout, not the panel minimums), and `minimum_window_size` runs the other
  way to keep the window and the splitters off the game. Both are kept; they are not
  alternatives.
- **The game is DPI-aware only because we start it so.** It declares no awareness, so
  Windows stretches it on a scaled display. Every launch goes through
  `launch_profile.start_instance`, never wizwalker's, and a window that is scaled anyway
  is held detached under a `ScaledTile`, never embedded (`bartleby/shared/game_dpi.py`).
- **The budget is the larger of the configured layout and the *live* game area.**
  Sidebar widths and the canvas height are startup seeds — nothing persists a splitter
  drag — so without the live term a user who makes room by dragging is still refused.
  Re-judging on a resize calls `set_unavailable` on the cards that already exist; it must
  never rebuild them, or a drag destroys and recreates every card several times a second.

## Game view slots vs. player indices

A tile's position and a tile's caption come from **two different numbers**, and conflating
them is the bug class this split exists to prevent. The full reasoning is in
`bartleby/runtime/clients/tile_slots.py`; the short form:

| | is a position in | pinned by | decides |
|---|---|---|---|
| **slot** | `ClientManager._client_order` | `reserve_slots`, before any window exists | *where* a tile sits |
| **player** | `ClientManager.clients` (hooked only, compacted) | whichever clients are in game right now | what the tile is *called* |

They agree only while every account in the ordering is in game. Any window at character
select, crashed, or unhooked by hand pulls them apart.

**Which one to look at when something is wrong:** a tile in the wrong grid cell, or one
that moved when it should not have, is a *slot* problem — `_client_order` and
`build_tile_slots`. A wrong caption, or a wizard name on the wrong tile, is a *player*
problem — `ClientManager.clients` and whatever compacted it. A game window that flashes or
pops out of the grid is `plan_tile_changes`, which exists so a slot change is a re-key and
never a re-embed.

Things to preserve:

- **A window is embedded as soon as it is identified, not when it is in game.** Embedding
  is `SetParent` and `ClientTile` takes an hwnd; the account comes from the *window title*,
  which the launcher sets right after login. That is what makes `auto_select_character`
  off usable: every window arrives in the game view at character select and the user picks
  characters in one place. It is also why `_refresh_account_map` runs over
  `self._handler.clients` rather than the hooked list.
- **A window that is not in game has `player is None`, and is captioned by its account.**
  Not a fallback — a bot cannot route to it, so it is not Player anything yet. Do not
  invent a number for it; the sidebar and the FSM would disagree with it immediately.
- **`ClientManager.clients` stays hooked-only and contiguous.** Bot nodes address players
  by position in that list. A hole in it is not expressible, which is why the slot index
  could not simply replace it.
- **`_sync_tiles()` runs every detection tick, not only when the client list changes.** A
  window reaching character select changes no client list — it is not a client — and it is
  precisely the window that needs to appear. `sync_tiles` is a no-op when nothing moved.
- **Vacate every moving slot before filling any.** A player swap moves two tiles at once;
  clearing them one at a time throws away the tile the previous move just wrote. That bug
  was live in the first draft of `_apply_moves` and the swap test is what caught it.

## Client section refresh architecture

`ClientSection` (`bartlebot/gui/sidebar/sections/client_section.py`) is a plain `QWidget` filling `RightSidebar`'s Players tab, and runs a long-lived asyncio task (`_refresh_loop`) that polls zone/XYZ data for each connected client. It holds a `CardColumn` of `PlayerCard`s for the hooked clients and a `SidebarSection` of them for the unhooked, and it supplies the Players tab's *only* scroll area — the column's. Key design points:

- **Refresh rate is adaptive**, decided by the pure `client_refresh_interval(bot_running, players_visible)`: `CLIENT_REFRESH_INTERVAL_IDLE` (0.1 s) while idle, `CLIENT_REFRESH_INTERVAL_RUN` (0.5 s) during a bot run, and `CLIENT_REFRESH_INTERVAL_HIDDEN` (2.0 s) whenever the Players tab is not the current tab — visibility is checked *first*, so a hidden tab during a run polls at the slowest rate. `AppController._start_bot()` calls `set_bot_running(True/False)` at the bot lifecycle boundary; `RightSidebar.currentChanged` calls `set_players_visible(True/False)`, which restarts the loop on becoming visible rather than waiting out the in-flight 2 s sleep. Polling never stops entirely, because `AppController`'s zone-dropdown getter reads each widget's cached zone via `client_zones()`. These constants live in `bartleby/runtime/timing.py`.
- **Per-widget timeout**: each `PlayerCard.refresh()` call is wrapped in `asyncio.wait_for(timeout=CLIENT_REFRESH_TIMEOUT)` inside the gather so a single slow client read can't stall the whole cycle.
- **Loading guard**: `PlayerCard._refresh()` checks `is_loading()` first and returns early if True, skipping the more expensive `zone_name()` and `body.position()` reads that produce stale/slow results during zone transitions.
- **WizWalker reads are synchronous**: despite the `async def` signatures, WizWalker memory reads (`zone_name()`, `body.position()`, etc.) call `ReadProcessMemory` synchronously on the event loop thread — they are not truly non-blocking. Keep this in mind when reasoning about latency or contention.
- **The wizard name costs nothing**: `PlayerCard._refresh()` reads it from `wizard_names.cached_name()`, a dict lookup. `ClientManager._client_loop` is what fills that cache, once per character. Do not make a sidebar label trigger detection — `resolve_names()` can open the character page, which would flash the game UI every refresh.
- **Two things besides the values are re-derived every tick**: the account stripe, because `ClientManager.account_for` learns an identity inside the client loop and signals nothing when it does; and nothing else. Anything resolved once in a card's constructor is resolved before the client is identified.
- **Cards are re-keyed, not rebuilt.** `set_all_clients` holds each card against its `hwnd` and re-uses it while that window is still in the same bucket, calling `set_index` rather than constructing a replacement — otherwise a drag-reorder resets the freecam toggle and blanks every row until the next tick. A client that crosses between hooked and unhooked *is* rebuilt: whether a card has a drag grip is fixed at construction.

## Every drag-reordered list takes the keyboard too

A drag is not reachable from the keyboard, so every list `DragReorder` hosts also takes
**Alt+Up / Alt+Down** and offers **Move up / Move down** first in a card's right-click menu
(#155). Both go through `DragReorder.move`, the path a drop takes, so a host needs nothing
for it; `set_cards` hands each card the route (`CardFrame.set_reorder_route`). A new card
list gets it by using `DragReorder` -- do not give one its own arrows.

- **The route holds the engine and the card weakly.** A card keeps its route, and a strong
  reference closed a cycle the collector broke by clearing the engine's attributes while
  Qt still had it filtering the grips' events -- an `AttributeError` in `eventFilter`, in
  an unrelated test, only under `-n auto`.
- **A Move entry's keys are text in its label** (`add_move_actions`), never the action's
  shortcut: the card already owns Alt+Up as a `QShortcut`, and two bindings for one key
  are ambiguous, so Qt fires neither.

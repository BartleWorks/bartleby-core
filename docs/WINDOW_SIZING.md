# Window sizing

How big a Wizard101 client is launched, why that is one setting and not two, and why an
unfittable configuration cannot be launched at all. The code is `bartleby/runtime/game/game_sizing.py`
(the arithmetic, pure) and `bartleby/qt/screen_fit.py` (the same question asked from a
widget, with a real screen behind it), each reading the product's panes as a list: BartleBot's
are its `game/pane_sizing.py` and `gui/window_panes.py`. Read this before changing any of
them, or before adding a resolution to `VALID_RESOLUTIONS`.

The problem it solves: at 800x600 — the lowest the game's own options menu offers — two
embedded clients do not fit side by side on a 1920x1080 screen, so the whole game-embedding
feature was unusable for a 2-4 client user on a standard monitor.

## The engine has no 800x600 floor; its options menu does

`Resolution` is a free-form `WxH` string in `preferences.xml` and the engine renders
whatever is in it. Verified against a live client by measuring `GetClientRect`:

| requested | measured client |
|---|---|
| 800x600 | 800x600 |
| 640x480 | 640x480 |
| 512x384 | 512x384 |
| 400x300 | 400x300 |

**If you repeat that measurement, do it from a process with per-monitor DPI awareness
set** (`SetProcessDpiAwarenessContext(-4)`). A DPI-unaware process on a scaled display
reads back virtualized coordinates — on a 125% display every result comes back at exactly
0.8x, *including the 800x600 control*, which looks convincingly like the engine clamping.
Qt sets awareness itself, so app code such as `ClientTile.game_size` already measures true
physical pixels.

That table also says the engine's `Resolution` is *physical* pixels, which is why
`tile_size` divides it by the screen's scale factor — a 2048x1536 client is 1638x1229
logical points of tile at 125%. It holds only because the client runs DPI-aware. The exe
declares no `dpiAware` manifest entry and imports no `SetProcessDPIAware`, so on its own
Windows runs it unaware and stretches it: 2560x1440 asked for at 150% became a 3840x2160
window, the whole of a 4K screen, and embedded it showed nothing. A fresh Windows account
found it while testing 0.1.0; the machine the table was measured on had the per-exe
compatibility flag set by hand (`HKCU\...\AppCompatFlags\Layers` -> `HIGHDPIAWARE`, the
"Override high DPI scaling behavior: Application" checkbox).

`shared/game_dpi.py` holds both ways to make it aware, and the rules:

- **Every client the app starts is aware.** `launch_profile.start_instance` starts it with
  `__COMPAT_LAYER=HIGHDPIAWARE` in its environment, which Windows applies to that process
  alone. It replaces wizwalker's `start_instance`, which ran the same command in the
  app's environment.
- **The user's own setting is never written unasked.** The flag would make every start
  aware, however started, but it also shrinks the game's interface when the user plays
  outside the app.
- **A scaled window is never embedded.** A game started some other way can still be
  scaled, and nothing makes a running process aware. `GameView` holds such a window as
  detached, where it draws correctly, and its slot shows a `ScaledTile`: why, and a button
  that sets the flag for the next start.

## Resolution and UI size are one setting

The HUD does not scale with the window. Shrink the resolution alone and the interface
keeps its original pixel size and overflows the client area. The compensation is the
game's own UI scale, which goes down to **Micro** — a step below anything the in-game
options menu offers.

So 400x300 *renders* correctly paired with Micro — every control draws, including the
Archmastery selector — and is broken at the default scale. The two must never be
independently selectable, or users land in the broken combination.

The verified render ladder — exactly 80x60 apart, all 4:3:

| UI size | `UISize` value | render floor |
|---|---|---|
| Micro | 5 | 400x300 |
| Tiny | 0 | 480x360 |
| Small | 1 | 560x420 |
| Regular | 2 | 640x480 |
| Large | 3 | 720x540 |
| Huge | 4 | 800x600 |

**Rendering is not the whole story, and this ladder alone is not safe to launch
against.** A narrow client has to run *below* its render floor — see "The HUD draws
before it is reachable" below. 400x300 is not offered at all as a result.

**The stored values are not in size order.** Micro was appended after the original five
and took the next free number. `UI_SIZE_LADDER` is in ascending *visual* order and that
is the only order anything may use — nothing may sort or compare these numerically.

**The game does not clamp UI size against resolution.** A UI too large for the client
area is reachable in-game and simply renders broken. That very likely explains the stock
800x600 floor: it is the lowest resolution at which the *largest* UI size still works, so
the game never has to police the pairing. Going below it is not fighting the engine — it
is relaxing a conservative floor by controlling the pairing ourselves.

## The HUD draws before it is reachable

**The failure is a click that silently misses, not a rendering glitch.** The HUD anchors
the compass and teleporter buttons (`compassAndTeleporterButtons`) to a screen edge and
centers the interact prompt. On a narrow client the prompt is wide enough to cover the hub
and dorm buttons — and those buttons are clicked *by coordinate*: `node_helpers.click_by_path`
resolves the window and hands its center to wizwalker's `mouse_handler.click_window`, so
whatever is drawn on top takes the click. A covered button therefore fails **quietly**:
`click_by_path` returns True (the window exists and is not grayed), the click lands on the
prompt, and the node burns its `TRIGGER_TIMEOUT` waiting for a loading screen that never
starts. Four steps ride on that button group — `teleport_to_world_hub`, `wizard_city`'s
go-home (which `handle_potions` and zone navigation's `home` step use), `mark_location` and `teleport_to_marked_location`.

There are two ways out, and the ladder uses both.

### Narrowness is priced in ladder steps

How far below the render floor a client has to run depends on its **aspect ratio, and
nothing else**:

| band | steps below the render floor |
|---|---|
| 16:10 or wider | 0 |
| 4:3 | 1 |
| narrower than 4:3 | 2 |

Every pairing below was driven on a live client with a sigil prompt up, checking whether
the prompt covered the hub and dorm buttons. `MEASURED_PAIRINGS` in
`tests/runtime/game/test_game_sizing.py` is this table, and the model is held to it — **if a change and
a measurement disagree, the measurement wins.**

| resolution | ratio | max scale | | resolution | ratio | max scale |
|---|---|---|---|---|---|---|
| 534x300 | ~16:9 | Micro | | 480x360 | 4:3 | Micro |
| 640x360 | 16:9 | Tiny | | 560x420 | 4:3 | Tiny |
| 746x420 | ~16:9 | Small | | 640x480 | 4:3 | Small |
| 854x480 | ~16:9 | Regular | | 720x540 | 4:3 | Regular |
| 960x540 | 16:9 | Large | | 800x600 | 4:3 | Large |
| 1128x634 | ~16:9 | Huge | | 1024x768 | 4:3 | Large |
| 1280x720 | 16:9 | Huge | | 1600x1200 | 4:3 | Large |
| 1680x1050 | 8:5 | Huge | | 2048x1536 | 4:3 | Large |
| 1920x1080 | 16:9 | Huge | | 1280x1024 | 5:4 | Regular |

**It is the ratio, not the size — that is the whole finding.** 2048x1536 is 4:3 with 1536
lines of height and still will not take Huge. So no amount of screen makes a 4:3 client
outgrow the occlusion, and **no 4:3 resolution reaches Huge at all**: Huge is the top rung,
and one step down from the top rung is Large.

This replaced a per-rung `min_narrow_resolution` column that stored the same thing as a
floor. Every entry in that column was, exactly, the *next* rung's render floor — Micro's
was 480x360, which is Tiny's floor, and so on for all five. Only Huge's could not be
derived that way, because there is no rung above it, and the invented value put there
(1280x960) was the one number in the file that was wrong. It made Huge look reachable at
4:3 given enough pixels, which is what 1600x1200 and 2048x1536 disprove. **Do not
reintroduce a per-rung narrow floor**; it is one subtraction, and the column existing is
what let a guess sit next to five measurements looking like data.

1280x1024 is the other correction from the same round. It had been recorded as Huge on the
reasoning that 5:4 is "close enough to 4:3, and it is big anyway". It is Regular.

**400x300 is therefore not offered at all.** It renders at Micro, but it is 4:3, and Micro
has no step to give. `has_unoccluded_ui_size` is the guard, and a test holds all of
`VALID_RESOLUTIONS` to it — that check, not the render floor, is the bar a new entry has to
clear. A settings file that still holds 400x300 is migrated on read by the
`game_resolution` getter rather than launched.

### Wide: take the width instead

`widescreen_form` derives a second resolution per rung — **the same height, the width
widened to 16:9** — which moves the edge-anchored buttons away from the centered prompt, so
the rung pays no steps and keeps the render floor's scale:

| UI size | 16:9 | scale |
|---|---|---|
| Micro | 534x300 | Micro |
| Tiny | 640x360 | Tiny |
| Small | 746x420 | Small |
| Regular | 854x480 | Regular |
| Large | 960x540 | Large |

**Widening, never shortening — that is what makes these safe to offer.** More width at the
same height cannot take a HUD back below a floor it already cleared, so the 16:9 form
inherits the render floor's verification. Deriving the 16:9 size from the *width* instead
(400x225) would cut the height below the floor, which is exactly the broken pairing the
ladder exists to prevent.

**These are the game's own series, continued downward.** `widescreen_form` rounds the width
to an even pixel, and fed the heights from the game's own dropdown it returns the game's
own entries: 634 → 1128x634, 990 → 1760x990, 720 → 1280x720, 1080 → 1920x1080. So the
sub-floor widescreen rungs are not an aspect ratio of our invention — they are the rule the
game already applies above 800x600, applied below it.

`is_widescreen` draws the band at **16:10**, compared by cross-multiplying integers so a
ratio sitting exactly on the line cannot be decided either way by a float. This is a
**measurement**: 1680x1050 is 8:5 and takes Huge with the prompt clear.

**Do not tighten the band toward exact 16:9.** The even-pixel rounding above lands some
derived rungs fractionally *under* 16:9 — 746x420 is 1.7762 against 1.7778 — so an exact
16:9 test would price them as narrow and cost each one a scale it does not owe. 7:4 is the
tightest safe boundary if it ever has to move.

The aspect ratio itself costs nothing. `Resolution` is free-form and the engine sets its
own frustum at launch, which is how the 16:9 sizes already in `VALID_RESOLUTIONS`
(1280x720, 1600x900, 1920x1080) work. That is a separate question from the runtime frustum
correction discussed under Deimos below, which only arises when something resizes a
*running* client.

## What is offered, and why those

`VALID_RESOLUTIONS` is a sorted union rather than a concatenation, because the two sets
interleave: a rung's 16:9 form is wider than the stock floor, so 854x480 and 960x540 sort
above 800x600 while still being sub-floor sizes in the sense that matters — they take a UI
scale below Huge. "Sub-floor" there is about the *scale*, not the size: the 4:3 entries at
and above 800x600 top out at Large too.

Every entry has one of exactly two provenances, and a test enforces it: the game's own
dropdown offers it, or it is a ladder rung run through `widescreen_form`.

The dropdown offers seventeen sizes. Four are left out as sizes no monitor is — 1128x634,
1760x990, 3072x1728, 3200x1800 — rather than padding every picker with entries nobody
picks. Two entries that were *not* on the dropdown have been removed: 1280x960, which is
where the mistaken Huge narrow floor came from, and 1280x800, which existed mostly as the
one resolution sitting exactly on the 16:10 boundary. That boundary still has a live case
in 1680x1050, and a measured one.

## Where the two values live

Not guessable, and the single most likely thing for a later change to get wrong:

| value | file | note |
|---|---|---|
| `Resolution` | `preferences.xml` | also present in `config.xml`, but that copy is a mirror the game rewrites at launch |
| `UISize` | `config.xml` | absent from `preferences.xml` entirely |

Both are patched by `_patch_window_config` in `bartleby/runtime/launcher/launch_profile.py`,
which is also where `WindowedX`/`WindowedY` and `QuickFriendTeleport` go.

## The UI scale is the user's, and is given back

They play Wizard101 outside BartleBot, so a scale clamped for the tiled layout must not
be left behind.

- The clamp only ever goes **down**. A resolution that can carry the user's preferred
  scale gets it untouched; nothing is ever raised.
- The original is captured **once**, before the first patch, into
  `settings.json` (`game_embed.original_ui_size`). It cannot be re-read from
  `config.xml` later, because after one clamped session that file holds *our* value —
  reading it back would adopt the clamp as the preference and lose the real one. Deimos
  hit the identical bug shape with camera frustums, where treating a previously-modified
  value as native compounded the error on every cycle.
- `restore_ui_size()` puts it back and clears the capture. It runs on quit **and** on the
  next start, because a session that crashed never reached the quit path and nothing else
  would ever undo it.
- A launch at a resolution that *can* carry the preference writes it back rather than
  doing nothing. Without that, one launch at 534x300 leaves Micro behind for every launch
  after it.

The known gap: a user who changes their UI scale in-game while a clamp is captured has
that change overwritten on quit. Re-reading the file to detect it is exactly the thing
that cannot be done, for the reason above.

## Screen space caps the game size

**The user must never have to scroll to see a game window.** So available screen space
sets a *maximum* game size, and only resolutions at or below it may be launched:

```
width  = screen_avail_w - sum(size of each pane beside the game) - SPLITTER_HANDLE_PX per such pane
height = screen_avail_h - sum(size of each pane below it) - WINDOW_CHROME_ESTIMATE_PX
```

For BartleBot's window, its two sidebars and its bot canvas, that is:

```
width  = screen_avail_w - left_sidebar_width - right_sidebar_width - 2*SPLITTER_HANDLE_PX
height = screen_avail_h - bottom_section_height - WINDOW_CHROME_ESTIMATE_PX
```

Per tile: `w + 2*FOCUS_BORDER` wide, `h + LABEL_RESERVE + 2*FOCUS_BORDER` tall. A tier
fits when `cols*tile_w <= width` and `rows*tile_h <= height`.

This reads the user's **configured** pane sizes (BartleBot's `left_sidebar_width`,
`right_sidebar_width`, `bottom_section_height`), **not** the panes' floors. Sizing against
the floors assumes the user wants their bot canvas crushed to its floor, which nobody does
— it would offer a resolution that only fits as long as they never touch a splitter.

### Panes and tiles are lists

Two products size their windows by this arithmetic, and their windows differ: BartleBot has
a left sidebar, a right sidebar and the bot canvas below; BartleBuddy a right sidebar alone,
which it can hide, and no pane below. So `game_sizing` reads a list of `Pane(name, floor,
side)`, where it read BartleBot's constants until Stage 9 of BartleBot #167:

- **`name`** is the key the product keeps the pane's size under, so the configured sizes are a
  mapping by it (`FitSettings.pane_sizes()`). A pane missing from it is at its floor.
- **`floor`** is what the pane may be dragged down to, measured where it can be (below).
- **`side`** is `LEFT` or `RIGHT`, costing width and a splitter handle, or `BOTTOM`, costing
  height, its handle inside `WINDOW_CHROME_ESTIMATE_PX`.

`screen_budget(screen, panes, sizes)` is the formula above; `layout_budget` is the same with
each pane held at its floor, since a stored size below one describes a layout the splitter
cannot produce; `required_window_size(resolution, tiles, panes, dpr)` is the window floor
below. Core's `screen_fit.configure(settings, panes)` is handed the product's settings and a
function listing its panes with their floors measured, asked again at each budget.
BartleBot's are `gui/window_panes.panes()`; BartleBuddy's controller lists its sidebar while
it shows and nothing while it is hidden.

**Tiles** are a count of equal tiles, which is BartleBot's one grid and every call it makes,
or each tile's size in slot order, `Main` or `Side` (`bartleby.runtime.game.tile_layouts`).
Main tiles alone are the equal grid. With Side tiles, the area is the one
`tile_layouts.arrange` places them in with the strip beside the Main tiles: the Main tiles
in a row, each a rectangle of the game's aspect holding a whole tile, and a strip of Side
tiles `SIDE_SHARE` of that height tall. A Side tile needs nothing of its own: a client that is
not resized shows its game at the launch size, cut to the tile from its top-left, since
`ClientTile._reposition` centers a game only when the tile is larger than it.
`test_arranged_in_the_area_it_needs_every_main_tile_holds_its_game` keeps the sizing and the
placement in agreement.

**Tiles by rung** are for a product whose clients can be resized, and turn that budget round,
because a client's size then follows its tile. `GameView.set_layout(..., by_rung=True)` asks
for them, as BartleBuddy does while its row is on. BartleBot does not, since a smaller Main with
no resize behind it only costs room. BartleBuddy hands it one of two layouts (#14): `ONE_LARGE`,
one Main tile for the Active wizard and the rest Side, and `ALL_EQUAL`, the equal grid. A player
sees them as One large and All equal, and the large and small tiles. Main and Side stay this
module's words.

- **Every Side tile holds its whole game, and Main takes what is left.** The strip reserves
  the `tile_size` of the family's smallest rung for each Side client, 546x351 at 16:9, in a
  column beside the Main tiles or a row below, whichever leaves the larger Main. On a tie it is
  whichever gives the larger Side tiles, and beside if those tie too: at 4K at 125% with a 480
  sidebar and four clients, both give 2560x1440, beside gives 534x300 Side tiles and below
  746x420. Each Main tile is exactly the `tile_size` of the largest rung that fits the rest, and
  the Side tiles share everything else.
- **A new Main trades places with the old one.** `GameView` keeps a placement order by slot, and
  `arrange` hands the strip's places out in it. When the Main tile changes hands,
  `tile_layouts.trade_places` puts the old Main where the new one was, so only those two tiles
  move, as ISBoxer's home regions and xmonad's DwmPromote do it. The equal grid is always in slot
  order, and the strip comes back in the order it had, with a swap made meanwhile trading places
  with the last Main.
- **A resize keeps the launch's aspect**, so no camera is written. `aspect_family` gives a
  client's rungs: 16:9's or 4:3's, and None for any other aspect, whose client has no target.
  The largest game with a ladder picks the family the tiles are placed by, and a client of the
  other family takes its own family's largest rung that fits, which a tile reserved for the
  first can still cut.
- **The equal grid grows, up to a cap.** `tile_targets(..., grow=True, cap=)`, which `GameView`
  passes by rung: with no Side tile, each client takes the largest rung of its family that fits
  its tile, with no more pixels than the cap, BartleBuddy's All equal size cap. By pixels, so one
  cap serves 16:9 and 4:3: a 1280x720 cap gives a 4:3 client 1024x768. The cap never touches a
  Main tile. Without `grow`, as BartleBot calls it, each target is the launch size, so a session
  that never has a Side tile never asks for a resize. With a Side tile, it answers the largest of
  the client's family that fits (`fitting_rung`), and for a Side tile always a rung below every
  Main tile's.
- **A game too large for its tile follows it down, beside another**, grown or not. The launch fit
  never judged a client started outside the app, or one left from an earlier session and since
  set larger. Without `grow` it takes the largest rung below its launch size that fits, and goes
  back up as room comes, no further. A lone client never resizes, however small its tile or large
  its room, since it gains nothing.
- **The equal grid is the fallback.** No strip fits where the reservation does not, as with
  three followers on 1080p with the sidebar shown. Nor does one where Main would come out at
  the smallest rung, since a Side tile then has no rung below it, or smaller than the equal
  grid makes it, which only two or more Main tiles reach. That last rule is what lets a launch
  be judged as the equal grid and always fit a Main tile.
- **The window floor is that same grid, at the smallest rung**, with two or more clients, never the
  sizes the games have now. Beside another, every game can follow its tile down to the smallest
  rung, so that is all the window needs, and a sidebar can widen as far as the games can shrink. A
  floor at the launch sizes, as it was, stopped the sidebar at 480 with four clients at 1600x900 on
  4K at 125%, and a floor taken from a Main grown by maximizing would stop the window being
  restored. A launch is still judged as the equal grid at its launch size. A lone client keeps its
  launch size as its floor, since it never resizes.

What it gives, at 100% display scale with a 40-pixel taskbar, where 1612 wide is the sidebar
shown and 1920 hidden; `test_tiles_by_rung.py` holds these rows:

| launch | game area | clients | Main | Side |
|---|---|---|---|---|
| 16:9 | 1920x971 | 1 Main, 1 or 2 Side | 1280x720 | 534x300 |
| 16:9 | 1612x971 | 1 Main, 1 or 2 Side | 960x540 | 534x300 |
| 4:3 | 1920x971 | 1 Main, 1 Side | 1024x768 | 800x600 |
| 4:3 | 1612x971 | 1 Main, 1 Side | 1024x768 | 480x360 |
| 16:9 | 1920x971 | 1 Main, 3 Side | 960x540 | 534x300, strip below |
| 16:9 | 1612x971 | 1 Main, 3 Side | equal grid, 746x420 at most | |
| 16:9 | 1920x971 | 2 Main, 1 Side | 854x480 | 640x360 |
| 16:9 | 2252x1331 | 1 Main, 1 to 3 Side | 1600x900 | 534x300 |
| 16:9 | 3532x2051 | 1 Main, 1 to 3 Side | 2560x1440 | 854x480 |

And #14's table, One large and All equal grown with no cap, at 4K at 125% (3072x1728 logical, a
48-pixel taskbar) as well; `test_each_row_of_the_sizes_table` holds each row:

| screen, clients, launch | sidebar | One large | All equal |
|---|---|---|---|
| 1080p, 2, 16:9 | hidden | 1280x720 + 534x300 | 854x480 |
| 1080p, 2, 16:9 | 300 | 960x540 + 534x300 | 746x420 |
| 1080p, 2, 4:3 | hidden | 1024x768 + 800x600 | 800x600 |
| 1080p, 2, 4:3 | 300 | 1024x768 + 480x360 | 720x540 |
| 4K at 125%, 4, 1600x900 | hidden | 2560x1440 + 960x540 each | 1600x900 |
| 4K at 125%, 4, 1600x900 | 300 | 2560x1440 + 854x480 each | 1600x900 |
| 4K at 125%, 4, 1600x900 | 480 | 2560x1440 + 746x420 each, below | 1600x900 |
| 4K at 125%, 4, 1600x900 | 600 | 2560x1440 + 746x420 each, below | 1366x768 |
| 1080p, 4, 16:9 | 300 | no strip: 746x420 each | 746x420 |

BartleBot's answers are the same as before the lists: its
`tests/game/test_pane_sizing.py` holds the old formulas written out and compares every
resolution on the ladder, at one to eight clients and four scale factors, for the window
floor, the budget and every fit.

### ...but the live game area counts too

Those three values are startup **seeds**, written only by the General settings tab.
Nothing connects `splitterMoved` and nothing persists a window resize, so the configured
layout can describe something the user dragged away from hours ago — which is why fixing
the layout by hand used to leave a launch still refused, and only editing the defaults
unlocked it.

So `screen_fit.budget_for` takes the **larger of each axis** of two terms:

| term | is | why it is in the max |
|---|---|---|
| screen | `layout_budget(available screen)` | the floor of what is always achievable — embedding raises the game view's minimum and Qt grows the window to satisfy it, so a launch bigger than the current window corrects itself provided it fits the screen |
| live | `GameView.game_area_size` | the only thing that sees a splitter drag, and exact where the screen term estimates: sidebars, canvas, title bar and the panel's own header are already subtracted, because they are simply not in it |

Taking the max rather than the live term alone is what stops a merely *small* window
refusing a resolution the screen could carry — including at construction, before the
window has been shown and the game view still holds a default size. Dragging a sidebar
*wider* therefore costs nothing, which is correct: the window minimum pushes back at
launch anyway.

Re-judging is deliberately **not** a rebuild. `launch_cards.refresh_column_fit()` calls
`set_unavailable` on the cards each column already has, and that returns immediately when the
reason has not changed — so a drag costs a few integer comparisons per card per event and
restyles only on the frame a verdict flips. Rebuilding the cards instead would
`deleteLater` and recreate every widget several times a second for the length of a drag.
It is driven from `GameView._recompute_geometry` rather than from the placeholder's own
`resizeEvent`, because Qt defers a resize event to a hidden widget until it is shown and
the launch screen is hidden for the whole life of an embedded client.

The live area is **passed in**, never looked up: `MainWindow.live_game_area` feeds the
Launch menu and the launch guards directly, `LauncherPopup` reads it from its parent, and
`SettingsDialog(..., live_game_area=...)` carries it down to the Game tab's resolution
picker *and* to the fit pass over its two card columns — so the settings combos and the
Launch menu cannot disagree. The tab is handed that size rather than measuring itself:
the dialog is not the game area, so measuring the tab would answer a question about the
settings window. A settings tab
reaching back up its parent chain to find the main window would be the coupling this
avoids.

Worked example, 1080p at the default layout (budget 1200x800), over the ladder rungs:

| clients | grid | largest that fits | scale |
|---|---|---|---|
| 1 | 1x1 | 960x540 | Large |
| 2 | 1x2 | 560x420 | Tiny |
| 3-4 | 2x2 | 534x300 | Micro |

Micro is not an edge case — it is what a four-client 1080p user actually runs.

Three and four land on Micro's **16:9** form, the smallest offered resolution. Ranking is
by area, so the widescreen form wins there while still fitting the 2x2 grid — 2*(534+12) =
1092 against a 1200 budget. Two clients keep a 4:3 form, which shows more game than the
16:9 of the same rung; the 16:9 forms only ever win where they are genuinely bigger.

The rungs here are one lower than they used to be, and the sidebar width bump is why: the left sidebar's
floor went from a bare 220px to a measured 390, and 170px off the budget costs a solo user
1280x720 and a pair 640x480. That is intrinsic to any honest floor above ~312px rather
than an artifact of where this one rounded — the old number simply described a panel whose
content was overlapping itself. The option cards (#138) moved the floor on to 404, for
their border and padding; the scroll bar's space is reserved with no extra gap, since the
page's own content margin already keeps the cards off it. That cost no rung at 1080p.

This is the **inverse** of `SettingsManager.minimum_window_size()`, which derives a window
minimum from a game size already chosen. Both are kept, and they ask opposite questions:

| | direction | measured against |
|---|---|---|
| `layout_budget` / `screen_budget` | screen → game size | the user's *configured* layout |
| `minimum_window_size` | game size → window floor | the panel *minimums* |

Once resolutions are capped to the screen, the minimum the second one derives is always
satisfiable, so it stops being able to refuse to open a usable window and does its real
job: stopping the window and the splitters being dragged to where the game gets clipped.
The splitter half of that works only because `GamePanel` (BartleBot's `GameViewPanel`) wraps `GameView` in a layout,
so the view's minimum becomes the panel's and `QSplitter` honors it —
`test_the_game_view_s_minimum_reaches_the_panel_that_sits_in_the_splitter` is the guard.

### The floor is only a floor if the panes honor it

`required_window_size` is not a description of the window — it is a *claim* about it, and
Qt holds the window to the larger of the claim and what the layout inside actually needs.
So every term in it has to be a size the corresponding pane will really go down to:

```
width  = game_w + sum(floor of each pane beside it) + SPLITTER_HANDLE_PX per such pane
height = game_h + GAME_PANEL_CHROME_PX + sum(floor of each pane below it)
         + SPLITTER_HANDLE_PX per such pane + TITLE_BAR_PX + BOTTOM_EDGE_PX
```

which for BartleBot's three panes is

```
width  = left_sidebar_floor() + right_sidebar_floor() + game_w + 2*SPLITTER_HANDLE_PX
height = game_h + GAME_PANEL_CHROME_PX + BOTCANVAS_MIN_HEIGHT + TITLE_BAR_PX
         + BOTTOM_EDGE_PX + SPLITTER_HANDLE_PX
```

where `game_w, game_h` is `required_game_area` — the tiles' own chrome included, which the
old sum left out. `BOTTOM_EDGE_PX` is the rule under the panes (#271), as thick as a
splitter handle, counted even while it is hidden, since a restored window on Windows 11
has Windows' border instead.

A pane that lets its *content* set its floor breaks the claim silently. Both sidebars used
to, and the widest thing either can hold is not in it at construction: the tab sets are
mode-swapped, so the 562px parameter panel only arrives when the user switches to edit
mode. On a 3840x2160 screen a 2560x1440 launch therefore passed the fit and produced a
window with a 3770x2100 floor — against a 2081px work area, which Windows refused with
`QWindowsWindow::setGeometry: Unable to set geometry` and a clipped window.

The fix is that each pane sets the arithmetic's term on itself with `setMinimumSize`. That
is not the same as being merely narrow: an explicit minimum is the one input that wins
outright over a layout's own minimum in Qt's `qSmartMinSize`, so it caps the pane rather
than competing with its content. Below that width the content clips, or scrolls where a
`QScrollArea` is already in the way, which is the right trade for a size only reachable by
dragging a splitter to its stop.

Two of the terms are floors with nothing on the other side of them, so they carry their own
constraints: `SIDEBAR_MIN_HEIGHT` must stay under the shortest tile any offered resolution
produces, and `BOTCANVAS_MIN_WIDTH` under the narrowest window the ladder implies —
otherwise a sidebar or the canvas, which have no height and width term respectively, become
the floor instead of the game view. BartleBot's `tests/gui/test_window_minimum.py` holds all of it together,
including the 4K case above as a regression.

### The sidebar floors are measured, not fixed

Both of them. The right sidebar's player card ends in a row of three buttons — `Entities`,
`UI Tree`, `Unhook` — and those, their padding and the scrollbar beside them are all font
metrics: the layout that fits in 300px at 100% system text needs 375 at 125%. A pixel
constant would have to choose which machine to be wrong on. Pinned low it clips the row on
a scaled display, which is how this came up; pinned high it charges every 1080p user 75px
of sidebar for a text size they are not running, and 75px there is a whole rung of the
ladder — 1280x720 solo becomes 960x540.

So `RIGHT_SIDEBAR_MIN_WIDTH` is the floor **at the reference metrics** (Segoe UI 9pt at
100% under `windowsvista`), and `game_sizing.right_sidebar_floor(log_row)` re-derives it
from what the panel measures here, never below the reference. 300 is deliberately the old
default sidebar width, so a machine at the reference sees no change at all.

**A floor is the forcing row plus the chrome around it**, and only the row is measured:

```
right_sidebar_floor = measured_log_header_row + RIGHT_SIDEBAR_CHROME_PX   # 19
left_sidebar_floor  = measured_filter_row     + LEFT_SIDEBAR_CHROME_PX    # 111
```

The chrome — margins, frames, the scroll bar, the gutter, the pinned control widths — is
**added, never scaled**, because a font does not widen any of it. Both floors used to be
the whole reference width multiplied by the larger of a text-height ratio and the row's
own, which charged the font's growth against all of that as well. It over-charged by
roughly half: raising the body text from 12px to 14px grew the left row 11% and its floor
19%, and grew the right floor 19% against content that had not moved a pixel. That was
129px of sidebar between the two panels, and on a 1080p screen it took three- and
four-client launches away outright.

**The row carries the style as well as the font**, which is what the old text-height term
was reaching for. A `QPushButton` is never narrower than its style's own minimum width,
and that is not a font metric: 75px under `windowsvista`, 80 under Fusion, 81 under
`windows11`. Measuring a row built from real widgets picks that up wherever it lands,
including on a style no Windows version here has yet — so one measurement answers both
questions and the two-term scale is gone.

**The right sidebar's forcing row is the Log tab's header**: the `Min level:` caption, the
level combo, `Export` and `Clear`. It used to be the player card's tool row, and that row
no longer exists — the card carries icon verbs and elided rows now — so the floor was a
button's width standing in for a row that had been gone for a while.
Core's `bartleby/qt/sidebar/log_panel.header_row_floor()` measures it, built from the panel's own widgets
so the two cannot drift. Mostly pinned widths, so it barely moves with the font: 281px at
the reference, 289 at 14px body text.

Everything inside the running app takes those numbers from **`bartleby.qt.screen_fit.right_sidebar_floor()`**,
which measures `QApplication.font()` — never a particular widget's. The width the sidebar
enforces and the floor its `Pane` lists have to be the same number, and two call
sites measuring two different fonts is the same class of bug one level down. `RightSidebar`
re-measures on `FontChange`, because the theme is applied to the application after the
widget is built.

`test_the_player_card_fits_the_right_sidebar_under_every_available_style` sweeps
`QStyleFactory.keys()` so the question CI was asking gets asked locally too.

**The left sidebar is the same shape of problem**. Most of what it holds elides or
scrolls — which is what its old 220px constant was justified by — but the quicksell
keep-list picker's filter row does not: a search field, a category `QComboBox` and a
`Kept only` `QCheckBox` side by side, and the last two carry a `Minimum` size policy,
which has no shrink flag. At 220px the checkbox was drawn over the dropdown. So
BartleBot's `LEFT_SIDEBAR_MIN_WIDTH` (`game/pane_sizing.py`) is 404 — the row's 293px at the
reference metrics plus the 111px of option gutter, option card inset, scroll content margins,
scroll area frame and scrollbar that `LEFT_SIDEBAR_CHROME_PX` holds — and
`pane_sizing.left_sidebar_floor(filter_row)` adds the two the same way, from
`gui/window_panes.left_sidebar_floor()`, which shares core's `screen_fit.measured_floor` cache. Everything else on that
surface elides or wraps by design, so it sets no floor; that is why there is no
text-height term.

Its probe is built from the *real* widget types, including the actual `SearchLineEdit`
with its clear button and the widest label the combobox can carry. That is not fussiness:
a bare `QLineEdit` stood in for the search field first and reported 27px less, which held
the measured scale at 1.0 under `windows11` while the row it had to hold grew by 10px —
the identical failure to the one above, found by the identical style sweep.

**Still an estimate on the screen side.** `screen_budget` does *not* subtract
`GAME_PANEL_CHROME_PX`, the horizontal splitter handle, or the window frame — roughly 78px
of height. It is why the worked example above offers 1280x720 to a 1080p screen that can
only just carry it. Tightening it would re-tier the offered ladder, so it is left as the
known estimate `budget_for` already describes it as.

`WINDOW_CHROME_ESTIMATE_PX` is 60 for the same reason. It was `MENU_AND_STATUS_PX`, the
menu bar and a status bar under Windows' caption. The title bar (`bartleby/qt/title_bar`) replaced
all three with one `TITLE_BAR_PX` row, but the 60 stays, since lowering it re-tiers the
ladder the other way. The window floor counts `TITLE_BAR_PX` exactly, and the live term
picks up the room the title bar freed without any constant changing.

## Prevent, do not warn; disable, do not hide

An unfittable configuration is **not launchable**, rather than launchable with a warning.
Both surfaces check the *resulting* client count:

- a **profile**, against its own account count, its resolution and the screen;
- an **additional client**, against the count it would produce — adding a third client to
  a fitting pair is refused when the 2x2 grid it forces would not fit at the running
  resolution.

A launch is judged at the size it opens at, and a client arriving is the one that has to
be refused. A running client can be resized (below), but only by a product that places its
tiles by rung, and only once it is in the world, which is long after this answer was needed.
So this is what still removes the need for the overflow behaviors considered earlier —
warn-and-proceed, auto-detach, relaunch-everything-smaller. Nothing has to degrade
gracefully into a state that cannot be reached.

**An entry that cannot launch is disabled with the reason, never hidden.** A profile that
silently vanishes leaves the user hunting for it with nothing on screen saying why, which
is worse than a grayed row reading "2 clients at 800×600 needs 1624×651; there is
1304×800 available — lower the resolution, launch fewer clients, or make the game area
larger." That third way out is real, not padding: the budget includes the live game area,
so dragging a sidebar narrower widens it there and then. The same applies to
the resolution pickers: a resolution too large for the current screen and client count
appears disabled with the reason, rather than being quietly dropped from the list.

**A surface showing many entries at once says the reason once, not once per entry.**
`fit_details` returns a `FitProblem` rather than a string, so a caller can take the half
it needs: `numbers` ("Needs 1624×651 · 1304×800 available") belongs to the entry, and the
ways out belong to the surface, because they are properties of the layout rather than of
any one profile. `fit_problem` still returns the whole sentence, which is what a tooltip
and a disabled menu entry take — those are read one at a time and have nothing to repeat.

Each column carries a `_FitBanner` under its header (core's `bartleby/qt/game_embed/card_columns.py`),
linking to the resolution setting so advice the user has to go hunting to follow is not
half an answer. **The two columns are worded separately, and must stay that way** — they
refuse different things:

| column | the banner says | the cards say |
|---|---|---|
| Accounts | "Your default game resolution (2048×1536) needs 1650×1279, but only 1200×800 is available. Either lower the default game resolution or increase the size of the game view panel." | nothing — they stay gray and keep showing their characters |
| Launch Profiles, none fitting | "No launch profiles fit in the game view panel at its current size (1304×800). Lower the resolution they launch at, increase the size of the game view panel, or launch fewer clients." | each its own "Needs …" |
| Launch Profiles, some fitting | "Some launch profiles do not fit in the game view panel at its current size (1304×800) and have been disabled." | each its own "Needs …" |

Three things in that table are the point rather than phrasing:

- **The accounts banner offers two ways out, not three.** "Launch fewer clients" cannot be
  taken there — an account card launches exactly one — and a remedy the user cannot act on
  reads as the app not knowing what it is refusing.
- **The accounts banner names the *default* resolution**, because that is the setting to
  change: an account card never launches at a pinned one.
- **The profiles banner is sized against the panel, not a resolution.** Profiles may each
  pin their own, so naming one would point at a setting that is not the problem for the
  cards beside it. It is also why the numbers stay on the profile cards, where they differ,
  and come off the account cards, where every one of them is the same number — that
  repetition is the bug this shape exists to prevent, and it is easy to reintroduce by
  "simplifying" the two columns into one message.

The surfaces that must stay consistent: `ProfileCard`/`AccountCard`
(`gui/game_embed/launch_cards.py`), the Launch menu (`gui/main_window.py`),
`LauncherPopup` (`gui/launcher_popup.py`), and both resolution combos — the global one in
`gui/settings/accounts_tab.py` and the per-profile one in
`gui/settings/profile_editor.py`, which share `mark_unfittable_resolutions`.

The cards themselves are drawn on three surfaces, and only one of them refuses. The Quick
Launch screen grays an unfittable card and refuses to launch it: a click still selects it,
and the screen's Launch button is then disabled with the reason under it. The Game settings tab and
the profile editor show the same card at full contrast with the banner above it, because
a click there edits — or, in the editor, does nothing at all — and editing is what the
refusal is asking for. That split is the card's `CardMode`, and `refuses_when_unfit` is
what reads it — never condition on the fit result itself to decide whether to gray
something.

### Which client count the settings combos judge against

The launch surfaces size against the count they are about to produce, which is exact.
The two settings combos have no such moment, and they answer it differently:

| combo | sized against | why |
|---|---|---|
| a profile's own resolution | that profile's account count | it is the number of clients this value will actually have to hold |
| the global default | **one client** | the common case, and the only one it is the sole answer for |

The global one used to be sized against the largest profile that inherits it. That is
defensible — those are launches this value decides — but it grays out most of the list for
anyone who merely keeps a four-account profile around, and four accounts is rare where one
or two is not. A user running several clients has a launch profile, and that profile has
its own combo judged against its own accounts. Judging the global default by the rarer case
made the common one look broken.

This is safe because neither combo is a gate. A profile that inherits a resolution too
large for its account count is still refused at launch, by `ProfileCard` and the Launch
menu, with `fit_problem`'s sentence naming both numbers and all three ways out.

An earlier suggestion of a preferred size plus a scroll area is explicitly rejected: the
user should never have to scroll to see a game window.

## Beyond that, stay out of the way

Nothing picks a resolution on the user's behalf, with one exception. Running four clients
at 534x300 on a 4K monitor to save the machine some work is a legitimate and probably
common choice — small resolutions are a performance lever, not only a screen-space
workaround — so nothing should push users toward "the biggest that fits".
`largest_fitting_resolution` exists to *say* what would work.

The exception is setup (`docs/FIRST_RUN.md`), which has to start from *some* resolution,
and 800x600 is wrong at both ends: a two-account profile on 1080p that cannot launch, and
a single client a quarter of a 4K screen. The Launch profile page starts its combo on
BartleBot's `window_panes.setup_resolution`, the largest that fits, as a value the user sees and can
lower before Next. When setup closes, `window_panes.fitted_layout` fits the global
resolution, while it is still the shipped one or setup's own, and the canvas and sidebars
to the launches: the one other place the layout is derived rather than configured.

## Deimos' runtime resize: what was rejected, and what was kept

Deimos added freeform client resizing (`src/client_resizing.py`, ~760 lines, plus two asm
hooks in their vendored wizsprinter): a `WS_THICKFRAME` sizing border with a `WM_NCCALCSIZE`
WndProc hook, two pattern-scanned asm hooks that force the engine's backbuffer at runtime,
and camera-frustum correction so the 3D projection matches. Reviewed and **not adopted as
it stands**, for BartleBot's launches. Its resolution forcer, without the drag border and
paired with a runtime `UISize` write, is what a running client is resized with; the
section after this one has the recipe.

The two efforts solve complementary halves, and the half they solved is not the blocker:

| | Deimos | XML patching |
|---|---|---|
| render at arbitrary size | yes (asm hooks) | yes (`preferences.xml`) |
| at runtime, no relaunch | yes | no — launch time only |
| per-account sizing | yes | no — one global setting |
| arbitrary aspect ratio | yes (frustum correction) | no — 4:3 and 16:9, fixed at launch |
| **HUD usable when small** | **no** | **yes (`UISize`)** |

They never touch `UISize` — no reference to it anywhere in their source — and their floor
is `_MIN_W, _MIN_H = 320, 240` with no UI-usability basis behind it. Their resize produces
a crisp, correctly hit-tested, undistorted render at 320x240 with a HUD that overflows the
client area. **Do not copy that 320x240 number.**

The cost would be two pattern-scanned asm hooks, a build-specific struct offset, and an
in-process WndProc hook — their own `VideoManagerHook` docstring records the function
being recompiled in a 2026-06 client update and needing a new pattern. That is the
per-patch offset chasing `docs/UPSTREAM.md` exists to manage, for a feature XML patching
already covers from 534x300 through 4K.

Cheap lessons kept from that review:

- **The backbuffer must equal the client size, or every click is offset.** The game maps
  clicks against the backbuffer, so a mismatch silently shifts all of them. This is what
  rules out the tempting shortcut of stretching the game window to fill its tile —
  WizWalker clicks would land in the wrong place, failing subtly rather than loudly.
  `ClientTile`'s 1:1 contract is load-bearing.
- **If an aspect ratio is ever changed at *runtime*, frustum correction must be restored
  on teardown.** This does not apply to the 16:9 rungs above — those are set in
  `preferences.xml` before launch and the engine builds its own frustum — only to a hook
  that reshapes a running client. Their code assumes first-seen extents are native, so a
  corrected frustum left behind makes the next hook read it as native and the POV creeps
  further on every cycle. We unhook and rehook routinely (`ClientHealthMonitor`,
  relogging, `_rehook_one`), so this would bite considerably harder here.
- **Exactly one backbuffer forcer per client**, if this is ever revisited: a second one
  pattern-scans `setMode` after the first hooked it, finds a `jmp` where the original
  bytes were, and fails with `PatternFailed`.
- **Per-account window config** is the model to copy if per-client resolution ever
  matters (their `apply_account_window`).

Runtime resolution change remains genuinely attractive — it would let
`_group_by_resolution` and the launch-a-batch-per-resolution constraint go away, and let
tiles resize on a splitter drag instead of requiring a relaunch. It is the expensive path,
and the next section is what a probe found it takes.

## Resizing a running client

Measured on the live realm's build (2026-11-01) by BartleBot's `tools/probe_runtime_resize.py`,
whose RESULTS have every offset and how each question was answered. wizwalker's
`client.video_manager` holds the apply, and `bartleby/runtime/clients/client_resizer.py` the
rest of the runtime's half: when a client may be resized, its lease, its config nodes and giving
it back. `GameView` and `ClientTile` hold the window's half. BartleBuddy uses it, to swap which
client gets the large tile when the user changes who they play. It is in the shared library so
BartleBot can follow.

The two parts of Deimos' resize were each rejected for a reason, and the reasons are about
those parts, not about resizing at runtime:

- **The drag border** made every edge of every floating window a resize grip, so windows
  resized by accident. Nothing here takes it: a size changes only when the app asks.
- **The missing `UISize`** is why their small windows had a broken HUD. A running client
  takes a new UI size through the same apply as a new resolution, Micro included.

A resize is three steps, in this order, and the game does the rest. An apply takes tens of
milliseconds, and up to about 600 ms when two clients apply at once. From asking to the answer,
embedded in a tile, it was 242 to 265 ms, so a swap is about 0.75 s with `RESIZE_SETTLE`, and about
3.5 s the first time, with the scans:

1. **The two nodes**: write the live `Resolution` node's string and the live `UISize` node's
   int32, in the game's config. The writes alone change nothing on screen. The nodes are among
   ~130,000 generic key/value objects, found once per client by a scan for their names, as the
   key bindings are (`docs/KEY_BINDINGS.md`). Both names take about 2.5 s, off the event loop, and
   its longest gap stays near 69 ms against 64 ms with nothing running: BartleBuddy's
   `tools/probe_resize_scan.py` has the numbers. Each name has several
   nodes: the live `UISize` is the one marked at `+0x45` with a pointer at `+0x18`, and the
   live `Resolution` the one with that pointer under the same parent. The string is an MSVC
   `std::string`, so its size field changes with its text.
2. **The pending byte**: set it on the video manager, and the engine runs its own apply on its
   next frame. It moves the device, the window and the camera to the new size, and draws the
   HUD as it would have chosen itself, with no set-mode hook and no camera write. The manager
   comes from a static slot, so no game code is patched, and the byte's offset from the code,
   since it moves between builds. The byte clears when the apply is done, and a failed attempt
   leaves it set and retries. While the device is lost, the apply only tests it and returns, so
   the window stays in its tile until the device can be reset.
3. **The window**: a top-level window follows by itself. An embedded one is taken out of its
   tile, as a hidden top-level window, by the time the byte clears. Embedding it again at the
   new size holds, and keyboard focus and a held key come through it.

The hook-based version Deimos uses, overriding set-mode's width and height, works too, but
leaves the game believing the old size: the HUD stays at the old scale. And the backbuffer
still equals the client size after every resize, so the 1:1 contract holds.

`ClientResizer` is the policy around those steps, all of it tested on `SimClient`:

- **`prepare(clients)`** finds the manager and the nodes ahead of the first swap, once there are
  two clients. With one it does nothing, so a one-account session never scans. The caller decides
  when a session may need a resize; a session with no Side tile should not ask.
- **`resize(client, size)`** only sets a target. One worker per client waits out loading and any
  lease above `Owner.DISPLAY`, then reads the target, so targets sent during a wait run only the
  last. A size it is already at writes nothing. A fight is not waited out: the tiles change shape
  at once, so a wait left a game at its old size, cut off by its new tile, until the fight ended.
- **The UI size** is `ui_size_for` of the user's own, captured by
  `launch_profile.capture_game_settings` before the first write, for a client no launch captured.
  A game with no UI scale in its file is not resized, since a resolution with the wrong UI size is
  the pairing the ladder exists to prevent.
- **The apply** checks the manager first, so a bad offset costs nothing, then writes the UI size,
  the resolution and the byte, and waits for the clear. It runs as a task of its own that no
  cancel reaches. `on_resized(client, size)` always hears the size it measures once an apply has
  begun, since the game has taken an embedded window out of its tile by then.
- **The 1:1 contract** is checked after the byte clears, with `read_device_size`.
- **A byte that never clears** is taken back after `RESIZE_APPLY_TIMEOUT`, and the nodes put back.
  Left set, the game would apply it whenever it next could, with nobody watching for a window it
  takes out of its tile. In game this happens only behind a secure screen held past the timeout,
  below.
- **`give_back(client)`** returns the size read from the device before the first resize and the
  UI size read from the node, within `RESIZE_GIVE_BACK_TIMEOUT`, waits included. One still in a
  loading or under a higher lease then is left as it is, with a warning. The files are `restore_ui_size` and
  `restore_game_prefs`, on quit and at the next start, so they run after the last apply.
- **Any failure** is one warning, and no more is asked of that client until `forget(client)` and
  it is hooked again.
- **`stop_all()`** ends every resize still waiting. An apply under way finishes and reports first.

The window's half is the product's two crossings around that:

- **Asking.** `GameView.game_targets()` is worked out on every geometry change, and
  `game_size_wanted(hwnd, size)` goes out once the area and the targets have held for
  `RESIZE_SETTLE`, for a window that differs from what it has or was last asked for. So a splitter
  dragged across several rungs asks once. A team view placing the tiles again with nothing changed
  does not put it off. Only windows named by `set_resizable` are asked for, the hooked, and with
  no Side tile the target is the launch size, so a session that never has one never asks and never
  scans. The first ask `prepare`s every client, so a later swap doesn't wait on a scan. A change
  the player makes in one press, a swap or a change of view, asks at once instead
  (`set_layout(..., at_once=True)`): the settle is for a drag, and on a swap it only adds half a
  second. Asking at once, the tiles hold their games where they are while they are placed
  (`ClientTile.hold_moves`), and a game whose size was asked for is left to its refit, which
  places it. A move at the old size in between was a call into each game, about 30 ms of the GUI
  thread a swap (Stage 5.2 of BartleBuddy #14). The refit is waited for only `_MOVE_GRACE_MS`
  (500 ms), since a resize can wait out a loading screen and a client that can't be resized never
  answers; then the game is moved after all. A drag moves each game as its tile changes, as before.
- **Answering.** `GameView.game_resized(hwnd, size)` hands the tile the size measured, and
  `ClientTile.refit` embeds the window again there (`EmbeddedWindow.reembed`), with no second
  `AttachThreadInput`. A size it has already, with the game still its child, moves nothing. While
  the window is out of its tile, read from the window on the focus poll, the tile draws
  `PendingTile`'s ring with no words, and leaves the window alone: it is top-level, and a tile's
  coordinates would throw it across the screen.
- **The refit runs on a worker.** `reembed` is `SetParent`, the child style, and one
  `SetWindowPos` that places and sizes the window together. `SetParent` waits on every game whose
  input queue the GUI thread is joined to, which is all of them: `embed`'s `AttachThreadInput`,
  plus the joining Windows does for a parent and child on different threads. On the GUI thread
  each refit held the app for 313 to 474 ms, and a swap, two refits in turn, took about 1.4 s.
  So the tile works out the parent and the place on the GUI thread, runs `reembed` on a shared
  `ThreadPoolExecutor` (`client_tile._refit_pool`), and finishes back on the GUI thread through a
  queued signal: the new `game_size`, the ring, a move only if the tile changed size meanwhile,
  then `refitted`. One refit runs per tile, and a size asked for meanwhile waits, the latest
  replacing any other. The tile isn't moved while one is under way, since a move between the
  worker's `SetParent` and its finish would be at the old size. `unembed` waits for one under way
  and drops the one waiting, so a detach or a quit never races it. The view runs what reads
  `game_size` (its minimum size, the settle) on `refitted`, and a settle meanwhile compares
  against `ClientTile.coming_size`, so nothing is asked for twice. BartleBuddy's
  `tools/probe_multibox.py` measured it (Stage 5 of BartleBuddy #14): with both refits on workers
  `SetParent` took 138 to 206 ms, a swap 660 to 1,001 ms, and the next key press 3 to 6 ms, where
  it had waited about 300 ms. The queues stay joined: unjoined, or joined only to give focus, the
  swap was no faster and the keys went to the wrong game or none.
- **The keyboard after a refit** is left on whichever game was embedded last. A product names
  the game it should be on with `GameView.set_keyboard_target(hwnd)`, and the view gives it focus
  once nothing is asked for, no refit is under way and every game is at its target, or at once
  when nothing is moving, and not at all to a game that has the keyboard already. BartleBuddy
  names the game the player is on. BartleBot names none, and its keyboard is never moved.
- **The ring through a swap.** Asking at once, each tile whose game is about to be asked for a new
  size shows the ring in its body, and not its game, from before any tile changes shape until every
  refit is in (`ClientTile.show_ring`). The view works the targets out from where the tiles are
  going (`_plan_geometry`, `_targets_at`), rings each tile `_ask_for_targets` will ask for, and only
  then places them. Once nothing is asked for, no refit is under way and every game is at its
  target, the condition the keyboard waits for, every ring comes off together `RING_LIFT_MS` (60)
  later, so the swap ends in one frame: the old layout, the rings, the new. A ring comes off by
  itself after `RING_LIMIT_MS` (1,500), a tile that leaves drops out of the wait, and `unembed`
  clears it first, so nothing is left clipped by a detach, a quit or Detach all. A drag shows no
  ring, and BartleBot never asks at once. Without it a swap showed each game out of place for
  about 410 ms and then white as it came back: the reembed's `SetParent` makes the game a visible
  child at its old size and place 40 to 120 ms before the `SetWindowPos` that places it, and then
  it is white at its new size for about a frame until it draws.
  - **The clip.** On, the tile's mask (`QWidget.setMask`, which is `SetWindowRgn` on the tile, our
    own window, so no call into the game) is its pill strip, `LABEL_RESERVE` high. A child's
    drawing honors its parent's region, so the game keeps running and drawing under it, out of
    sight. Setting it costs 0 to 1 ms. The pill is drawn in the strip while it is on, wherever the
    game is: a swap's large tile still holds its small game, centered well below the strip, until
    its refit.
  - **The pixel.** One logical pixel of the game is left showing, at its top left, rounded up into
    it at any scale. A game with nothing of its window showing, whether hidden, covered by a
    sibling or wholly clipped, ran 200 to 500 ms slower through its apply and its reembed, and what
    it drew meanwhile never reached the screen, so it came back white. With the pixel it does
    neither. That is inferred from the timings, not read from the game: every variant that left
    nothing showing paid, and the one that left a pixel didn't. The pixel follows the place the
    tile last put its game (`_reposition`, and the refit's `_refit_place`), with two while a refit
    is under way, since the worker's `SetWindowPos` moves the game before the finish runs. A held
    move doesn't move the game, so the pixel holds through the layout.
  - **The ring behind.** The view paints it: `_RingBehind`, an alien child of the view over the
    tile's body, so it shows only where the clip lets the view through, filling `THEME.bg` and
    turning the ring at the body's center, a square at a time.
  - **Rejected,** in BartleBuddy's `tools/probe_swap_look.py` (2026-11-06, four clients on 4K):
    hiding the games, which looks right but doubled a swap (1,265 ms against 625), since a hidden
    game is about 500 ms slower and each hide and show holds the GUI thread; a native child over
    the tile, since the reembed's `SetParent` puts the game above it; a top-level window over the
    view, 100 to 280 ms to show; and a stretched picture of each game, too coarse at the large
    size. With the pixel the swap took 652 ms, as the swap without the clip did in the same run, and showed no white frame.
  - **As built,** filmed by `probe_swap_look.py --built` over twelve swaps: a game out of place in
    no frame, 2 or 3 jumps, the large tile's pill in its strip all through, the keys on the Active
    wizard every time, and in place at a median of 722 ms. A white frame still showed in 3 of 12,
    each as the rings came off, and holding them 150 ms instead of 60 cleared it no better (2 of
    12). Most likely the area the clip hid is white until the game's next present, since what it
    draws while clipped never reaches the screen. Not proven; open as BartleBuddy #24.
- **The player's focus.** `GameView.player_focus_changed` names the game the player last gave
  the keyboard to: a click on an embedded game (`ClientTile.game_clicked`), a detached window
  brought to the front, or the keyboard target given its keyboard. The 50 ms poll never sets it,
  since it also sees the game a refit left the keyboard on for the moment, and it holds while
  another application or the app's own widgets have the keyboard. A press on a widget a product
  named with `keep_keyboard_over` leaves the keyboard with the game, and `give_keyboard_back()`
  hands it to the target again after a menu or a button took it. A caption press is reported
  before the keyboard is given, 20 to 50 ms sooner, still on the press (#93).
- **Swap first.** With `GameView.set_swap_first(True)` each Side tile keeps the mouse and keyboard
  from its game (`ClientTile.swallow_clicks`, `EmbeddedWindow.set_input_enabled`, which is
  `EnableWindow`), so a press on the game reaches the tile: on the pill it is `caption_clicked`,
  anywhere else `body_clicked`, and neither gives the game focus. A Main tile and every tile of an
  equal grid let clicks through. The game still draws and takes what the runtime posts. Which
  tiles swallow is settled each time the tiles are placed, before any size is asked for, and a
  reembed writes the style whole with `WS_DISABLED` kept, and the finish on the GUI thread puts
  the state back if it changed while the worker had the window (`EmbeddedWindow.sync_input`). It
  reads `IsWindowEnabled` first, since an `EnableWindow` after every refit, needed or not, was 35
  to 97 ms of the GUI thread a swap. `focus_game` lets the input
  through first, since a disabled window takes no keys, and `restore` does before anything
  else, so a game is never left disabled on the desktop. Measured on four clients on 4K
  (Stage 5.2 of BartleBuddy #14), a swap under it took 612 ms against 533 ms with every game
  enabled, the difference all in the reembeds on the workers; why is open, for Stage 5.3.
- **Leaving.** `window_detached(hwnd)` says a window has gone to the desktop, and the product gives
  it back. `EmbeddedWindow.restore` sizes the frame from the client area it has at that moment and
  the frame it was found with, since a frame of its old size no longer matches the backbuffer.
  It gives the game no `WS_THICKFRAME`, which the game has none of: a grip would resize its window
  with no apply behind it. A top-level window follows an apply by itself, so its frame is right
  from then on.

Measured and worth knowing:

- **A temporary size reaches the disk.** While a window was embedded, `preferences.xml` was
  rewritten during the session with the `Resolution` of the moment, and clients share that file.
  On exit `config.xml` is rewritten with the file's own `UISize`, and `preferences.xml` is left
  alone. The user's own settings have to be captured before a first resize and given back
  after the last.
- **An embedded game dies with its host.** If the app is killed, the game's window is destroyed
  and its process goes on with no window, so it needs ending and a relaunch.
- **`PrintWindow` returns all white** once a client's device has been reset, while the game
  draws normally. Read the screen instead.
- **Under VS Code's debugger a launched game dies with the app.** debugpy's launcher runs the app
  in a job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and a game started by `start_instance` inherits
  it. The windows were measured handed back whole and at their launch size half a second before
  the app exited, then destroyed in the same instant, with exit code 0 and no crash report. It looks
  like a crash on quit, and is not one: from a terminal they stay open.

Swaps in a fight ran with no issues in game, with two accounts, as did zone changes, so a fight is
not waited out.

**Through a device loss**, as BartleBuddy's `tools/probe_device_loss.py` ran it with four clients on
2026-11-04 (BartleBuddy #11), a resize waits for the device and then completes, or fails with its one
warning, and the game is in its tile either way:

- **A refresh-rate change, Ctrl+Alt+Del held 3.5 s, and a lock** each completed, 0.5 s or less after
  the device came back. The byte stayed set and the window stayed in its tile through the loss.
- **A lock loses the device only briefly**: about 0.5 s as it locks, and for as long as the PIN
  prompt is up, since the lock screen runs on the user's own desktop. A resize asked for as the
  screen locks completes behind the lock screen.
- **A secure screen held past the timeout**, such as Ctrl+Alt+Del, a slow PIN or a UAC prompt left
  open, is the one case that reaches it. The byte was taken back while the device was still lost,
  the nodes were put back, and when the screen came back the game reset its own device at its old
  size, in its tile. Resizing then stays off for that client until it is hooked again.
- **Taking the byte back cancels nothing.** A second client, with nothing asked of it, lost its
  device at the same moments: its byte never rose, its window never left its tile, and the game
  logged its own reset (`GamebryoClient::OnReset`) on every loss. Only one instruction in the exe
  sets the byte, and the renderer's own recovery never reads it.

**As a multibox team uses it**, as BartleBuddy's `tools/probe_multibox.py` ran it with four clients
on 4K at 125% on 2026-11-05 (BartleBuddy #15), a resize is right every time, and it is the
window's half that is slow:

- **Every client at once** comes out right: four to the rung below and back, ten times, in 1.7 to
  2.1 s. So does a hundred changes of view with two clients resizing and a hundred with four, with
  no crash, no device failure in the game's log and no climb in the games' memory.
- **In a fight** a resize holds up as it starts, during a round's animation, during card selection
  and as it ends. The picture, the hands and clicks are right after each.
- **The game applies a size in 30 to 90 ms. The rest of a swap is the GUI thread.** A swap took 1.3
  to 1.6 s. `ClientTile.refit` (`EmbeddedWindow.reembed`: `SetParent`, `SetWindowLongW`,
  `SetWindowPos`) is a call into the game's thread, and held the GUI thread 414 to 579 ms each, one
  client after the other. A press took about 300 ms to reach Qt, and `focus_game`'s first call on
  a tile 470 ms.
- **A refit leaves the foreground and the keyboard on its game.** After a swap they are on
  whichever client was resized last, which is the old Active one as often as the new.
- **A held key is let go when the keyboard moves.** W held on one game stopped it once the keyboard
  went to another, and the other stood.
- **A disabled embedded game passes its clicks to its tile.** After `EnableWindow(hwnd, False)` a
  real click reaches the tile as a Qt press and never the game, while the game still draws and
  takes posted keys and clicks by path.

Open: a resize across 4:3 and 16:9 corrects the selected camera too, read in the world only, so
combat's is unchecked; a device removed or hung, as by a driver reset, was not tried; the Test
realm's offset was read from its exe, and never run; and two clients on a 1080p display, the core
case, have not been run through the probe.

## Still open

- **Splitter positions are not persisted.** They are seeds, by design — the fit check
  reads the live geometry instead, so this costs nothing there, but a dragged layout is
  still forgotten at restart.
- **Monitor changes.** The budget depends on which screen the window is on and that
  screen's scale factor, so moving the window between monitors re-evaluates fit. What
  happens to an already-running configuration that no longer fits after such a move is
  unanswered — the one path into an unfittable state that prevention at launch does not
  close.
- **`game_resolution` is still a single global setting**, with a per-profile override.
  Per-*account* resolution would need the settings model to grow; launch batching
  (`_group_by_resolution`) already anticipates it.

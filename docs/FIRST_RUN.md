# First-run setup, shared

Setup's frame and the pages every product asks, moved from BartleBot in Stage 11 of #167
(`bartleby/qt/dialogs/first_run_dialog.py`). Each product hands the dialog its list of
pages, these and its own, and asks `needs_asking` of the same list's types at startup.
BartleBot asks all four shared pages and four of its own, after Quick or Custom (its
`docs/FIRST_RUN.md`); BartleBuddy asks the game folder, the accounts and the launch
profile, with no Quick or Custom (`ask_mode=False`), and the combat question waits for its
phase 2.

**An answer in one product is an answer in the other.** A shared page reads and writes only
the vendor's files, `%APPDATA%/Bartleby/preferences.json` and `accounts.json`, so a page
answered in BartleBot is passed over by BartleBuddy's first run, and the other way round.
The texts name the product in use (`named`, from the `Product`); a product may say more on
the Accounts page (`intro=`, `empty=`), and hands the Launch profile page the area it
judges a resolution in (`setup_area=`), or none, for its panes as configured.

## The walk

1. **Quick or Custom**, unless the product built the dialog with `ask_mode=False`. Two
   cards, Quick first, recommended and selected. The choice is not stored: it only
   decides which pages follow. Custom adds the pages only it asks, BartleBot's Options
   page. Like the combat question's, the cards are stacked at the page's full width, each led by a radio button, so they read as
   two answers to one question rather than a gallery (`_CardsPage`).
2. **Every page after it that applies.** A page applies under the mode chosen and with
   what is set up so far.

The footer is **Back** and **Next**, and on the last page that applies, **Finish**. Under
the heading, **Step N of M**, where M counts the pages that apply now, Quick or Custom
included when it is asked, so choosing Custom can change it.

- **Next records the page shown**, and moves on only once that reached disk. A failed
  write keeps the user on the page, says so under it, and logs a warning.
- **Next waits for a page that isn't ready.** A page's `ready()` says its answer can be
  recorded, and it emits `ready_changed` when that may have changed; Next is disabled
  until it is. The game folder page is the one that uses it.
- **Back and Next wait for a page with work running.** A page's `busy()` holds the user on
  it, and both are disabled until it ends. BartleBot's Game art page is the one that uses it,
  while the extraction runs.
- **Back records nothing.**
- **There is no Skip.** Escape and the close button reject and write nothing more. What
  was answered stays answered, and the next launch opens on the pages still unanswered.

## First run and rerun

A product asks `needs_asking(prefs, page_types)` at startup, which reads each page's answer
from the stores without building it (`FirstRunPage.asks_first_run`), and builds the pages
and opens the dialog only when one is unanswered and applies under Quick. Building them
searches the registry and every Steam library, draws the account cards and measures the
screen, which a launch with nothing to ask should not pay for. It opens from a synchronous
slot a turn after the window shows, since the dialog is modal.

- **A first run** walks the pages unanswered when the dialog opened. A page answered on the
  way stays in the walk, so Back still reaches it.
- **A rerun**, from the product's File › Set up…, walks every page that applies, each on
  the current value.

The Launch profile page and the combat question, unanswered with fewer than two accounts,
open nothing on their own: they are in the walk whenever another page opens setup, so
accounts added on the way bring them in. Closing setup, finished or not, calls each page's
`stop()`.

## The pages

| page | writes | answered when |
|---|---|---|
| Quick or Custom | nothing | never: it is always the first page |
| Where is Wizard101 installed? | `game.install_dir` in `preferences.json`, through `shared/game_install.set_install_dir`, only for a folder the user changed to | `find_install` finds an install, stored or detected |
| Add your Wizard101 accounts | each account as it is added, edited or removed, to `accounts.json` (and its password to Credential Manager), through `accounts_tab`'s card actions; on Next, `setup.accounts_answered` in `preferences.json`, through `shared/account_setup.record_accounts_answered` | `accounts_answered`: `accounts.json` holds an account, from either product, or the key is set |
| Which accounts do you play together? (two or more accounts) | one launch profile of the accounts checked, to `accounts.json`, through `shared/launch_profiles.add_profile`, the function Settings › Game's Add uses; and `setup.launch_profile_answered` in `preferences.json`, through `record_launch_profile_answered` | `launch_profile_answered`: `accounts.json` holds a launch profile, from either product, or the key is set |
| How should your team fight bosses? (two or more accounts) | the Team default, through `shared/first_run.record_answer`, and `setup.combat_defaults_answered` in `preferences.json` | `combat_question_answered`: the key is set (by either product, or BartleBot #209's Skip), or the Team default is a duel neither answer names |

### The combat question

Two cards, the recommended answer (one hitter, the others support) first.
`docs/COMBAT_CONFIG.md` ("First run: one question about the team") has what each answer
stores. The page opens on the answer the stored Team default already is. On a rerun that
default can be a duel neither answer names, chosen in Settings › Combat; then no card is
selected, and Next with none selected keeps that duel and records only the key.

**It is asked only of a team** (BartleBot #264): with fewer than two accounts
(`shared/account_setup.has_team`) the page is out of the walk, since one wizard has no one
to support it. The Accounts page's `walk_changed` recounts the walk, so a second account
added there brings it in. A page asked but left out of the walk is offered
`FirstRunPage.record_passed_over` on Finish, and this one records the key there on the
stored Team default, as Next with no card selected does: otherwise a second account added
later would open setup on the next launch for this question alone. Closing setup records
nothing, as always. Settings › Combat is still where the Team default is changed. The
other team question, Player Realms, is a bot option rather than a setup page, and is
grayed on the Options tab instead (`bartleby-core/docs/PLAYER_REALMS.md`).

### The game folder

wizwalker's `get_wiz_install()` looks only where the KingsIsle launcher installs, so a
Steam install was never found. Core's `bartleby/shared/game_install.py` looks further, and
`find_install` takes the first of these that holds `Bin/WizardGraphicalClient.exe`:

1. **The folder chosen**, `game.install_dir` in `preferences.json`, written by this page
   or Settings › General › Game folder, in either product.
2. **The launcher's folder**, `C:/ProgramData/KingsIsle Entertainment/Wizard101`
   (wizwalker's `DEFAULT_INSTALL`).
3. **The launcher's install record**, `InstallLocation` under its uninstall key in
   `HKEY_CURRENT_USER`.
4. **Each Steam library.** Steam's root comes from the registry (`SteamPath`, then the
   machine's `InstallPath`); its `steamapps/libraryfolders.vdf` (or `config/` on older
   clients) lists the libraries, in either of that file's two layouts, read with the
   `vdf` package. A library counts when it holds Wizard101's manifest,
   `steamapps/appmanifest_799960.acf`, and the game is in the `installdir` it names under
   `steamapps/common`.
5. **The game running now**: the folder above `Bin` of each running
   `WizardGraphicalClient.exe` (`running_executables`, a process snapshot read with
   ctypes). Last, because it is there only while the game runs, but it finds an install
   every other place missed: a user who can play has one somewhere. So `apply_install`
   stores a folder found this way as the folder chosen, and the next launch finds it with
   the game closed.

A folder without the client is passed over for the next, so a stale choice or a library
on a drive that is gone falls back rather than failing. At startup each product's `AppController` calls
`runtime/clients/install_location.apply_install`, which hands what is found to wizwalker's
`override_wiz_install_location` before anything asks `get_wiz_install()`; nothing found
clears the override, and wizwalker's own checks apply. The page and the Settings row call
it again whenever they store a folder.

The page shows a path field with **Browse…**, and a line under it: the place the folder
was found in, that the game is installed there, or that it isn't, naming the missing
client. Next is enabled only for a folder holding it. On a rerun the page opens on the
folder in use, and Next on a detected folder left as it was writes nothing, so a detected
install that moves is still found.

- **It watches for the game.** While nothing is found and the field is empty, the page
  looks for a running game every 2 seconds, while it is shown (`look_for_running_game`),
  and its intro says so: a user who starts Wizard101 the way they usually do has the
  folder filled in, "Found in the game running now." A folder the user typed is never
  replaced. Next stores the folder, since only a running game would find it again.
- **A rerun with nothing found passes it.** There, an empty field enables Next, the line
  says the folder stays unset and launching fails until it is chosen, and Next writes
  nothing. The rerun is how every other answer is changed, and a game on a drive that is
  gone must not hold them all. A folder typed that lacks the client is still refused. A
  first run still needs the folder: without it the app has nothing to launch.

Core's and BartleBot's suites look in no real place: their `tests/conftest.py` puts `NoPlaces` in, so a game on the
developer's machine can't decide which pages setup shows. A test that wants an install
sets `game_install._places` to one of its own.

### Accounts

The page draws the account cards Settings › Game draws, in the same `CardColumn`, with
**Add Wizard101 account** at its foot. Add, Edit (a click on the card, or its pencil), Remove (its
trash) and dragging to reorder are `accounts_tab`'s card actions, the ones Settings and the
launch screen call, so the editor, the duplicate and empty-field refusals, the delete
cascade and the writes are theirs. Each writes `accounts.json` as it happens, and a
password goes to Windows Credential Manager through `CredentialStore`, never to disk. An
account added here is on Settings › Game and in the Launch menu at once.

The intro says the game opens inside the product's window and is still the user's to play.
BartleBot's intro adds that while a bot runs their input reaches the game too (BartleBot #265). Testers took
the embedded clients for the app's own, and so neither played in them nor kept out of a
run. It is said here, where launching is first set up, rather than as a hint on the game
view, which would be on screen at every launch after the first.

So Next has nothing of the accounts left to store. It records `setup.accounts_answered`,
which is what keeps a user who chose no account from being asked again: the page may be
left empty, since clients started by hand can still be hooked, and with none it says so
and names **Launch › Manage Wizard101 Accounts…** for later. With an account in `accounts.json`,
added by either product, the page is answered without the key.

Every change on the page emits `walk_changed`, since how many accounts there are decides
whether the Launch profile page follows.


### Launch profile

The page applies once there are two or more accounts, so it follows the Accounts page
only when that page leaves two. It lists every account as a check box, all checked, in
the Accounts page's order, which is player order: the first checked is Player 1. The list
is read each time the page is entered (`FirstRunPage.enter`), so an account added or
removed on the page before is there, and one the user unchecked stays unchecked.

Next makes one profile of the checked accounts, named **Everyone**, or the first of
"Everyone 2", "Everyone 3"... no profile has (`shared/launch_profiles.free_name`). It is
built and added by `build_profile` and `add_profile`, which Settings › Game's Add uses
too. A line under the list says what Next will do.

The profile gets a resolution of its own, from a combo under the list that starts on the
largest that fits the accounts checked (`LaunchProfilePage.largest_fitting`), with every size
too big for them grayed. Left on the global 800×600, two accounts on a 1080p screen would
make a profile the launch screen refuses as soon as setup closes; 560×420 is what fits
them. The fit is judged in the area the product hands the page (`setup_area`): BartleBot's
is its sidebars at their floors and its canvas at 220 px, whatever they are set to, so panes
setup already fitted to one account leave room for four. A smaller size the user picks is kept while it still fits the accounts checked, and falls back to the largest
once it doesn't. With none that fits, the line says so and the profile follows the global
resolution.

- **Fewer than two checked** makes no profile, and still records the page.
- **The same accounts in the same order already launching together** make no second
  profile (`matching_profile`), so a rerun, or Next tried again after a failed write,
  finds the one already made.
- **A failed write** keeps the user on the page, as any page's does.

More profiles, and every edit after, are in Settings › Game, under Launch Profiles.

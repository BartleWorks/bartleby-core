# Upstream libraries

wizwalker and wizsprinter are **ours**. Both live under `BartleWorks`, both are
maintained here rather than staged for someone else's PR queue, and both are pinned by
exact commit. The local checkout paths and repo URLs are in the root `CLAUDE.md`.
bartleby-core itself is released, and a product pins a release by its tag: *Core's
releases*, at the end.

## The libraries

- **WizWalker**: low-level Wizard101 memory hooking — `BartleWorks/wizwalker`
- **WizSprinter**: safe wrappers + combat DSL — `BartleWorks/wizsprinter`

Both are pinned by exact commit `rev` in `pyproject.toml`, using HTTPS URLs so CI can
fetch them without SSH credentials. They carry fixes the original projects do not have,
including the `SpellListControl` / `DeckListControl` memory offsets that
`bartlebot/combat/deck_io.py` depends on. Pointing either back at its original repo will break
deck reads.

## Branches

Both repos keep the same two long-lived branches:

| branch | holds | moves when |
|---|---|---|
| `main` | what BartleBot may pin | `dev` has been run in game: `git merge --ff-only dev`, then tag |
| `dev` | merged work, its test suite green | a feature branch is done |

A change is a feature branch off `dev`, merged back into it. **Pin only from `main`.**
`main` only ever fast-forwards to `dev`, so the two cannot drift and promoting is one
command; a fix that cannot wait goes on `main` and is then merged into `dev`.

`dev` is the state that had no home before: passing the suite but not yet tried in
game. Without it, wizsprinter's pins sat on the tip of a stack of feature branches
(`enchant-ahead` through `review-fixes`) while its `main` fell behind; those were folded
into `main` with `bartlebot-pin-20261021`.

Each library has its own suite (`uv sync && uv run pytest` in the checkout), against fakes
of the game's memory and windows: run it before merging into `dev`. wizsprinter's runs
against our wizwalker at the commit pinned here, named in its dev dependency group, so
move that with this pin. It sits there and not in `[tool.uv.sources]` because uv reads a
git dependency's sources too, and two wizwalker pins then refuse to lock.

## Where updates come from

Both began as forks of projects that still exist, and **the two relationships are not
the same**. Do not assume what is true of one holds for the other.

| | Track | How |
|---|---|---|
| **wizwalker** | `LaurenzLikeThat/wizwalker` (`development`) | **Unchanged by the move.** Laurenz is still the practical source of memory offset fixes after a game patch. Merge from her. |
| **wizsprinter** | `Deimos-Wizard101/wizsprinter` (`main`) | Selective. Take individual fixes worth having; their `main` is **not** our baseline. |

### wizwalker — still merge from Laurenz

Owning the library did not change this. `Deimos-Wizard101` is barely maintained and
resistant to PRs; Laurenz keeps a fork current, and she is the practical source of
offset fixes after a game patch.

The merge base with her `development` branch is what makes that a *merge* rather than a
manual re-derivation, so **never squash or rewrite wizwalker's history**. Losing the
common ancestor turns every post-patch update into a whole-tree conflict across every
file, permanently.

### wizsprinter — divergence is expected

We maintain this library. Changes land here directly; there is no upstream PR to open
first, and divergence from Deimos is the normal state rather than a smell. #6 is the
first change under this policy.

Pull from Deimos *selectively* — cherry-pick or merge a specific fix when it is worth
having, and read what changed before taking it:

```bash
cd ../wizsprinter && git fetch deimos && git log --oneline main..deimos/main
```

Do not fast-forward onto their `main`, and do not treat being behind it as a problem
to fix.

**Do not pull wizsprinter from Laurenz.** As of 2026-08 her fork differs from Deimos
only in packaging and docs — the library code is byte-identical — but she has not taken
the poetry→hatchling migration, so merging from her would regress the build system.

Commit counts between any two of these repos look wildly divergent (≈35 each way); that
is squash-merged PRs producing new SHAs for identical content, not real drift.
**Compare trees, not commit counts.**

## After a game update breaks memory offsets

Offsets change with most game patches. Laurenz usually fixes them first — check before
doing the work by hand. `docs/GAME_UPDATES.md` is the whole procedure for an update, from
the Test realm on: which hooks it breaks, the type dump, and replacing a pattern ourselves.
Changes for an update not yet live go on a branch named for it, here and in the library,
and none merges until the update is live.

```bash
cd ../wizwalker
git fetch laurenz
git switch dev
git log --oneline HEAD..laurenz/development     # anything new?
git merge laurenz/development                   # a real merge, never squashed or rebased;
                                                # expect conflicts: our commits touch offsets too
uv run pytest                                   # then smoke-test against a live client
git switch main && git merge --ff-only dev
git push origin main dev
```

Then bump the pin in this repo (see below). If Laurenz has not fixed it yet, re-derive
the offsets with `tools/probe_item_cards.py` and commit them to our repo — that script
exists specifically to make offsets recoverable.

## Bumping a pin

Local edits do **not** affect the app until they reach `main`, are pushed, and the pin
moves:

```bash
cd ../wizwalker
git switch main && git merge --ff-only dev      # once dev has been run in game
git tag bartlebot-pin-$(date +%Y%m%d) main
git push origin main dev refs/tags/bartlebot-pin-$(date +%Y%m%d)
cd ../BartleBot
# update the rev in pyproject.toml to main's full commit hash
uv lock && uv sync
```

Bumping wizwalker's pin also means moving the wizwalker reference in wizsprinter's dev
dependency group, on its `dev`, so its suite keeps testing against what ships.

While iterating rapidly, install the local copy as editable instead of bumping the pin
each time. **Every plain `uv run` syncs the venv back to `uv.lock` first**, which
uninstalls the editable copy and reinstalls the pinned commit without a word, so the
run that follows tests the pin, not your edit. Pass `--no-sync` on every command while
the editable copy is in:

```bash
uv pip install --no-deps -e ../wizsprinter   # live editing; --no-deps leaves the other pin alone
uv run --no-sync pytest                      # --no-sync every time, or the pin comes back
uv sync                                      # back to the pinned commit
```

Before trusting a result, check which copy was imported — this names the checkout when
the editable copy is live, and `.venv` when it is not:

```bash
uv run --no-sync python -c "import wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser as p; print(p.__file__)"
```

Never copy an edited file over the one in `.venv` instead. uv installs by hardlinking
from its cache, so the copy rewrites the cached pinned wheel too, and `uv sync` then
"restores" your edit. If it has happened, `uv cache clean <lib>` and then
`uv sync --reinstall-package <lib>`.

**Tag every commit you pin.** A pinned SHA reachable only from a branch tip can be
orphaned by a force-push, rebase, or branch rename, and `uv sync` then fails for
everyone including CI. Tag it *before* bumping the pin:

```bash
cd ../wizwalker && git tag bartlebot-pin-$(date +%Y%m%d) main && git push origin refs/tags/bartlebot-pin-$(date +%Y%m%d)
```

Tags follow `bartlebot-pin-YYYYMMDD`. A second pin on the same day takes a letter after
the date, as `bartlebot-pin-20260926b` did. Never delete a tag that an older commit of
this repo still pins, or you break the build at that revision. The tags are also what
keeps a pin reachable once the feature branch it was made on is deleted. This is the
forks' scheme alone: core has releases, tagged `vX.Y.Z` (*Core's releases*, below).

Current pins, both on `main`:

- **wizwalker `20c7ba4`** (`bartlebot-pin-20261101`): `client.video_manager`, which changes a
  running client's resolution with no hook. It writes the live `Resolution` node and sets the
  pending byte, the manager coming from the static slot its base constructor stores `this` in,
  and every field offset from the code of the function that applies a pending change
  (`memory/memory_objects/video_manager.py`). Nothing is installed, so it adds nothing to
  `_active_hooks` or the autobot. Nothing calls it yet: it is for BartleBuddy #3's resizer. On
  top of `e73a736` (`bartlebot-pin-20261024`): every click and key sent from a
  worker thread of the window's own (`window_messages.send_message`), not the event
  loop. `SendMessageW` waits for the game to handle the message, and one client that
  took 6 s to answer a friend teleport's Go To Friend click held every other client and
  the app's window with it. The caller still waits for the answer; nothing else does.
  Messages to one window keep their order, and a cancelled send is still delivered, so a
  release follows its press. `instance_login` still sends synchronously. On top of
  `53929b7` (`bartlebot-pin-20261023c`): windows found by their paths from
  the UI Tree Browser, not by a search of the whole UI, which is about 30 ms of reads
  a time and takes the first window with the name (`window_at_path`, `windows_at_path`;
  wizsprinter imports them). The combat handler, the deck builder and the friend
  teleport use them, falling back to the old search for a layout a path does not
  describe. On top of `b9b0ff7` (`bartlebot-pin-20261023`), which reads the chat log from
  its tail, past `MAX_STRING`, so drops are seen again once the log grows, and `8783db0`
  (`bartlebot-pin-20261021b`): its test suite, `utils.poll_until`,
  and a review's fixes. The ones BartleBot meets: a port to a friend from a friends list
  left on a later page went to whoever sat in that row there; `in_battle()` never
  returned before the client hook had run; `DeckBuilder.open_deck_page` never recorded
  the deck page (so `deck_io.apply_deck` no longer sets `_on_deck_page` itself);
  `clear_deck` answered its confirm box before the box was up; and a combatant's Health
  window was never waited for. The deck page, friends list and message boxes are polled
  where they were fixed waits, and the round waits read the duel every 0.2 s, not 0.5 s.
  On top of `7900369` (`bartlebot-pin-20261021`), which polls for the friend-teleport
  confirmation.
- **wizsprinter `088fc83`** (`bartlebot-pin-20261023d`): casts that land. Reads by path
  are fast enough to catch the game mid-layout, and a Heretical Academy run lost five
  casts in 173 rounds, three Balaur fights ending in the idle popup's flee. A fused card's
  cast is now seen to leave the hand and is cast again when the game dropped it; the card
  to cast after a fuse is found once the hand reads the same twice; each cast reads its
  target again from fresh combatants, so a Balaur head rebuilt since the round began is
  not clicked; and an enemy target missed while the fight reads empty is read again for
  up to a second. On top of `7be91be` (`bartlebot-pin-20261023c`): the Draw button and
  the pet card found by their paths under the planning phase's window, where a grayed
  Draw found first ended a draw that should have gone ahead; its suite runs against
  wizwalker `53929b7`. On top of `3d21005` (`bartlebot-pin-20261023`): the deck's card count read from
  `Hand/DeckCounter/CountText` by its path, where a search of the whole UI by name read
  another `CountText` and spent 2 s a call timing out to (0, 0), and each config text
  parsed once however many providers are made from it. On top of `6f06c87`
  (`bartlebot-pin-20261021`): its test suite, including copies of the `SprintyCombat`
  tests in `tests/wizsprinter` here, which stay as this repo's guard on what it pins
  while the forks have no CI; each card effect read once however many requirements ask
  about it; an enchant's fuse seen when it lands rather than after a fixed
  `cast_time * 2`; `CombatConfigProvider.from_string`; and the generator backend's
  unawaited round. Earlier pins are in `git log` and the earlier tags.

## Core's releases

Core has a version of its own, independent of either product, and a product pins a release
of it. Two products take core, so a change one product drove can break the other, and the
version and the changelog are how the other one finds out.

- **SemVer, with Cargo's rule while core is 0.x.** A minor bump (0.2.0 to 0.3.0) means a
  product may have to change: a name removed or moved, a call an old caller makes that now
  fails, a stand-in or protocol that needs more, a behavior a caller relied on. A patch bump
  (0.2.0 to 0.2.1) means nothing breaks: additions, fixes and docs.
- **A release is cut when a product needs to pin a change**, not per merge. A docs-only
  merge never needs one, and several merges can wait for the same release.
- **The tag is the version.** A release is a tag `vX.Y.Z` on a commit of `master`, and
  `hatch-vcs` reads it at build time (`[tool.hatch.version]` in `pyproject.toml`), so no
  file holds the number. A commit past the tag builds as the next patch, marked as a
  development build (`0.2.1.dev3+g1a2b3c4`). Only `v` tags count: the old
  `bartlebot-pin-YYYYMMDD` tags on core are ignored, and kept, since older BartleBot
  commits pin them. Never delete or move either kind.
- **`CHANGELOG.md` says what each release changes**, with a **Breaking** list of what a
  product must fix to take it. A merge that changes what a product sees adds its line in
  the merge itself, under `## Unreleased` at the top, starting that heading if the last
  release left none.
- **Core's milestones are named for its versions** (`0.2.0`), in BartleWorks/bartleby-core.
  An issue meant for the next release goes in its milestone, which closes when the tag is
  pushed.

### Cutting a release

From an up-to-date `master` with the suite green. The last release tag is the base:

```bash
cd ../bartleby-core
git switch master && git pull --ff-only
git log --oneline v0.2.0..master            # what the release holds
# Breaking not empty: the minor (0.3.0). Otherwise the patch (0.2.1).
# CHANGELOG.md: "## Unreleased" becomes "## 0.3.0", committed on master
git tag -a v0.3.0 -m "bartleby-core 0.3.0"
uv build --wheel -o <a scratch folder>      # the wheel's name says 0.3.0
git push origin master refs/tags/v0.3.0
gh api -X PATCH repos/BartleWorks/bartleby-core/milestones/<number> -f state=closed
```

### How a product takes a release

By tag, in its `[tool.uv.sources]`; `uv.lock` records the commit the tag named:

```toml
bartleby-core = { git = "https://github.com/BartleWorks/bartleby-core.git", tag = "v0.3.0" }
```

Moving the pin is a change in the product, on its own branch:

```bash
cd ../BartleBot
# read CHANGELOG.md's Breaking for every release after the old pin, and fix each item
#   bartleby-core = { git = "https://github.com/BartleWorks/bartleby-core.git", tag = "v0.3.0" }
uv lock && uv sync && uv run pytest -n auto
```

A product pins a release, never a commit between releases: when it needs a change, cut a
release. While a stage spanning the repos is built, the product's branch takes the sibling
checkout instead, `bartleby-core = { path = "../bartleby-core", editable = true }`, and an
edit to core is live at once, with no `--no-sync`, since uv keeps an editable path install
as it is. Core merges first and is released, and the product's merge carries the new tag.

BartleBot always pins a release, since its release build cannot build from a path. Its
`rev` pin at `1b1caab` predates core's releases, and BartleBot #301 moves it to `v0.2.0`.
BartleBuddy takes core by path, editable, until it is packaged (BartleBuddy #4), and then
pins by tag the same way. With the path, its installed version is the development build
after the nearest tag, built again on a sync once core's commit or tags move
(`[tool.uv] cache-keys` in core's `pyproject.toml`).

Core has one long-lived branch, `master`, and it is never rewritten, so every tag stays
reachable.

## Remotes and identity

Each checkout has the same shape:

| remote | points at |
|---|---|
| `origin` | `BartleWorks/<lib>` — where our work goes |
| `laurenz` | `LaurenzLikeThat/wizwalker` — offset fixes (wizwalker only) |
| `deimos` | `Deimos-Wizard101/<lib>` — the original project |

Both checkouts set a repo-local `user.name` / `user.email`. Check
`git config --local user.email` before committing — fixing an author afterwards means
rewriting, and rewriting is what orphans a pinned SHA.

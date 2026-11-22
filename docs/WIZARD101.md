# Wizard101, as it shapes the design

Game rules that constrain how a Bartleby product works and that the code cannot show. Each
was stated by the user while a feature was being designed, and several corrected an
assumption carried over from other games, so ask before designing around a mechanic not
listed here. The date and issue say where each came from.

## Characters and accounts

- **An account can only have one active character at a time** (2026-09-06, BartleBot #201).
  That is what makes "one hooked client = one account" true: any per-client identity, color
  or label scheme can treat account and client as 1:1 while the client is hooked. The
  converse does not hold: a client in slot 2 can be the account's *fourth* character, so slot
  number and character are unrelated (`qt/game_embed/client_tile.py`).
- **Each character has its own bank**, and a full backpack overflows into it. The **shared
  bank** moves items between an account's characters; nothing ever overflows into it
  (2026-10-22).
- **Crowns items are always sellable** through quicksell (2026-10-22).
- **A character's id lasts.** `m_characterId`, which `live_character_id` reads, was the same
  for each of four wizards after a logout to character select and back, and after the client
  was closed and relaunched (measured 2026-11-21, BartleBuddy #32). It can key what is saved
  about a wizard.

## Moving, and what the server allows

The server checks movement the client claims, never movement the server causes. How much it
lets the client claim has changed twice in 2026:

| movement | who causes it | reset? |
|---|---|---|
| a position written to memory, far from where the server has the wizard | the client | no, on Ahmarra's live build (r813697, 2026-11-16); yes, at once, from late October until then |
| a short hop written to memory, as past a gate | the client | no |
| walking by the movement keys | the client, at walking pace | no, but see resets below |
| a teleport volume stepped into, an interact teleporter | the server | no |
| a zone change, the World Gate, porting to a friend, Mark and Recall | the server | no |
| the run to a duel circle when a fight starts | the server | no |
| the yaw (facing) written to memory | the client | no: the camera turns a wizard at any rate |

- **A long written teleport holds again.** One of 12,842 units across Olde Town held 15 s
  each way on r813697 (`GAME_UPDATES.md`, *Record: r813697*). From late October the server
  reset any position it could not have seen walked to, and Ahmarra first shipped with that;
  it was rolled back. A change like it can come back with any update, so a bot's teleports
  are worth a look after each one.
- **Resets happen during ordinary walking too.** On a +40% mount the server threw a wizard
  back 221 to 655 units, to where it had stood about 0.7 s earlier; players see the same
  rubber-banding.
- **Walking pace** measured 600 units a second on foot on flat ground (wizwalker's constant
  says 580). A mount multiplies it, most by 1.4 and the fastest by 1.55; the height of a
  stair is free.
- **The speed multiplier may be written, up to 2.0x.** The engine is built to allow 2.0x, so
  the client's `speed_multiplier` (a percentage, `write_speed_multiplier` in wizwalker) may be
  written up to 100. The field holds the whole bonus, a mount's included, so 100 is 2.0x in
  total, never 2.0x on top of a mount. It is never raised above 100 to find where the
  server's limit is.
- **The game's own group tools**, on the group HUD since Ahmarra: **Teleport to Leader** ports
  to the leader as a friend or roster port does, in fewer clicks and with no confirmation, so
  between zones it is the fastest of the three ways, and within a zone close to a written
  position. **Follow Leader** walks the leader's path and ports when it falls behind, worse
  than our own follower (`bartleby.runtime.movement.follow`) at zone changes and on winding
  paths (both tried by hand on 2026-11-16). The client asks for it with `MSG_RequestGroupFollow`; `GroupMember` has
  `m_followingLeader`. The group menu button (`windowHUD/GroupSystemButton`) shows only
  while the player is in a group, so the HUD says who is grouped with no window opened. Its
  `LeaderText` label shows only on the leader, and **only while that client's group menu is
  open**: its visibility flag follows the screen, so a leader with its menu closed reads
  the same as a member, and who leads is read by opening the menu (two clicks, putting it
  back). A Gather from a leader whose menu was closed sent everyone through the friends
  list; with the menu open, they went by the group (2026-11-17). **The menu and its buttons
  hide in battle**, so a read then says nothing about the group, and nor does one while loading.
  The menu never closes by itself, not after a teleport and not on a zone change. On the
  leader its teleport button is **Ask Group to Teleport**, a summon; on a member it is
  Teleport to Leader, which is refused with the same dialogs as a friends-list port when the
  leader cannot be reached, and otherwise lands on the leader's exact position, as a
  friends-list port does (all checked on r813697, 2026-11-17). Leadership cannot be handed on: the leader changes only when the leader
  leaves the group or logs off.
- **A port is no substitute for walking with the leader**, even with long teleports holding
  (BartleBuddy's testing, 2026-11-20): a solo zone refuses a port to someone inside it, a port
  over a short distance costs a loading screen that walking it doesn't, and a wizard ported
  beside a fight was not seen to join it. So following the leader's trail by keys
  (`bartleby.runtime.movement.follow`), shelved on 2026-11-16, is back, porting only as its
  fallback.

Walking routes, planned from a zone's own files, were built while long teleports were reset,
and are still shelved on the `walking-shelf` branches of core and BartleBot since 2026-11-16.
Core's holds `docs/WALKING.md`, with every measurement and what was not done.

## Battles, the ghost window and wisps

Stated 2026-09-27 for BartleBot #117; the full write-up is the design comment on that issue.

- Battles trigger on proximity to a mob, a fixed radius that counts height. Mobs walk fixed
  per-zone paths and do not chase. Client lag makes a fast approaching mob trigger from what
  looks like far away on the client (measured up to ~886 units).
- At the trigger the wizard is force-run in a straight line to a duel circle seat;
  `in_battle()` turns true noticeably later.
- **The ghost window after a fight**: 30 s if the wizard stays completely still, ~4-5 s from
  its first movement otherwise. Rotating and teleporting both count as movement. It is not in
  memory.
- Duel circles are fixed spots, visible only while a fight is on them; stepping on an active
  one pulls you in.
- **In a solo zone every wizard has an instance and a duel of its own**, in a zone of the same name
  as the others'. Only the duel's participants tell them apart (`runtime/combat/duel_members.py`),
  which is how the fight runner's reports always did; BartleBuddy ran four of them as one team fight
  on 2026-11-22.
- Wisps are shared, but simultaneous arrivals both get the reward. A wizard full on a
  resource does not collect that wisp kind.

## Fleeing, defeat and the hub

Stated 2026-09-30 for BartleBot #116.

- **Flee is never disabled** in any fight; it is clickable only during the planning phase.
- Fleeing lands the player in its world's hub as if teleported, with **empty mana**. Mana
  wisps are almost always reachable from where fled players land.
- **Mana never regenerates on its own** (stated 2026-11-06). Only mana wisps and potions
  restore it.
- **Every world's hub zone has mana wisps, but at Lemuria's hub teleport point none is in
  render range** (checked in game 2026-11-06). Every other world has one in range there.
- A player defeated in a fight the group wins comes back at 1 health and otherwise behaves
  as if it won.
- The world hub is always a safe spot. The planning timer (30 s) starts when cards become
  selectable, and restarts whenever a new player joins.

## Level scaling and the Scales of Knowledge

Stated 2026-10-02 and 2026-10-04; BartleBot's `docs/LEVEL_SCALING.md` has what was built.

- Every world except Wizard City has a **Scales of Knowledge** in its final dungeon, and some
  level-gated dungeons (each Aquila dungeon) have one too: roughly 25 in all. One offers an
  elixir that scales the player's stats down to the level the dungeon was released for, in
  10-level steps (each world raised the cap by 10). A scaled player can receive additional
  loot.
- So level scaling is a **finite, fixed set** of actions, one per Scales, not free-form
  per-bot data.
- Which Scales a farm uses follows the level of what it farms, normally the Scales of that
  same world. An alternative is chosen only when the main one is unusable: Azteca's level 90
  Scales are bugged, so its farms use Tartarus's.
- Almost every Scales is right inside its dungeon's entrance. The two exceptions are the
  level 90 Scales of secret bosses nested in another dungeon: Pit of the Noxii (inside Mount
  Olympus) and The Poseidon Adventure (inside Atlantea), each one zone change in, through a
  gateway that can be teleported to once the dungeon starts.

## Challenge Mode, stat caps and potions

Stated 2026-10-04.

- Challenge Mode **level scales players automatically** to the dungeon's level (Ransacked
  Throne Room 40, Temple of Sisyphus 90; max player level is 180), so inside it their max
  health and mana are a fraction of normal.
- A dungeon scales players to the level of **the Scales of Knowledge in its own dungeon
  area** if it has one, otherwise to **its world's Scales**. Wallaru (170) and Darkmoor (180)
  have Challenge Mode and no Scales; Darkmoor is max level, so it scales nobody.
- **Stat caps** scale a stat proportionally: scaled = stat × cap(target level) / cap(own
  level). With cap 100 at 180 and cap 50 at 90, a player with 50 has 25 when scaled to 90. A
  player obeys the caps of **the lowest step it has not exceeded**: level 90 takes 90's caps,
  91-100 take 100's. The caps apply only in **advanced combat**, which for a bot means only
  Challenge Mode.
- The caps are game data: `LevelScaledData.xml` in `Root.wad`, a `LevelScaledInfo` per school
  per 10 levels (10..180) with `m_canonicalMaxHealth` and `m_canonicalMaxMana`, readable with
  katsuba. Zone wads do not record a Challenge zone's level; that is server side.
- **On entering**, current health is **clamped** to the scaled max, not scaled. **On leaving**,
  the caps return to normal but current health and mana do **not** refill: the player keeps
  the absolute values it left with, which almost always reads below normal potion thresholds.
- **Potions cannot be used inside a Challenge Mode dungeon**, so potion handling happens
  outside, before entry, asking "current / scaled max", capped at 1.
- **Side boss rooms inside larger dungeons** (Gladiator Dimachaerus in Mount Olympus, Cronus,
  Sand Squid Tentacles): in Challenge Mode their own Scales disappear and the boss is fought at
  the *greater* dungeon's level.
- **Boss rematches have Challenge Mode** but are entered gauntlet-style from housing items, so
  their level band cannot be found automatically and needs entering by hand.

## Marks, recall and leaving a dungeon

Stated 2026-10-03 and corrected 2026-10-05.

- A mark survives recalls: recall as many times as you like while you have mana.
- Marking has **no cooldown**, but it **costs mana**, so a drop in current mana shows a mark
  click landed. MarkButton is grayed when marking is impossible: no mana, in combat, or in a
  zone that refuses marks (a house). Marking works in the Four Dungeons' entrance zones.
- **Marking and recalling each cost 10 mana, or a tenth of the player's maximum mana if that
  is smaller** (stated 2026-11-06). How the game rounds the tenth isn't known.
- **The hub and HOME buttons, and teleporting to a friend or group member, cost no mana**
  (stated 2026-11-06). A player with empty mana can still reach the Commons and teleport
  back to another player.
- **Recall has its own 30 s cooldown, not shared** with the hub and HOME buttons; only hub
  and HOME share a timer. While recall's runs, `RecallButton/txtRecallTimer` is visible and
  RecallButton is grayed. Grayed with no timer means no mark is set anywhere, or recall is
  impossible right now (too little mana, combat).
- Marking again while standing on the current mark **clears** it; marking anywhere else
  moves it.
- Drops are granted the moment the final boss is defeated, with no cutscene wait. Recalling
  right after can skip the cutscene, which is usually what you want.
- Many dungeons have no walk-out exit: players teleport to the hub, or use a teleporter that
  appears only after the final boss.

## Skeleton key doors

Stated 2026-10-31 for BartleBot #284. The carry-in was seen at Castle Darkmoor's Stone
Skeleton Key room; its 7 s has not been timed at other doors.

- Only a player holding the right key gets the door's interact prompt, so a missing prompt
  means no key, or a door already open.
- One press spends one key and opens the door for everyone in the instance. A player on
  another realm has their own instance and their own locked door.
- The door's zone-change trigger works once it is open. Anyone can walk into it.
- The player who used the key can walk in like anyone else. **If they have not within about
  7 s of the press, the game carries them in by itself**, even from outside the trigger. A
  teleport into the trigger just as that fires is applied once they land, which puts them back
  out.

## Dungeon sigils and relogging

Stated 2026-10-20 for BartleBot #279.

- Standing on a sigil does **not** join you to anyone's entry. Each player presses X on it,
  which starts a countdown, and up to four players who press X within the same countdown
  window enter together. A stranger idling on the sigil is harmless; one who ends up inside
  pressed X in our window.
- **What one entry looks like** (stated 2026-11-20, for BartleBuddy's followers). In range of a
  sigil, the interact prompt shows, with a **Team Up button on every dungeon sigil** in the
  game, and on no other prompt (`paths.DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH`; gauntlets have
  none). Pressing X **closes the prompt** and auto-walks the wizard to one of the sigil's seats,
  usually four, as the run into a fight does. The walk starts a moment after the press, so a
  reader sees the wizard stand still where it pressed first (observed 2026-11-20: taking that
  spot for its seat made every follower cancel when the walk began). A **10 s countdown**
  follows. The first press starts it, and every press within it shares it, so a late player is
  fine as long as it pressed before the others loaded. **Any movement cancels a player's
  countdown**, forward, back or a turn, and its prompt almost always comes back within a second.
  **One player cancelling cancels no one else's.** Then a loading screen, and the dungeon. **As
  the countdown ends, the prompt shows again for a moment** before the loading screen (observed
  2026-11-20: back 11.1 s after the press, gone 0.9 s later, then the zone changed), so a prompt
  back that late is no sign of a cancel.
- **The prompt's range reaches far.** Across BartleBot's 91 `enter_dungeon` nodes, whose
  `sigil_xyz` is the first seat and whose `reset_xyz` an author puts outside the range, the two
  are 430 to 1,438 units apart, median 660. So the range is usually a few hundred units, and
  a team standing behind a player at its edge can leave its last wizard outside.
- Relogging from inside a dungeon puts players back **where they were before entering**:
  usually on the entrance sigil, but elsewhere if they teleported in.

## Decks and the backpack

Stated by the user and measured on live clients on 2026-11-21, for Stage 1 of BartleBuddy's
Auto-combat epic (#31, #32); the measurements are `tools/probe_combat_facts.py`'s RESULTS, in
BartleBuddy.

- **A wizard owns several deck items and wears one.** Players keep, for instance, a Main Deck
  and a Boss Deck and switch between them; a deck can't be changed in a fight. **Next Deck**
  (Ctrl-K) cycles through every deck the wizard has, so with three or more it can't flip
  between two.
- **Equipment sets can include a deck**, so equipping a set can change the deck too.
- **A deck's name is picked from three wheels**, `DeckNames.lang`'s `Class_N` (a school or
  world), `Type_N` (Main, Boss, Dungeon, Piercing…) and `Num_N` (1 to 7), each with an empty
  choice at 0, and composed by its `DeckName` key (`$PART0$$PART1$Deck $PART2$` in en-US). The
  text differs by locale, and so can the wheels: en-US has a `Class_36` (Ahmarra) that de does
  not yet. Two `Type` entries share their English text (`Healer` is 12 and 18, `Tank` 13 and
  21), so a name's text doesn't always say its keys. Equipment sets are named the same way,
  from `EquipmentSetNames.lang`'s `Wheel0` to `Wheel2`. `bartleby.shared.deck_names` holds a
  name by its keys (`DeckName`), with its text in every locale and each wheel's choices, from
  a snapshot `tools/dump_deck_names.py` writes. Only en-US's text has been compared with a
  backpack; another locale's is composed the same way, and matched whatever its spacing. A few
  locales' parts are a verb the game conjugates (it's `Class_7`, Storm), and what it shows for
  one is not known.
- **Two decks of one wizard can share a name**: the rename window accepts a name another deck
  already has.
- **In memory, a deck's name is three uint32 wheel indices** in its `BasicDeckBehavior`, past
  the fields the type dump reflects: +0xF0 `Class`, +0xF4 `Type`, +0xF8 `Num`. The deck item
  itself (`WizClientObjectItem`) holds no name; `m_displayKey` is empty. wizwalker reads them
  (`ClientDeckBehavior.name_indices`). **A deck never named reads (0, 0, 0)**, and the game shows it by its item's own name ("Headmistress' Dynamic
  Deck"), not as "Deck". An equipment set's `m_equipmentSetName` packs its wheels a byte each
  (`[0, 1, 0, 0]` is `Wheel1_1`).
- **The equipped deck is only in the equipment's `item_list`**
  (`ClientWizEquipmentBehavior`); every other deck is in the inventory's
  (`ClientWizInventoryBehavior`), the equipped one never. A wizard's decks are the two
  together: `bartleby.runtime.decks.owned_decks`. `worn_deck` reads the equipped one alone.
  Which deck each wizard wants is a deck set's (`bartleby.shared.deck_sets`), by these names.
- **The backpack's Decks tab** (`InventorySpellbookPage`, `ButtonLayout/Tab_Deck`) lists the
  equipped deck first, its `fist` icon visible, then the rest alphabetically: eight rows a page,
  `Item_1` to `Item_8`, paged by `rightscroll` and `leftscroll`, with `PageNumber` reading "1/2".
  A row's text is the deck's name as the game shows it. The order is fixed when the backpack
  opens and stays until it is reopened; the `fist` follows the deck on.
- **Equipping a deck is select, then `Equip_Item`**: click the row, then the button under the
  list (`windowForBtns/Layout/Equip_Item`). Double-clicking a row, or selecting it alone, equips
  nothing. From a closed backpack to closed again takes **1.7 to 2.1 s a wizard**, and four
  clients at once took 2.2 s. It works while the wizard walks.
- **The equipment behavior shows the new deck 0.0 to 0.6 s after the click**, not at once, so
  an equip is confirmed by polling it.
- **Equipping an empty deck asks first**: a message box, "Equipping Empty Deck", offers to copy
  the old deck's spells into it (`centerButton` Yes, `rightButton` No). **No equips it empty**,
  as it is. Until it is answered the backpack won't close. The box is under `WorldView`, not at
  the root where other message boxes are, so `MESSAGE_BOX_*` doesn't find it
  (`WORLD_MESSAGE_BOX_*`).
- **In code, `bartleby.runtime.decks.equip_deck(client, name)`** takes that route, under the
  caller's lease. Already on, not owned, owned twice, empty, in a fight, and a backpack that
  can't open are results with nothing pressed. Empty is the spell list reading 0 cards; what a
  deck holding only item cards reads is not known yet. The deck is confirmed by its global id,
  and a row whose deck turns out to be another that reads the same is passed over for the next.
  The worn row is never clicked, on the assumption that Equip on it takes the deck off, as it
  does a castle. The backpack is closed on every way out, a cancel included, unless the player
  had it open. `tools/equip_deck.py` lists and equips on live clients. Turning a page of the
  list, and a list in a language other than English, have not been seen in game.

## Combat as players fight it

Stated 2026-10-14 while reworking BartleBot's shipped default strategies (#212), and
2026-10-26 during the cheat survey.

### How fights usually go

- **Solo, mobs:** buff self, then AoE. A solo player usually can't kill mobs in round 1.
- **Solo, bosses:** several buffs, then AoE to kill the boss and its minions. A typical solo
  deck goes Potent Feint the boss, Feint the boss, Sharpened blade self, blade self, Frenzy,
  AoE.
- **Two players, mobs:** both AoE in round 1. Even strong mobs rarely take more than two
  rounds. A weak and a strong hitter both buff the strong one; two strong hitters each buff
  themselves and both hit.
- **Two players, bosses:** the support feints the boss and blades the hitter; the hitter buffs
  itself, then AoEs.
- **Three or four players, mobs:** everyone AoEs in round 1; against strong mobs three buff
  one hitter. Realistically never more than one round.
- **Three or four players, bosses:** three support, one hits, with the same roles as with two.
- **Team hitters usually carry no Feints** (they cost a pip), only zero-pip buffs, though what
  goes in a hitter's deck is the player's call.
- **Challenge Mode very often needs a multi-hit strategy**: minions then boss, or the reverse.
- **A hitter keeps buffing until a hit would kill.** Having a buff up already is no reason to
  stop.

### Buffs

- **Blade:** raises the bladed player's outgoing damage. Pairs well with AoE, less well with
  single-target hits.
- **Trap:** raises incoming damage on the trapped enemy. Traps tend to be stronger than blades,
  so they pair with single-target hits, and poorly with AoE on even-health enemies.
- **Blades and traps are usually school-specific**: Stormblade does not buff Fire attacks.
  Universal ones exist and are almost always weaker.
- **Feint:** a trap, generally the strongest buff in the game, used in every boss fight that
  allows it.
- **Mass Feint:** a 75% universal trap on every enemy, used whenever available. The only
  version players get is the item card `Feint Mass - Amulet`; the other "Mass Feint" spells
  belong to enemies or special duels.
- **Other mass traps (Mass Hex) rank below blades.** Most are ~30% and come from item cards, so
  can't be enchanted; a plain blade is ~35%, an enchanted one 45-55%. With a boss: Mass Feint
  first, then Feint on the boss, ahead of other mass traps.
- **Global:** raises all damage of its school in the duel, for both teams. The weakest buff
  kind; suits AoE and multi-hit strategies. Any school can cast any school's global, given
  access by training or gear.
- **Aura:** a buff on a target for several rounds, usually four. Positive auras go only on
  yourself (Frenzy: +40% outgoing, +30% incoming; Stabilize: -25% incoming; Mend: +outgoing
  healing). Negative auras go on enemies, are single-target today, and are usually weak, but
  still beat passing. The main one is **Attenuate** (+20% incoming damage, four rounds);
  special versions are school-specific: Typhoon is +35% Death and +35% Storm, Epiphany +35%
  Storm and Myth, and "Attenuate - Storm" and the like +25% for one school.
- **One aura per participant:** a negative aura cast on someone with a positive aura replaces
  it. A support should only replace an aura on the boss with a stronger one.
- **Shields:** teams usually carry none (killing faster wins). When a deck holds them it is out
  of necessity, and they are cast before any buff, especially solo.

### Stacking

- Two buffs with the same spell id don't stack, whoever cast them.
- An enchant makes a different buff: two Stormblades, one with trained Sharpened Blade and one
  with the item-card Sharpened Blade, stack; two plain Stormblades don't.
- A support should still cast a buff that won't stack rather than pass.
- A support's hit can use up buffs meant for the hitter. That, not attacking as such, is the
  risk in letting supports attack.

### Cheats and joining

- **Late** is joining after the first round's execution phase has run. **Stagger joining** is
  a common, legitimate strategy: the first players join, the planning phase starts, then the
  rest join; enemy aggression is set at the start of planning, so the late joiners take less
  damage. A bot must allow it.
- **A helpful or harmful aura** is decided by who it is cast on (self is helpful, an enemy
  harmful), never by its effects: Frenzy is helpful despite its +incoming damage.
- A cheat about wizards "having" blades or traps counts the **active effects on them**, not
  cards in hand.
- Prisms usually count as harmful Wards for cheats; helpful Charms usually include healing,
  accuracy and pierce charms; absorbs behave like shields (cast on allies).
- Elites and minions almost never cheat in standard mode; only bosses do. Standard and
  Challenge versions of a boss usually cheat differently.

### Seats and casting order

Stated by the user on 2026-11-21, for BartleBuddy's Auto-combat epic (#31).

- **A team sits in the order it enters a fight**, unless it is an Adventure Party group, which
  sits in the slots its members chose (core #1).
- **The HUD shows the team in seat order**, the first seat on the right, going left, and every
  client in the duel shows the same order: no client puts its own wizard first.
- **The team casts in seat order** every round, so a blade from an earlier seat lands before a
  later seat's hit in the same round, and a team's hitter wants the last seat.
- **wizsprinter's team list is that order on every client**: `get_team()` reads the planning
  phase's combatant windows, which are those HUD portraits. So `ally(last)` is the wizard that
  entered last, the same one on every client. BartleBot enters its clients in combat order
  (4, 3, 2, 1) so that P1 sits last; a team that enters on its own, as BartleBot's ambient fights
  do (#212), seats whoever arrives last there. Since BartleBuddy #31's Stage 4 the fight runner
  hands wizsprinter the duel's combat order (`team_order`), which `ally(-N)` counts through
  instead of the seats, and BartleBuddy's followers walk in in that order
  (`bartleby.runtime.movement.join_order`). The casting order is still the seats'.
- **`is_boss` reads correctly at the first planning phase.** `runtime/combat/fight_kind.py` reads it
  there, from the duel's participants, for which kind of fight it is.

### Special cards

- **Incindiate** (`Detonate All 200 - Amulet`): detonates every damage-over-time effect,
  dealing all its remaining damage at once at 200%. A team combo: buff a hitter who casts a
  DoT, and the last player detonates it.
- **Willcast:** a pet ability that casts a second spell in the same turn, cast by the pet.
  Which spell depends on the pet's Willcast talent.

## Combat math, and what the DSL does not say

Neither is in our libraries, so the facts are recorded here. wizsprinter has no damage math
that uses stats: its `spell_ranking.py` scores a card only by the damage printed on it.
Deimos (`C:\GitHub\Deimos-Wizard101`) has a real caster-to-target calculation in
`src/combat_math.py` (`base_damage_calculation_from_id`: stat curves, blades and traps
counted once per stacking id of spell and enchant, prism, pierce against shields, resist,
globals, crit against block), used by its stat viewer; `src/effect_simulation.py` is a
fuller simulator nothing imports. Checked 2026-10-14 against wizsprinter `e0bd4b8`:

- `auras`, `beneficial_auras` and `harmful_auras` work as condition attributes (0 or 1),
  though `CONDITIONS.md` does not say so.
- `willcast` casts the pet's card at whatever target the line names, without reading what
  the pet's spell does, so a heal or blade Willcast aimed `@ enemy` is wasted.
- A spell template's `m_disposition` is 0 for every spell (Feint, Attenuate, Frenzy, Tower
  Shield...), so it cannot tell a buff from a debuff. Judge by effect type and the sign of
  `m_effectParam` (wizsprinter `_AURA_SIGN`). Attenuate is `modify_incoming_damage` at
  `enemy_single`, 4 rounds; a trap has 0.

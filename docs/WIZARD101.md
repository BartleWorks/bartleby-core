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
- Wisps are shared, but simultaneous arrivals both get the reward. A wizard full on a
  resource does not collect that wisp kind.

## Fleeing, defeat and the hub

Stated 2026-09-30 for BartleBot #116.

- **Flee is never disabled** in any fight; it is clickable only during the planning phase.
- Fleeing lands the player in its world's hub as if teleported, with **empty mana**. Mana
  wisps are almost always reachable from where fled players land.
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

## Dungeon sigils and relogging

Stated 2026-10-20 for BartleBot #279.

- Standing on a sigil does **not** join you to anyone's entry. Each player presses X on it,
  which starts a countdown, and up to four players who press X within the same countdown
  window enter together. A stranger idling on the sigil is harmless; one who ends up inside
  pressed X in our window.
- Relogging from inside a dungeon puts players back **where they were before entering**:
  usually on the entrance sigil, but elsewhere if they teleported in.

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

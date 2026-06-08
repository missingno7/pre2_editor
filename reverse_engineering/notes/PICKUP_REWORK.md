# Pickup logic rework + checkpoint anim + monster-on-secret guard

## How collidability/pickup actually works (from ASM/blues)

- An object becomes collidable when it is DRAWN on screen: level_draw_objects sets
  `obj->spr_num |= 0x2000` (level.c:3290). Collision loops skip anything without
  0x2000.
- WHAT an item does on pickup is decided purely by `num = (spr & 0x1FFF) - 53` in
  the level_update_player_collision item branch. There is no separate "collectible"
  flag in the level data (verified: no item raw sprite carries 0x2000).

## Fixes (runtime/game.py)

1. **Bonus drops weren't collidable.** `_add_object23_bonus` now sets 0x2000 (the
   original gets it at draw time), so dropped food/fruit (incl. big secret food,
   sprite 110+) can be picked up.

2. **Faithful `_pickup_bonus_or_item`** keyed by `num`:
   - special/effect: 226/258 semaphore -> level advance; 228 checkpoint;
     13/182/44/224 club; 174 life; 167/168/458/459 damage; 169 screen-kill;
     170 bomb; 180/181 lights.
   - collectibles by range: <=20 energy; <=44 BONUS letters (index num-39 ->
     player.bonus_letters_mask); <=50 utensils; <=166 food/score
     (SCORE_SPR_LUT[num-57]+74); 173 full-food.
   - ANYTHING ELSE (password digits num 230+, WELL DONE, decorative) -> NOT
     consumed/pickable (returns without removing). This is the one intentional
     deviation from blues, which removes every collided item; it matches the DOS
     behaviour the player described.
   Verified: big food picked up; BONUS letter sets mask; password char (num 230)
   left intact; semaphore triggers level transition instead of vanishing.

3. **Checkpoint animation** (`_trigger_checkpoint`): on touch, set respawn AND
   change the checkpoint item sprite 281->280 (triggered state), reverting any
   previously-triggered checkpoint 280->281 — mirrors blues, so the player sees
   the state change. Item stays in the world (not consumed).

4. **Monsters emerging on secret platforms** (`_is_unstable_secret_floor`): the
   type-10 ground-emerge search now skips tiles holding an active secret whose
   solidity changes when revealed (attr1[hidden] != attr1[revealed]); otherwise a
   monster spawns on a floor that later disappears -> the "mid-air" enemies. blues
   has no explicit secret check here (the original relies on tile attributes), so
   this is a targeted guard matching observed DOS behaviour.

All 16 levels smoke-test clean (move/jump/club).

## Still pending
- Player energy/lives/utensils/flying/light state not modelled (those pickups
  just vanish/score for now).
- Performance: render_frame still per-pixel; stutter pass outstanding.

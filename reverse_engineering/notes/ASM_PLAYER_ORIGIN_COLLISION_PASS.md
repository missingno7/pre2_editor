# ASM player origin / collision pass

Goal: make `run_game` converge to the DOS `PRE2.EXE` update order, not to the
`blues-master` rewrite where behavior may already be interpreted or simplified.

## Important correction: sprite anchor/origin

The sprite offset table stores the anchor point inside the sprite.  For player
sprites this is effectively the bottom-origin/contact point used by the gameplay
object.  The previous Python runtime rendered with:

```text
screen_x = object_x + origin_x
screen_y = object_y + origin_y - sprite_height
```

That is backwards for PRE2.  It made the player visually sink into floors and,
when horizontally flipped, moved the whole sprite by roughly the sprite width.
That exactly matched the visible bug where turning changed the apparent player
position.

Current rule:

```text
left = object_x - origin_x
 top = object_y - origin_y
```

For horizontal flip the anchor must stay fixed:

```text
flipped_origin_x = sprite_width - origin_x
left = object_x - flipped_origin_x
```

This is now implemented in `RuntimeWorld._draw_sprite()`.

## Important correction: object position is a bottom-origin point

The DOS code does not move a broad rectangular player hitbox and then snap its
edges to the map.  The player object has a single origin point and velocities in
1/16 px/tick.  The relevant `level_update_player()` order from the reference
control flow is:

```text
update input / animation / velocity
x_pos += x_velocity >> 4
y_pos += y_velocity >> 4
level_update_player_decor()
```

The previous runtime swept horizontally and vertically pixel-by-pixel with a
wide `PLAYER_W` rectangle.  That was playable, but not ASM-like and created
small jitter/snaps on slopes and against walls.

Current `run_game` changes:

- no broad horizontal/vertical sweep for the player
- x/y are advanced once by `velocity >> 4`
- floor/ceiling/side resolution happens afterwards in an ASM-shaped decor pass
- side collision reverts the last `x_velocity >> 4` step and zeroes velocity,
  matching `level_update_tile_type_1()` behavior

## Ground probe shape

The important DOS pattern from `level_update_player_decor()` / `level_update_tile0()`:

```text
y_pos = (player.y_pos >> 4) - 1
x_pos = player.x_pos >> 4
level_update_tile0((y_pos << 8) | x_pos)
```

`level_update_tile0()` then checks the tile one row below that offset.  In plain
words: ground is checked under the player's **origin x**, not under three sample
points or a full width box.

`tile_attributes3` slope offset also uses:

```text
(player.x_pos & 15) / 3
```

not a left/right sample position.  `run_game` now has:

- `_tile_attr3_player_offset()`
- `_asm_update_tile0()`
- `_apply_attr1_ground()`

These are deliberately partial but preserve this origin-based invariant.

## Side probe shape

The DOS side probe chooses exactly one horizontal probe column:

```text
dx = +9 if x_velocity > 0
   = -9 if x_velocity < 0
   =  0 if x_velocity == 0
```

Then it checks the tile at the player's lower probe and repeatedly moves upward
by 16 px until the sprite height is covered.  The sprite height comes from
`spr_size_tbl[spr_num*2+1]`.

`run_game` now implements this in `_asm_update_side_tiles()`.

## Still not complete / next work

This is still not tick-perfect.  It is a correction of the biggest structural
mistake.  Remaining work:

1. Replace remaining C-reference assumptions with confirmed PRE2.EXE offsets.
2. Build a better segmented 16-bit disassembly map instead of relying on a flat
   objdump stream.
3. Port the rest of `level_update_tile0()` behavior:
   - decor tile below player animation
   - top spikes / underside death
   - ceiling side nudges
   - hit object creation on landing
   - `player_prev_y_pos`, `shake_screen_counter`, reset edge cases
4. Port exact scroll interaction from `level_update_tilemap()` rather than the
   current smooth camera approximation.
5. Keep `blues-master/p2` as a guide only; the acceptance target is the DOS ASM.

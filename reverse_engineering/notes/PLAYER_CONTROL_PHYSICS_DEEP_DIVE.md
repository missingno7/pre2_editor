# Player control / physics deep dive

Source of this pass:

- `reverse_engineering/reference/blues_p2/level.c`
  - `level_update_player()`
  - `level_update_player_x_velocity()`
  - `level_update_player_hdir_x_velocity()`
  - `level_update_player_y_velocity()`
  - `level_update_player_jump()`
  - `level_update_player_anim_0/1/2/3/4/5/6/7/8/34()`
  - `level_update_player_decor()`
  - tile attr helpers around `level_update_tile_attr1_*()`
- `reverse_engineering/reference/blues_p2/staticres.c`
  - `player_anim_lut[]`
  - `object_anim_tbl[]`
  - `spr_size_tbl[]`

The C rewrite is still treated as a map, not as final proof.  Important facts
here should later be checked against `PRE2_unpacked_full.asm`.

## Original input mask

`level_update_player()` builds a 5-bit index in this order:

```c
mask |= key_right & 1;
mask <<= 1;
mask |= key_left & 1;
mask <<= 1;
mask |= (jump_button ? key_jump : key_up) & 1;
mask <<= 1;
mask |= key_down & 1;
mask <<= 1;
mask |= key_space & 1;
```

So the final index is:

```text
bit4 right
bit3 left
bit2 jump/up
bit1 down
bit0 space/action
```

The index is looked up in `player_anim_lut[32]`.

Important consequence: default controls use **Up** as jump and **Space** as club/action.  Space is not jump in the original default path.

## Horizontal direction semantics

The original computes:

```c
key_vdir = key_up | key_down;
key_hdir = key_left | key_right;
```

Then it changes `hdir` only when exactly one horizontal direction is pressed:

```c
if (key_right && !key_left) hdir =  1;
if (key_left  && !key_right) hdir = -1;
```

But acceleration uses `key_hdir`, not “exactly one direction”:

```c
if (key_hdir) {
    f = hdir << 4;
}
```

This means pressing **both left and right** still accelerates in the last facing
`hdir`, while also selecting the special both-directions animation through the
mask.  Our previous runtime incorrectly treated both directions as zero input.

## Horizontal velocity

Friction:

```c
abs(vx) -= 12 >> x_friction;
```

Acceleration/clamp:

```c
f = key_hdir ? (hdir << 4) : 0;
f >>= x_friction;
dx = vx + f;
vx = clamp(dx, -max, +max);
```

Main constants:

```text
run max       80  = 5 px/tick
jump max      48  = 3 px/tick
friction base 12  = 0.75 px/tick in 1/16 units
ground accel  16  = 1 px/tick in 1/16 units before friction shift
```

The jump helper has a subtle signed branch:

```c
if (x_velocity >= 48) friction();
else hdir_x_velocity(48);
```

So high leftward speed is not handled by the `friction()` branch; it is clamped
through `hdir_x_velocity(48)`.

## Vertical velocity and jump profile

Gravity:

```c
y_velocity += 16;
max fall = 192; // 12 px/tick
```

In flying/glide gravity mode:

```c
f = 4;
y_vel_max >>= 3;
```

Jump while the jump state is active:

```c
-65, -51, -35, -20, -10, -5, -2, -1, 0
```

These values are added to `y_velocity` over the first 9 jump-state ticks.  After
that, normal gravity applies.  Releasing jump leaves the jump animation state and
the normal airborne path then applies gravity through `level_update_player_jump()`.

## Ground detection and x_friction

The original does not only check ground during downward movement.  It runs tile
interaction every player update via `level_update_player_decor()`, then
`level_update_tile0()`, then the tile attr helpers.

When the player is on a tile top:

```text
attr1 type 1 -> x_friction = 0
attr1 type 2 -> x_friction = 1
attr1 type 3 -> x_friction = 2
attr1 type 4 -> x_friction = 3
attr1 type 5 -> one-way/action-sensitive handling
attr1 type 6 -> deadly
```

Our previous runtime only set `on_ground` during positive vertical motion, so the
player could stand still with `on_ground=False`.  That broke jump gating, run
state selection, and friction.  The runtime now refreshes ground contact every
tick even when `vy == 0`.

## Collision height

`level_update_player_decor()` uses the active sprite height:

```c
spr_h = spr_size_tbl[spr_num * 2 + 1];
```

It starts from the player's bottom point and walks upward through tile rows.  The
horizontal probe is roughly +/-9 px in the movement direction, not the full
visible sprite width.

The runtime now syncs `PlayerState.collision_h` from the active sprite's height
instead of using a fixed 32 px box for every animation frame.

## Update order in current Python runtime

Current approximation after this pass:

```text
1. update platforms
2. refresh ground contact, even if vy == 0
3. update hdir/facing exactly like level_update_player()
4. build player_anim_lut mask
5. run selected animation/physics branch
6. x += vx >> 4
7. y += vy >> 4 with collision probing
8. refresh ground contact if dy == 0
9. camera/render
```

Remaining differences to investigate:

- exact `level_update_tile0/1/2()` side/ceiling dispatch
- exact attr2 underside behavior and special tile reactions
- exact `level_update_screen_x_velocity()` wind/snow interaction
- exact `player_anim_0x40_flag` club timing and hit object spawning
- flying/gliding state (`player_flying_flag`) and its animation overlay
- camera scroll side effects in idle/both-direction animation paths
- landing bounce/shake logic after long falls

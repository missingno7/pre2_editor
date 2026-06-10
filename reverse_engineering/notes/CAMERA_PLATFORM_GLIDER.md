# Vanilla camera default + stutter, platform collision, glider flying state

## Vanilla camera is now the DEFAULT + walk-drag stutter fixed

`camera_mode` defaults to "vanilla" (the original tilemap scroll); smooth stays on
the F4 toggle. The stutter while dragging the camera at the screen edge was the DOS
`tilemap_xpos &= ~1` even-pixel snap in `_adjust_x_scroll_original` (mode-13h scrolled
2px-granular). With our 1px render + interpolation that snap makes the player jitter
±1px. Removed the snap (pixel-exact follow). Verified: player screen-x stays constant
(192) while walking and dragging the camera — no jitter.

## Platform "stand next to it" — FIXED to blues' exact collision

blues platform landing (`level_update_objects_decors_helper`) calls
`level_objects_collide(player-as-sprite-7, platform)`, whose horizontal overlap uses
the two sprites' spr_offs/spr_size boxes and the LEFT-most object's HALVED width
(`width >> 1`) — a tight, centre-biased check. Ours used a hardcoded `[x-4, x+52]`
box (too wide + no halving), so the player could stand beside the platform and still
be carried. Now uses the platform's real sprite box + the spr-7 player box with the
halved-width rule (PlatformState gained `surf_spr`). Verified: lands only within ~±8px
of the platform centre (32px sprite), not at the edges/beside.

Idle placement: verified render == collision (origin_y == surf_h == sprite height, so
render top == collision surface top == `y - spr_height`), matching blues.

## Glider — full flying STATE (was just a halved jump)

Implemented the proper flying state (blues player_flying_flag / player_gravity_flag):
- Pickup (num<=74) sets flying; the player now HOLDS the glider.
- Running fast (|vx|>=64) builds player_runup_counter; a jump with runup>=24 LAUNCHES
  into a glide (level_update_player_anim_34: gravity_flag=1, lift, runup=0).
- While gliding, gravity is gentle (level_update_player_y_velocity: accel 4, fall cap
  >>3) so the player drifts down ~1px/tick instead of falling.
- Flying sprites: 45 rising / 46 falling (airborne), 48-51 glide flap.
- Wing overlay (objects_tbl[0]) positioned from player_flying_anim_data
  (level_update_player_flying). Verified visually: the hang-glider wing sits above the
  caveman and the player glides slowly across a gap.

Remaining (documented): the full in-air CONTROL scheme (blues level.c 2429-2465) —
hold UP to flap/gain height, DOWN to dive, the player_flying_counter stamina, and the
gravity_flag bit-2 handling. The foundation (state, glide gravity, launch, wing,
sprites) is in; the UP/DOWN flap control + stamina is the next piece.

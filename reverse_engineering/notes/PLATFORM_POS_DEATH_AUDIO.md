# Platform position, death animation, audio

## Platform standing position (vs ASM)

A platform's record `y_pos` is the sprite's BOTTOM anchor; blues
level_update_objects_decors_helper stands the player at `y_pos - spr_height`
(top of the sprite). The runtime was snapping the player to `state.y` (the
bottom), so he sank into the platform by its height (13px on level 0).
Added `PlatformState.surf_h` (sprite height, computed at init) and use
`state.y - surf_h` as the standing surface in `_apply_platform_landing`,
`_player_is_standing_on_platform`, and the carry test. Verified: player stands
at 578 (= 591 - 13), gap 0.

## Death animation + edge kill

Death restarted the level instantly. blues level_player_die sets restart_level_flag
then level_player_death_animation plays the falling death sprite (33, sound 7,
~60 frames) before the level reloads. Added a death state:
- `_start_death`: dying=True, death_timer=60, spr_num=33, vy=16, vx=+/-80, play_sound(7).
- `_update_death_animation` (runs at the top of tick while dying): falls with
  gravity, updates monsters/bonuses/camera, counts the timer down.
- `_respawn_player` at timer 0: lives--, energy reset, back to checkpoint.
Falling off the bottom (`y > world_h + 128`) now triggers this instead of an
instant respawn. Verified: death sprite falls for 60 frames, then respawn (lives
2->1).

## Audio (runtime/sound.py) + menu

`SoundEngine` mirrors blues p2/sound.c assignment:
- play_sound(num): 11 effects, raw 8-bit/8000 Hz PCM from SAMPLE.SQZ, indexed by
  sound_sizes_tbl offsets.
- play_music(num): TRK per music number (trk_names_tbl); level music table wired
  in load_level.
- Added an "Audio" menu: Sound effects / Music checkbuttons -> sound.sound_enabled
  / music_enabled.
pygame.mixer backend initialised (8-bit/8000 Hz/mono). Everything degrades to a
safe no-op.

KNOWN FORMAT BLOCKERS (so actual audio is silent for now, verified against data +
blues unpack.c):
- SAMPLE.SQZ uses signature 0x0000 — not EAT (0x4CB4) and not SQZ (0x10xx); the
  sample bank can't be decoded yet (samples=None). Needs the format identified
  (likely a 'stored'/raw variant or a third codec).
- TRK files ARE EAT-compressed (decodable) but hold a Titus tracker module;
  playing them needs the tracker player from blues mixer.c (not ported).
So: sound-effect + music wiring, level/music assignment, and the on/off menu are
in place; producing sound waits on the SAMPLE decode and a TRK tracker player.

All 16 levels render + smoke clean.

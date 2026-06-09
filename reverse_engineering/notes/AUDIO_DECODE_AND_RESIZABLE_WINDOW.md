# Audio decode (SQV samples + MOD music) + resizable window

## Sound effects: SAMPLE.SQZ is the SQV codec

The engine's unpack() dispatch is: 0x4CB4 -> EAT, 0x10xx -> SQZ, else -> SQV.
SAMPLE.SQZ (signature 0x0000) is therefore SQV. Ported `unpack_sqv` into
pre2lib/formats.py (dictionary + bitstream RLE, direct port of blues
p2/unpack.c) and pointed the unpack_file fallback at it. Decodes to 60768 bytes
= sum of the 11 sound_sizes_tbl entries (8-bit unsigned PCM, 8000 Hz). A 1-byte
trailing shortfall is padded with 0x80 silence.

## Music: .TRK are EAT-compressed .MOD modules

unpack_eat on a .TRK yields a standard ProTracker module ("M.K." at offset 1080,
module name in the header). pygame/SDL_mixer plays MOD via `mixer.music`.

## SoundEngine (runtime/sound.py)

- Mixer opened at 44100 / -16 / stereo (so MOD music sounds right).
- Effects: 8-bit/8000 Hz are converted to the mixer format with numpy
  (unsigned8 -> signed16, nearest-neighbour resample 8000->44100, mono->stereo)
  via `pygame.sndarray.make_sound`, cached per sound number.
- Music: `play_music(num)` decompresses TRK_NAMES[num] (unpack_eat) and plays it
  from a BytesIO via `mixer.music.load/play(-1)`; per-level music table already
  wired in load_level.
- All degrades to a safe no-op without numpy/pygame/assets.
Verified: all 10 non-empty effects build Sound objects (sound0 ~0.79s); music
loads for every level.

## Resizable window with integer / aspect-ratio control

GameApp is now `resizable(True, True)` with the canvas filling the window (black
letterbox) and the game image centred. `_display_size(win_w, win_h)`:
- Keep aspect OFF       -> stretch to the full window.
- Keep aspect + integer -> largest integer scale of 320x200 that fits.
- Keep aspect, non-int   -> float fit (letterboxed).
The display transform (offset + sx/sy) is stored in `self._disp` and used to
place the canvas image and the vector overlays (FPS text, debug cross/probe/rect)
so they track any window size/scale. View menu gains "Keep aspect ratio" and
"Integer scaling" checkbuttons (both default on).

Verified: 1000x650 -> integer 960x600, aspect-only 1000x625, stretch 1000x650.
All 16 levels smoke + render clean.

## Sample format: SIGNED 8-bit (not unsigned)

Effects sounded over-amplified + distorted ("slower") because they were converted
as unsigned 8-bit (`(byte-128)<<8`). They are SIGNED 8-bit: blues mixer.c does
`(int8_t)data[pos] * 256` (sign-extend, scale to int16, silence = 0). The unsigned
reading mapped silence (0) to -32768 (a full-scale pop) and doubled the RMS.
Fixed `_make_sound` to `np.frombuffer(raw, dtype=np.int8).astype(int16) << 8`.
Verified: silence-start now 0, RMS ~11.7k (was ~25.3k), duration unchanged.

## Follow-ups

- Music toggle: re-enabling Music required a restart because `stop_music` cleared
  `_music_num` and nothing replayed. Added `_last_music` (remembered separately)
  + `resume_music()`; `_apply_music` now calls it on enable so the level track
  restarts immediately.
- "No sounds": only the death sound (7) was wired. Wired the rest from the blues
  play_sound sites: club swing 5/0/10 by club type, monster-die-by-club 2, hurt
  9, stomp 3, life 4, food <=64 -> 4 else 8, energy/letters/utensils 8, damage 1,
  screen-kill/bomb 0. Verified play() allocates a busy channel and clubbing fires
  sound 5.

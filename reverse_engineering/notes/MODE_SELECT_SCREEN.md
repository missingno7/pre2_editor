# Mode-select screen (BEGINNER / EXPERT) — the first screen on startup

Not in blues; reconstructed from the DOS game (screenshots) + asset investigation.

## Background: it IS a real image (MOTIF.SQZ), not sprites+filter

`MOTIF.SQZ` (French "motif" = pattern) decodes (EAT) to 32000 bytes = a
**320x200 16-colour planar 4bpp** image: the caveman wallpaper seen behind the
MODE text. It uses only palette indices 0-3. So the moving background is this one
tiling image **scrolled**, not a field of live sprites with a filter.

Implemented as a diagonally-scrolling wallpaper: the 320x200 image is tiled 2x2
into a 640x400 buffer once, and each frame a 320x200 window is taken at
`(scroll % 320, scroll % 200)` so it wraps seamlessly. `MODE_SCROLL` (px/tick) is
the drift; direction/speed are a reasonable guess pending DOSBox confirmation.

## Palette (reconstructed)

The mode-select palette is hardcoded in the EXE data (DS-relative; the DGROUP base
is still unresolved in the flat disasm, so it can't be read directly). Built from
the screenshots in `_MODE_PALETTE6`:
- indices 0-3 = the blue wallpaper (bg + caveman shades),
- the sprite-font letters (sprites 241+) use indices **6, 13, 15** (verified by
  rendering letters against a unique-index palette) — set to gold so MODE /
  BEGINNER / EXPERT read gold like the in-game text.
Renders match the screenshots closely.

## Logic

State machine in `RuntimeWorld` (one step/tick = DOS rate): Up/Down toggles the
choice (sel 0=BEGINNER, 1=EXPERT), a confirm key (Space/Ctrl=fire, Enter=action)
sets `self.expert` and begins the first level's map intro. `expert` is read live
during gameplay (monster `expert_only` filter), so setting it before the first
tick is enough — no level reload needed.

Flow: startup -> mode select -> (confirm) -> map intro -> entry wipe -> gameplay.
Wired in `RuntimeWorld.__init__` (`_begin_mode_select` after the initial
load_level). Rendered in BOTH renderers (`game.py` `_render_mode_select` PIL +
`pygame_backend` `_render_mode_select`); see [[the dual-renderer caveat]] in
LEVEL_COMPLETE_MAPINTRO_WIPES.md.

## Accuracy pass (user feedback: too slow / music slow / palette off)

- **Scroll speed**: DOS title/presentation screens animate synced to the VGA
  vertical retrace (~70 Hz), NOT the ~21.8 Hz gameplay logic tick this screen runs
  in (see disasm anchors: the video effects sync to retrace internally). So the
  per-tick drift was ~6x too slow at 0.5 px/tick. Bumped `MODE_SCROLL` to 3.0
  (~70/21.8 ≈ 3.2) to match the real on-screen speed.
- **Music = CODE.TRK (index 1), user-confirmed.** (Earlier wrong guesses: PRES.TRK
  "mystere" sounded slow; PRESENTA.TRK "presentation" is the title-screen track.)
- **Letter spacing + layout**: the gold lettering is spaced wider than the 16px
  bonus-screen font — `MODE_LETTER_ADV = 24`, lines at `MODE_TEXT_Y1 = 74` /
  `MODE_TEXT_Y2 = 112` — matching the screenshots. Palette retuned again: brighter
  blue bg (idx0), near-black navy cavemen (idx1/3) with a faint mid detail (idx2)
  for higher contrast like the screenshots.
- **Background palette (user close-up)**: the cavemen are a 3-tone sprite — medium
  grey-blue body (idx1), near-black navy outline (idx3), and a LIGHT grey-blue
  highlight (idx2). My earlier idx2 was dark, flattening them; set to a light
  grey-blue per the close-up so the caveman detail shows. (The exact VGA palette is
  still DS-relative data not recoverable from the flat disasm; a scan of the binary
  for palette-shaped runs found no blue 16-colour table for this screen, so the
  values are sampled from the screenshots.)
- **Palette**: index 0 is the background (45410/64000 px); 1/3/2 are the cavemen.
  Retuned so the bg is a medium blue and the cavemen read as darker navy
  silhouettes with a faint lighter highlight (idx2), matching the screenshots.
  Still reconstructed (real palette is in the unreadable EXE data).

## Still pending RE (DGROUP base unresolved)

The DGROUP/DS base is genuinely hard to recover statically: PRE2 is a multi-segment
LZEXE program whose far calls/jmps target relocated segments, so the recursive
disassembler only reaches ~2.9% of code (the random/mode-select routines aren't
covered), and `random_reset` doesn't write the seed constants as findable absolute
immediates. The static seed data `05 22 86 8D E5` confirms `g_vars.random` lives at
file 0x79D1, but no code ref to it was locatable to back out the base.

Exact palette, scroll direction/speed, music track, and the precise key/confirm
mapping are reconstructions. Verified end-to-end headlessly (mode -> confirm ->
intro -> wipe -> play) in both renderers with no crash.

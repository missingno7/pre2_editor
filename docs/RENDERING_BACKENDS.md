# Runtime rendering backend

`run_game.py` renders with the **pygame/SDL2 backend only**. Gameplay is the
fixed-tick `RuntimeWorld`; rendering converts decoded tiles, sprites, HUD glyphs and
backgrounds to pygame surfaces (cached per level) and presents them scaled by SDL2
(`pygame.SCALED`). (The original Tk/Pillow game backend was removed; Tk lives on only
in the separate level editor, `gui.py`.)

Recommended launch:

```bash
pip install -r requirements.txt
python run_game.py --scale 3 --level 1
```

### IMPORTANT: two render paths to keep in sync

`runtime/pygame_backend.py` (`PygameSurfaceRenderer`) is a native renderer that
duplicates the draw code in `runtime/game.py` `RuntimeWorld.render_frame` (the PIL
renderer, kept for headless tests). Animation/game **state** lives in `game.py`
(shared), but the **draw** code is duplicated. When changing any render path (intro,
mode-select, map, bonus screen, wipes, HUD, light fade, etc.) update BOTH and test
the pygame one headlessly: `SDL_VIDEODRIVER=dummy`, `pygame.display.set_mode`,
`PygameSurfaceRenderer(world, pygame).render(alpha)` then `pygame.image.save`.

Useful keys in the pygame backend:

- Arrow keys / WASD: movement
- Space / Ctrl: club/fire
- Enter: action
- `F1`: debug collision/probe overlay
- `F2`: FPS/TPS overlay
- `F3`: interpolation on/off
- `F4`: smooth/vanilla camera toggle
- `[` / `]`: previous/next level
- `R`: restart level
- `Esc`: quit

## Why surfaces are cached

The PIL path creates a Pillow image for the DOS viewport, optionally resizes it on
the CPU, and uploads it.  The pygame backend converts decoded tiles, sprites, HUD
glyphs, and backgrounds to pygame surfaces once per level and reuses them.  With
`pygame.SCALED`, SDL2 handles the final 320x200-to-window scaling, which benefits
from GPU/driver acceleration.  Cached surfaces are keyed by `(level_index, level id,
palette generation)` so the sun/light palette fade re-bakes them.

## Local smoke benchmark

On the sandbox's headless SDL dummy driver (so this does **not** measure real GPU presentation), normal gameplay rendering for level 1 was checked for pixel parity against the old Pillow renderer:

- old `RuntimeWorld.render_frame(alpha=0.5)`: about 7.4 ms/frame before Tk upload/resize
- pygame chunk renderer after cache warm-up: about 0.6 ms/frame in the same headless environment
- pixel diff against the old renderer: none for the tested static and moving frames

The first visible chunk build is intentionally lazy; it avoids pre-rendering the full 4096 px wide level.  Typical first visible build was ~45 ms in the headless sandbox rather than several seconds for a full-level pre-render.

### Crash note

The pygame backend intentionally uses only the stable `pygame.display` API for
window creation/resizing.  Avoid `pygame._sdl2.Window.from_display_module()` here:
on some SDL2 drivers it can corrupt the display/event state and terminate the
process without a Python traceback, often right after the music starts.


## Audio diagnostics

The pygame backend uses pygame.mixer for both MOD music and SAMPLE.SQZ sound effects.
Sound effects are converted from the original signed 8-bit / 8000 Hz PCM data to
the active mixer format without requiring numpy.

Useful checks:

```bash
python run_game.py --audio-debug
```

While the pygame window is focused, press **F8** to cycle through the raw sound
effects directly. This is useful because gameplay does not play a sound on every
movement/jump; most effects are tied to actions such as club swings, pickups,
stomps, hurt/death, and level events.

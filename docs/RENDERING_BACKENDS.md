# Runtime rendering backends

`run_game.py` now supports two presentation paths:

- `--backend pygame` — pygame/SDL2 backend. Gameplay is still the same fixed-tick `RuntimeWorld`, but rendering is done directly with cached pygame surfaces and SDL2 scaled presentation instead of Pillow -> Tk `PhotoImage` every frame.
- `--backend tk` — old Tk/Pillow backend, kept as a fallback and for comparison.
- `--backend auto` — default. It tries pygame first and falls back to Tk if pygame is not installed or the SDL display cannot initialise.

Recommended launch:

```bash
pip install -r requirements.txt
python run_game.py --backend pygame --scale 3 --level 1
```

Useful keys in the pygame backend:

- Arrow keys / WASD: movement
- Space / Ctrl: club/fire
- Enter: action
- `F1`: debug collision/probe overlay
- `F2`: FPS/TPS overlay
- `F3`: interpolation on/off
- `[` / `]`: previous/next level
- `R`: restart level
- `Esc`: quit

## Why this is faster

The old path creates a Pillow image for the DOS viewport, optionally resizes it on the CPU, and then uploads it into Tk.  The pygame backend converts decoded tiles, sprites, HUD glyphs, and backgrounds to pygame surfaces once per level and then reuses those surfaces.  With `pygame.SCALED`, SDL2 handles the final 320x200-to-window scaling outside Pillow/Tk, which is the part that is most likely to benefit from GPU/driver acceleration.

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
python run_game.py --backend pygame --audio-debug
```

While the pygame window is focused, press **F8** to cycle through the raw sound
effects directly. This is useful because gameplay does not play a sound on every
movement/jump; most effects are tied to actions such as club swings, pickups,
stomps, hurt/death, and level events.

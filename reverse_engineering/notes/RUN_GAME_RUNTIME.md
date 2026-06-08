# `run_game` runtime RE notes

`run_game.py` is the second application: a gameplay/source-port runtime rather than an editor.

Current implemented baseline:

- loads original game data directly through `pre2lib`,
- renders a 320×200 DOS viewport with nearest-neighbour scaling,
- uses original level background, base tiles, foreground tiles, sprites, palette, and tile attributes,
- simulates at 19 Hz with integer positions/velocities,
- starts the player at the parsed level header start coordinates,
- implements a first pass of horizontal collision, floor collision, slope-profile handling from `tile_attributes3`, ceiling collision, gravity, jumping, camera follow, death reset on deadly tiles/fall,
- draws static item/platform/monster sprites for runtime context.

Important boundary:

The physics constants and player animation-frame choices are still bootstrap approximations.  The architecture is meant for replacing each block with exact PRE2.EXE-derived logic as we identify it in the disassembly.  Do not mix editor-only UI assumptions into `runtime/game.py`; runtime state should mirror the DOS game structures as closely as we uncover them.

Next RE targets:

1. recover exact player state machine, hitbox, acceleration, jump, fall caps, and camera scrolling masks from `PRE2_unpacked_full.asm`,
2. replace static object drawing with real per-tick platform and item logic,
3. implement sprite animation sequence tables instead of hard-coded player frame ranges,
4. map bonus/secret tile mutation and collectibles,
5. add enemy movement handlers type-by-type using the decoded monster records.

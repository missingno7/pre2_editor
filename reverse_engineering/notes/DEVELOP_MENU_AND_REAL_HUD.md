# Develop menu + real-graphics HUD

## Develop menu (GameApp)

Added a Tk menubar with a "Develop" cascade:
- Difficulty: Beginner / Expert (radio). Expert sets `world.expert = True` and
  reloads the level; `_spawn_monsters` shows expert-only monsters when expert.
- Level: radio submenu (Level 1..16) -> load_level.
- God mode (checkbutton) -> `world.god_mode`; the player hurt branch skips energy
  loss / death when set.
- Debug overlay (checkbutton, also F1) -> the F1 probe overlay; now DEFAULT OFF
  (`show_debug = False`). F1 and the menu checkbutton stay in sync.
- Restart level (R).
Room to add more dev toggles later.

## Real-graphics HUD (ALLFONTS panel)

blues level_draw_panel / video_draw_panel format (screen.c):
- ALLFONTS.SQZ decoded (`unpack_file`). Panel background strip is a planar 4bpp
  320x23 image at offset `41*48`; number/icon glyphs are 16x12 (96 bytes) at
  `48*41 + 160*23`: 0-9 digits, 10 = heart, 12-16 = BONUS letters.
- `decode_planar_bitmap` matches blues `decode_planar` layout, reused via
  `_panel_planar_image` (palette-mapped, optional transparent index).
- `_build_panel_assets` (per level, palette-dependent) builds the bg image + glyph
  images; `_draw_hud` pastes the bg at y=176 and overlays glyphs at the original
  8px-aligned framebuffer offsets (`x=(offset%40)*8, y=offset//40`): lives 0x1CED,
  score 0x1CF1 (7 digits, value*10), hearts 0x1D01+, BONUS letters
  {0x1C91,0x1BF2,0x1CE3,0x1C6C,0x1C1D}.

Verified: panel renders the real face/labels/digits/hearts on all 16 levels
(matches the DOS reference screenshot).

## Pending / notes
- Panel uses the current level palette (indices 0-15); colours may shift slightly
  between levels if those entries differ.
- Camera still uses the full 200px height (panel overlays the bottom 24px) rather
  than a separate 176px play area.
- Score value is still the +10/pickup placeholder.

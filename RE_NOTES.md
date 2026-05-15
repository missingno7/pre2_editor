# Prehistorik 2 reverse-engineering notes

## Data-first rule

The viewer keeps decoding/rendering logic in Python and reads as much as possible from original game data. Reimplementation projects are used as semantic references, not runtime dependencies.

## Confirmed file path

The supplied DOS data set uses compressed resource files:

- `LEVEL*.SQZ`
- `UNION.SQZ`
- `FRONT.SQZ`

The Python parser reads these directly and implements both compression formats encountered in the project.

## Level height is now inferred from game data

The viewer no longer carries a copied level-height table. `load_level()` scans candidate heights and accepts only a level layout that simultaneously satisfies:

1. a valid `256 × height` tilemap and following 256-entry tile LUT,
2. compact local tile indices in the LUT,
3. structurally valid metadata tables and variable-length monster records,
4. metadata ending exactly at EOF.

For the supplied 16 levels this independently recovers:

`49, 104, 49, 45, 128, 128, 128, 86, 110, 12, 24, 51, 51, 38, 173, 84`

This matches the known engine behavior while remaining self-derived from each `LEVEL*.SQZ` file.

## Level file structure, now implemented

For level index `n`:

1. Tilemap: `256 × inferred_height` bytes.
2. Tile LUT: 256 little-endian `u16` entries.
3. Level-local tile graphics: count derived from LUT local entries, 128 bytes per tile.
4. Metadata payload:
   - 256 bytes tile attributes 0,
   - 256 bytes tile attributes 1,
   - 256 bytes tile attributes 2,
   - scrolling/start header,
   - 256×`u16` front tile LUT,
   - 20 gate records,
   - 15 shifting-column records,
   - fixed 0x800-byte monster attribute region containing variable-length records,
   - item/monster sprite-number raw bases,
   - 80 secret bonus records,
   - 256 bytes tile attributes 3,
   - 70 item records,
   - 16 platform records,
   - boss data block.

The parser has been run against all 16 shipped levels and reaches exact EOF without offset drift.

## Confirmed coordinate domains for overlays

The viewer now draws object overlays with distinct coordinate interpretations instead of treating every pair of integers as a pixel position:

- **Gates**: `enter_pos`, `dst_pos`, `tilemap_pos` are tilemap positions.
- **Secrets**: `pos` is a tilemap position.
- **Shifting columns**: `tilemap_pos` and `trigger_pos` are tilemap positions; width/height are tile counts.
- **Items**: `x_pos`, `y_pos` are level-space pixel coordinates.
- **Platforms**: `x_pos`, `y_pos` are level-space pixel coordinates.
- **Boss block**: `x_pos`, `y_pos`, `x_min`, `x_max` are level-space pixels.
- **Monsters**:
  - types **0** and **10** use `x_pos/y_pos` as four packed trigger-rectangle bytes: `(x0, y0, width, height)` in tile coordinates;
  - other types use level-space pixel coordinates for their logical spawn anchor.

The overlay renderer intentionally labels these raw logical anchors/regions. Exact top-left sprite extents require sprite-size/offset tables, which are an upcoming EXE-extraction milestone.

## Renderer progress

- Base level map rendering is working.
- Level-local and shared `UNION` tiles are distinguished through the LUT.
- `FRONT.SQZ` is loaded.
- Front overlays use:
  - `tile_attributes2[tile] & 0x40`
  - `front_tiles_lut[tile]`
  - `FRONT.SQZ` tile graphics with color 0 treated as transparent.
- Object overlays are enabled for:
  - monsters,
  - items,
  - platforms,
  - gates,
  - shifting columns,
  - secret bonuses,
  - boss data.

## Level-table spot checks

Current parser counts from supplied data:

| Level | Monsters | Gates | Columns | Secrets active | Items active | Platforms active | Boss |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 | 16 | 8 | 0 | 48 | 65 | 3 | no |
| 2 | 41 | 7 | 0 | 9 | 68 | 9 | no |
| 3 | 36 | 2 | 0 | 45 | 68 | 5 | yes |
| 9 | 58 | 4 | 4 | 77 | 66 | 13 | yes |
| E | 10 | 7 | 3 | 2 | 46 | 0 | no |

## EXE/decompile track

`PRE2.EXE` is detected as `LZEXE v0.91` packed. It still needs unpacking before useful disassembly/decompilation. That track matters especially for moving the remaining runtime tables into data-driven Python extraction, notably:

- palettes currently represented by `palettes.json`,
- sprite size/offset tables,
- sprite ID rebasing tables/logic,
- background palette-selection or asset-selection tables.

The level-height dependency has already been eliminated without needing the EXE table.

## Milestone 5 — file browser visualization, non-raster overlays, sprite-backed object preview

### Game File Browser layout
The game file browser is now split into:
- left: file list / browsing tree,
- right: file-oriented tabs (`File Info`, `Visual Preview`).

`SPRITES.SQZ` has a visualizer that decodes it into a full sprite atlas using the active level palette. This is the first file-specific visualization pane and establishes the pattern for future `UNION.SQZ`, backgrounds, fronts, maps, and music/sound previews.

### Overlay architecture change
Tile/object overlays are no longer painted into the rendered level bitmap. They are drawn as independent Tk canvas primitives above the level image:
- grid lines,
- tile-attribute overlays,
- gates, bonuses, columns,
- object origin markers and labels,
- sprite images for items/platforms/monsters.

The level bitmap can now be zoomed independently while labels, lines, and markers remain proper vector/canvas overlay objects instead of nearest-neighbor-scaled pixels. This is also the correct foundation for future picking, moving, and editing.

### Object sprite visualization
The viewer now decodes `SPRITES.SQZ` and uses parsed level sprite references to draw real visuals for:
- active items,
- active platforms,
- ordinary fixed-position monsters.

Monster types 0 and 10 remain special: the level record stores an activation rectangle rather than a fixed spawn point. The viewer therefore draws:
- the true trigger rectangle,
- a representative monster sprite thumbnail centered inside it,
- a label explicitly marking it as a trigger region.

This avoids pretending that those enemies have a static world spawn coordinate.

### Per-level sprite-number normalization
The level stores per-level sprite number bases in metadata (`items_sprite_num_offset`, `monsters_sprite_num_offset`). The viewer keeps raw values visible in the tables, but also computes a `sprite_view` ID matching the runtime sprite namespace so it can draw the correct picture.

This operation currently mirrors the reference engine's bank normalization:
- item/platform bank base → 53,
- monster bank base → 312,
- high sprite flags are masked away when selecting a frame image.

### Current remaining temporary bootstrap
Sprite pixel data itself comes from the original `SPRITES.SQZ` game file. However, sprite geometry/origin tables (`spr_size_tbl`, `spr_offs_tbl`) are currently loaded from `sprite_tables_reference.json`, generated from the uploaded `blues` reference source.

That file is intentionally named and documented as a **temporary RE bootstrap**, not as a hidden authoritative game table. The next intended milestone is:
1. unpack `PRE2.EXE`,
2. locate and validate the geometry/origin tables directly in the executable,
3. replace the reference JSON loader with automatic PRE2.EXE extraction.

The sprite blob length already validates against the current geometry table: the sum of decoded frame byte sizes equals the decoded `SPRITES.SQZ` length exactly.

## Milestone 6 — difficulty variants and secret-bonus semantics

### Beginner vs Expert preview
The level files do **not** contain two independent monster tables. Instead, each monster record has a `type/expert` byte:

- bits `0..6`: movement type,
- bit `7`: **expert-only** flag.

The viewer now has a **Beginner / Expert** switch in the top toolbar. It affects the monster overlay and parsed monster table:

- **Beginner**: hides monsters whose type byte has bit 7 set,
- **Expert**: shows every monster record.

Counts recovered from the shipped `LEVEL*.SQZ` files:

| Level | Total monsters | Beginner-visible | Expert-only |
|---|---:|---:|---:|
| 1 | 16 | 13 | 3 |
| 2 | 41 | 29 | 12 |
| 3 | 36 | 30 | 6 |
| 4 | 16 | 16 | 0 |
| 5 | 49 | 40 | 9 |
| 6 | 32 | 27 | 5 |
| 7 | 53 | 49 | 4 |
| 8 | 37 | 35 | 2 |
| 9 | 58 | 58 | 0 |
| E | 10 | 10 | 0 |

The `blues` loader/update path filters monsters with exactly this expert bit. In the same code path, regular level **items are not difficulty-filtered**. The only clearly difficulty-dependent item-related logic seen so far is the randomized level-password digit assignment, which seeds differently for beginner/expert; the item table itself is shared.

### Secret record identity: raw level slot only
A level always stores exactly **80 secret bonus records**. Many are inactive (`pos = FFFF`).

The viewer now labels secrets only by their **real raw slot number** inside this 80-record table:

- `S12`, `S17`, `S44`, ...

This avoids a misleading second numbering scheme based on active-order. The same slot identity should be used by the future editor and save path.

### Secret record layout and map mutation
Each secret record is 5 bytes:

```
initial_tile : u8   # tile installed during level initialization
revealed_tile: u8   # tile restored when the secret resolves
count        : u8   # behavior class + count
pos          : u16  # tilemap position: x + (y << 8)
```

The runtime initialization pass does this for active records:

1. read the tile currently present in the map at `pos`,
2. if it matches the record's `revealed_tile`, replace it with `initial_tile`,
3. keep `revealed_tile` as the tile to restore later.

So hidden platforms, hidden wall chunks, and other revealed structures are **already present in the map data** as their final tile arrangement, then temporarily covered/removed by secret initialization.

### `count` byte classes
The `count` byte has three runtime behavior families:

| Range | Viewer mode | Meaning inferred from runtime |
|---|---|---|
| `00..3F` | `small_random_bonus` | Repeated club hits drop small random bonus objects. Resolution occurs after roughly `count + 1` hits. |
| `40..7F` | `tile_reveal` | Hit-to-reveal tile secret. On final resolution the map tile is restored to `revealed_tile`. Intermediate hits emit level-dependent particles / special food effects. Approx. hit count is `(count & 0x3F) + 1`. |
| `80..FF` | `big_random_bonus` | Multi-hit big/random secret. On final resolution, it spawns one of the `110 + random(1..7)` bonus sprites and removes the secret tile. Approx. hit count is `(count & 0x7F) + 1`. |

The viewer now decodes these classes and exposes them in the **Secrets** parsed table as:

- raw table slot,
- initial/revealed tile,
- count hex,
- mode,
- approximate hits until resolution.

### Concrete examples from Level 1
Using raw secret slot labels:

#### `S12` — repeated random-item/drop secret
Level 1 raw slot `S12`:

```
pos=(8,40), count=0x02, initial_tile=0x8C, revealed_tile=0x8C
```

It falls into `00..3F`, therefore it behaves as a **small random bonus drop** point. With `count=2`, the runtime branch implies roughly **3 successful hit resolutions** before the secret is consumed.

#### `S44..S47`-style hidden platform clusters
Level 1 raw slots `#44..#47` are:

```
#44 pos=(57,30) count=0x41 initial=0x7E revealed=0xA8
#45 pos=(58,29) count=0x41 initial=0x7E revealed=0x97
#46 pos=(58,30) count=0x41 initial=0x7E revealed=0xAB
#47 pos=(57,29) count=0x41 initial=0x7E revealed=0x94
```

These four records form a 2×2 block. On level init, those final platform tiles are replaced by tile `0x7E`; after the secret resolves, the original four distinct tiles are restored. This is why they behave visually as a single appearing platform structure even though the file stores four independent 5-byte secret records.

### Important implication for an editor
The editor should **not** model secrets only as isolated points. It needs at least two UX layers:

1. **Raw secret records** — exact 80-slot table for faithful roundtrip.
2. **Detected secret clusters / structures** — adjacent records grouped by proximity and compatible count/mode, rendered as one higher-level object when it improves usability.

Examples of editor-friendly groupings:

- a 2×2 appearing platform assembled from four `tile_reveal` records,
- larger multi-tile walls/statues that reveal as a contiguous region,
- repeated item-drop points that remain isolated secrets.

The grouping itself is an editor interpretation; the on-disk format remains a flat 80-record table.

### Special giant-food / particle behavior is runtime logic, not an explicit level sprite ID
The `40..7F` `tile_reveal` class does **not** store an explicit "spawn hamburger sprite" field in the level record. The runtime chooses special effect/food sprites from control flow that depends on the current level and on whether the record is at its final hit state.

Observed in the reference engine path:

- certain levels use sprite `306`, `300`, or `308` for intermediate/final effects,
- a second spawn path chooses `310` in one level and `229` elsewhere.

This means a user-friendly editor must distinguish:

- **what is explicitly stored in `LEVEL*.SQZ`**: tile mutation position, initial/revealed tiles, behavior/count byte,
- **what is runtime policy**: which giant-food/effect sprite is emitted for that behavior in a given level.

For the final "no hidden hardcode" architecture, this runtime policy should be derived from the unpacked `PRE2.EXE`/decompile, then represented explicitly in Python as an extracted/interpreted game rule rather than silently baked into GUI labels.

## Milestone 8 additions: player start, viewport background, and extra displayable logic

### Player start
The level header already exposes `start_x_pos` and `start_y_pos` from the metadata block. The viewer now:

- draws a `START` crosshair at that exact pixel position,
- enables that overlay by default,
- opens a newly selected level centered on the player start position.

This is editor-relevant: the start position is a first-class level property, not an inferred object.

### Backgrounds and tile transparency
The original game uses `BACK0.SQZ` … `BACK5.SQZ` as 320×200 planar 4bpp background pictures. During level drawing it copies a viewport-sized background first, then draws level tiles over it with palette index 0 treated as transparent. The viewer now mirrors this structure:

- level tiles are rendered as RGBA with color index 0 transparent,
- the GUI keeps a viewport-fixed stretched background below the transparent level layer,
- the Game File Browser can visualize any `BACK*.SQZ` file.

The level→background routing table is part of level-loader logic, not stored inside `LEVEL*.SQZ`; the Python implementation currently carries the decoded routing values with an explicit note to replace them by direct `PRE2.EXE` extraction when the executable unpack pass is completed.

### Additional level data worth visualizing
The viewer now exposes an additional `Attr3 nonzero / floor-shape logic` tile overlay. This is the fourth per-tile property table in the level metadata and is used by the game for slope/floor-shape behavior.

Other level-facing data that is already parsed but still deserves dedicated visual overlays or semantic labels in future milestones:

- scrolling constraints (`scrolling_top`, `tilemap_w`, `scrolling_mask`),
- item semantics encoded by sprite number, especially checkpoints, level exits, and password glyph items,
- richer per-platform motion previews derived from platform flags and movement fields,
- animated tile triplets as animation groups rather than a simple flag overlay.

## Tile collision / surface model: attr0, attr1, attr3

The per-tile tables are best treated as one combined **tile physics model**, not as three unrelated flags.

### `tile_attributes0`: side contact / horizontal collision

Observed original values are only `0`, `1`, and `2`:

| Value | Meaning |
|---:|---|
| `0` | Open from sides |
| `1` | Solid from sides; horizontal movement is blocked |
| `2` | Deadly from sides |

The runtime consults this table for side contacts, side hazards, monster wall reactions, and some bounce logic.

### `tile_attributes1`: top surface / landing behavior

Observed original values are `0..6`:

| Value | Meaning |
|---:|---|
| `0` | No top surface |
| `1` | Solid top surface |
| `2` | Slightly slippery top |
| `3` | Slippery top |
| `4` | Very slippery top; not common in original levels |
| `5` | Action-pass-through hatch / drop-through floor state |
| `6` | Deadly from above |

This is the table that decides what happens when the player or falling objects land on a tile. It also drives gravity support for many monsters and bouncing physical bonus objects.

### `tile_attributes3`: floor profile / internal surface height

`attr3` refines **where inside the 16×16 tile the top contact surface lies**. It is not a replacement for `attr1`; instead, `attr1` says whether the tile acts as a top surface and `attr3` shapes that surface.

Encoding inferred from runtime code:

| Bits | Meaning |
|---|---|
| `0..3` | Base vertical surface offset inside the tile |
| bit `4` (`0x10`) | Surface slopes down to the right |
| bit `5` (`0x20`) | Surface slopes up to the right |

The runtime computes a surface offset approximately as:

- `0x00`: flat surface at tile top
- `0x01..0x0F`: flat surface inset lower in the tile
- `0x10..0x1F`: down-right slope, offset = base + floor(local_x / 3)
- `0x20..0x2F`: up-right slope, high point on the right, offset = base + floor((15 - local_x) / 3)

Original level data uses flat offsets plus one-direction slope encodings; no evidence of both slope-direction bits being set together in shipped levels.

### How the tables work together

Typical combinations:

| `attr0` | `attr1` | `attr3` | User-facing meaning |
|---:|---:|---:|---|
| `0` | `0` | `0` | Empty / passable tile |
| `1` | `0` | `0` | Side wall only |
| `0` | `1` | `0` | One-way floor / top-only solid |
| `1` | `1` | `0` | Full block: side-solid and top-solid |
| `0` | `2/3/4` | `0` or slope profile | Slippery floor / icy floor, optionally shaped |
| `0` | `6` | `0` | Top-only hazard |
| `2` | `6` | `0` | Hazardous from side and top, e.g. spike-like tile |

`attr3` most often appears on top-surface tiles (`attr1 != 0`) and shapes their floor. There are also shipped combinations with `attr1 == 0` but `attr3 != 0` (`attr3` values `1` and `0x20` are observed). The runtime explicitly checks the profile of the next-lower tile during ground transition logic, which appears to support smooth descent/row transitions on slopes rather than introducing a fully independent collision class.

### Relationship to `tile_attributes2`

A complete editor-facing tile physics model should include `attr2` as well:

- low bits encode underside behavior (`solid bottom`, `deadly from bottom`),
- higher bits encode dynamic/visual behavior such as fly emission, step-trigger tile changes, front masking, and animation.

So the eventual user-facing tile editor should expose semantic groups such as:

- **Side behavior**: open / wall / deadly side
- **Top behavior**: none / solid / slippery / hatch / deadly top
- **Bottom behavior**: none / solid underside / deadly underside
- **Floor shape**: flat, inset height, slope down-right, slope up-right
- **Dynamic/visual flags**: animated, foreground mask, player-step tile change, fly emitter

The GUI milestone following this note renames the previous raw overlays from `Attr0/Attr1/Attr3` to these more user-friendly concepts and shows decoded tile-physics text on tile selection.

## Physics diagram overlay design

The GUI now has a semantic `Physics diagram: sides / surfaces / slopes` overlay. It is intentionally drawn as a vector overlay over the level image rather than baked into the tile raster, because the same visual language should later be reused by the editor.

Visual vocabulary:

- **Top surface**: drawn along the actual floor profile computed from `attr3`, not as a full tile highlight.
  - green = ordinary solid surface (`attr1 = 1`)
  - cyan = slippery surface (`attr1 = 2..4`), with progressively denser hatch marks
  - yellow dashed surface + downward arrows = action-pass-through hatch (`attr1 = 5`)
  - red surface + hazard ticks = deadly from above (`attr1 = 6`)
- **Slope / floor shape**: the surface line follows the real profile encoded by `attr3`, including flat insets and both slope directions.
- **Side behavior**:
  - blue vertical side rails = side-solid (`attr0 = 1`)
  - red zig-zag rails = deadly from sides (`attr0 = 2`)
- **Underside behavior** (`attr2 & 0x0F`):
  - purple bottom rail = underside-solid (`1`)
  - red bottom zig-zag = deadly from below (`2`)
- **Auxiliary profile**: rare shipped combinations where `attr1 == 0` but `attr3 != 0` are rendered as a faint dashed profile line; these appear to support smooth slope transition logic rather than a standalone floor.

This gives a direct editor-facing model: clicking a tile in the future editor can open grouped controls for `Side`, `Top`, `Bottom`, and `Surface shape`, while the overlay instantly previews the collision semantics as a compact diagram.

## Monster behavior model — level record semantics

### Core separation: enemy visual vs enemy behavior

A monster record combines **two different axes**:

1. **Visual / animation family**
   - `spr_num` in the level record selects a sprite/animation entry.
   - The runtime looks up animation sequences by the pair `(movement type, sprite number)`, not just by sprite.

2. **Movement / spawn logic**
   - `type & 0x7F` selects one of the hard-coded behavior classes `T0..T12`.
   - This is independent from the sprite choice.

Concrete Level 1 proof:

- `M5:T4` uses sprite `318` and behaves as a swinging/rotating hanging spider.
- `M4:T2` uses sprite `319` and behaves as a vertical hanging oscillator.
- `M7:T2` uses sprite `318`, the same base spider sprite as `M5:T4`, but still behaves as a vertical hanging oscillator because its movement type is `T2`.

So a future editor must expose **behavior type** and **visual/animation preset** separately.

---

### Common monster record fields

Every monster record starts with:

| Field | Meaning |
|---|---|
| `len` | Variable record size; determined by behavior type |
| `type` | Low 7 bits = movement type `T0..T12`; high bit = Expert-only |
| `spr_num` | Raw sprite/animation family reference |
| `flags` | Initial/runtime state flags; some shipped records initialize them, but many are overwritten at spawn |
| `energy` | Enemy health/strength used by combat logic |
| `respawn_ticks` | Delay/cooldown used by spawn/activation logic; units are runtime ticks divided by 4 in several behaviors |
| `current_tick` | Initial runtime counter; often `0` or `255`, then updated by runtime |
| `score` | Score reward tier/value |
| `x_pos`, `y_pos` | Usually fixed spawn position in pixels; for `T0` and `T10` these four bytes encode a trigger rectangle in tile coordinates |

#### Position modes

- `T0`, `T10`: `x_pos/y_pos` are **packed trigger rectangles**, not direct object positions.
  - `x_pos low` = trigger left tile X
  - `x_pos high` = trigger top tile Y
  - `y_pos low` = width in tiles
  - `y_pos high` = height in tiles
- Most other types: `x_pos/y_pos` are fixed monster anchor/spawn positions in pixels.
- `T11`, `T12`: fixed anchor, but activation is delayed/conditional.

---

### Behavior types T0–T12

#### T0 — Triggered fly-in spawner

- **Spawn mode:** trigger rectangle.
- When the player is inside the rectangle, the enemy spawns off-screen above and to alternating sides of the player.
- It then drops in and transitions into horizontal motion.
- Used by airborne/entering enemies.
- Level record has no extra trailing parameters beyond the common fields.

**Editor-facing controls:** trigger rectangle, sprite preset, respawn delay, energy, score, expert-only.

---

#### T1 — Static / idle hazard

- **Spawn mode:** fixed pixel position when visible.
- No movement-specific update besides generic despawn/offscreen handling.
- In shipped levels this is used for static hanging hazard anchors / decorative monster objects.

**Editor-facing controls:** position, sprite preset, energy/score if meaningful, expert-only.

---

#### T2 — Vertical hanging oscillator

- **Spawn mode:** fixed pixel position when visible.
- The enemy moves vertically down and up around its anchor point.
- This is one spider behavior.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `vertical_range_px` | `+0x0D` | Maximum downward travel from anchor |
| `vertical_speed_step` | `+0x0E` | Vertical motion speed step; runtime scales it by 16 |

Level 1 example:

- `M4:T2`: sprite `319`, anchor `(667,64)`, range `40`, speed `3`.
- `M7:T2`: sprite `318`, anchor `(3354,536)`, range `40`, speed `1`.

**Editor-facing controls:** anchor position, vertical travel, vertical speed, sprite/animation preset, respawn, expert-only.

---

#### T3 — Proximity drop then ground charge

- **Spawn mode:** fixed pixel position when visible.
- Waits until the player is horizontally nearby, then falls downward.
- On touching a solid top surface, it lands and starts horizontal charging.

Trailing parameter:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `activation_x_range_tiles` | `+0x0D` | Horizontal player distance needed to trigger the drop |

**Editor-facing controls:** anchor, trigger range, sprite preset, respawn, expert-only.

---

#### T4 — Swinging / rotating hanging enemy

- **Spawn mode:** fixed pixel position when visible.
- Starts below/near its anchor, moves into a circular/pedulum path, and then oscillates around an angle range.
- This is the second spider behavior.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `swing_radius_px` | `+0x0D` | Orbit/string radius |
| `swing_angle_limit` | `+0x0E` | End angle reached before switching to oscillating swing |
| `initial_runtime_angle` | `+0x0F` | Present in level record, but reset to `0` at runtime spawn in `blues` |
| `initial_runtime_angle_step` | `+0x10` | Present in level record, but reset to `0` at runtime spawn in `blues` |

Level 1 example:

- `M5:T4`: sprite `318`, anchor `(592,56)`, radius `32`, angle limit `50`, respawn `3`.

**Editor-facing controls:** anchor, swing radius, angle/arc extent, sprite/animation preset, respawn, expert-only. The two trailing runtime-state bytes should be hidden or placed under an advanced/raw section unless the original EXE proves they are author-controlled.

---

#### T5 — Aimed dash when player is nearby

- **Spawn mode:** fixed pixel position when visible.
- Continuously faces horizontally toward the player.
- When player is within configured X/Y ranges, it transitions into a directed dash/attack.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `activation_x_range_tiles` | `+0x0D` | Horizontal trigger distance |
| `activation_y_range_tiles` | `+0x0E` | Vertical trigger distance |
| `dash_speed_step` | `+0x0F` | Dash speed step; runtime scales it by 16 |

**Editor-facing controls:** anchor, activation box, dash speed, sprite preset, expert-only.

---

#### T6 — Scripted follower pattern

- **Spawn mode:** fixed pixel position when visible.
- Activates when the player is within horizontal range.
- Runtime moves the enemy through a **hard-coded offset pattern around the player**.
- The trailing bytes in shipped level records are `FF` fillers in observed data; the actual path pattern comes from a static runtime table, not those bytes.

Trailing parameter:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `activation_x_range_tiles` | `+0x0D` | Horizontal activation range |
| `record_tail_bytes` | `+0x0E..` | Observed filler; not used as the path source in `blues` |

**Editor-facing controls:** anchor, activation range, sprite preset, expert-only. Path should be shown as engine-defined unless EXE inspection finds a data-backed override.

---

#### T7 — Proximity launch / dash

- **Spawn mode:** fixed pixel position when visible.
- Turns toward the player and, once within horizontal range, launches with configured speed and changes animation.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `activation_x_range_tiles` | `+0x0D` | Horizontal activation distance |
| `launch_speed_step` | `+0x0E` | Launch speed step; runtime scales it by 16 |

**Editor-facing controls:** anchor, trigger range, launch speed, sprite preset, expert-only.

---

#### T8 — Periodic jumping enemy

- **Spawn mode:** fixed pixel position when visible.
- Waits for cooldown, checks whether player is within configured X/Y range, then jumps toward the player.
- Transitions through up / down / landed states and can repeat.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `activation_x_range_tiles` | `+0x0D` | Horizontal trigger distance |
| `jump_up_speed_step` | `+0x0E` | Initial upward speed step; runtime negates and scales by 16 |
| `jump_horizontal_speed_step` | `+0x0F` | Horizontal jump speed step; runtime scales by 16 |
| `activation_y_range_tiles` | `+0x10` | Vertical trigger distance |

**Editor-facing controls:** anchor, trigger box, jump height/speed, horizontal jump speed, respawn/cooldown, sprite preset, expert-only.

---

#### T9 — Horizontal patrol between bounds

- **Spawn mode:** fixed pixel position when visible.
- Patrols between two X boundaries and accelerates/decelerates horizontally.
- Used heavily for walking enemies.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `patrol_left_x_px` | `+0x0D` | Left patrol boundary in pixels |
| `patrol_right_x_px` | `+0x0F` | Right patrol boundary in pixels |
| `initial_runtime_x_step` | `+0x11` | Runtime state; reset to `0` on spawn in `blues` |
| `patrol_max_speed_step` | `+0x12` | Maximum horizontal patrol speed step |

**Editor-facing controls:** anchor Y, left/right patrol bounds shown directly in level view, max speed, sprite preset, expert-only. Hide/reset-only runtime step in normal editor UI.

---

#### T10 — Ground-emerge spawner in trigger region

- **Spawn mode:** trigger rectangle.
- After respawn cooldown and player-in-region check, runtime chooses an X position near the player, searches for suitable ground, and spawns the enemy emerging from the floor.
- It then performs a timed attack phase and may dive back/reset.

Trailing parameter:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `emerge_attack_speed_step` | `+0x0D` | Horizontal attack speed used after emergence |

**Editor-facing controls:** trigger region, respawn delay, attack speed, sprite preset, expert-only. Spawn point should be visualized as “runtime-selected near player,” not as a fixed dot.

---

#### T11 — Hit-triggered leap attacker

- **Spawn mode:** fixed anchor, conditional activation.
- Waits for the global monster-hit context (`hit_mask`) before launching.
- On activation it leaps horizontally toward player with an upward component and then accelerates downward until a terminal fall speed.

Trailing parameters:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `leap_horizontal_speed_step` | `+0x0D` | Horizontal launch speed step |
| `leap_up_speed_step` | `+0x0E` | Initial upward launch speed step |
| `max_fall_speed_step` | `+0x0F` | Target downward speed cap |

**Editor-facing controls:** anchor, launch speeds, fall speed cap, respawn delay, sprite preset, expert-only. The hit-trigger dependency should be explained in the editor.

---

#### T12 — Delayed horizontal ambusher

- **Spawn mode:** fixed anchor with player-position gate.
- Spawns only when the player is below it and within a particular distance band.
- Moves horizontally toward the player, and after a timeout can transition into a falling/reset state.

Trailing parameter:

| Decoded name | Source byte | Meaning |
|---|---:|---|
| `horizontal_speed_step` | `+0x0D` | Initial horizontal movement speed step |
| `record_tail_byte` | `+0x0E` | Present in level record; observed as `0xFF` in sampled shipped data, not yet given a user-facing meaning |

**Editor-facing controls:** anchor, horizontal speed, respawn/cooldown, sprite preset, expert-only. Distance-band trigger should be visualized rather than hidden.

---

### Initial interpretation of monster flags

The initial `flags` byte is stored in the level record, but the runtime often overwrites it during spawn (`monster_func2_*`). Therefore a future editor should **not** expose it as a simple row of user-facing checkboxes yet.

Flags observed in runtime logic appear to include:

- bit `0x04`: record currently active/spawned guard
- bit `0x08`: object uses tile-following movement update / ground movement path
- bit `0x10`: one of the collision/axe interaction filters
- bit `0x20`, `0x40`, `0x80`: additional motion/collision/animation-state flags used by specific behaviors

These need one more dedicated pass before being promoted into editor controls. For now, keep raw flags visible in advanced/debug views only.

---

### Suggested future Enemy Editor panel structure

#### Common section

- Slot ID / raw record index
- Enabled / Expert-only
- Visual preset / sprite-animation family
- Behavior type (friendly names, not `T2` only)
- Energy
- Respawn/cooldown
- Score reward

#### Position / activation section

Varies by behavior:

- Fixed spawn point
- Trigger rectangle (`T0`, `T10`)
- Patrol range (`T9`)
- Activation distance box/range (`T3`, `T5`, `T7`, `T8`)

#### Behavior-specific section

Only show controls relevant to the selected type:

- T2: vertical range + speed
- T4: swing radius + arc limit
- T8: jump speeds + activation box
- T9: patrol left/right + max speed
- T10: emerge region + attack speed
- etc.

#### Advanced/raw section

- Raw flags
- Runtime-initialized counters
- Bytes that appear reset by runtime or are not yet proven author-facing

This keeps the editor friendly without losing roundtrip fidelity.

## Mechanics as read-only editor panels

The GUI `Mechanics` tab is now intentionally closer to a future editor UI than to a raw binary debugger:

- The left side is a human-facing object list. Enemies and items are identified by editor labels rather than leading with raw slot/sprite IDs.
- Selecting a row centers the Level Viewer on that object and draws a yellow selection highlight over the map.
- The right side is a read-only inspector. Monster detail panels show only behavior-relevant authored controls first, then an `Advanced / raw` section for record IDs, sprite numbers, and runtime-initialization fields.
- Enemy labels are composed from a visual label plus the separately decoded behavior record. This matters because the same visual enemy sprite can use different movement logic.

Object label provenance:

- Prehistorik 2 level files store sprite numbers and behavior records, not text names.
- A few semantic item names are grounded directly in the `blues` runtime comments/logic, for example checkpoint, bomb, light toggles, end-of-level semaphore, and tap.
- The rest of the current names are editor-facing visual annotations kept in `pre2lib.labels`. They are deliberately separated from the binary parser and can be refined as RE knowledge improves.

## Viewer UX notes — object/tile inspectors

- Mechanics is now treated as a read-only editor preview rather than a raw debug dump.
- Selecting an object in Mechanics focuses it in the level preview; clicking a visible overlay object in the level preview opens the matching Mechanics inspector.
- Gates deliberately cycle their focus between source and destination when the same gate row is clicked repeatedly. Clicking a rendered source/destination gate tile in the level preview opens the same gate and focuses the clicked endpoint.
- The former "Tile Physics Lab" viewer tab is now a read-only **Tile Props** inspector. Clicking a map tile in the Level Viewer shows its visual collision diagram, decoded surface behavior, dynamic attr2 flags, and raw attribute bytes. Clicking the Tiles browser remains an atlas/status action and does not steal the inspector. The future editor can reuse this panel with editable controls.

## Platform behavior type labels

The low nibble of a platform's flags byte is not a UI-facing numeric “type”; it selects movement logic.
The viewer now names these behaviors directly:

| Type | Viewer label | Runtime vector interpretation |
|---:|---|---|
| 0 | Vertical shuttle — starts upward | `dx=0, dy=-velocity` |
| 1 | Diagonal shuttle — up-right / down-left | `dx=velocity, dy=-velocity` |
| 2 | Horizontal shuttle — starts right | `dx=velocity, dy=0` |
| 3 | Diagonal shuttle — down-right / up-left | `dx=velocity, dy=velocity` |
| 4 | Vertical shuttle — starts downward | `dx=0, dy=velocity` |
| 5 | Diagonal shuttle — down-left / up-right | `dx=-velocity, dy=velocity` |
| 6 | Horizontal shuttle — starts left | `dx=-velocity, dy=0` |
| 7 | Diagonal shuttle — up-left / down-right | `dx=-velocity, dy=-velocity` |
| 8 | Falling platform — drops when ridden, then resets | dedicated state machine |

Types 0–7 use the generic oscillating platform record (`max_velocity`, travel counter/range, current velocity); type 8 uses a separate falling-platform record.

## Animated tile groups — corrected model

The earlier viewer treated `attr2 & 0x80` as if it meant “this single tile animates”. That was incomplete.

The runtime logic in the user-provided `blues` reference engine shows that `0x80` marks the **base tile** of a **3-tile consecutive animation group**:

- base tile `i`
- phase tile `i+1`
- phase tile `i+2`

Only `i` carries `attr2 bit 0x80` in shipped data, but all three tiles are animated members.

The renderer remap is global-frame based:

| Stored map tile | Frame 1 | Frame 2 | Frame 3 |
|---|---:|---:|---:|
| `i` | `i` | `i+1` | `i+2` |
| `i+1` | `i+1` | `i+2` | `i` |
| `i+2` | `i+2` | `i` | `i+1` |

Consequences:

- animation does **not** rewrite the map,
- maps can intentionally place the three phases to create phase-shifted repeated patterns,
- viewer overlays must mark all group members, not only the base tile,
- future editor authoring should expose one “3-frame animated tile group” concept rather than three independent “animated” flags.

The current Python model now stores animated groups explicitly and renders Frame 1/2/3 via the toolbar. See also `reverse_engineering/notes/ANIMATED_TILES.md`.

## Tile Props UX correction

The Tile Props inspector is intentionally tied to **tiles selected in the level map**. The Tiles browser remains an atlas/browser and does not steal the Tile Props inspector when clicked.

# Animated tiles — reverse-engineering summary

## What the level stores

Animated background tiles are controlled by **tile attribute table 2**, bit `0x80`.
The important detail is that this bit marks **only the first tile ID** of a group.
The actual animation unit is always a **3-tile consecutive group**:

- group base `i`
- member tiles `i`, `i+1`, `i+2`

The shipped levels consistently follow this rule. For example, Level 1 contains
bases such as `0x50`, `0x64`, `0x78`, `0xB4`, ... and each base owns the next two
tile IDs as phases.

## What the runtime does

The `blues` reference engine mirrors the original runtime logic in
`p2/level.c::load_level_data_init_animated_tiles()` and
`p2/level.c::level_update_tilemap()`:

1. Scan tile definitions 0..255.
2. If `attr2[i] & 0x80`, create three remap tables for `i,i+1,i+2`.
3. During tile rendering, do not change the map tile. Instead map that tile ID
   through the current animation remap table.
4. Cycle between three global animation frames.

For a group starting at tile `i`:

| Stored map tile | Frame 1 | Frame 2 | Frame 3 |
|---|---:|---:|---:|
| `i` | `i` | `i+1` | `i+2` |
| `i+1` | `i+1` | `i+2` | `i` |
| `i+2` | `i+2` | `i` | `i+1` |

This means maps can place different phases intentionally and the animation keeps
those cells phase-shifted.

## Timing

The runtime uses a global tile animation counter. In `blues`:

```c
const uint8_t mask = (g_vars.snow.value >= 20) ? 1 : 3;
++g_vars.level_animated_tiles_counter;
if ((counter & mask) == 0) { cycle frame table; }
```

The exact visible speed therefore depends on game timing and the snow variable.
The editor shell currently exposes **Frame 1 / 2 / 3** in the toolbar for
accurate static inspection. Auto-play can be added later without changing the
parser/model.

## Viewer/editor consequences

The previous viewer interpretation was incomplete because it highlighted only
`attr2 & 0x80` base tiles. The corrected model:

- stores explicit animation groups,
- treats **all three** member tile IDs as animated,
- allows the level preview to render Frame 1/2/3,
- shows group base, phase and visual sequence in **Tile Props**.

## Editor consequences

A future tile editor should not expose this as three unrelated checkboxes.
Recommended authoring UI:

- `Animated 3-frame tile group` toggle on the base tile,
- group base tile ID,
- read-only/validated members `base`, `base+1`, `base+2`,
- preview of all three global frames,
- warning if a group would overlap another group or exceed tile `0xFF`.

The serializer should preserve the original compact encoding: only the group
base tile carries `attr2 bit 0x80`.

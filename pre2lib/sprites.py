from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json
from PIL import Image

from .formats import unpack_file, vga6_to_rgb, LevelData


@dataclass(frozen=True, slots=True)
class SpriteTables:
    offsets: tuple[int, ...]
    sizes: tuple[int, ...]
    provenance: str

    @property
    def count(self) -> int:
        return min(len(self.offsets), len(self.sizes)) // 2

    def size(self, sprite_num: int) -> tuple[int, int]:
        if not 0 <= sprite_num < self.count:
            raise IndexError(f"sprite {sprite_num} is outside 0..{self.count - 1}")
        return self.sizes[sprite_num * 2], self.sizes[sprite_num * 2 + 1]

    def origin(self, sprite_num: int) -> tuple[int, int]:
        if not 0 <= sprite_num < self.count:
            raise IndexError(f"sprite {sprite_num} is outside 0..{self.count - 1}")
        # Offsets are stored as signed 8-bit values in the original table.
        x = self.offsets[sprite_num * 2]
        y = self.offsets[sprite_num * 2 + 1]
        return (x - 256 if x >= 128 else x, y - 256 if y >= 128 else y)


@dataclass(frozen=True, slots=True)
class SpriteResolver:
    """Resolve per-level sprite numbers into the runtime global sprite namespace.

    The level stores two sprite-number bases in its metadata. The shipped runtime
    normalizes them into common banks before drawing. We expose that operation
    explicitly instead of hiding it inside the level parser, so the raw values
    remain inspectable. Constants 53/312 are currently RE bootstrap values from
    the reference engine; they are isolated here pending direct PRE2.EXE table
    extraction.
    """

    item_runtime_base: int = 53
    monster_runtime_base: int = 312

    def item_sprite(self, level: LevelData, raw_num: int) -> int | None:
        if raw_num == 0xFFFF or level.items_sprite_num_offset == 0xFFFF:
            return None
        return (raw_num - level.items_sprite_num_offset + self.item_runtime_base) & 0x1FFF

    def platform_sprite(self, level: LevelData, raw_num: int) -> int | None:
        return self.item_sprite(level, raw_num)

    def item_raw_for_runtime(self, level: LevelData, runtime_num: int) -> int:
        """Return a level-file item/platform sprite number for a runtime sprite.

        Placement presets are stored in the stable runtime/global sprite
        namespace, while LEVEL*.SQZ uses the level-local item bank base.  This
        is the exact inverse of :meth:`item_sprite` for authored item/platform
        visuals.
        """
        runtime_num = int(runtime_num) & 0x1FFF
        if level.items_sprite_num_offset == 0xFFFF:
            return runtime_num
        if runtime_num >= self.item_runtime_base:
            return (runtime_num - self.item_runtime_base + level.items_sprite_num_offset) & 0xFFFF
        return runtime_num

    def platform_raw_for_runtime(self, level: LevelData, runtime_num: int) -> int:
        return self.item_raw_for_runtime(level, runtime_num)

    def monster_sprite(self, level: LevelData, raw_num: int) -> int | None:
        if raw_num == 0xFFFF:
            return None
        if level.items_sprite_num_offset == 0xFFFF:
            return raw_num & 0x1FFF
        if raw_num >= level.monsters_sprite_num_offset:
            return (raw_num - level.monsters_sprite_num_offset + self.monster_runtime_base) & 0x1FFF
        if raw_num >= level.items_sprite_num_offset:
            return (raw_num - level.items_sprite_num_offset + self.item_runtime_base) & 0x1FFF
        return raw_num & 0x1FFF

    def monster_raw_for_runtime(self, level: LevelData, runtime_num: int) -> int:
        """Return a level-file monster sprite number for a runtime/global sprite.

        Enemy placement uses a global visual catalog, while LEVEL*.SQZ stores
        sprite IDs relative to each level's monster bank.  Existing shipped
        enemy visuals live in the monster runtime bank (312+), so reversing
        that bank transform gives a stable per-level raw ID for newly placed
        monsters.
        """
        runtime_num = int(runtime_num) & 0x1FFF
        if level.items_sprite_num_offset == 0xFFFF:
            return runtime_num
        if runtime_num >= self.monster_runtime_base:
            return (runtime_num - self.monster_runtime_base + level.monsters_sprite_num_offset) & 0xFFFF
        if runtime_num >= self.item_runtime_base:
            return (runtime_num - self.item_runtime_base + level.items_sprite_num_offset) & 0xFFFF
        return runtime_num


@lru_cache(maxsize=8)
def load_sprite_tables(project_dir: str | Path) -> SpriteTables:
    path = Path(project_dir) / "sprite_tables_reference.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SpriteTables(
        offsets=tuple(int(v) & 0xFF for v in payload["spr_offs_tbl"]),
        sizes=tuple(int(v) & 0xFF for v in payload["spr_size_tbl"]),
        provenance=str(payload.get("provenance", "")),
    )


@lru_cache(maxsize=8)
def load_sprites_blob(data_path: str | Path) -> bytes:
    return unpack_file(Path(data_path) / "sprites.sqz")


def sprite_data_offsets(tables: SpriteTables) -> tuple[int, ...]:
    offsets = [0]
    current = 0
    for sprite_num in range(tables.count):
        width, height = tables.size(sprite_num)
        if width == 0 or height == 0:
            byte_len = 0
        else:
            if width % 8 != 0:
                raise ValueError(f"sprite {sprite_num}: width {width} is not byte-aligned")
            byte_len = (width // 8) * height * 4
        current += byte_len
        offsets.append(current)
    return tuple(offsets)


@lru_cache(maxsize=8)
def _cached_sprite_data_offsets(offsets: tuple[int, ...], sizes: tuple[int, ...]) -> tuple[int, ...]:
    return sprite_data_offsets(SpriteTables(offsets=offsets, sizes=sizes, provenance=""))


def _sprite_slice(blob: bytes, tables: SpriteTables, sprite_num: int) -> bytes:
    offs = _cached_sprite_data_offsets(tables.offsets, tables.sizes)
    start = offs[sprite_num]
    end = offs[sprite_num + 1]
    if end > len(blob):
        raise ValueError(
            f"sprite {sprite_num}: data slice 0x{start:X}..0x{end:X} exceeds SPRITES.SQZ decoded size 0x{len(blob):X}"
        )
    return blob[start:end]


def decode_planar_bitmap(data: bytes, width: int, height: int) -> list[int]:
    if width == 0 or height == 0:
        return []
    if width % 8 != 0:
        raise ValueError(f"planar width must be a multiple of 8, got {width}")
    bytes_per_row = width // 8
    plane_size = bytes_per_row * height
    expected = plane_size * 4
    if len(data) != expected:
        raise ValueError(f"planar bitmap size mismatch: got {len(data)}, expected {expected}")
    pixels: list[int] = []
    for y in range(height):
        for xb in range(bytes_per_row):
            src = y * bytes_per_row + xb
            for bit in range(8):
                mask = 1 << (7 - bit)
                color = 0
                for plane in range(4):
                    if data[plane * plane_size + src] & mask:
                        color |= 1 << plane
                pixels.append(color)
    return pixels


def render_sprite_image(
    sprites_blob: bytes,
    tables: SpriteTables,
    palette: list[int],
    sprite_num: int,
    *,
    scale: int = 1,
    transparent_zero: bool = True,
) -> Image.Image:
    width, height = tables.size(sprite_num)
    if width == 0 or height == 0:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    pixels = decode_planar_bitmap(_sprite_slice(sprites_blob, tables, sprite_num), width, height)
    rgb = vga6_to_rgb(palette)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out = img.load()
    idx = 0
    for y in range(height):
        for x in range(width):
            color = pixels[idx]
            idx += 1
            if transparent_zero and color == 0:
                continue
            r, g, b = rgb[color]
            out[x, y] = (r, g, b, 255)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    return img


def render_sprite_atlas(
    sprites_blob: bytes,
    tables: SpriteTables,
    palette: list[int],
    sprite_nums: list[int] | None = None,
    *,
    cell_w: int = 96,
    cell_h: int = 96,
    columns: int = 8,
    scale: int = 1,
) -> Image.Image:
    if sprite_nums is None:
        sprite_nums = list(range(tables.count))
    rows = max(1, (len(sprite_nums) + columns - 1) // columns)
    atlas = Image.new("RGBA", (columns * cell_w, rows * cell_h), (24, 24, 24, 255))
    for idx, sprite_num in enumerate(sprite_nums):
        try:
            spr = render_sprite_image(sprites_blob, tables, palette, sprite_num, scale=scale)
        except Exception:
            continue
        cell_x = (idx % columns) * cell_w
        cell_y = (idx // columns) * cell_h
        px = cell_x + max(0, (cell_w - spr.width) // 2)
        py = cell_y + max(0, (cell_h - spr.height) // 2)
        atlas.alpha_composite(spr, (px, py))
    return atlas.convert("RGB")

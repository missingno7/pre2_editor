from __future__ import annotations

from PIL import Image, ImageDraw

from .formats import (
    LevelData,
    decode_planar_tile,
    resolve_front_tile_bytes,
    resolve_tile_bytes,
    vga6_to_rgb,
)


def _draw_indexed_tile(
    img: Image.Image,
    ox: int,
    oy: int,
    pixels: list[int],
    rgb: list[tuple[int, int, int]],
    transparent_zero: bool = False,
) -> None:
    px = img.load()
    rgba = img.mode == "RGBA"
    for y in range(16):
        base = y * 16
        py = oy + y
        for x in range(16):
            color = pixels[base + x]
            if transparent_zero and color == 0:
                continue
            if rgba:
                r, g, b = rgb[color]
                px[ox + x, py] = (r, g, b, 255)
            else:
                px[ox + x, py] = rgb[color]


def _draw_indexed_tile_alpha(
    img: Image.Image,
    ox: int,
    oy: int,
    pixels: list[int],
    rgb: list[tuple[int, int, int]],
    alpha: int,
) -> None:
    px = img.load()
    for y in range(16):
        base = y * 16
        py = oy + y
        for x in range(16):
            color = pixels[base + x]
            if color == 0:
                continue
            r, g, b = rgb[color]
            px[ox + x, py] = (r, g, b, alpha)


def render_tile_image(
    level: LevelData,
    union_tiles: bytes,
    palette: list[int],
    tile_num: int,
    scale: int = 1,
    transparent_zero: bool = False,
) -> Image.Image:
    rgb = vga6_to_rgb(palette)
    pixels = decode_planar_tile(resolve_tile_bytes(level, union_tiles, tile_num))
    img = Image.new("RGBA" if transparent_zero else "RGB", (16, 16), (0, 0, 0, 0) if transparent_zero else 0)
    _draw_indexed_tile(img, 0, 0, pixels, rgb, transparent_zero=transparent_zero)
    if scale != 1:
        img = img.resize((16 * scale, 16 * scale), Image.Resampling.NEAREST)
    return img


def render_front_tile_image(
    level: LevelData,
    front_tiles: bytes,
    palette: list[int],
    tile_num: int,
    scale: int = 1,
) -> Image.Image:
    rgb = vga6_to_rgb(palette)
    pixels = decode_planar_tile(resolve_front_tile_bytes(level, front_tiles, tile_num))
    img = Image.new("RGB", (16, 16))
    _draw_indexed_tile(img, 0, 0, pixels, rgb)
    if scale != 1:
        img = img.resize((16 * scale, 16 * scale), Image.Resampling.NEAREST)
    return img


def render_tile_atlas(
    level: LevelData,
    union_tiles: bytes,
    palette: list[int],
    scale: int = 2,
    columns: int = 16,
) -> Image.Image:
    tile_size = 16 * scale
    rows = (256 + columns - 1) // columns
    atlas = Image.new("RGB", (columns * tile_size, rows * tile_size))
    for tile_num in range(256):
        tile = render_tile_image(level, union_tiles, palette, tile_num, scale=scale)
        x = (tile_num % columns) * tile_size
        y = (tile_num // columns) * tile_size
        atlas.paste(tile, (x, y))
    return atlas


def render_front_tile_atlas(
    level: LevelData,
    front_tiles: bytes,
    palette: list[int],
    scale: int = 2,
    columns: int = 16,
) -> Image.Image:
    tile_size = 16 * scale
    rows = (256 + columns - 1) // columns
    atlas = Image.new("RGB", (columns * tile_size, rows * tile_size))
    for tile_num in range(256):
        tile = render_front_tile_image(level, front_tiles, palette, tile_num, scale=scale)
        x = (tile_num % columns) * tile_size
        y = (tile_num // columns) * tile_size
        atlas.paste(tile, (x, y))
    return atlas


def decode_planar_bitmap(src: bytes, width: int, height: int) -> list[int]:
    expected = width * height // 2
    if len(src) != expected:
        raise ValueError(f"planar bitmap length {len(src)} does not match {width}×{height}×4bpp ({expected})")
    plane_size = width * height // 8
    pixels = [0] * (width * height)
    for y in range(height):
        for byte_x in range(width // 8):
            off = y * (width // 8) + byte_x
            for bit in range(8):
                mask = 1 << (7 - bit)
                color = 0
                for plane in range(4):
                    if src[plane * plane_size + off] & mask:
                        color |= 1 << plane
                pixels[y * width + byte_x * 8 + bit] = color
    return pixels


def render_background_image(background_blob: bytes, palette: list[int], scale: int = 1) -> Image.Image:
    rgb = vga6_to_rgb(palette)
    width, height = 320, 200
    pixels = decode_planar_bitmap(background_blob, width, height)
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        base = y * width
        for x in range(width):
            px[x, y] = rgb[pixels[base + x]]
    if scale != 1:
        img = img.resize((width * scale, height * scale), Image.Resampling.NEAREST)
    return img


def render_level_image(
    level: LevelData,
    union_tiles: bytes,
    palette: list[int],
    front_tiles: bytes | None = None,
    scale: int = 1,
    show_grid: bool = False,
    show_front_layer: bool = False,
    show_flag_overlays: set[str] | None = None,
    show_object_overlays: set[str] | None = None,
    animation_frame: int = 0,
    show_secret_reveals: bool = True,
) -> Image.Image:
    rgb = vga6_to_rgb(palette)
    tile_cache: dict[int, list[int]] = {}
    front_cache: dict[int, list[int]] = {}
    # The DOS renderer copies the 320×200 background first and then draws
    # level tiles with palette index 0 treated as transparent. Keep that
    # transparency here so the GUI can place a viewport-fixed background below.
    img = Image.new("RGBA", (level.width_tiles * 16, level.height_tiles * 16), (0, 0, 0, 0))

    for ty in range(level.height_tiles):
        row_off = ty * level.width_tiles
        for tx in range(level.width_tiles):
            source_tile_num = level.tilemap[row_off + tx]
            tile_num = level.tile_for_animation_frame(source_tile_num, animation_frame)
            pixels = tile_cache.get(tile_num)
            if pixels is None:
                pixels = decode_planar_tile(resolve_tile_bytes(level, union_tiles, tile_num))
                tile_cache[tile_num] = pixels
            ox = tx * 16
            oy = ty * 16
            _draw_indexed_tile(img, ox, oy, pixels, rgb, transparent_zero=True)

    if show_secret_reveals:
        for bonus in level.active_bonuses:
            if bonus.initial_tile == bonus.revealed_tile:
                continue
            tx, ty = level.tilemap_xy(bonus.pos)
            if not (0 <= tx < level.width_tiles and 0 <= ty < level.height_tiles):
                continue
            ghost = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            for tile_num in (bonus.initial_tile, bonus.revealed_tile):
                pixels = tile_cache.get(tile_num)
                if pixels is None:
                    pixels = decode_planar_tile(resolve_tile_bytes(level, union_tiles, tile_num))
                    tile_cache[tile_num] = pixels
                _draw_indexed_tile_alpha(ghost, 0, 0, pixels, rgb, 128)
            ox = tx * 16
            oy = ty * 16
            ImageDraw.Draw(img).rectangle((ox, oy, ox + 15, oy + 15), fill=(0, 0, 0, 0))
            img.alpha_composite(ghost, (ox, oy))

    if show_front_layer and front_tiles is not None:
        for ty in range(level.height_tiles):
            row_off = ty * level.width_tiles
            for tx in range(level.width_tiles):
                tile_num = level.tilemap[row_off + tx]
                if not (level.tile_attributes2[tile_num] & 0x40):
                    continue
                pixels = front_cache.get(tile_num)
                if pixels is None:
                    pixels = decode_planar_tile(resolve_front_tile_bytes(level, front_tiles, tile_num))
                    front_cache[tile_num] = pixels
                _draw_indexed_tile(img, tx * 16, ty * 16, pixels, rgb, transparent_zero=True)

    if show_flag_overlays:
        draw = ImageDraw.Draw(img, "RGBA")
        for ty in range(level.height_tiles):
            row_off = ty * level.width_tiles
            for tx in range(level.width_tiles):
                tile_num = level.tilemap[row_off + tx]
                color = None
                if "front" in show_flag_overlays and level.tile_attributes2[tile_num] & 0x40:
                    color = (255, 255, 255, 60)
                elif "animated" in show_flag_overlays and level.is_animated_tile_member(tile_num):
                    color = (255, 255, 255, 60)
                elif "solid_attr1" in show_flag_overlays and level.tile_attributes1[tile_num] != 0:
                    color = (255, 255, 255, 60)
                elif "special_attr0" in show_flag_overlays and level.tile_attributes0[tile_num] != 0:
                    color = (255, 255, 255, 60)
                if color:
                    x0, y0 = tx * 16, ty * 16
                    draw.rectangle((x0, y0, x0 + 15, y0 + 15), fill=color)

    if show_object_overlays:
        draw = ImageDraw.Draw(img, "RGBA")
        label_draw = ImageDraw.Draw(img)

        def marker(px: int, py: int, label: str, fill: tuple[int, int, int, int], radius: int = 5) -> None:
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=fill, outline=(255, 255, 255, 220))
            label_draw.text((px + radius + 2, py - radius - 2), label, fill=(255, 255, 255))

        def tile_rect(tile_pos: int, label: str, fill: tuple[int, int, int, int], outline: tuple[int, int, int, int]) -> None:
            tx, ty = level.tilemap_xy(tile_pos)
            x0, y0 = tx * 16, ty * 16
            draw.rectangle((x0, y0, x0 + 15, y0 + 15), fill=fill, outline=outline, width=1)
            label_draw.text((x0 + 2, y0 + 2), label, fill=(255, 255, 255))

        if "gates" in show_object_overlays:
            for index, gate in enumerate(level.active_gates):
                tile_rect(gate.enter_pos, f"G{index}", (80, 180, 255, 90), (80, 180, 255, 220))
                tile_rect(gate.dst_pos, f"D{index}", (80, 180, 255, 45), (80, 180, 255, 180))
                # tilemap_pos is the camera destination after teleport, not a world object.
                cam_x, cam_y = level.tilemap_xy(gate.tilemap_pos)
                draw.rectangle((cam_x * 16, cam_y * 16, cam_x * 16 + 15, cam_y * 16 + 15), outline=(80, 180, 255, 140), width=1)

        if "bonuses" in show_object_overlays:
            for index, bonus in enumerate(level.active_bonuses):
                tile_rect(bonus.pos, f"S{index}", (255, 215, 60, 90), (255, 215, 60, 220))

        if "columns" in show_object_overlays:
            for index, column in enumerate(level.active_columns):
                tx, ty = level.tilemap_xy(column.tilemap_pos)
                x0 = tx * 16
                y0 = ty * 16
                x1 = x0 + max(1, column.width) * 16 - 1
                y1 = y0 + max(1, column.height) * 16 - 1
                draw.rectangle((x0, y0, x1, y1), fill=(180, 100, 255, 55), outline=(180, 100, 255, 220), width=2)
                label_draw.text((x0 + 2, y0 + 2), f"C{index}", fill=(255, 255, 255))
                if column.trigger_pos not in (0xFFFF, 0xFFFE):
                    tile_rect(column.trigger_pos, f"CT{index}", (180, 100, 255, 35), (180, 100, 255, 150))

        if "items" in show_object_overlays:
            for index, item in enumerate(level.active_items):
                marker(item.x_pos, item.y_pos, f"I{index}", (50, 220, 120, 180), radius=4)

        if "platforms" in show_object_overlays:
            for index, platform in enumerate(level.active_platforms):
                marker(platform.x_pos, platform.y_pos, f"P{index}:{platform.platform_type}", (255, 140, 70, 190), radius=5)

        if "monsters" in show_object_overlays:
            for index, monster in enumerate(level.monsters):
                if monster.uses_trigger_rect:
                    rect = monster.trigger_rect_tiles
                    if rect is not None:
                        tx, ty, tw, th = rect
                        x0, y0 = tx * 16, ty * 16
                        x1 = (tx + tw + 1) * 16 - 1
                        y1 = (ty + th + 1) * 16 - 1
                        draw.rectangle((x0, y0, x1, y1), fill=(255, 60, 60, 38), outline=(255, 60, 60, 220), width=2)
                        label_draw.text((x0 + 2, y0 + 2), f"M{index}:T{monster.movement_type}", fill=(255, 255, 255))
                else:
                    marker(monster.x_pos, monster.y_pos, f"M{index}:T{monster.movement_type}", (255, 60, 60, 190), radius=5)

        if "boss" in show_object_overlays and level.boss.active:
            boss = level.boss
            marker(boss.x_pos, boss.y_pos, "BOSS", (255, 80, 210, 210), radius=7)
            draw.line((boss.x_min, boss.y_pos, boss.x_max, boss.y_pos), fill=(255, 80, 210, 200), width=2)
            label_draw.text((boss.x_min, max(0, boss.y_pos - 16)), "boss x-range", fill=(255, 255, 255))

    if show_grid:
        draw = ImageDraw.Draw(img)
        for x in range(0, img.width, 16):
            draw.line((x, 0, x, img.height), fill=(255, 255, 255))
        for y in range(0, img.height, 16):
            draw.line((0, y, img.width, y), fill=(255, 255, 255))

    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    return img

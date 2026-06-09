from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from .assets import find_data_file

LEVEL_IDS = "123456789ABCDEFG"

# Reverse-engineered from the original level loader control flow; this mapping
# is not present inside LEVEL*.SQZ. The next RE step is to extract this tiny
# table directly from unpacked PRE2.EXE rather than keep the decoded values here.
BACKGROUND_FILE_BY_LEVEL = (0, 0, 0, 1, 1, 1, 2, 3, 3, 0, 4, 4, 4, 5, 0, 2)

MAX_LEVEL_GATES = 20
MAX_LEVEL_COLUMNS = 15
MAX_LEVEL_BONUSES = 80
MAX_LEVEL_ITEMS = 70
MAX_LEVEL_PLATFORMS = 16
MAX_LEVEL_MONSTERS = 150
MONSTER_ATTR_REGION_SIZE = 0x800

EXPECTED_MONSTER_LENGTHS = {
    0: 13,
    1: 13,
    2: 15,
    3: 14,
    4: 17,
    5: 16,
    6: 21,
    7: 15,
    8: 17,
    9: 19,
    10: 14,
    11: 16,
    12: 15,
}


class Pre2FormatError(RuntimeError):
    pass


def _need(data: bytes, off: int, size: int, label: str) -> None:
    if off < 0 or off + size > len(data):
        raise Pre2FormatError(
            f"Unexpected EOF while reading {label}: offset=0x{off:X}, size={size}, file_size=0x{len(data):X}"
        )


def _read_u16le(data: bytes, off: int) -> int:
    _need(data, off, 2, "u16")
    return int.from_bytes(data[off:off + 2], "little")


def _read_s16le(data: bytes, off: int) -> int:
    value = _read_u16le(data, off)
    return value - 0x10000 if value >= 0x8000 else value


def _read_s8(data: bytes, off: int) -> int:
    _need(data, off, 1, "s8")
    value = data[off]
    return value - 0x100 if value >= 0x80 else value


def unpack_file(path: str | Path) -> bytes:
    data = Path(path).read_bytes()
    if len(data) < 2:
        raise Pre2FormatError(f"{path}: compressed file is too short")
    sig = int.from_bytes(data[:2], "little")
    if sig == 0x4CB4:
        return unpack_eat(data)
    if (sig >> 8) == 0x10:
        return unpack_sqz(data)
    # Everything else (e.g. SAMPLE.SQZ, signature 0x0000) is the SQV codec,
    # mirroring the dispatch in the engine's unpack().
    return unpack_sqv(data)


def unpack_sqv(data: bytes) -> bytes:
    """Decompress the SQV format (dictionary + bitstream RLE) used by the
    shipped PRE2 sound bank. Direct port of blues p2/unpack.c unpack_sqv."""
    def u16(o: int) -> int:
        return data[o] | (data[o + 1] << 8)

    if len(data) < 6:
        raise Pre2FormatError("SQV file is too short")
    out_size = (u16(0) << 16) + u16(2)
    dict_len = u16(4)
    dict_buf = data[6:6 + dict_len]
    n = len(data)
    src = 6 + dict_len
    out = bytearray()
    bits = 0
    bits_count = 1
    state = 0
    count = 0
    prev = 0
    val = 0
    while len(out) < out_size:
        bits_count -= 1
        if bits_count == 0:
            if src + 2 > n:
                break
            bits = (data[src] << 8) | data[src + 1]
            src += 2
            bits_count = 16
        carry = bits & 0x8000
        bits = (bits << 1) & 0xFFFF
        if carry:
            val += 2
        if val + 1 >= dict_len:
            break
        val = dict_buf[val] | (dict_buf[val + 1] << 8)
        if (val & 0x8000) == 0:
            continue
        val &= 0x7FFF
        if state == 0:
            code = val & 0xFF
            if val >> 8:
                if code == 0:
                    state = 1
                elif code == 1:
                    state = 2
                else:
                    out += bytes((prev,)) * code
            else:
                prev = code
                out.append(code)
        elif state == 1:
            out += bytes((prev,)) * val
            state = 0
        elif state == 2:
            count = (val & 0xFF) << 8
            state = 3
        else:  # state == 3
            count |= val & 0xFF
            out += bytes((prev,)) * count
            state = 0
        val = 0
    if len(out) < out_size:  # pad a trailing shortfall with 8-bit silence
        out += bytes((0x80,)) * (out_size - len(out))
    return bytes(out)


def unpack_eat(data: bytes) -> bytes:
    """Decompress the EAT format used by the shipped Prehistorik 2 DOS data.

    Direct Python translation of `blues/p2/unpack.c`, kept here so the editor
    reads the original game files without an external conversion step.
    """
    if len(data) < 19:
        raise Pre2FormatError("EAT file is too short")
    if _read_u16le(data, 4) != 0x899D or _read_u16le(data, 6) != 0x6C64:
        raise Pre2FormatError("Unexpected EAT signature")

    output_size = (data[14] << 14) + _read_u16le(data, 15)
    pos = 17
    bits = _read_u16le(data, pos)
    pos += 2
    bit_len = 16
    out = bytearray()

    def next_bit() -> int:
        nonlocal bits, bit_len, pos
        bit = 1 if bits & (1 << (16 - bit_len)) else 0
        bit_len -= 1
        if bit_len == 0:
            _need(data, pos, 2, "EAT bitstream word")
            bits = _read_u16le(data, pos)
            pos += 2
            bit_len = 16
        return bit

    def zero_bits(count: int) -> int:
        i = 0
        while i < count:
            if next_bit():
                break
            i += 1
        return i

    def get_bits(count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | next_bit()
        return value

    def signed16(value: int) -> int:
        value &= 0xFFFF
        return value - 0x10000 if value >= 0x8000 else value

    def copy_reference(count: int, offset_hi: int, offset_lo: int) -> None:
        offset = signed16(offset_hi * 256 + offset_lo)
        for _ in range(count):
            src = len(out) + offset
            if src < 0 or src >= len(out):
                raise Pre2FormatError(
                    f"Invalid EAT back-reference offset={offset}, src={src}, out={len(out)}"
                )
            out.append(out[src])

    while True:
        while next_bit():
            _need(data, pos, 1, "EAT literal")
            out.append(data[pos])
            pos += 1

        b = next_bit()
        _need(data, pos, 1, "EAT reference offset")
        offset_lo = data[pos]
        pos += 1

        if b:
            offset_hi = 0xFE | next_bit()
            if not next_bit():
                i = 1
                while i < 4 and not next_bit():
                    offset_hi = (offset_hi << 1) | next_bit()
                    i += 1
                offset_hi -= 1 << i

            n = zero_bits(4)
            if n != 4:
                copy_reference(n + 3, offset_hi, offset_lo)
            elif next_bit():
                copy_reference(next_bit() + 7, offset_hi, offset_lo)
            elif not next_bit():
                copy_reference(get_bits(3) + 9, offset_hi, offset_lo)
            else:
                _need(data, pos, 1, "EAT long copy length")
                copy_reference(data[pos] + 17, offset_hi, offset_lo)
                pos += 1
        else:
            if next_bit():
                offset_hi = (0xF8 | get_bits(3)) - 1
                copy_reference(2, offset_hi, offset_lo)
            elif offset_lo == 0xFF:
                break
            else:
                copy_reference(2, 0xFF, offset_lo)

    if len(out) != output_size:
        raise Pre2FormatError(f"EAT output size mismatch: got {len(out)}, expected {output_size}")
    return bytes(out)


def unpack_sqz(data: bytes) -> bytes:
    """Decompress the alternate SQZ LZW format known from the engine."""
    if len(data) < 4:
        raise Pre2FormatError("SQZ file is too short")
    header = data[:4]
    if (header[1] & 0xF0) != 0x10:
        raise Pre2FormatError("Unexpected SQZ header")
    output_size = ((header[0] & 0x0F) << 16) | _read_u16le(header, 2)

    pos = 4
    top_code = 1 << 9
    code_size = 9
    new_codes = 258
    bits_left = 0
    current_bits = 0
    prefix = [0] * 0x1000
    suffix = [0] * 0x1000
    out = bytearray()
    previous_code = 0
    last_code = 0

    def get_bits(count: int) -> int:
        nonlocal pos, bits_left, current_bits
        _need(data, pos, 1, "SQZ bitstream")
        current_bits = (current_bits << 8) | data[pos]
        pos += 1
        bits_left += 8
        if bits_left < count:
            _need(data, pos, 1, "SQZ bitstream")
            current_bits = (current_bits << 8) | data[pos]
            pos += 1
            bits_left += 8
        code = current_bits >> (bits_left - count)
        bits_left -= count
        current_bits &= (1 << bits_left) - 1 if bits_left else 0
        return code

    def get_code() -> int:
        nonlocal top_code, code_size
        if top_code == new_codes and code_size != 12:
            code_size += 1
            top_code <<= 1
        return get_bits(code_size)

    def clear_code() -> int:
        nonlocal top_code, code_size, new_codes, previous_code, last_code
        top_code = 1 << 9
        code_size = 9
        new_codes = 258
        code = get_code()
        if code != 257:
            previous_code = code
            last_code = code & 0xFF
            out.append(last_code)
        return code

    code = clear_code()
    if code == 257:
        return bytes(out)

    while True:
        code = get_code()
        if code == 257:
            break
        if code == 256:
            clear_code()
            continue

        current_code = code
        stack: list[int] = []
        if new_codes <= code:
            stack.append(last_code)
            code = previous_code

        while code >= 256:
            stack.append(suffix[code])
            code = prefix[code]

        last_code = code & 0xFF
        stack.append(last_code)
        out.extend(reversed(stack))

        if new_codes < 0x1000:
            suffix[new_codes] = last_code
            prefix[new_codes] = previous_code
            new_codes += 1
        previous_code = current_code

    if len(out) != output_size:
        raise Pre2FormatError(f"SQZ output size mismatch: got {len(out)}, expected {output_size}")
    return bytes(out)


@dataclass(slots=True)
class LevelHeader:
    scrolling_top: int
    start_x_pos: int
    start_y_pos: int
    tilemap_w: int
    scrolling_mask: int


@dataclass(slots=True)
class Gate:
    enter_pos: int
    tilemap_pos: int
    dst_pos: int
    scroll_flag: int

    @property
    def active(self) -> bool:
        return self.enter_pos != 0xFFFF


@dataclass(slots=True)
class Column:
    tilemap_pos: int
    width: int
    height: int
    trigger_pos: int
    tiles_offset_buf: int
    y_target: int
    unk9: int

    @property
    def active(self) -> bool:
        return self.trigger_pos != 0xFFFF


@dataclass(slots=True)
class Monster:
    raw_offset: int
    length: int
    type_byte: int
    sprite_num_raw: int
    flags: int
    energy: int
    respawn_ticks: int
    current_tick: int
    score: int
    x_pos: int
    y_pos: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def movement_type(self) -> int:
        return self.type_byte & 0x7F

    @property
    def expert_only(self) -> bool:
        # Bit 7 of the monster type byte is the original level-file expert flag.
        return (self.type_byte & 0x80) != 0

    @property
    def unknown_type_flag(self) -> int:
        # Kept for compatibility with older table output; this is now understood.
        return self.type_byte & 0x80

    @property
    def uses_trigger_rect(self) -> bool:
        # In the original runtime, monster types 0 and 10 interpret x_pos/y_pos
        # as four packed trigger-rectangle bytes, not as a fixed sprite spawn point.
        return self.movement_type in {0, 10}

    @property
    def trigger_rect_tiles(self) -> tuple[int, int, int, int] | None:
        if not self.uses_trigger_rect:
            return None
        x0 = self.x_pos & 0xFF
        y0 = (self.x_pos >> 8) & 0xFF
        width = self.y_pos & 0xFF
        height = (self.y_pos >> 8) & 0xFF
        return x0, y0, width, height

    @property
    def spawn_pos_pixels(self) -> tuple[int, int] | None:
        if self.uses_trigger_rect:
            return None
        return self.x_pos, self.y_pos

    @property
    def behavior_name(self) -> str:
        return {
            0: "triggered fly-in spawner",
            1: "static / idle hazard",
            2: "vertical hanging oscillator",
            3: "proximity drop then ground charge",
            4: "swinging / rotating hanging enemy",
            5: "aimed dash when player is nearby",
            6: "scripted follower pattern",
            7: "proximity launch / dash",
            8: "periodic jumping enemy",
            9: "horizontal patrol between bounds",
            10: "ground-emerge spawner in trigger region",
            11: "hit-triggered leap attacker",
            12: "delayed horizontal ambusher",
        }.get(self.movement_type, f"unknown behavior type {self.movement_type}")

    @property
    def spawn_mode(self) -> str:
        if self.movement_type in {0, 10}:
            return "trigger region; runtime chooses spawn point"
        if self.movement_type in {11, 12}:
            return "fixed anchor with delayed/conditional activation"
        return "fixed spawn point when visible"


@dataclass(slots=True)
class Bonus:
    tile_num0: int
    tile_num1: int
    count: int
    pos: int

    @property
    def active(self) -> bool:
        return self.pos != 0xFFFF

    @property
    def mode(self) -> str:
        # Directly mirrors the runtime branching seen in the game logic:
        # 00..3F = repeatable small random bonus drops, 40..7F = hidden/revealed
        # tile secrets, 80..FF = multi-hit big random bonus.
        if self.count & 0x80:
            return "big_random_bonus"
        if self.count & 0x40:
            return "tile_reveal"
        return "small_random_bonus"

    @property
    def hit_count_estimate(self) -> int:
        # For the three count classes, the runtime decrements until the record is
        # resolved/disabled. These formulas match the branching in level.c.
        if self.count & 0x80:
            return (self.count & 0x7F) + 1
        if self.count & 0x40:
            return (self.count & 0x3F) + 1
        return self.count + 1

    @property
    def initial_tile(self) -> int:
        return self.tile_num0

    @property
    def revealed_tile(self) -> int:
        return self.tile_num1


@dataclass(slots=True)
class Item:
    x_pos: int
    y_pos: int
    sprite_num_raw: int
    y_delta: int

    @property
    def active(self) -> bool:
        return self.sprite_num_raw != 0xFFFF


@dataclass(slots=True)
class Platform:
    x_pos: int
    y_pos: int
    sprite_num_raw: int
    flags: int
    variant: str
    extra: dict[str, Any]

    @property
    def active(self) -> bool:
        return self.sprite_num_raw != 0xFFFF

    @property
    def platform_type(self) -> int:
        return self.flags & 0x0F


@dataclass(slots=True)
class Boss:
    x_min: int
    x_max: int
    speed: int
    energy: int
    state: int
    x_pos: int
    y_pos: int

    @property
    def active(self) -> bool:
        return self.state != 0xFF


@dataclass(slots=True, frozen=True)
class AnimatedTileGroup:
    """A 3-tile animation group controlled by attr2 bit 0x80 on its base tile.

    The original engine treats the three consecutive tile IDs as three visual
    phases and cyclically remaps *every* member during rendering:
      frame 0: base, base+1, base+2
      frame 1: base+1, base+2, base
      frame 2: base+2, base, base+1
    This preserves per-cell phase offsets when maps intentionally place
    base/base+1/base+2 in different cells.
    """
    base_tile: int

    @property
    def members(self) -> tuple[int, int, int]:
        return self.base_tile, self.base_tile + 1, self.base_tile + 2

    def phase_for_tile(self, tile_num: int) -> int | None:
        phase = tile_num - self.base_tile
        return phase if 0 <= phase <= 2 else None

    def tile_for_frame(self, tile_num: int, animation_frame: int) -> int:
        phase = self.phase_for_tile(tile_num)
        if phase is None:
            return tile_num
        return self.base_tile + ((phase + animation_frame) % 3)


@dataclass(slots=True)
class LevelData:
    level_index: int
    level_id: str
    raw: bytes
    height_tiles: int
    tilemap: bytes
    tile_lut: list[int]
    tiles_blob_offset: int
    local_tiles_count: int
    metadata_offset: int
    metadata_end_offset: int
    tile_attributes0: bytes
    tile_attributes1: bytes
    tile_attributes2: bytes
    tile_attributes3: bytes
    header: LevelHeader
    front_tiles_lut: list[int]
    gates: list[Gate]
    columns: list[Column]
    monsters: list[Monster]
    monster_attr_region_offset: int
    monster_attr_region_end: int
    items_sprite_num_offset: int
    monsters_sprite_num_offset: int
    bonuses: list[Bonus]
    items: list[Item]
    platforms: list[Platform]
    boss: Boss

    @property
    def width_tiles(self) -> int:
        return 256

    @property
    def tilemap_size(self) -> int:
        return self.width_tiles * self.height_tiles

    def tile_num_at(self, tx: int, ty: int) -> int | None:
        if tx < 0 or ty < 0 or tx >= self.width_tiles or ty >= self.height_tiles:
            return None
        return self.tilemap[ty * self.width_tiles + tx]

    def lut_value(self, tile_num: int) -> int:
        return self.tile_lut[tile_num & 0xFF]

    def tilemap_xy(self, tilemap_pos: int) -> tuple[int, int]:
        return tilemap_pos & 0xFF, tilemap_pos >> 8

    @property
    def animated_tile_groups(self) -> list[AnimatedTileGroup]:
        groups: list[AnimatedTileGroup] = []
        tile_num = 0
        while tile_num < 256:
            if self.tile_attributes2[tile_num] & 0x80:
                if tile_num + 2 < 256:
                    groups.append(AnimatedTileGroup(tile_num))
                tile_num += 3
            else:
                tile_num += 1
        return groups

    def animated_tile_group_for_tile(self, tile_num: int) -> AnimatedTileGroup | None:
        tile_num &= 0xFF
        for group in self.animated_tile_groups:
            if group.phase_for_tile(tile_num) is not None:
                return group
        return None

    def is_animated_tile_member(self, tile_num: int) -> bool:
        return self.animated_tile_group_for_tile(tile_num) is not None

    def animated_tile_phase(self, tile_num: int) -> int | None:
        group = self.animated_tile_group_for_tile(tile_num)
        return group.phase_for_tile(tile_num & 0xFF) if group is not None else None

    def tile_for_animation_frame(self, tile_num: int, animation_frame: int) -> int:
        group = self.animated_tile_group_for_tile(tile_num)
        if group is None:
            return tile_num & 0xFF
        return group.tile_for_frame(tile_num & 0xFF, animation_frame % 3)

    @property
    def active_gates(self) -> list[Gate]:
        return [gate for gate in self.gates if gate.active]

    @property
    def active_columns(self) -> list[Column]:
        return [column for column in self.columns if column.active]

    @property
    def active_bonuses(self) -> list[Bonus]:
        return [bonus for bonus in self.bonuses if bonus.active]

    @property
    def active_items(self) -> list[Item]:
        return [item for item in self.items if item.active]

    @property
    def active_platforms(self) -> list[Platform]:
        return [platform for platform in self.platforms if platform.active]


def _parse_monster_extra(data: bytes, p: int, movement_type: int, length: int) -> dict[str, Any]:
    """Decode movement-specific monster record parameters.

    Names below are intentionally editor-facing where the runtime behavior is
    clear from the original control flow. Values that are copied from the level
    file but then reset by the runtime are marked as initial_runtime_* rather
    than presented as authored gameplay controls.
    """
    extra: dict[str, Any] = {}
    if movement_type == 0:
        # x_pos/y_pos are packed trigger region bytes; no trailing parameters.
        pass
    elif movement_type == 1:
        # Static/idle object. No trailing parameters.
        pass
    elif movement_type == 2:
        extra.update(
            vertical_range_px=data[p + 0xD],
            vertical_speed_step=_read_s8(data, p + 0xE),
        )
    elif movement_type == 3:
        extra.update(
            activation_x_range_tiles=data[p + 0xD],
        )
    elif movement_type == 4:
        extra.update(
            swing_radius_px=data[p + 0xD],
            swing_angle_limit=data[p + 0xE],
            initial_runtime_angle=data[p + 0xF],
            initial_runtime_angle_step=data[p + 0x10],
        )
    elif movement_type == 5:
        extra.update(
            activation_x_range_tiles=data[p + 0xD],
            activation_y_range_tiles=data[p + 0xE],
            dash_speed_step=data[p + 0xF],
        )
    elif movement_type == 6:
        # Shipped data stores filler bytes here; the actual chase offset pattern
        # is a static runtime table in the original logic.
        extra.update(
            activation_x_range_tiles=data[p + 0xD],
            record_tail_bytes=data[p + 0xE:p + length].hex(" "),
            movement_pattern_source="runtime static table, not level tail",
        )
    elif movement_type == 7:
        extra.update(
            activation_x_range_tiles=data[p + 0xD],
            launch_speed_step=data[p + 0xE],
        )
    elif movement_type == 8:
        extra.update(
            activation_x_range_tiles=data[p + 0xD],
            jump_up_speed_step=_read_s8(data, p + 0xE),
            jump_horizontal_speed_step=_read_s8(data, p + 0xF),
            activation_y_range_tiles=data[p + 0x10],
        )
    elif movement_type == 9:
        extra.update(
            patrol_left_x_px=_read_s16le(data, p + 0xD),
            patrol_right_x_px=_read_s16le(data, p + 0xF),
            initial_runtime_x_step=_read_s8(data, p + 0x11),
            patrol_max_speed_step=data[p + 0x12],
        )
    elif movement_type == 10:
        extra.update(
            emerge_attack_speed_step=data[p + 0xD],
        )
    elif movement_type == 11:
        extra.update(
            leap_horizontal_speed_step=data[p + 0xD],
            leap_up_speed_step=data[p + 0xE],
            max_fall_speed_step=data[p + 0xF],
        )
    elif movement_type == 12:
        extra.update(
            horizontal_speed_step=data[p + 0xD],
            record_tail_byte=data[p + 0xE] if length > 14 else None,
        )
    else:
        extra.update(raw_extra=data[p + 0xD:p + length].hex(" "), unhandled_type=True)
    return extra


def _parse_level_with_height(raw: bytes, level_index: int, level_id: str, height: int) -> LevelData:
    if height <= 0 or height > 255:
        raise Pre2FormatError(f"Invalid candidate level height {height}")

    tilemap_size = height * 256
    lut_offset = tilemap_size
    lut_end = lut_offset + 512
    _need(raw, 0, lut_end, f"LEVEL{level_id} tilemap + tile LUT")

    tilemap = raw[:tilemap_size]
    tile_lut = [_read_u16le(raw, lut_offset + i * 2) for i in range(256)]
    tiles_blob_offset = lut_end

    # The original runtime advances by one 128-byte tile block for every local
    # LUT entry. We do the same; this keeps metadata discovery tied to the level
    # file itself rather than a hidden table copied from the executable.
    local_lut_values = [value for value in tile_lut if value != 0xFFFF and value < 0x100]
    local_tiles_count = len(local_lut_values)
    # Shipped levels use a compact local-tile index set 0..N-1. This is not an
    # external level-height lookup; it is an internal consistency rule that
    # distinguishes the real tile LUT from accidental 512-byte windows inside
    # level data when inferring the map height.
    if local_lut_values and sorted(local_lut_values) != list(range(local_tiles_count)):
        raise Pre2FormatError(
            f"Non-compact local tile LUT for LEVEL{level_id} height {height}: "
            f"count={local_tiles_count}, unique={len(set(local_lut_values))}, "
            f"min={min(local_lut_values)}, max={max(local_lut_values)}"
        )
    metadata_offset = tiles_blob_offset + local_tiles_count * 128
    _need(raw, metadata_offset, 256 * 3 + 9 + 512, f"LEVEL{level_id} metadata header")

    p = metadata_offset
    tile_attributes0 = raw[p:p + 256]; p += 256
    tile_attributes1 = raw[p:p + 256]; p += 256
    tile_attributes2 = raw[p:p + 256]; p += 256

    header = LevelHeader(
        scrolling_top=_read_u16le(raw, p),
        start_x_pos=_read_u16le(raw, p + 2),
        start_y_pos=_read_u16le(raw, p + 4),
        tilemap_w=_read_u16le(raw, p + 6),
        scrolling_mask=raw[p + 8],
    )
    p += 9

    front_tiles_lut = [_read_u16le(raw, p + i * 2) for i in range(256)]
    p += 512

    gates: list[Gate] = []
    for _ in range(MAX_LEVEL_GATES):
        gates.append(
            Gate(
                enter_pos=_read_u16le(raw, p),
                tilemap_pos=_read_u16le(raw, p + 2),
                dst_pos=_read_u16le(raw, p + 4),
                scroll_flag=raw[p + 6],
            )
        )
        p += 7

    columns: list[Column] = []
    for _ in range(MAX_LEVEL_COLUMNS):
        columns.append(
            Column(
                tilemap_pos=_read_u16le(raw, p),
                width=raw[p + 2],
                height=raw[p + 3],
                trigger_pos=_read_u16le(raw, p + 4),
                tiles_offset_buf=_read_u16le(raw, p + 6),
                y_target=raw[p + 8],
                unk9=raw[p + 9],
            )
        )
        p += 10

    monster_attr_region_offset = p
    monster_attr_region_end = monster_attr_region_offset + MONSTER_ATTR_REGION_SIZE
    _need(raw, monster_attr_region_offset, MONSTER_ATTR_REGION_SIZE, "monster attribute region")

    monsters: list[Monster] = []
    mp = monster_attr_region_offset
    while mp < monster_attr_region_end and raw[mp] < 50:
        length = raw[mp]
        if length < 13:
            raise Pre2FormatError(f"Invalid monster record length {length} at 0x{mp:X}")
        _need(raw, mp, length, "monster record")
        movement_type = raw[mp + 1] & 0x7F
        expected_length = EXPECTED_MONSTER_LENGTHS.get(movement_type)
        if expected_length is not None and length != expected_length:
            raise Pre2FormatError(
                f"Monster type {movement_type} has length {length}, expected {expected_length}, at 0x{mp:X}"
            )
        monsters.append(
            Monster(
                raw_offset=mp,
                length=length,
                type_byte=raw[mp + 1],
                sprite_num_raw=_read_u16le(raw, mp + 2),
                flags=raw[mp + 4],
                energy=raw[mp + 5],
                respawn_ticks=raw[mp + 6],
                current_tick=raw[mp + 7],
                score=raw[mp + 8],
                x_pos=_read_u16le(raw, mp + 9),
                y_pos=_read_u16le(raw, mp + 11),
                extra=_parse_monster_extra(raw, mp, movement_type, length),
            )
        )
        if len(monsters) > MAX_LEVEL_MONSTERS:
            raise Pre2FormatError(f"Monster table exceeds MAX_LEVEL_MONSTERS={MAX_LEVEL_MONSTERS}")
        mp += length

    p = monster_attr_region_end
    items_sprite_num_offset = _read_u16le(raw, p); p += 2
    monsters_sprite_num_offset = _read_u16le(raw, p); p += 2

    bonuses: list[Bonus] = []
    for _ in range(MAX_LEVEL_BONUSES):
        bonuses.append(
            Bonus(
                tile_num0=raw[p],
                tile_num1=raw[p + 1],
                count=raw[p + 2],
                pos=_read_u16le(raw, p + 3),
            )
        )
        p += 5

    tile_attributes3 = raw[p:p + 256]
    _need(raw, p, 256, "tile attributes 3")
    p += 256

    items: list[Item] = []
    for _ in range(MAX_LEVEL_ITEMS):
        items.append(
            Item(
                x_pos=_read_s16le(raw, p),
                y_pos=_read_s16le(raw, p + 2),
                sprite_num_raw=_read_u16le(raw, p + 4),
                y_delta=_read_s8(raw, p + 6),
            )
        )
        p += 7

    platforms: list[Platform] = []
    for _ in range(MAX_LEVEL_PLATFORMS):
        x_pos = _read_u16le(raw, p)
        y_pos = _read_u16le(raw, p + 2)
        sprite_num_raw = _read_u16le(raw, p + 4)
        flags = raw[p + 6]
        platform_type = flags & 15
        p += 7
        if platform_type == 8:
            extra = {
                "y_velocity": raw[p],
                "unk8": raw[p + 1],
                "unk9": raw[p + 2],
                "state": raw[p + 3],
                "y_delta": _read_u16le(raw, p + 4),
                "counter": raw[p + 6],
                "padding": raw[p + 7],
            }
            variant = "type8"
            p += 8
        else:
            extra = {
                "max_velocity": _read_s8(raw, p),
                "padding": raw[p + 1],
                "unk9": raw[p + 2],
                "unkA": _read_u16le(raw, p + 3),
                "counter": _read_u16le(raw, p + 5),
                "velocity": _read_s8(raw, p + 7),
            }
            variant = "other"
            p += 8
        platforms.append(
            Platform(
                x_pos=x_pos,
                y_pos=y_pos,
                sprite_num_raw=sprite_num_raw,
                flags=flags,
                variant=variant,
                extra=extra,
            )
        )

    boss = Boss(
        x_min=_read_u16le(raw, p),
        x_max=_read_u16le(raw, p + 2),
        speed=raw[p + 4],
        energy=_read_s16le(raw, p + 5),
        state=raw[p + 7],
        x_pos=_read_u16le(raw, p + 8),
        y_pos=_read_u16le(raw, p + 10),
    )
    p += 12

    if p != len(raw):
        raise Pre2FormatError(
            f"LEVEL{level_id} candidate height {height} leaves {len(raw) - p} trailing bytes "
            f"after metadata (metadata_end=0x{p:X}, file_size=0x{len(raw):X})"
        )

    return LevelData(
        level_index=level_index,
        level_id=level_id,
        raw=raw,
        height_tiles=height,
        tilemap=tilemap,
        tile_lut=tile_lut,
        tiles_blob_offset=tiles_blob_offset,
        local_tiles_count=local_tiles_count,
        metadata_offset=metadata_offset,
        metadata_end_offset=p,
        tile_attributes0=tile_attributes0,
        tile_attributes1=tile_attributes1,
        tile_attributes2=tile_attributes2,
        tile_attributes3=tile_attributes3,
        header=header,
        front_tiles_lut=front_tiles_lut,
        gates=gates,
        columns=columns,
        monsters=monsters,
        monster_attr_region_offset=monster_attr_region_offset,
        monster_attr_region_end=monster_attr_region_end,
        items_sprite_num_offset=items_sprite_num_offset,
        monsters_sprite_num_offset=monsters_sprite_num_offset,
        bonuses=bonuses,
        items=items,
        platforms=platforms,
        boss=boss,
    )


def _discover_level_height(raw: bytes, level_index: int, level_id: str) -> tuple[int, LevelData]:
    candidates: list[tuple[int, LevelData]] = []
    errors: list[str] = []
    for height in range(1, 256):
        try:
            level = _parse_level_with_height(raw, level_index, level_id, height)
        except (Pre2FormatError, IndexError) as exc:
            if len(errors) < 5:
                errors.append(f"h={height}: {exc}")
            continue
        candidates.append((height, level))
    if len(candidates) != 1:
        details = "; ".join(errors)
        raise Pre2FormatError(
            f"Unable to uniquely infer LEVEL{level_id} height from its own data: "
            f"{len(candidates)} valid candidates. Sample rejects: {details}"
        )
    return candidates[0]


def load_level(data_path: str | Path, level_index: int) -> LevelData:
    if level_index < 0 or level_index >= len(LEVEL_IDS):
        raise ValueError(f"level_index must be 0..{len(LEVEL_IDS) - 1}")

    level_id = LEVEL_IDS[level_index]
    level_file = find_data_file(data_path, f"LEVEL{level_id}.SQZ")
    raw = unpack_file(level_file)
    _height, level = _discover_level_height(raw, level_index, level_id)
    return level


def describe_tile_side_behavior(attr0: int) -> str:
    """Human-readable meaning of level tile attribute table 0.

    This table is used by the runtime for side contacts / horizontal collision.
    """
    return {
        0: "open sides",
        1: "solid from sides",
        2: "deadly from sides",
    }.get(attr0, f"unknown side behavior 0x{attr0:02X}")


def describe_tile_top_behavior(attr1: int) -> str:
    """Human-readable meaning of level tile attribute table 1.

    This table defines what happens when the player or other simulated objects
    approach the tile from above / land on it.
    """
    return {
        0: "no top surface",
        1: "solid top surface",
        2: "slightly slippery top",
        3: "slippery top",
        4: "very slippery top",
        5: "action-pass-through hatch",
        6: "deadly from above",
    }.get(attr1, f"unknown top behavior 0x{attr1:02X}")


def describe_tile_floor_profile(attr3: int) -> str:
    """Human-readable meaning of level tile attribute table 3.

    attr3 refines the vertical contact position inside a 16x16 tile. It is not
    a standalone collision class; attr1 generally says whether there is a top
    surface, and attr3 says what that top surface looks like.
    """
    if attr3 == 0:
        return "flat surface at tile top"
    base = attr3 & 0x0F
    slope = attr3 & 0x30
    if slope == 0x10:
        return f"slope down to the right, base y={base}"
    if slope == 0x20:
        return f"slope up to the right, base y={base}"
    if slope == 0x00:
        return f"flat surface inset y={base}"
    return f"unknown floor profile 0x{attr3:02X}"


def describe_tile_physics(attr0: int, attr1: int, attr3: int) -> str:
    """Compact collision/surface summary for GUI status lines."""
    return (
        f"sides: {describe_tile_side_behavior(attr0)}; "
        f"top: {describe_tile_top_behavior(attr1)}; "
        f"profile: {describe_tile_floor_profile(attr3)}"
    )

def load_union_tiles(data_path: str | Path) -> bytes:
    return unpack_file(find_data_file(data_path, "UNION.SQZ"))


def load_front_tiles(data_path: str | Path) -> bytes:
    return unpack_file(find_data_file(data_path, "FRONT.SQZ"))


def load_background_bitmap(data_path: str | Path, level_index: int) -> bytes:
    if level_index < 0 or level_index >= len(BACKGROUND_FILE_BY_LEVEL):
        raise ValueError(f"level_index must be 0..{len(BACKGROUND_FILE_BY_LEVEL) - 1}")
    background_index = BACKGROUND_FILE_BY_LEVEL[level_index]
    blob = unpack_file(find_data_file(data_path, f"BACK{background_index}.SQZ"))
    if len(blob) != 32000:
        raise Pre2FormatError(
            f"BACK{background_index}.SQZ decoded to {len(blob)} bytes; expected planar 320×200×4bpp (32000 bytes)"
        )
    return blob


def load_palettes(project_dir: str | Path) -> list[list[int]]:
    # Temporary fallback. These values were previously extracted for viewer bring-up.
    # Next RE milestone is to recover them directly from the unpacked PRE2.EXE.
    return json.loads((Path(project_dir) / "palettes.json").read_text(encoding="utf-8"))


def vga6_to_rgb(palette: list[int]) -> list[tuple[int, int, int]]:
    if len(palette) != 48:
        raise ValueError("A Prehistorik 2 level palette must contain 16 RGB triplets")
    return [
        tuple(round(channel * 255 / 63) for channel in palette[i:i + 3])
        for i in range(0, len(palette), 3)
    ]


def resolve_tile_bytes(level: LevelData, union_tiles: bytes, tile_num: int) -> bytes | None:
    lut_value = level.lut_value(tile_num)
    if lut_value == 0xFFFF:
        return None
    if lut_value < 0x100:
        start = level.tiles_blob_offset + lut_value * 128
        end = start + 128
        tile = level.raw[start:end]
    else:
        start = (lut_value - 256) * 128
        end = start + 128
        tile = union_tiles[start:end]
    return tile if len(tile) == 128 else None


def resolve_front_tile_bytes(level: LevelData, front_tiles: bytes, tile_num: int) -> bytes | None:
    if not (level.tile_attributes2[tile_num & 0xFF] & 0x40):
        return None
    front_num = level.front_tiles_lut[tile_num & 0xFF]
    start = front_num * 128
    end = start + 128
    tile = front_tiles[start:end]
    return tile if len(tile) == 128 else None


def decode_planar_tile(tile: bytes | None) -> list[int]:
    if tile is None:
        return [0] * 256
    if len(tile) != 128:
        raise ValueError("Prehistorik 2 tiles are 128-byte 16x16 planar 4bpp blocks")
    pixels: list[int] = []
    plane_size = 32
    for y in range(16):
        for xb in range(2):
            src = y * 2 + xb
            for bit in range(8):
                mask = 1 << (7 - bit)
                color = 0
                for plane in range(4):
                    if tile[plane * plane_size + src] & mask:
                        color |= 1 << plane
                pixels.append(color)
    return pixels


# ---------------------------------------------------------------------------
# Editor save path
# ---------------------------------------------------------------------------

def _write_u16le(buf: bytearray, off: int, value: int) -> None:
    if not 0 <= int(value) <= 0xFFFF:
        raise Pre2FormatError(f"u16 value out of range at 0x{off:X}: {value}")
    buf[off:off + 2] = int(value).to_bytes(2, "little", signed=False)


def _write_s16le(buf: bytearray, off: int, value: int) -> None:
    if not -0x8000 <= int(value) <= 0x7FFF:
        raise Pre2FormatError(f"s16 value out of range at 0x{off:X}: {value}")
    buf[off:off + 2] = int(value).to_bytes(2, "little", signed=True)


def _write_s8(buf: bytearray, off: int, value: int) -> None:
    if not -0x80 <= int(value) <= 0x7F:
        raise Pre2FormatError(f"s8 value out of range at 0x{off:X}: {value}")
    buf[off] = int(value) & 0xFF


def _pack_bits_msb(codes: list[tuple[int, int]]) -> bytes:
    out = bytearray()
    bit_buffer = 0
    bit_count = 0
    for code, width in codes:
        if not 0 <= code < (1 << width):
            raise Pre2FormatError(f"SQZ code {code} does not fit {width} bits")
        bit_buffer = (bit_buffer << width) | code
        bit_count += width
        while bit_count >= 8:
            shift = bit_count - 8
            out.append((bit_buffer >> shift) & 0xFF)
            bit_buffer &= (1 << shift) - 1 if shift else 0
            bit_count = shift
    if bit_count:
        out.append((bit_buffer << (8 - bit_count)) & 0xFF)
    return bytes(out)


def pack_sqz(raw: bytes) -> bytes:
    """Write a conservative SQZ stream accepted by the original decoder.

    The shipped assets use LZW-style dictionary compression. For editor saves
    correctness is more important than compression ratio, so this writer emits
    a standards-compliant SQZ stream that clears the dictionary before each
    subsequent literal. The files are larger than the originals, but they
    round-trip exactly through :func:`unpack_sqz` and avoid fragile edge cases in
    variable-width LZW encoding while editing is still evolving.
    """
    size = len(raw)
    if size > 0xFFFFF:
        raise Pre2FormatError(f"SQZ payload too large: {size} bytes")
    header = bytes(((size >> 16) & 0x0F, 0x10, size & 0xFF, (size >> 8) & 0xFF))
    # ``unpack_sqz`` starts by resetting the dictionary and immediately reading
    # the first data code. Unlike a conventional LZW container, the very first
    # code is therefore the first literal (or END for an empty payload), not a
    # leading CLEAR code.
    codes: list[tuple[int, int]] = []
    if not raw:
        codes.append((257, 9))
        return header + _pack_bits_msb(codes)
    codes.append((raw[0], 9))
    for byte in raw[1:]:
        codes.append((256, 9))
        codes.append((byte, 9))
    codes.append((257, 9))
    return header + _pack_bits_msb(codes)


def _write_monster_extra(raw: bytearray, monster: Monster, *, base_offset: int | None = None) -> None:
    """Patch authored movement-specific monster parameters kept in ``monster.extra``.

    These offsets mirror :func:`_parse_monster_extra`. Runtime-init/filler fields
    intentionally remain untouched unless they are already represented as normal
    authored controls in the editor.
    """
    p = monster.raw_offset if base_offset is None else base_offset
    t = monster.movement_type
    extra = monster.extra

    def u8(off: int, key: str) -> None:
        if key in extra and extra[key] is not None:
            raw[p + off] = int(extra[key]) & 0xFF

    def s8(off: int, key: str) -> None:
        if key in extra and extra[key] is not None:
            _write_s8(raw, p + off, int(extra[key]))

    def s16(off: int, key: str) -> None:
        if key in extra and extra[key] is not None:
            _write_s16le(raw, p + off, int(extra[key]))

    if t == 2:
        u8(0xD, "vertical_range_px")
        s8(0xE, "vertical_speed_step")
    elif t == 3:
        u8(0xD, "activation_x_range_tiles")
    elif t == 4:
        u8(0xD, "swing_radius_px")
        u8(0xE, "swing_angle_limit")
    elif t == 5:
        u8(0xD, "activation_x_range_tiles")
        u8(0xE, "activation_y_range_tiles")
        u8(0xF, "dash_speed_step")
    elif t == 6:
        u8(0xD, "activation_x_range_tiles")
    elif t == 7:
        u8(0xD, "activation_x_range_tiles")
        u8(0xE, "launch_speed_step")
    elif t == 8:
        u8(0xD, "activation_x_range_tiles")
        s8(0xE, "jump_up_speed_step")
        s8(0xF, "jump_horizontal_speed_step")
        u8(0x10, "activation_y_range_tiles")
    elif t == 9:
        s16(0xD, "patrol_left_x_px")
        s16(0xF, "patrol_right_x_px")
        u8(0x12, "patrol_max_speed_step")
    elif t == 10:
        u8(0xD, "emerge_attack_speed_step")
    elif t == 11:
        u8(0xD, "leap_horizontal_speed_step")
        u8(0xE, "leap_up_speed_step")
        u8(0xF, "max_fall_speed_step")
    elif t == 12:
        u8(0xD, "horizontal_speed_step")


def monster_extra_defaults(movement_type: int) -> dict[str, Any]:
    """Return editor-safe default behavior parameters for a monster behavior.

    The game stores monster records with behavior-specific variable tails.  When
    the editor converts a monster to a different behavior, those tails cannot be
    re-used byte-for-byte.  A zeroed record of the target behavior gives us a
    deterministic decoded baseline, after which shared named values may be kept
    by the UI where that is meaningful.
    """
    length = EXPECTED_MONSTER_LENGTHS.get(movement_type)
    if length is None:
        raise Pre2FormatError(f"Cannot build defaults for unknown monster behavior {movement_type}")
    raw = bytes([0] * length)
    return _parse_monster_extra(raw, 0, movement_type, length)


def _write_monster_record(raw: bytearray, offset: int, monster: Monster, *, original_record: bytes | None = None) -> int:
    """Write one compact monster record at *offset* and return its end offset."""
    movement_type = monster.movement_type
    length = EXPECTED_MONSTER_LENGTHS.get(movement_type)
    if length is None:
        # Unknown behavior records can only be preserved at their existing size.
        length = monster.length
    if length < 13:
        raise Pre2FormatError(f"Invalid monster record length {length} for behavior {movement_type}")
    end = offset + length
    if end > len(raw):
        raise Pre2FormatError("Monster record write would exceed output buffer")
    if original_record is not None and len(original_record) == length:
        raw[offset:end] = original_record
    else:
        raw[offset:end] = b"\x00" * length
    raw[offset] = length & 0xFF
    raw[offset + 1] = monster.type_byte & 0xFF
    _write_u16le(raw, offset + 2, monster.sprite_num_raw)
    raw[offset + 4] = monster.flags & 0xFF
    raw[offset + 5] = monster.energy & 0xFF
    raw[offset + 6] = monster.respawn_ticks & 0xFF
    raw[offset + 7] = monster.current_tick & 0xFF
    raw[offset + 8] = monster.score & 0xFF
    _write_u16le(raw, offset + 9, monster.x_pos)
    _write_u16le(raw, offset + 11, monster.y_pos)
    _write_monster_extra(raw, monster, base_offset=offset)
    return end


def rebuild_monster_attr_region(level: LevelData) -> bytes:
    """Build the fixed-size compact monster attribute region from editor monsters."""
    region = bytearray([0xFF]) * MONSTER_ATTR_REGION_SIZE
    cursor = 0
    if len(level.monsters) > MAX_LEVEL_MONSTERS:
        raise Pre2FormatError(f"Monster table exceeds MAX_LEVEL_MONSTERS={MAX_LEVEL_MONSTERS}")
    for monster in level.monsters:
        movement_type = monster.movement_type
        expected_length = EXPECTED_MONSTER_LENGTHS.get(movement_type, monster.length)
        if cursor + expected_length > MONSTER_ATTR_REGION_SIZE:
            raise Pre2FormatError(
                f"Monster table no longer fits the 0x{MONSTER_ATTR_REGION_SIZE:X}-byte region "
                f"after behavior conversion (needs at least 0x{cursor + expected_length:X})."
            )
        original_record = None
        ro = monster.raw_offset
        if level.monster_attr_region_offset <= ro < level.monster_attr_region_end:
            original_length = level.raw[ro]
            old_end = ro + original_length
            if (
                original_length == expected_length
                and old_end <= level.monster_attr_region_end
                and (level.raw[ro + 1] & 0x7F) == movement_type
            ):
                original_record = level.raw[ro:old_end]
        cursor = _write_monster_record(region, cursor, monster, original_record=original_record)
    return bytes(region)


def serialize_level(level: LevelData) -> bytes:
    """Serialize the mutable editor model back to the decompressed LEVEL*.SQZ payload.

    The parser keeps the original decompressed bytes in ``level.raw``. The
    serializer patches every table the current editor can mutate and leaves all
    still-unknown regions byte-for-byte untouched.
    """
    raw = bytearray(level.raw)
    if len(level.tilemap) != level.tilemap_size:
        raise Pre2FormatError(
            f"Tilemap size mismatch: got {len(level.tilemap)}, expected {level.tilemap_size}"
        )
    raw[:level.tilemap_size] = level.tilemap

    p = level.metadata_offset
    raw[p:p + 256] = level.tile_attributes0; p += 256
    raw[p:p + 256] = level.tile_attributes1; p += 256
    raw[p:p + 256] = level.tile_attributes2; p += 256

    _write_u16le(raw, p, level.header.scrolling_top)
    _write_u16le(raw, p + 2, level.header.start_x_pos)
    _write_u16le(raw, p + 4, level.header.start_y_pos)
    _write_u16le(raw, p + 6, level.header.tilemap_w)
    raw[p + 8] = level.header.scrolling_mask & 0xFF
    p += 9

    for value in level.front_tiles_lut:
        _write_u16le(raw, p, value)
        p += 2

    if len(level.gates) != MAX_LEVEL_GATES:
        raise Pre2FormatError(f"Gate table must contain {MAX_LEVEL_GATES} records")
    for gate in level.gates:
        _write_u16le(raw, p, gate.enter_pos)
        _write_u16le(raw, p + 2, gate.tilemap_pos)
        _write_u16le(raw, p + 4, gate.dst_pos)
        raw[p + 6] = gate.scroll_flag & 0xFF
        p += 7

    if len(level.columns) != MAX_LEVEL_COLUMNS:
        raise Pre2FormatError(f"Column table must contain {MAX_LEVEL_COLUMNS} records")
    for column in level.columns:
        _write_u16le(raw, p, column.tilemap_pos)
        raw[p + 2] = column.width & 0xFF
        raw[p + 3] = column.height & 0xFF
        _write_u16le(raw, p + 4, column.trigger_pos)
        _write_u16le(raw, p + 6, column.tiles_offset_buf)
        raw[p + 8] = column.y_target & 0xFF
        raw[p + 9] = column.unk9 & 0xFF
        p += 10

    # Monster records are variable-length.  Rebuild the entire compact fixed-size
    # attribute region on every save so Apply-driven behavior conversions can
    # freely switch between target record sizes without leaving stale records.
    if p != level.monster_attr_region_offset:
        raise Pre2FormatError("Monster attribute region offset drifted during serialization")
    monster_region = rebuild_monster_attr_region(level)
    raw[level.monster_attr_region_offset:level.monster_attr_region_end] = monster_region

    p = level.monster_attr_region_end
    _write_u16le(raw, p, level.items_sprite_num_offset); p += 2
    _write_u16le(raw, p, level.monsters_sprite_num_offset); p += 2

    if len(level.bonuses) != MAX_LEVEL_BONUSES:
        raise Pre2FormatError(f"Bonus table must contain {MAX_LEVEL_BONUSES} records")
    for bonus in level.bonuses:
        raw[p] = bonus.tile_num0 & 0xFF
        raw[p + 1] = bonus.tile_num1 & 0xFF
        raw[p + 2] = bonus.count & 0xFF
        _write_u16le(raw, p + 3, bonus.pos)
        p += 5

    raw[p:p + 256] = level.tile_attributes3
    p += 256

    if len(level.items) != MAX_LEVEL_ITEMS:
        raise Pre2FormatError(f"Item table must contain {MAX_LEVEL_ITEMS} records")
    for item in level.items:
        _write_s16le(raw, p, item.x_pos)
        _write_s16le(raw, p + 2, item.y_pos)
        _write_u16le(raw, p + 4, item.sprite_num_raw)
        _write_s8(raw, p + 6, item.y_delta)
        p += 7

    if len(level.platforms) != MAX_LEVEL_PLATFORMS:
        raise Pre2FormatError(f"Platform table must contain {MAX_LEVEL_PLATFORMS} records")
    for platform in level.platforms:
        _write_u16le(raw, p, platform.x_pos)
        _write_u16le(raw, p + 2, platform.y_pos)
        _write_u16le(raw, p + 4, platform.sprite_num_raw)
        raw[p + 6] = platform.flags & 0xFF
        p += 7
        if platform.platform_type == 8:
            _write_u16le(raw, p + 4, int(platform.extra.get("y_delta", 0)))
            raw[p] = int(platform.extra.get("y_velocity", 0)) & 0xFF
            raw[p + 1] = int(platform.extra.get("unk8", 0)) & 0xFF
            raw[p + 2] = int(platform.extra.get("unk9", 0)) & 0xFF
            raw[p + 3] = int(platform.extra.get("state", 0)) & 0xFF
            raw[p + 6] = int(platform.extra.get("counter", 0)) & 0xFF
            raw[p + 7] = int(platform.extra.get("padding", 0)) & 0xFF
        else:
            _write_s8(raw, p, int(platform.extra.get("max_velocity", 0)))
            raw[p + 1] = int(platform.extra.get("padding", 0)) & 0xFF
            raw[p + 2] = int(platform.extra.get("unk9", 0)) & 0xFF
            _write_u16le(raw, p + 3, int(platform.extra.get("unkA", 0)))
            _write_u16le(raw, p + 5, int(platform.extra.get("counter", 0)))
            _write_s8(raw, p + 7, int(platform.extra.get("velocity", 0)))
        p += 8

    boss = level.boss
    _write_u16le(raw, p, boss.x_min)
    _write_u16le(raw, p + 2, boss.x_max)
    raw[p + 4] = boss.speed & 0xFF
    _write_s16le(raw, p + 5, boss.energy)
    raw[p + 7] = boss.state & 0xFF
    _write_u16le(raw, p + 8, boss.x_pos)
    _write_u16le(raw, p + 10, boss.y_pos)
    p += 12

    if p != len(raw):
        raise Pre2FormatError(f"Serialized metadata ended at 0x{p:X}, file ends at 0x{len(raw):X}")
    return bytes(raw)


def save_level_sqz(path: str | Path, level: LevelData) -> None:
    Path(path).write_bytes(pack_sqz(serialize_level(level)))

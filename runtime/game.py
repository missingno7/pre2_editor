from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageTk

from pre2lib.formats import (
    LEVEL_IDS,
    LevelData,
    load_background_bitmap,
    load_front_tiles,
    load_level,
    load_palettes,
    load_union_tiles,
    resolve_front_tile_bytes,
    resolve_tile_bytes,
    decode_planar_tile,
    unpack_file,
    find_data_file,
    vga6_to_rgb,
)
from pre2lib.renderer import decode_planar_bitmap
from pre2lib.sprites import SpriteResolver, load_sprite_tables, load_sprites_blob, render_sprite_image
from runtime.prng import Pre2Prng
from runtime.sound import SoundEngine
from runtime.original_tables import (
    OBJECT_ANIMS,
    PLATFORM_TYPE_VECTOR,
    PLAYER_ANIM_LUT,
    club_anim_spec,
    club_overlay_for_player_sprite,
    club_projectile_start,
    PLAYER_GRAVITY16,
    PLAYER_GROUND_FRICTION16,
    PLAYER_HIT_ANIM_NUM,
    PLAYER_JUMP_FALL_SPR,
    PLAYER_JUMP_RUN_MAX16,
    PLAYER_JUMP_SPR,
    PLAYER_JUMP_Y_DELTA16,
    PLAYER_MAX_FALL16,
    PLAYER_RUN_ACCEL16,
    PLAYER_RUN_MAX16,
    sprite_logic_size,
    COS_TBL,
    SIN_TBL,
    SCORE_TBL,
)


def _s8(v: int) -> int:
    return v - 256 if v >= 128 else v

DOS_W = 320
DOS_H = 200
PANEL_H = 24
# Unified hit/expire blink colours (the game only ever blinks black or white).
_BLINK_WHITE = (255, 255, 255)
_BLINK_BLACK = (0, 0, 0)
# Gate-teleport curtain durations (ticks).
TRANS_CLOSE_FRAMES = 12
TRANS_OPEN_FRAMES = 12
# Active play area height (blues TILEMAP_SCREEN_H = GAME_SCREEN_H - PANEL_H).
# The bottom PANEL_H lines are the HUD, not part of the playfield/camera.
PLAY_H = DOS_H - PANEL_H  # 176
# Logic ticks/sec. The original PRE2 is vsync/work-limited (mode 13h ~70 Hz), so
# the effective logic rate is ~21.8 ticks/sec measured from the real DOS game
# (not a clean retrace divider, which is why it isn't an integer). The engine
# steps at exactly this rate; render interpolation smooths it to the display FPS
# even though the numbers don't divide evenly.
TICK_HZ = 21.8
TILE = 16

# Pre-level "you are here" adventure-map screen (MAP.SQZ, music CARTE.TRK). The
# map is a 640x200 16-colour planar image the journey crosses left->right; the
# screen pans to the current level's spot before play. MAP_W is the map width;
# MARKER_X/Y are the per-level marker position on the map (estimated across the
# 16 levels — exact values await PRE2.EXE RE, the EXE is DIET-packed). MAP_SCROLL
# is the pan speed in px/tick; MAP_HOLD is how long the marker is shown before
# play once the pan settles.
MAP_W = 640
MAP_SCROLL = 4
MAP_HOLD = 44
MAP_MARKER_X = (60, 100, 150, 200, 250, 300, 340, 380, 420, 455, 490, 520, 550, 575, 600, 620)
MAP_MARKER_Y = 96

# Runtime constants are deliberately centralized. Values with *16 suffix are
# original-style 1/16 px per tick units transcribed from blues-master/p2 and
# still pending direct PRE2.EXE confirmation.
PLAYER_W = 18
PLAYER_H = 32
PLAYER_HALF_W = PLAYER_W // 2
CAMERA_MARGIN_X = 112
# Vertical dead zone within the PLAY_H (176px) view: the player can move freely
# between CAMERA_MARGIN_TOP from the top and CAMERA_MARGIN_BOTTOM_PLAY from the
# bottom before the camera scrolls. Wider = more relaxed vertical scrolling.
CAMERA_MARGIN_TOP = 52
CAMERA_MARGIN_BOTTOM_PLAY = 52
CAMERA_MARGIN_BOTTOM = 112

# p2/staticres.c: score_spr_lut[].  Used by the original pickup code to map
# most collectible runtime sprite IDs to one of the floating score sprites
# 74..90.  Keeping it here avoids inventing ad-hoc pickup feedback.


def _extract_c_array_numbers(source: str, name: str) -> tuple[int, ...]:
    """Parse a small C initializer array from the bundled blues_p2 reference.

    This keeps monster animation tables in a local inspectable reference file
    instead of hiding a 1100-byte blob directly in runtime code.  The values are
    still marked blues-confirmed until the matching PRE2.EXE segment is mapped.
    """
    marker = f"{name}[] ="
    start = source.find(marker)
    if start < 0:
        return ()
    brace = source.find("{", start)
    end = source.find("};", brace)
    if brace < 0 or end < 0:
        return ()
    body = source[brace + 1:end]
    out: list[int] = []
    token = ""
    for ch in body:
        if ch.isalnum() or ch in "xX+-":
            token += ch
        else:
            if token:
                try:
                    out.append(int(token, 0))
                except ValueError:
                    pass
                token = ""
    if token:
        try:
            out.append(int(token, 0))
        except ValueError:
            pass
    return tuple(out)


def _bytes_to_s16_words(data: tuple[int, ...]) -> tuple[int, ...]:
    words: list[int] = []
    for i in range(0, len(data) - 1, 2):
        value = (data[i] & 0xFF) | ((data[i + 1] & 0xFF) << 8)
        words.append(value - 0x10000 if value & 0x8000 else value)
    return tuple(words)
SCORE_SPR_LUT: tuple[int, ...] = (
    0x10,0x0F,0x0D,0x0E,0x0C,0x0C,0x0D,0x0E,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x03,0x03,0x02,0x02,0x01,0x01,0x03,0x02,0x03,0x03,0x03,0x02,0x03,0x00,
    0x00,0x00,0x00,0x00,0x01,0x02,0x02,0x03,0x03,0x01,0x03,0x03,0x01,0x03,0x0B,0x01,
    0x00,0x03,0x01,0x03,0x02,0x02,0x02,0x00,0x03,0x02,0x01,0x00,0x02,0x03,0x01,0x01,
    0x01,0x02,0x03,0x01,0x03,0x03,0x00,0x09,0x0A,0x08,0x08,0x09,0x0A,0x08,0x07,0x08,
    0x0B,0x0A,0x04,0x06,0x04,0x07,0x05,0x04,0x04,0x05,0x07,0x04,0x0B,0x07,0x03,0x06,
    0x06,0x06,0x06,0x03,0x06,0x0A,0x07,0x05,0x05,0x0B,0x0A,0x08,0x09,0x00,
)


@dataclass(slots=True)
class InputState:
    left: bool = False
    right: bool = False
    up: bool = False
    down: bool = False
    jump: bool = False
    action: bool = False
    fire: bool = False


@dataclass(slots=True)
class PlayerState:
    x: int
    y: int
    ipx: int = 0  # interpolation snapshot
    ipy: int = 0
    vx: int = 0
    vy: int = 0
    facing: int = 1
    on_ground: bool = False
    frame_tick: int = 0
    jump_latch: bool = False
    jump_phase: int | None = None
    x_friction: int = 0
    spr_num: int = 9
    collision_h: int = 35
    current_anim_num: int = 0xFF
    special_anim_num: int = 0xFF
    anim_seq_num: int = 0
    anim_index: int = 0
    current_anim_type: int = 0
    update_counter: int = 0
    moving_counter: int = 0
    anim2_counter: int = 0
    nojump_counter: int = 0
    jumping_counter: int = 0
    action_counter: int = 0
    # Exact-ish club/action state names from level_update_player():
    # player_club_anim_duration masks input while the club swing is in its
    # active/recovery window; player_anim_0x40_flag means the player is still
    # inside a 0x40-tagged club animation sequence.
    club_type: int = 0
    bonus_letters_mask: int = 0
    energy: int = 3
    bonus_energy_counter: int = 0
    lives: int = 2
    club_power: int = 20
    club_powerup_duration: int = 0
    club_anim_duration: int = 0
    anim_0x40_flag: int = 0
    hit_counter: int = 0
    prev_y: int = 0
    tile_flags: int = 0
    death_flag: int = 0
    dying: bool = False
    death_timer: int = 0
    death_vx: int = 0  # death-arc pixel velocities (not 1/16 units)
    death_vy: int = 0
    restart_level_flag: int = 0
    shake_screen_counter: int = 0
    level_force_x_scroll_flag: int = 0

    @property
    def left(self) -> int:
        return self.x - PLAYER_HALF_W

    @property
    def right(self) -> int:
        return self.x + PLAYER_HALF_W - 1

    @property
    def top(self) -> int:
        return self.y - self.collision_h + 1

    @property
    def bottom(self) -> int:
        return self.y


@dataclass(slots=True)
class RuntimeObject:
    """Small objects_tbl subset used by the gameplay source-port scaffold.

    PRE2 keeps the player in objects_tbl[1], the visible club overlay in
    objects_tbl[0], club projectiles in objects_tbl[2..5], short-lived hit
    sparks in objects_tbl[6..10], live monsters in objects_tbl[11..22],
    dynamic bonus drops in objects_tbl[23..54], visible items in 55..74, and
    score popups in 75..90.  Modelling those slots explicitly is more useful
    than ad-hoc draw/collision code because the original engine routes almost
    every gameplay interaction through the same table.
    """

    slot: int
    spr_num: int = 0xFFFF
    x: int = 0
    y: int = 0
    ipx: int = 0  # interpolation snapshot (position before the last tick)
    ipy: int = 0
    iact: bool = False  # was this slot active at the snapshot
    iref: int = -2      # slot identity at the snapshot (ref_index) -> detect slot reuse
    x_velocity: int = 0
    y_velocity: int = 0
    x_friction: int = 0
    anim_words: tuple[int, ...] = ()
    anim_index: int = 0
    # Monsters use the original absolute byte-stream model instead of a slice:
    # anim_ptr is a word index into RuntimeWorld.monster_anim_words (mirrors
    # blues obj->data.m.anim). anim_fallback is the sprite shown if the stream
    # could not be resolved.
    anim_ptr: int = -1
    anim_fallback: int = 0
    ttl: int = 0
    counter: int = 0
    data_y_velocity: int = 0
    ref_index: int | None = None
    monster_state: int = 0
    monster_flags: int = 0  # mirrors blues m->flags (level_monster AI/movement flags)
    monster_obj_flags: int = 0  # mirrors blues obj->data.m.flags (object runtime flags)
    monster_energy: int = 0
    monster_hit_jump_counter: int = 0
    monster_type: int = 0
    monster_base_spr: int = 0
    hit_flash: int = 0  # frames to draw the monster white after a club hit

    @property
    def active(self) -> bool:
        return self.spr_num != 0xFFFF


@dataclass(slots=True)
class RuntimeMonsterState:
    index: int
    movement_type: int
    runtime_sprite: int
    record_flags: int
    energy: int
    respawn_ticks: int
    current_tick: int
    score: int
    x_pos: int
    y_pos: int
    extra: dict
    active_slot: int | None = None
    flags: int = 0
    x_step: int = 0
    type6_pattern_index: int = 0
    type0_side: int = 0
    type4_angle: int = 0
    type4_angle_step: int = 0


@dataclass(slots=True)
class PlatformState:
    slot: int
    base_x: int
    base_y: int
    x: int
    y: int
    prev_x: int
    prev_y: int
    sprite_num_raw: int
    flags: int
    platform_type: int
    variant: str
    max_velocity: int = 0
    velocity: int = 0
    counter: int = 0
    unkA: int = 0
    y_delta: int = 0
    y_velocity16: int = 0
    drop_counter: int = 0
    reset_delay: int = 0
    falling_state: int = 0
    surf_h: int = 0  # sprite height; standing surface = y - surf_h (blues y_pos - spr_height)

    @property
    def dx(self) -> int:
        return self.x - self.prev_x

    @property
    def dy(self) -> int:
        return self.y - self.prev_y


class RuntimeWorld:
    """Mutable gameplay state for the second app.

    The goal is to become an exact source port.  At this stage it already uses
    original decompressed map/tile/sprite/background data, original tile
    collision attribute tables, 19 Hz integer ticks, and a separate runtime state
    model so editor code does not become gameplay code.
    """

    def __init__(self, project_dir: Path, data_dir: Path, level_index: int = 0, *, audio_debug: bool = False) -> None:
        self.project_dir = project_dir
        self.data_dir = data_dir
        self.resource_dir = project_dir / "resources"
        self.level_index = level_index
        self.palettes = load_palettes(self.resource_dir)
        self.palette = self.palettes[level_index]
        self.rgb = vga6_to_rgb(self.palette)
        self.union_tiles = load_union_tiles(data_dir)
        self.front_tiles = load_front_tiles(data_dir)
        self.sprite_tables = load_sprite_tables(self.resource_dir)
        self.sprites_blob = load_sprites_blob(data_dir)
        self.sprite_resolver = SpriteResolver()
        try:
            self._allfonts = unpack_file(find_data_file(data_dir, "ALLFONTS.SQZ"))
        except Exception:
            self._allfonts = None
        self.tick_count = 0
        # Develop/debug toggles (driven by the menu).
        self.expert = False
        self.god_mode = False
        self.tile_cache: dict[int, list[int]] = {}
        self.front_cache: dict[int, list[int]] = {}
        self.sprite_cache: dict[tuple[int, int], Image.Image] = {}
        self.prng = Pre2Prng()
        self.sound = SoundEngine(data_dir, debug=audio_debug)
        # blues player_jump_monster_flag / monster.collide_y_dist, written by the
        # collide primitive and read by the player-monster collision stomp branch.
        self._jump_monster_flag = 0
        self._collide_y_dist = 0
        # True while the player is riding a platform (blues force_x_scroll_flag):
        # suppresses the decor's airborne fall-update so it doesn't keep setting
        # nojump_counter and block jumping off the platform.
        self._on_platform = False
        # Gate/teleport screen transition (the DOS curtain effect; not in blues):
        # phase 0 none, 1 closing (black rolls top+bottom -> centre), 2 opening
        # (black recedes centre -> left+right). Teleport applied at close apex.
        self._trans_phase = 0
        self._trans_t = 0
        self._trans_pending = None
        self.platforms: list[PlatformState] = []
        self.monster_anim_words, self.monster_spr_hit_table = self._load_monster_reference_tables()
        self._key_hdir = False
        self._input_hdir = 0
        self._last_jump_held = False
        self.monster_type0_side = 0
        self.monster_type10_dist = 0
        self.load_level(level_index)

    def load_level(self, level_index: int) -> None:
        self.level_index = level_index % len(LEVEL_IDS)
        self.level = load_level(self.data_dir, self.level_index)
        self.runtime_tilemap = bytearray(self.level.tilemap)
        # Incremented whenever gameplay mutates a tile. Renderers can use this
        # to update cached tile layers instead of rebuilding the visible map.
        self._tilemap_version = 0
        self._tile_dirty_positions: list[tuple[int, int]] = []
        self._init_secret_bonus_tiles()
        self.decor_tile0_offset: int | None = None
        self.palette = self.palettes[self.level_index]
        self.rgb = vga6_to_rgb(self.palette)
        self.tile_cache.clear()
        self.front_cache.clear()
        self.sprite_cache.clear()
        # Image caches for fast rendering (paste instead of per-pixel loops).
        self._tile_img_cache: dict[int, Image.Image] = {}
        self._front_img_cache: dict[int, Image.Image] = {}
        self.background_pixels = decode_planar_bitmap(load_background_bitmap(self.data_dir, self.level_index), DOS_W, DOS_H)
        # Precompute the (static, viewport-fixed) background as one image so each
        # frame is a copy + sprite/tile pastes rather than 64000 pixel writes.
        self._bg_image = Image.new("RGB", (DOS_W, DOS_H))
        self._bg_image.putdata([self.rgb[p] for p in self.background_pixels])
        self._build_panel_assets()
        self.player = PlayerState(
            x=int(self.level.header.start_x_pos),
            y=int(self.level.header.start_y_pos),
        )
        self.player.prev_y = self.player.y
        # Checkpoint (washing-machine) respawn point; defaults to the level start
        # and is moved when the player touches a checkpoint item (sprite 281).
        self.checkpoint_x = int(self.level.header.start_x_pos)
        self.checkpoint_y = int(self.level.header.start_y_pos)
        self._pending_level = None
        self._complete = None  # level-completed bonuses animation state (or None)
        self._map_intro = None  # pre-level "you are here" map screen state (or None)
        if not hasattr(self, "score"):
            self.score = 0
        self.platforms = self._init_platform_states()
        self.runtime_objects = [RuntimeObject(slot=i) for i in range(91)]
        self.monster_states = self._init_monster_states()
        self.current_hit_object_slot = 6
        self.camera_x = max(0, self.player.x - DOS_W // 2)
        self.camera_y = max(0, self.player.y - DOS_H // 2)
        self._icam_x = self.camera_x
        self._icam_y = self.camera_y
        self._sync_player_collision_size()
        self._asm_update_player_decor()
        self._update_runtime_items()
        self._clamp_camera()
        # Per-level music (blues p2/game.c do_level music_tbl).
        music_tbl = (9, 9, 0, 0, 0, 13, 4, 4, 10, 13, 16, 16, 16, 9, 14, 4)
        if hasattr(self, "sound") and 0 <= self.level_index < len(music_tbl):
            self.play_music(music_tbl[self.level_index])

    @property
    def world_w(self) -> int:
        return self.level.width_tiles * TILE

    @property
    def world_h(self) -> int:
        return self.level.height_tiles * TILE

    def _load_monster_reference_tables(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        staticres = self.project_dir / "reverse_engineering" / "reference" / "blues_p2" / "staticres.c"
        if not staticres.exists():
            return (), ()
        text = staticres.read_text(encoding="utf-8", errors="replace")
        anim_bytes = _extract_c_array_numbers(text, "monster_anim_tbl")
        spr_tbl = _extract_c_array_numbers(text, "monster_spr_tbl")
        return _bytes_to_s16_words(anim_bytes), tuple(v & 0xFFFF for v in spr_tbl)

    def _init_monster_states(self) -> list[RuntimeMonsterState]:
        states: list[RuntimeMonsterState] = []
        for index, monster in enumerate(self.level.monsters):
            runtime_num = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
            if runtime_num is None:
                runtime_num = 0xFFFF
            states.append(RuntimeMonsterState(
                index=index,
                movement_type=monster.movement_type,
                runtime_sprite=runtime_num & 0x1FFF,
                record_flags=int(monster.flags),
                energy=int(monster.energy),
                respawn_ticks=int(monster.respawn_ticks),
                current_tick=int(monster.current_tick),
                score=int(monster.score),
                x_pos=int(monster.x_pos),
                y_pos=int(monster.y_pos),
                extra=dict(monster.extra),
            ))
        return states

    def _find_free_monster_slot(self) -> RuntimeObject | None:
        for slot in range(11, 23):
            obj = self.runtime_objects[slot]
            if not obj.active:
                return obj
        return None

    def _monster_anim_start(self, movement_type: int, runtime_sprite: int) -> int:
        """Resolve the start word index in monster_anim_words for type+sprite.

        Faithful port of the anim search in level_update_objects_monsters():
        find the `0x7D01, type` marker, skip both marker words, then walk word
        by word until the (relative) sprite number is found; the anim pointer is
        set AT that word. Returns -1 if not found (caller uses anim_fallback).
        """
        words = self.monster_anim_words
        n = len(words)
        rel_sprite = (runtime_sprite - self.sprite_resolver.monster_runtime_base) & 0x1FFF
        # blues advances p by 2 before the first compare, so start scanning at 1.
        w = 1
        while w + 1 < n:
            if (words[w] & 0xFFFF) == 0x7D01 and (words[w + 1] & 0xFFFF) == (movement_type & 0xFFFF):
                j = w + 2
                while j < n and (words[j] & 0x1FFF) != rel_sprite:
                    j += 1
                return j if j < n else -1
            w += 1
        return -1

    def _monster_anim_step(self, obj: RuntimeObject) -> int:
        """Read the current anim word and advance, like the original.

        Negative words are signed byte jump-back offsets (loop points); the
        table is u16-aligned so they convert to word deltas. Returns the sprite
        word to display (high bits carry the hit mask, as in blues).
        """
        words = self.monster_anim_words
        n = len(words)
        p = obj.anim_ptr
        if not words or p < 0 or p >= n:
            return obj.anim_fallback
        for _ in range(64):  # guard against malformed streams
            num = words[p]
            if num >= 0:
                obj.anim_ptr = p + 1  # advance one word (blues p += 2 bytes)
                return num
            p += num // 2  # negative byte offset -> word delta
            if p < 0 or p >= n:
                return obj.anim_fallback
        return obj.anim_fallback

    def _monster_change_next_anim(self, obj: RuntimeObject) -> None:
        words = self.monster_anim_words
        n = len(words)
        p = obj.anim_ptr
        while 0 <= p < n and words[p] >= 0:
            p += 1
        obj.anim_ptr = p + 1  # skip past the negative terminator to next sequence

    def _monster_change_prev_anim(self, obj: RuntimeObject) -> None:
        words = self.monster_anim_words
        p = obj.anim_ptr
        while True:
            p -= 1
            if p < 0:
                p = 0
                break
            if words[p] < 0:
                break
        obj.anim_ptr = p

    def _monster_rotate_pos(self, obj: RuntimeObject, ms: RuntimeMonsterState, radius: int) -> None:
        """blues monster_rotate_pos(): place the type-4 spider on a circle of
        `radius` around its anchor at the current angle (signed cos/sin >> 2)."""
        a = ms.type4_angle & 0xFF
        obj.x = ms.x_pos + ((radius * (_s8(COS_TBL[a]) >> 2)) >> 4)
        obj.y = ms.y_pos + ((radius * (_s8(SIN_TBL[a]) >> 2)) >> 4)

    def _monster_update_anim(self, obj: RuntimeObject) -> None:
        """blues level_monster_update_anim(): switch to the death sequence.

        Scans forward to the 0x7D00 marker (end-of-type / death anim) and points
        the stream just past it.
        """
        words = self.monster_anim_words
        n = len(words)
        p = obj.anim_ptr
        while 0 <= p < n and (words[p] & 0xFFFF) != 0x7D00:
            p += 1
        obj.anim_ptr = (p + 1) if p < n else obj.anim_ptr

    def _monster_is_visible(self, x: int, y: int) -> bool:
        dx = (x >> 4) - (self.camera_x >> 4)
        if dx < -2 or dx > ((DOS_W >> 4) + 2):
            return False
        dy = (y >> 4) - (self.camera_y >> 4)
        if dy < -2 or dy > ((DOS_H >> 4) + 2):
            return False
        return True

    def _init_platform_states(self) -> list[PlatformState]:
        states: list[PlatformState] = []
        for slot, platform in enumerate(self.level.active_platforms):
            ptype = platform.platform_type
            spr = self.sprite_resolver.platform_sprite(self.level, int(platform.sprite_num_raw))
            surf_h = self._object_logic_size(spr)[1] if spr is not None else 0
            states.append(
                PlatformState(
                    surf_h=surf_h,
                    slot=slot,
                    base_x=int(platform.x_pos),
                    base_y=int(platform.y_pos),
                    x=int(platform.x_pos),
                    y=int(platform.y_pos),
                    prev_x=int(platform.x_pos),
                    prev_y=int(platform.y_pos),
                    sprite_num_raw=int(platform.sprite_num_raw),
                    flags=int(platform.flags),
                    platform_type=ptype,
                    variant=platform.variant,
                    # Types 0..7 and type 8 now mirror the blues-master/p2
                    # state fields instead of recomputing movement from base+range.
                    max_velocity=int(platform.extra.get("max_velocity", 0)),
                    velocity=int(platform.extra.get("velocity", 0)),
                    counter=int(platform.extra.get("counter", 0)),
                    unkA=int(platform.extra.get("unkA", 0)),
                    y_delta=int(platform.extra.get("y_delta", 0)) if platform.variant == "type8" else 0,
                    y_velocity16=int(platform.extra.get("y_velocity", 0)) if platform.variant == "type8" else 0,
                    drop_counter=int(platform.extra.get("counter", 0)) if platform.variant == "type8" else 0,
                    reset_delay=int(platform.extra.get("unk9", 0)) if platform.variant == "type8" else 0,
                    falling_state=int(platform.extra.get("state", 0)) if platform.variant == "type8" else 0,
                )
            )
        return states

    def _platform_source(self, state: PlatformState):
        # Slot numbers are stable because states are created from active_platforms
        # in order and only active records are simulated.
        return self.level.active_platforms[state.slot]

    def tile_num_at_tile(self, tx: int, ty: int) -> int | None:
        if tx < 0 or ty < 0 or tx >= self.level.width_tiles or ty >= self.level.height_tiles:
            return None
        return self.runtime_tilemap[ty * self.level.width_tiles + tx]

    def _tile_num_at_linear_offset(self, offset: int) -> int | None:
        tx = offset & 0xFF
        ty = offset >> 8
        return self.tile_num_at_tile(tx, ty)

    def _set_tile_num_at_linear_offset(self, offset: int, tile_num: int) -> None:
        tx = offset & 0xFF
        ty = offset >> 8
        if 0 <= tx < self.level.width_tiles and 0 <= ty < self.level.height_tiles:
            idx = ty * self.level.width_tiles + tx
            new_tile = tile_num & 0xFF
            if self.runtime_tilemap[idx] != new_tile:
                self.runtime_tilemap[idx] = new_tile
                self._tilemap_version = getattr(self, "_tilemap_version", 0) + 1
                if hasattr(self, "_tile_dirty_positions"):
                    self._tile_dirty_positions.append((tx, ty))

    def tile_num_at_pixel(self, x: int, y: int) -> int | None:
        return self.tile_num_at_tile(x // TILE, y // TILE)

    def side_solid_at_pixel(self, x: int, y: int) -> bool:
        tile = self.tile_num_at_pixel(x, y)
        if tile is None:
            return True
        return self.level.tile_attributes0[tile] in (1, 2)

    def bottom_solid_at_pixel(self, x: int, y: int) -> bool:
        tile = self.tile_num_at_pixel(x, y)
        if tile is None:
            return True
        return (self.level.tile_attributes2[tile] & 0x0F) == 1

    def deadly_at_pixel(self, x: int, y: int) -> bool:
        tile = self.tile_num_at_pixel(x, y)
        if tile is None:
            return False
        underside = self.level.tile_attributes2[tile] & 0x0F
        return self.level.tile_attributes0[tile] == 2 or self.level.tile_attributes1[tile] == 6 or underside == 2

    def floor_y_for_tile(self, tx: int, ty: int, local_x: int) -> int | None:
        tile = self.tile_num_at_tile(tx, ty)
        if tile is None:
            return ty * TILE
        top_attr = self.level.tile_attributes1[tile]
        if top_attr not in (1, 2, 3, 4, 5, 6):
            return None
        attr3 = self.level.tile_attributes3[tile]
        base = attr3 & 0x0F
        slope = attr3 & 0x30
        if slope == 0x10:
            # Original-style 16 px tile slope quantized to 6 vertical steps.
            return ty * TILE + min(15, base + (local_x // 3))
        if slope == 0x20:
            # Opposite direction, also quantized; this matches the decoded attr3 note.
            return ty * TILE + max(0, base + ((15 - local_x) // 3))
        return ty * TILE + base

    def floor_y_at_pixel(self, x: int, y: int) -> int | None:
        return self.floor_y_for_tile(x // TILE, y // TILE, x & 15)

    def _sync_player_collision_size(self) -> None:
        """Use the engine's spr_size_tbl height, not decoded bitmap height.

        p2/level.c uses spr_size_tbl[spr_num*2+1] for the upward side/ceiling
        probe loop.  The sprite bitmap and draw origin can be larger/different,
        so using the rendered sprite dimensions made the player look like they
        were walking inside floors and changed collision when animation frames
        changed.
        """
        _w, h = sprite_logic_size(self.player.spr_num)
        self.player.collision_h = max(8, int(h))

    def _tile_ground_info_at_pixel(self, x: int, y: int) -> tuple[int, int] | None:
        tx = x // TILE
        ty = y // TILE
        tile = self.tile_num_at_tile(tx, ty)
        if tile is None:
            return (ty * TILE, 0)
        top_attr = self.level.tile_attributes1[tile]
        if top_attr not in (1, 2, 3, 4, 5, 6):
            return None
        if top_attr == 6:
            return None
        fy = self.floor_y_for_tile(tx, ty, x & 15)
        if fy is None:
            return None
        friction = {2: 1, 3: 2, 4: 3}.get(top_attr, 0)
        return (fy, friction)

    def _player_tile_offset(self, x: int, y: int) -> tuple[int, int, int]:
        """Return (tx, ty, linear_offset) in the original 256-wide tilemap space."""
        tx = x >> 4
        ty = y >> 4
        return tx, ty, (ty << 8) | tx

    def _tile_attr3_player_offset(self, attr3: int) -> int:
        """Mirror level_get_tile_player_offset(): slope uses player origin x, not sample x."""
        if (attr3 & 0x30) == 0:
            return attr3 & 0xFF
        x = (self.player.x & 15) // 3
        if attr3 & 0x10:
            return (attr3 & 15) + x
        return (attr3 & 15) - x

    def _player_reset_after_ground_contact(self) -> None:
        """Mirror level_player_reset() from p2/level.c.

        Important: nojump_counter is decremented from the ground/contact path,
        not blindly once per tick.  Doing it globally made jump timing too loose
        compared with the DOS state machine.
        """
        if self.player.nojump_counter > 0:
            self.player.nojump_counter -= 1
        self.player.anim2_counter = 0
        self.player.tile_flags = 2
        self.player.prev_y = self.player.y
        self.player.on_ground = True

    def _mark_player_dead(self) -> None:
        # Equivalent to level_update_tile_type_2()/level_player_die() for the
        # runtime scaffold.  Full lives/energy accounting will be added later;
        # for now we defer the reset until the end of the tick so the update
        # order remains close to the original routine.
        self.player.death_flag = 1
        self.player.restart_level_flag = 2

    def _update_player_jump_fall_from_decor(self) -> None:
        """Port of level_update_player_jump()."""
        # While riding a platform the player is not airborne; skip the fall update
        # so it doesn't re-arm nojump_counter and block jumping off (blues gates
        # this with force_x_scroll_flag).
        if self._on_platform:
            return
        self._apply_player_hdir_accel(PLAYER_RUN_MAX16)
        self._apply_player_gravity(PLAYER_MAX_FALL16)
        if self.player.vy <= 0:
            return
        self.player.nojump_counter = 6
        # If a club animation is in its 0x40-tagged active section, the C rewrite
        # skips replacing spr_num with jump/fall frames.
        if self.player.anim_0x40_flag != 0:
            return
        self.player.spr_num = PLAYER_JUMP_FALL_SPR if self.player.jumping_counter >= 12 else PLAYER_JUMP_SPR
        self._sync_player_collision_size()

    def _apply_attr1_ground(self, tx: int, ty: int, attr1: int) -> None:
        """ASM-shaped level_update_tile_attr1_helper/type_1..5.

        Player x/y is one bottom anchor point.  Landing snaps `y` to the tile
        row, applies tile_attributes3 using `(player.x & 15) / 3`, then handles
        hard-fall bounce before zeroing y_velocity.  This intentionally avoids a
        broad rectangle ground sweep.
        """
        self.player.x_friction = 0
        if self.player.vy < 0:
            self.player.tile_flags |= 1
            self.player.on_ground = False
            return

        self.player.y &= ~15
        tile = self.tile_num_at_tile(tx, ty)
        attr3 = self.level.tile_attributes3[tile] if tile is not None else 0
        if attr3 != 0:
            dy = self._tile_attr3_player_offset(attr3)
            by = self.player.vy >> 4
            if by > 0 and dy >= by:
                dy = by
            self.player.y += dy
        else:
            tile_above = self.tile_num_at_tile(tx, ty - 1)
            attr3_above = self.level.tile_attributes3[tile_above] if tile_above is not None else 0
            if attr3_above != 0:
                ax = self._tile_attr3_player_offset(attr3_above)
                if ax < 16:
                    self.player.y += ax - 16

        if self.player.jumping_counter > 4:
            fall_dy = self.player.y - self.player.prev_y
            if fall_dy >= 32 and self.player.vy >= 80:
                self.player.prev_y = self.player.y
                if self.player.jumping_counter >= 20 and self.player.vy > 160:
                    self.player.shake_screen_counter = 8
                if self.player.jumping_counter > 10:
                    if (self.level.header.scrolling_mask & 1) == 0:
                        self.player.vy = -32
                    self.player.spr_num = PLAYER_JUMP_SPR
                    self.player.jumping_counter = 0
                    self.player.on_ground = False
                    return

        self.player.vy = 0
        self._player_reset_after_ground_contact()
        if attr1 in (2, 3, 4):
            self.player.x_friction = attr1 - 1
    def _asm_update_tile0(self, y_pos: int | None = None, x_pos: int | None = None) -> None:
        """Partial port of level_update_tile0().

        `y_pos` is `(player.y >> 4) - 1`, so `offset + 0x100` is the tile under
        the player's bottom-origin point.  `player.tile_flags` is managed by the
        caller exactly like level_update_player_decor().
        """
        if self.player.y <= -1:
            self._update_player_jump_fall_from_decor()
            self.player.tile_flags = 0xFF
            self.player.on_ground = False
            return
        if y_pos is None:
            y_pos = (self.player.y >> 4) - 1
        if x_pos is None:
            x_pos = self.player.x >> 4
        below_ty = y_pos + 1
        tile = self.tile_num_at_tile(x_pos, below_ty)
        attr1 = self.level.tile_attributes1[tile] if tile is not None else 1

        # level_update_tile0() also animates/decrements one special tile under
        # the player's origin when attr2 bit 0x20 is set.  This is visual state,
        # but it is part of the original tile0 routine and therefore belongs in
        # the runtime tilemap rather than the renderer.  Note that attr1 above
        # is intentionally captured before this mutation, like the C rewrite.
        offset_nextline = (below_ty << 8) | x_pos
        if self.decor_tile0_offset != offset_nextline:
            if self.decor_tile0_offset is not None:
                prev = self._tile_num_at_linear_offset(self.decor_tile0_offset)
                while prev is not None:
                    prev = (prev - 1) & 0xFF
                    if (self.level.tile_attributes2[prev] & 0x20) == 0:
                        self.decor_tile0_offset = None
                        break
                    self._set_tile_num_at_linear_offset(self.decor_tile0_offset, prev)
            tile_for_decor = self.tile_num_at_tile(x_pos, below_ty)
            if tile_for_decor is not None and (self.level.tile_attributes2[tile_for_decor] & 0x20):
                self.decor_tile0_offset = offset_nextline
                self._set_tile_num_at_linear_offset(offset_nextline, tile_for_decor + 1)

        if attr1 == 0:
            if self.player.vy == 0:
                tile2 = self.tile_num_at_tile(x_pos, below_ty + 1)
                attr3 = self.level.tile_attributes3[tile2] if tile2 is not None else 0
                if attr3 != 0 and self._tile_attr3_player_offset(attr3) < 16:
                    self.player.y += 16
                    self._apply_attr1_ground(x_pos, below_ty + 1, 1)
                    return
            self.player.tile_flags |= 1
            self.player.on_ground = False
        elif attr1 in (1, 2, 3, 4):
            self._apply_attr1_ground(x_pos, below_ty, attr1)
        elif attr1 == 5:
            self.player.x_friction = 0
            if self.player.action_counter == 0:
                self._apply_attr1_ground(x_pos, below_ty, attr1)
            else:
                self.player.tile_flags |= 1
                self.player.on_ground = False
        elif attr1 == 6:
            # spikes/deadly top
            self._mark_player_dead()
        else:
            self.player.tile_flags |= 1
            self.player.on_ground = False

        # Exact shape of the upward attr2/side-nudge tail from level_update_tile0():
        # after floor handling, if moving upward and there is a tile row above,
        # inspect attr2 of the tile one row above (offset-0x100).  Then, only if
        # attr0 of the current upper tile is solid-ish, nudge x by +/-2 pixels
        # toward the free side.  This tiny nudge is one of the places where a
        # broad rectangle collider produced visible jitter in earlier runtimes.
        if y_pos >= 1 and self.player.vy < 0:
            tile_above = self.tile_num_at_tile(x_pos, y_pos - 1)
            tile_current = self.tile_num_at_tile(x_pos, y_pos)
            current_attr0 = self.level.tile_attributes0[tile_current] if tile_current is not None else 1
            underside = self.level.tile_attributes2[tile_above] & 15 if tile_above is not None else 1
            if underside == 1:
                self.player.vy = 0
                self.player.y &= ~15
                self.player.y += 16
            elif underside == 2:
                self._mark_player_dead()

            if (current_attr0 & 1) != 0 and self.player.y > 0:
                nudge = -1 if self.player.vx > 0 else 1
                side_tile = self.tile_num_at_tile(x_pos + nudge, y_pos)
                side_attr0 = self.level.tile_attributes0[side_tile] if side_tile is not None else 1
                if side_attr0 != 0:
                    nudge = -nudge
                    side_tile = self.tile_num_at_tile(x_pos + nudge, y_pos)
                    side_attr0 = self.level.tile_attributes0[side_tile] if side_tile is not None else 1
                    if side_attr0 != 0:
                        return
                self.player.x += 2 * nudge
    def _asm_update_side_tiles(self, y_pos: int | None = None, dx: int | None = None, spr_h: int | None = None) -> None:
        """Port of level_update_tile1()/level_update_tile2() probe column.

        The DOS engine checks one tile column at x+9/x-9/x, starting one tile
        above the bottom anchor and walking upward by sprite height.  Solid side
        collision reverts the single fixed-point x step and zeroes velocity.
        """
        if y_pos is None:
            y_pos = (self.player.y >> 4) - 1
        if dx is None:
            dx = 9 if self.player.vx > 0 else (-9 if self.player.vx < 0 else 0)
        if spr_h is None:
            _w, spr_h = sprite_logic_size(self.player.spr_num)

        pos_ty = y_pos
        pos_tx = (dx + self.player.x) >> 4

        # level_update_tile1(pos)
        tile = self.tile_num_at_tile(pos_tx, pos_ty)
        typ = self.level.tile_attributes0[tile] if tile is not None else 1
        if typ == 1:
            self.player.x -= self.player.vx >> 4
            self.player.vx = 0
        elif typ == 2:
            self._mark_player_dead()

        # while loop with level_update_tile2(pos-0x100...)
        while True:
            pos_ty -= 1
            if pos_ty < 0:
                break
            spr_h -= 16
            if spr_h <= 0:
                break
            tile = self.tile_num_at_tile(pos_tx, pos_ty)
            typ = self.level.tile_attributes0[tile] if tile is not None else 1
            if typ == 2:
                self._mark_player_dead()

    def _asm_update_player_decor(self) -> None:
        """ASM-shaped level_update_player_decor().

        This is now the only path used after applying x/y velocity.  It avoids
        broad hitbox sweeps and keeps the original order: bottom tile, possible
        jump/fall update, then side/upper column probes.
        """
        y_pos = (self.player.y >> 4) - 1
        _w, spr_h = sprite_logic_size(self.player.spr_num)
        dx = 9 if self.player.vx > 0 else (-9 if self.player.vx < 0 else 0)
        x_pos = self.player.x >> 4

        # Out-of-visible-level death checks in the rewrite are based on the
        # current scroll window.  Until scroll is ported exactly, use absolute
        # level bounds only and keep the gameplay probes tick-shaped.
        if self.player.y >= 0 and self.player.y > ((self.level.height_tiles + 1) << 4):
            self._mark_player_dead()

        self.player.tile_flags = 0
        self._asm_update_tile0(y_pos, x_pos)
        if self.player.tile_flags == 1:
            if self.player.level_force_x_scroll_flag != 0:
                self._player_reset_after_ground_contact()
                self.player.jumping_counter = 0
            else:
                self._update_player_jump_fall_from_decor()
                if self.player.vy > 0:
                    self.player.jumping_counter = min(255, self.player.jumping_counter + 1)
            self.player.on_ground = False
        else:
            self.player.jumping_counter = 0

        if self.player.y <= 0:
            return
        self._asm_update_side_tiles(y_pos, dx, spr_h)
    def _refresh_ground_contact(self) -> None:
        self._asm_update_player_decor()

    def _apply_platform_landing(self) -> None:
        """Make platforms solid (blues level_update_objects_decors_helper): a
        falling player whose feet cross a platform's top surface lands on it and
        is carried. One-way (only from above). Uses a crossing test (prev->now)
        so a fast fall can't tunnel through a thin platform."""
        self._on_platform = False
        if self.player.vy < 0:
            return
        pb = self.player.y  # bottom-origin
        prev_pb = pb - (self.player.vy >> 4)  # feet position before this step
        for state in self.platforms:
            top = state.y - state.surf_h  # standing surface = anchor - sprite height
            if not (state.x - 4 <= self.player.right and self.player.left <= state.x + 52):
                continue
            # Landed when the feet were at/above the surface and are now at/below
            # it (crossing), or resting in a small band on top.
            if prev_pb <= top + 4 and pb >= top - 1:
                self.player.y = top
                self.player.vy = 0
                self.player.x_friction = 0
                state.flags |= 0x40
                self._on_platform = True
                # blues level_update_objects_decors_helper calls level_player_reset()
                # here: this decrements nojump_counter so the player can jump off
                # the platform (otherwise it stays at 6 and jumps are blocked).
                self._player_reset_after_ground_contact()
                return

    def _player_is_standing_on_platform(self, platform: PlatformState) -> bool:
        return (
            self.player.on_ground
            and abs(self.player.bottom - (platform.y - platform.surf_h)) <= 2
            and platform.x - 4 <= self.player.right
            and self.player.left <= platform.x + 52
        )

    def _update_platforms(self) -> None:
        """Update moving/falling platforms using the p2/level.c state model."""
        for state in self.platforms:
            state.prev_x = state.x
            state.prev_y = state.y
            # Riding flag (blues platform->flags 0x40): an oscillating platform
            # only starts moving once the player stands on it. Without this the
            # move condition (velocity!=0 || max_velocity<0 || flags&0xC0) is
            # never satisfied for a freshly-loaded platform, so it never moves.
            if self._player_is_standing_on_platform(state):
                state.flags |= 0x40
            else:
                state.flags &= ~0x40
            if state.platform_type == 8:
                # p2/level.c temporarily subtracts y_delta, updates it, then adds
                # it back. Keeping base_y + y_delta is equivalent for the runtime.
                if state.falling_state == 0:
                    state.y_delta = max(0, state.y_delta - 8)
                    if state.y_delta == 0:
                        state.y_velocity16 = 0
                    if self._player_is_standing_on_platform(state):
                        state.drop_counter -= 1
                        if state.y_velocity16 != 0 or state.drop_counter <= 0:
                            state.falling_state = 1
                            state.y_velocity16 = 0
                elif state.falling_state == 1:
                    y = state.y_velocity16
                    if y < PLAYER_MAX_FALL16:
                        state.y_velocity16 += 8
                    dy = y >> 4
                    state.y_delta += dy
                    tile_x = state.x >> 4
                    tile_y = (state.base_y + state.y_delta) >> 4
                    if tile_y >= self.level.height_tiles + 3:
                        state.falling_state = 2
                        state.drop_counter = 22
                    else:
                        tile = self.tile_num_at_tile(tile_x, tile_y)
                        if tile is not None:
                            attr1 = self.level.tile_attributes1[tile]
                            if attr1 not in (0, 6):
                                state.falling_state = 2
                                state.drop_counter = 22
                elif state.falling_state == 2:
                    if not self._player_is_standing_on_platform(state):
                        state.drop_counter -= 1
                        if state.drop_counter <= 0:
                            state.falling_state = 0
                            state.drop_counter = state.reset_delay
                state.y = state.base_y + state.y_delta
                continue

            if state.velocity != 0 or state.max_velocity < 0 or (state.flags & 0xC0) != 0:
                if state.max_velocity > state.velocity:
                    state.velocity += 1
                elif state.max_velocity < state.velocity:
                    state.velocity -= 1
                vx_mul, vy_mul = PLATFORM_TYPE_VECTOR.get(state.platform_type & 7, (0, 0))
                dx = vx_mul * state.velocity
                dy = vy_mul * state.velocity
                state.x += dx
                state.y += dy
                if state.max_velocity == state.velocity:
                    next_counter = state.counter + 1
                    if state.unkA == next_counter:
                        state.max_velocity = -state.max_velocity
                        next_counter = 0
                    state.counter = next_counter

        # Carry the player with any platform that was under their feet before it moved.
        for platform in self.platforms:
            if (
                self.player.on_ground
                and abs(self.player.bottom - (platform.prev_y - platform.surf_h)) <= 2
                and platform.prev_x - 4 <= self.player.right
                and self.player.left <= platform.prev_x + 52
            ):
                self.player.x += platform.dx
                self.player.y += platform.dy

    def _move_vertical(self, dy: int) -> None:
        if dy == 0:
            self._refresh_ground_contact()
            return
        self.player.on_ground = False
        if dy < 0:
            for _ in range(-dy):
                new_top = self.player.top - 1
                if self._ceiling_blocked(new_top):
                    self.player.vy = 0
                    return
                self.player.y -= 1
            return
        if dy > 0:
            for _ in range(dy):
                new_bottom = self.player.bottom + 1
                ground = self._ground_probe(new_bottom)
                if ground is not None:
                    floor_y, friction = ground
                    self.player.y = floor_y
                    self.player.vy = 0
                    self.player.x_friction = friction
                    self.player.on_ground = True
                    return
                self.player.y += 1


    def _anim_word_at(self, seq_num: int, index: int) -> tuple[int, int]:
        """Return (animation word, next index) using original relative jumps."""
        seq = OBJECT_ANIMS[seq_num] if 0 <= seq_num < len(OBJECT_ANIMS) else None
        if not seq:
            return 9, 0
        index %= len(seq)
        # Original animation words are 16-bit. Negative words are relative byte
        # jumps; because we store words, divide the C byte offset by 2.
        for _ in range(8):
            word = seq[index]
            if word >= 0:
                return word, (index + 1) % len(seq)
            index = (index + (word // 2)) % len(seq)
        return seq[0] if seq[0] >= 0 else 9, 0

    def _set_anim_seq(self, seq_num: int, *, current: bool = True, special: bool = False) -> None:
        if not (0 <= seq_num < len(OBJECT_ANIMS)) or OBJECT_ANIMS[seq_num] is None:
            seq_num = 0
        if current and self.player.current_anim_num != seq_num:
            self.player.current_anim_num = seq_num
            self.player.anim_seq_num = seq_num
            self.player.anim_index = 0
        if special and self.player.special_anim_num != seq_num:
            self.player.special_anim_num = seq_num
            self.player.anim_seq_num = seq_num
            self.player.anim_index = 0
        if not current and not special:
            self.player.anim_seq_num = seq_num
            self.player.anim_index = 0

    def _step_player_anim(self) -> None:
        word, next_index = self._anim_word_at(self.player.anim_seq_num, self.player.anim_index)
        self.player.anim_index = next_index
        self.player.current_anim_type = (word >> 8) & 0xFF
        self.player.spr_num = word & 0x1FFF
        self._sync_player_collision_size()

    def _apply_player_friction(self) -> None:
        friction = PLAYER_GROUND_FRICTION16 >> min(3, max(0, self.player.x_friction))
        if self.player.vx > 0:
            self.player.vx = max(0, self.player.vx - friction)
        elif self.player.vx < 0:
            self.player.vx = min(0, self.player.vx + friction)

    def _apply_player_hdir_accel(self, max_speed16: int) -> None:
        accel = 0
        # In p2/level.c this is hdir<<4 shifted by current tile friction.
        if self._key_hdir:
            accel = (self.player.facing << 4) >> min(3, max(0, self.player.x_friction))
        candidate = self.player.vx + accel
        if candidate > max_speed16:
            candidate = max_speed16
        elif candidate < -max_speed16:
            candidate = -max_speed16
        self.player.vx = candidate

    def _apply_player_gravity(self, max_fall16: int = PLAYER_MAX_FALL16) -> None:
        self.player.vy = min(max_fall16, self.player.vy + PLAYER_GRAVITY16)

    def _update_player_state_anim0_idle_or_slide(self, inp: InputState) -> None:
        self._apply_player_friction()
        if not self.player.on_ground and self.player.vy != 0:
            return
        speed = abs(self.player.vx)
        if speed == 0:
            self.player.current_anim_num = 0
            if self.player.moving_counter >= 30 and not (inp.left and inp.right):
                self.player.moving_counter = max(0, self.player.moving_counter - 3)
                self._set_anim_seq(16, current=False, special=True)  # exhausted/stop animation
                self._step_player_anim()
                return
            if inp.left and inp.right:
                self._set_anim_seq(19, current=False, special=True)  # both directions pressed
                self._step_player_anim()
                return
            # Original uses timer windows to occasionally play idle animation 17.
            if (self.tick_count & 0x1FF) in range(96, 128):
                self._set_anim_seq(17, current=False, special=True)
                self._step_player_anim()
                return
        self._set_anim_seq(0, current=False, special=True)
        self._step_player_anim()
        self.player.current_anim_num = 0

    def _update_player_state_run(self) -> None:
        if self.player.moving_counter < 255:
            self.player.moving_counter += 1
        self._apply_player_hdir_accel(PLAYER_RUN_MAX16)
        self._set_anim_seq(1, current=True)
        self._step_player_anim()

    def _update_player_state_jump(self) -> None:
        if self.player.nojump_counter > 0:
            self._update_player_state_anim0_idle_or_slide(InputState())
            return
        if self.player.anim2_counter < len(PLAYER_JUMP_Y_DELTA16):
            self.player.vy += PLAYER_JUMP_Y_DELTA16[self.player.anim2_counter]
            self.player.anim2_counter += 1
        else:
            self._apply_player_gravity()
        # p2/level.c uses a signed compare here: if x_velocity >= 48
        # then friction, otherwise clamp through hdir acceleration.  That means
        # high leftward velocity is clamped by hdir_x_velocity(), not by the
        # friction branch.
        if self.player.vx >= PLAYER_JUMP_RUN_MAX16:
            self._apply_player_friction()
        else:
            self._apply_player_hdir_accel(PLAYER_JUMP_RUN_MAX16)
        self._set_anim_seq(2, current=True)
        self._step_player_anim()

    def _init_object_hit_from_xy(self, x: int, y: int) -> None:
        obj = self.runtime_objects[self.current_hit_object_slot]
        obj.x = x
        obj.y = y
        obj.spr_num = 53 | 0x2000
        obj.ttl = 0
        self.current_hit_object_slot -= 1
        if self.current_hit_object_slot < 6:
            self.current_hit_object_slot = 10

    def _init_object_hit_from_player_pos(self) -> None:
        self._init_object_hit_from_xy(self.player.x, self.player.y)

    def _player_update_club_power(self) -> None:
        if self.player.club_powerup_duration <= 48:
            self.player.club_powerup_duration += 2

    def _find_free_club_projectile(self) -> RuntimeObject | None:
        for slot in range(2, 6):
            obj = self.runtime_objects[slot]
            if not obj.active:
                return obj
        return None

    def _spawn_club_projectile_if_needed(self) -> bool:
        start = club_projectile_start(self.player.club_type)
        if start is None:
            return False
        obj = self._find_free_club_projectile()
        if obj is None:
            return False
        x_vel, y_vel, anim_words = start
        if self.player.facing < 0:
            x_vel = -x_vel
        obj.x_friction = (club_anim_spec(self.player.club_type)[3] >> 1) & 3
        obj.anim_words = anim_words
        obj.anim_index = 0
        obj.y_velocity = y_vel
        obj.x_velocity = x_vel
        obj.spr_num = (anim_words[0] & 0x1FFF) | 0x2000 | (0x8000 if x_vel < 0 else 0)
        obj.x = self.player.x + (x_vel >> 4)
        obj.y = self.player.y + (y_vel >> 4)
        self.runtime_objects[0].spr_num = 0xFFFF
        return True

    def _update_player_state_attack_367(self, anim_num: int) -> None:
        # level_update_player_anim_3_6_7(): the real club swing path.  Besides
        # animation it can push the player upward, create hit objects, create a
        # visible club overlay in objects_tbl[0], and spawn club projectiles.
        self._set_anim_seq(anim_num, current=True)
        self._step_player_anim()
        self._apply_player_friction()
        if self.player.moving_counter < 255:
            self.player.moving_counter += 1

        # club_anim_spec is (offset, a, power, c) — matching blues club_anim_t:
        #   a     = club_anim_duration (recovery/cooldown between swings)
        #   power = club damage
        # These were previously unpacked swapped, which made the swing cooldown
        # huge (~25 ticks → no autoswing) and the damage tiny (2 → wrong hits).
        _off, duration, power, _flags = club_anim_spec(self.player.club_type)
        self.player.club_power = power << 2 if self.player.club_powerup_duration != 0 else power
        self.player.anim_0x40_flag = ((self.player.current_anim_type & 0x40) ^ 0x40)

        if self.player.anim_0x40_flag == 0:
            self.player.club_anim_duration = duration
            # Club swing sound by club type (blues anim_3_6_7: 5 / 0 / 10).
            self.play_sound(5 if self.player.club_type == 0 else (0 if self.player.club_type == 1 else 10))
            dy = 0
            if anim_num != 6:
                dy = -32
                if anim_num != 3:
                    dy = -48
                    if self.tick_count & 3:
                        self._init_object_hit_from_player_pos()
            if self.player.level_force_x_scroll_flag == 0:
                self.player.vy += dy
            if self._spawn_club_projectile_if_needed():
                return
            if self.player.jumping_counter != 0:
                self.runtime_objects[0].spr_num = 0xFFFF
                return

        overlay = club_overlay_for_player_sprite(self.player.club_type, self.player.spr_num)
        if overlay is not None:
            club_spr, x_off, y_off = overlay
            if self.player.facing < 0:
                x_off = -x_off
            club = self.runtime_objects[0]
            club.spr_num = (club_spr & 0x1FFF) | (0x8000 if self.player.facing < 0 else 0)
            club.x = self.player.x + (self.player.vx >> 4) - x_off
            club.y = self.player.y + (self.player.vy >> 4) - y_off
        else:
            self.runtime_objects[0].spr_num = 0xFFFF

    def _update_player_state_attack_4(self, anim_num: int) -> None:
        if self.player.anim_0x40_flag != 0:
            self._update_player_state_attack_367(self.player.current_anim_num)
            return
        self.player.moving_counter = 0
        self.player.action_counter = 4
        self._player_update_club_power()
        if abs(self.player.vx) > 32:
            self._update_player_state_anim0_idle_or_slide(InputState())
        else:
            self._apply_player_hdir_accel(32)
            self._set_anim_seq(anim_num, current=True)
            self._step_player_anim()

    def _update_player_state_attack_5(self, anim_num: int) -> None:
        if self.player.anim_0x40_flag != 0:
            self._update_player_state_attack_367(self.player.current_anim_num)
            return
        self.player.moving_counter = 0
        self.player.action_counter = 4
        self._player_update_club_power()
        self._set_anim_seq(anim_num, current=True)
        self._step_player_anim()
        self._apply_player_friction()
        self._player_update_club_power()

    def _update_player_state_hit(self) -> None:
        self._apply_player_friction()
        self._set_anim_seq(PLAYER_HIT_ANIM_NUM, current=True)
        self._step_player_anim()

    def _object_logic_size(self, spr_num: int) -> tuple[int, int]:
        return sprite_logic_size(spr_num & 0x1FFF)

    def _object_origin_x(self, spr_num: int) -> int:
        base = spr_num & 0x1FFF
        if 0 <= base < self.sprite_tables.count:
            return self.sprite_tables.offsets[base * 2]
        return self._object_logic_size(base)[0] // 2

    def _objects_collide(self, a: RuntimeObject, b: RuntimeObject, *, player_using_club: bool = False) -> bool:
        """Port of level_objects_collide() for runtime objects.

        The original does not use a rectangular hitbox centered on objects.  It
        first rejects by anchor distance, then performs a vertical overlap using
        spr_size_tbl height, then a horizontal overlap using spr_offs_tbl X
        origins.  This helper is intentionally slot/object based so player,
        club, bonus and item collisions all use the same primitive.
        """
        if not a.active or not b.active:
            return False
        if abs(a.x - b.x) >= 64:
            return False
        if abs(a.y - b.y) >= 70:
            return False

        si, di = a, b
        num = si.spr_num
        ay = si.y
        dy = di.y
        if ay < dy:
            si, di = di, si
            ay, dy = dy, ay
            num = si.spr_num
        _w, h = self._object_logic_size(num)
        top = ay - h
        if top >= dy:
            return False

        # blues sets player_jump_monster_flag / collide_y_dist here (when not a
        # club hit): depth of vertical overlap, flagged as a stomp if the player
        # is falling fast or only shallowly overlapping the top of the lower
        # object (si, the larger-y one) and the player is the upper object.
        if not player_using_club:
            depth = dy - top
            if self.player.vy >= 128 or (depth <= (h >> 1) and si.slot != 1):
                self._jump_monster_flag += 1
                self._collide_y_dist = depth

        si_left = si.x - self._object_origin_x(si.spr_num)
        di_left = di.x - self._object_origin_x(di.spr_num)
        # blues uses the LEFT-most object's width as the overlap span: it swaps so
        # `a` is the smaller left edge and sets `b` to that object's width.
        if di_left >= si_left:
            left, right, width = si_left, di_left, self._object_logic_size(si.spr_num)[0]
        else:
            left, right, width = di_left, si_left, self._object_logic_size(di.spr_num)[0]
        if not player_using_club:
            width >>= 1
        return left + width > right

    def _player_as_object(self) -> RuntimeObject:
        return RuntimeObject(slot=1, spr_num=self._player_sprite_num() | 0x2000, x=self.player.x, y=self.player.y)

    def _clear_item_ref(self, obj: RuntimeObject) -> None:
        if obj.ref_index is not None and 0 <= obj.ref_index < len(self.level.items):
            self.level.items[obj.ref_index].sprite_num_raw = 0xFFFF
        obj.ref_index = None

    def _add_score_object(self, ref_obj: RuntimeObject, score_sprite_num: int) -> RuntimeObject | None:
        """Partial level_add_object75_score(): allocate score popup 75..90."""
        self.score += 10
        for slot in range(75, 91):
            obj = self.runtime_objects[slot]
            if not obj.active:
                obj.spr_num = score_sprite_num & 0xFFFF
                obj.x = ref_obj.x
                obj.y = ref_obj.y
                obj.counter = 44
                obj.x_velocity = obj.y_velocity = 0
                obj.data_y_velocity = 0
                obj.ref_index = None
                if ref_obj.slot >= 23:
                    self._clear_item_ref(ref_obj)
                return obj
        return None

    def _consume_item(self, obj: RuntimeObject) -> None:
        obj.spr_num = 0xFFFF
        if obj.slot >= 23:
            self._clear_item_ref(obj)

    # ------------------------------------------------------------------
    # Pre-level "you are here" adventure-map screen.
    #
    # Not present in blues; it IS in the DOS game (assets MAP.SQZ + CARTE.TRK,
    # neither referenced by blues). It shows before every level (including the
    # first): the 640x200 world map pans in from the right to the current level's
    # spot, a marker blinks, and any key skips straight to play. One pan step per
    # engine tick, so it runs at the DOS logic rate.
    # ------------------------------------------------------------------
    def _build_map_image(self) -> None:
        """Decode MAP.SQZ (640x200 planar 4bpp) once into an RGB image. The map
        has no embedded palette; the DOS map screen sets one before showing it —
        the forest level-0 palette reproduces the natural greens/browns (exact
        map palette pending EXE RE)."""
        if getattr(self, "_map_image", None) is not None:
            return
        try:
            blob = unpack_file(find_data_file(self.data_dir, "MAP.SQZ"))
            pixels = decode_planar_bitmap(blob, MAP_W, DOS_H)
            rgb = vga6_to_rgb(self.palettes[0])
            img = Image.new("RGB", (MAP_W, DOS_H))
            img.putdata([rgb[p & 15] for p in pixels])
            self._map_image = img
        except Exception:
            self._map_image = None

    def _begin_map_intro(self) -> None:
        """Start the pre-level map screen for the current level."""
        self._build_map_image()
        if self._map_image is None:
            return  # asset missing -> just skip straight to play
        lvl = self.level_index
        marker_x = MAP_MARKER_X[lvl] if 0 <= lvl < len(MAP_MARKER_X) else MAP_W // 2
        # Screen x of a map column wx is `wx + offset`. Pan the map in from the
        # right (offset = +DOS_W -> black screen) to where the marker is centred,
        # clamped so the map always fills the screen (no black margin).
        target = max(-(MAP_W - DOS_W), min(0, DOS_W // 2 - marker_x))
        self._map_intro = {
            "offset": float(DOS_W),
            "target": float(target),
            "marker_x": marker_x,
            "hold": MAP_HOLD,
            "settled": False,
            "armed": False,  # require a key release before a press can skip
        }
        try:
            self.play_music(2)  # CARTE.TRK
        except Exception:
            pass

    def _map_intro_finish(self) -> None:
        self._map_intro = None
        # Restore the level's own music for gameplay (blues do_level music_tbl).
        music_tbl = (9, 9, 0, 0, 0, 13, 4, 4, 10, 13, 16, 16, 16, 9, 14, 4)
        if 0 <= self.level_index < len(music_tbl):
            try:
                self.play_music(music_tbl[self.level_index])
            except Exception:
                pass

    def _map_intro_tick(self, inp: InputState) -> None:
        m = self._map_intro
        key = bool(inp.jump or inp.action or inp.fire or inp.up
                   or getattr(inp, "quit", False))
        # Require the key to be released once before a press skips, so a key held
        # from the previous screen/level doesn't skip instantly.
        if not key:
            m["armed"] = True
        elif m["armed"]:
            self._map_intro_finish()
            return
        if m["offset"] > m["target"]:
            m["offset"] = max(m["target"], m["offset"] - MAP_SCROLL)
        else:
            m["settled"] = True
            m["hold"] -= 1
            if m["hold"] <= 0:
                self._map_intro_finish()

    def _render_map_intro(self) -> Image.Image:
        frame = Image.new("RGB", (DOS_W, DOS_H), (0, 0, 0))
        m = self._map_intro
        off = int(round(m["offset"]))
        if self._map_image is not None:
            frame.paste(self._map_image, (off, 0))
        # Blinking "you are here" marker (drawn once the map is in place).
        mx = m["marker_x"] + off
        if -8 < mx < DOS_W + 8 and (self.tick_count & 2):
            d = ImageDraw.Draw(frame)
            r = 5
            d.polygon([(mx, MAP_MARKER_Y - r), (mx + r, MAP_MARKER_Y),
                       (mx, MAP_MARKER_Y + r), (mx - r, MAP_MARKER_Y)],
                      fill=(236, 0, 0), outline=(255, 255, 255))
        return frame

    # ------------------------------------------------------------------
    # Level-completed bonuses animation (blues level_completed_bonuses_animation)
    #
    # After the exit is touched the gameplay tick is replaced by this scripted
    # animation: the player walks to the left, a cauldron slides in from the
    # right, the player tosses in every food item collected during the level (one
    # per ~8 ticks), each landing adding its score_tbl bonus, then everything
    # slides off. One animation step runs per engine tick, so the whole sequence
    # plays at the DOS logic rate.
    # ------------------------------------------------------------------
    def _start_level_complete(self) -> None:
        # Convert the player to screen space and freeze the camera at the origin
        # (blues zeroes tilemap.x/y and subtracts them from the player position).
        self.player.x -= self.camera_x
        self.player.y -= self.camera_y
        self.camera_x = 0
        self.camera_y = 0
        self._icam_x = 0
        self._icam_y = 0
        self.player.vx = 0
        self.player.vy = 0
        self.player.facing = 1  # hdir = 0 -> face right
        self.player.dying = False
        self.player.hit_counter = 0
        self.player.shake_screen_counter = 0
        # Hide every object; the animation manages slots 2-4 (cauldron) and the
        # food it tosses in 55-74.
        for o in self.runtime_objects:
            o.spr_num = 0xFFFF
        self._set_anim_seq(1, current=True)
        self._step_player_anim()
        try:
            self.play_music(15)
        except Exception:
            pass
        self._complete = {
            "phase": "walk_in",
            "draw_counter": 0,
            "bp_burst": 0,
            "bp": 0,
            "di": 0,
            "al": 0,
            "pvx": 0,
        }

    def _complete_set_pot(self) -> None:
        o2 = self.runtime_objects[2]
        o2.x, o2.y, o2.spr_num = 360, 175, 100
        o3 = self.runtime_objects[3]
        o3.x, o3.y, o3.spr_num = 360, 148, 98
        o4 = self.runtime_objects[4]
        o4.x, o4.y, o4.spr_num = 360, 155, 104

    def _complete_fixup_hearts(self, c: dict) -> None:
        """blues level_completed_bonuses_animation_fixup_object4_spr_num: cycle
        the cauldron heart-bubble sprite 104..109 every 4th frame."""
        if c["draw_counter"] & 3:
            return
        o4 = self.runtime_objects[4]
        spr = (o4.spr_num & 0x1FFF) + 1
        if spr >= 110:
            spr = 104
        o4.spr_num = spr

    def _complete_update_food(self, c: dict) -> int:
        """blues helper inner body: gravity the flying food, score + despawn the
        ones that reach the cauldron. Returns the number still in flight."""
        active = 0
        for i in range(20):
            obj = self.runtime_objects[55 + i]
            if obj.spr_num == 0xFFFF:
                continue
            yv = obj.data_y_velocity
            if yv < 128:
                yv += 8
                obj.data_y_velocity = yv
            obj.y += yv >> 4
            active += 1
            if obj.y >= 145:
                idx = (obj.spr_num & 0x1FFF) - 110
                if 0 <= idx < len(SCORE_SPR_LUT):
                    self.score += SCORE_TBL[SCORE_SPR_LUT[idx]]
                obj.spr_num = 0xFFFF
                self.play_sound(8)
        return active

    def _complete_tally_decision(self, c: dict) -> None:
        """Outer di/al loop of blues level_completed_bonuses_animation: run at a
        burst boundary, feed the next collected food into a free slot or, when all
        are spawned and none are still falling, end the tally."""
        while True:
            di = c["di"]
            if di >= 113:
                di = c["di"] = 0
            if self.level_items_count[di] != 0:
                for i in range(20):
                    obj = self.runtime_objects[55 + i]
                    if obj.spr_num == 0xFFFF:
                        obj.spr_num = 110 + di
                        obj.x = 155
                        obj.y = 0
                        obj.data_y_velocity = 0
                        self.level_items_count[di] -= 1
                        c["di"] = di + 1
                        break
                c["al"] = 0
                return
            c["al"] += 1
            if c["al"] < 113:
                c["di"] = di + 1
                continue
            if c["bp"] == 0:
                self._complete_enter_outro(c)
                return
            c["al"] = 0
            return

    def _complete_enter_outro(self, c: dict) -> None:
        c["phase"] = "outro"
        self._set_anim_seq(1, current=True)

    def _complete_tick(self, inp: InputState) -> None:
        c = self._complete
        if getattr(inp, "quit", False):
            self._complete = None
            self.load_level(self.level_index + 1)
            return
        self._step_player_anim()  # level_update_object_anim(objects_tbl[1].anim)
        phase = c["phase"]

        if phase == "walk_in":
            flag = False
            # X toward 60 (blues moves only when at least 2px to the right).
            dx = self.player.x - 60
            x_offs = -2 if dx < 0 else 2
            if dx >= 2:
                self.player.x -= x_offs
                flag = True
            else:
                self.player.x = 60
            # Y toward 175.
            dy = self.player.y - 175
            y_offs = -2 if dy < 0 else 2
            if dy >= 2:
                self.player.y -= y_offs
                flag = True
            else:
                self.player.y = 175
            if not flag:
                c["phase"] = "pot_in"
                self._complete_set_pot()
            return

        if phase == "pot_in":
            self._complete_fixup_hearts(c)
            for s in (2, 3, 4):
                self.runtime_objects[s].x -= 3
            c["draw_counter"] += 1
            if self.runtime_objects[2].x <= 155:
                if self.level_items_total != 0:
                    c["phase"] = "throw"
                    c["pvx"] = 64
                    self._set_anim_seq(18, current=True)
                else:
                    self._complete_enter_outro(c)
            return

        if phase == "throw":
            self._complete_fixup_hearts(c)
            c["draw_counter"] += 1
            # blues level_update_player_x_velocity with x_friction = 2.
            pvx = c["pvx"]
            pvx = max(0, pvx - (12 >> 2))
            c["pvx"] = pvx
            xv = pvx >> 4
            self.player.x += xv
            if xv == 0:
                c["phase"] = "tally"
                self._set_anim_seq(17, current=True)
            return

        if phase == "tally":
            self._complete_fixup_hearts(c)
            c["bp_burst"] += self._complete_update_food(c)
            c["draw_counter"] += 1
            if (c["draw_counter"] & 7) == 0:  # burst boundary
                c["bp"] = c["bp_burst"]
                c["bp_burst"] = 0
                self._complete_tally_decision(c)
            return

        if phase == "outro":
            self._complete_fixup_hearts(c)
            for s in (2, 3, 4):
                self.runtime_objects[s].x -= 2
            self.player.x += 3 if self.runtime_objects[4].x < 0 else 2
            c["draw_counter"] += 1
            if self.runtime_objects[2].x <= -52:
                self._complete = None
                self.load_level(self.level_index + 1)
            return

    def _activate_exit_semaphore(self) -> None:
        """blues lighter pickup: the level-exit semaphore item (runtime sprite
        278, num 225) becomes the active exit (279, num 226) so the player can
        touch it to complete the level."""
        for item in self.level.items:
            if item.sprite_num_raw == 0xFFFF:
                continue
            rt = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            if rt is not None and (rt & 0x1FFF) == 278:
                item.sprite_num_raw += 1

    def _trigger_checkpoint(self, obj: RuntimeObject) -> None:
        """blues checkpoint branch: set respawn and change the checkpoint sprite
        to its triggered state (runtime 281 -> 280), reverting any previously
        triggered checkpoint (280 -> 281). The item stays in the world."""
        self.checkpoint_x = self.player.x
        self.checkpoint_y = self.player.y
        for item in self.level.items:
            if item.sprite_num_raw == 0xFFFF:
                continue
            rt = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            if rt is not None and (rt & 0x1FFF) == 280:  # previously triggered
                item.sprite_num_raw += 1
        if obj.ref_index is not None and 0 <= obj.ref_index < len(self.level.items):
            item = self.level.items[obj.ref_index]
            if item.sprite_num_raw != 0xFFFF:
                item.sprite_num_raw -= 1  # 281 -> 280: show triggered state

    def _kill_all_monsters(self, *, bomb: bool) -> None:
        for slot in range(11, 23):
            mon = self.runtime_objects[slot]
            if not mon.active or (mon.monster_flags & 0x10) or (mon.spr_num & 0x2000) == 0:
                continue
            if bomb:
                mon.monster_state = 0xFF
                mon.spr_num = 0xFFFF
            else:
                self._monster_die(mon, self._player_as_object())

    def _pickup_bonus_or_item(self, obj: RuntimeObject) -> None:
        """Port of the level_update_player_collision() item branch.

        Whether an item is pickupable is decided purely by its sprite number
        (num = (spr & 0x1FFF) - 53), exactly as in the DOS engine. Collectibles
        (food, fruit, BONUS letters, utensils, club, life, energy...) are
        consumed; effect items (semaphore, checkpoint, screen-kill, bomb,
        lights, damage) run their effect. Anything that matches no case
        (password characters, WELL DONE letters and other decorative sprites)
        is left untouched — it is NOT pickupable.
        """
        num = (obj.spr_num & 0x1FFF) - 53

        # --- special / effect items -------------------------------------
        if num == 226 or num == 258:  # end-of-level / game-completed semaphore
            obj.spr_num = 0xFFFF
            self._start_level_complete()  # bonus-tally animation, then advance
            return
        if num == 228:  # checkpoint ("washing machine")
            self._trigger_checkpoint(obj)
            return
        if num == 13:
            self.player.club_type = 0; self._consume_item(obj); return
        if num == 182:
            self.player.club_type = 1; self._consume_item(obj); return
        if num == 44:
            self.player.club_type = 2; self._consume_item(obj); return
        if num == 224:
            self.player.club_type = 3; self._consume_item(obj); return
        if num == 174:  # extra life
            self.play_sound(4)
            self.player.lives += 1
            self._add_score_object(obj, 227); self._consume_item(obj); return
        if num in (167, 168, 458, 459):  # damage
            self.play_sound(1)
            self.player.shake_screen_counter = 7  # blues
            if self.player.hit_counter == 0:
                self.player.hit_counter = 44
                self.player.anim_0x40_flag = 0
            self._consume_item(obj); return
        if num == 169:  # screen kill
            self.play_sound(0)
            self.player.shake_screen_counter = 9  # blues
            self._kill_all_monsters(bomb=False); self._consume_item(obj); return
        if num == 170:  # bomb
            self.play_sound(0)
            self._kill_all_monsters(bomb=True); self._consume_item(obj); return
        if num in (180, 181):  # lights on/off (no light state yet)
            self.play_sound(1)
            self._consume_item(obj); return

        # --- collectibles by range --------------------------------------
        if num <= 20:  # bones / small energy food: 6 -> +1 energy heart
            self.play_sound(8)
            self._consume_item(obj)
            self.player.bonus_energy_counter += 1
            if self.player.bonus_energy_counter >= 6 and self.player.energy != 3:
                self.player.energy += 1
                self.player.bonus_energy_counter = 0
                self._add_score_object(obj, 226)
            return
        if num <= 44:  # BONUS letters (index 0..4)
            self.play_sound(8)
            index = num - 39
            if 0 <= index <= 4:
                self.player.bonus_letters_mask |= (1 << index)
            self._consume_item(obj); return
        if num <= 50:  # utensils
            self.play_sound(8)
            if num == 46:  # lighter: activates the exit semaphore (item 278 -> 279)
                self._activate_exit_semaphore()
            self._consume_item(obj); return
        if num <= 64:  # food
            self.play_sound(4)
            # blues: food still falling fast (>=128) is kicked up off the player's
            # head instead of being collected (and shakes 50% of the time).
            if obj.data_y_velocity >= 128:
                obj.data_y_velocity = -obj.data_y_velocity
                x = 32
                if self.prng.next_u8() & 1:
                    x = -x
                    self.player.shake_screen_counter = 7
                obj.x_velocity = x
                return  # not collected
            idx = num - 57
            if 0 <= idx < len(self.level_items_count):
                self.level_items_count[idx] += 1
                self.level_items_total += 1
            score_num = SCORE_SPR_LUT[idx] + 74 if 0 <= idx < len(SCORE_SPR_LUT) else 74
            self._add_score_object(obj, score_num)
            if obj.ref_index is not None:  # blues: ref'd food counts toward bonuses
                self.level_complete_bonuses += 1
            self._consume_item(obj); return
        if num <= 166:  # fruit / score collectibles
            self.play_sound(8)
            idx = num - 57
            if 0 <= idx < len(self.level_items_count):
                self.level_items_count[idx] += 1
                self.level_items_total += 1
            score_num = SCORE_SPR_LUT[idx] + 74 if 0 <= idx < len(SCORE_SPR_LUT) else 74
            self._add_score_object(obj, score_num)
            self._consume_item(obj); return
        if num == 173:  # full-food / energy
            self._add_score_object(obj, 226); self._consume_item(obj); return

        # Anything else (password digits, WELL DONE, decorative): not pickupable.
        return

    def _update_player_collision(self) -> None:
        """Partial level_update_player_collision().

        v11 adds the real object slots for monsters (11..22) before the existing
        bonus/item branch.  The collision primitive is still the original
        anchor/spr_size/spr_offs model; only damage/lives accounting is kept as a
        scaffold until the full panel/state machine is ported.
        """
        player_obj = self._player_as_object()

        if self.player.hit_counter == 0:
            for slot in range(11, 23):
                obj = self.runtime_objects[slot]
                if not obj.active or (obj.spr_num & 0x2000) == 0:
                    continue
                if obj.monster_state == 0xFF or (obj.monster_flags & 0x10):
                    continue
                # Reset the stomp flag so it reflects only this collision pair;
                # _objects_collide sets it when the player lands on the monster.
                self._jump_monster_flag = 0
                self._collide_y_dist = 0
                if not self._objects_collide(player_obj, obj):
                    continue

                # blues: hurt when not a stomp, or moving upward into the monster.
                if not self._jump_monster_flag or self.player.vy < 0:
                    self.play_sound(9)  # hurt
                    self.player.hit_counter = 44
                    self.player.anim_0x40_flag = 0
                    self.player.vy = -128
                    self.player.vx = -(self.player.vx << 2)
                    obj.monster_flags &= ~1
                    if not self.god_mode:
                        self.player.energy -= 1
                        if self.player.energy < 0:
                            self.player.energy = 3
                            self.player.death_flag = 1
                    return

                # Stomp (normal, non-gravity mode): bounce the player and count
                # the stomp; the monster is NOT killed by jumping — only the club
                # kills it. Score on every other stomp. (blues 2614-2628.)
                self.play_sound(3)  # stomp
                self.player.vy = -224 if self._last_jump_held else -64
                self.player.jumping_counter = 0
                self.player.y -= self._collide_y_dist
                num = obj.monster_hit_jump_counter >> 2
                if num != 11:
                    obj.monster_hit_jump_counter += 4
                    num += 1
                    if (num & 1) == 0:
                        score_data = (0xFF46, 0x00E0, 0x00E1, 0x012D, 0x012E, 0x012F)
                        sval = score_data[num >> 1]
                        if sval >= 0x8000:
                            sval -= 0x10000
                        self._add_score_object(player_obj, sval)
                return

        for slot in range(23, 75):
            obj = self.runtime_objects[slot]
            if not obj.active or (obj.spr_num & 0x2000) == 0:
                continue
            if obj.counter > 188:
                continue
            if self._objects_collide(obj, player_obj):
                self._pickup_bonus_or_item(obj)

    def _update_runtime_items(self) -> None:
        """Port shape of level_update_objects_items() into objects_tbl[55..74]."""
        slot = 55
        count = 20
        cam_tx = self.camera_x >> 4
        cam_ty = self.camera_y >> 4
        for index, item in enumerate(self.level.items):
            if count == 0:
                break
            if not item.active:
                continue
            x_tile = (item.x_pos >> 4) - cam_tx
            if x_tile < 0 or x_tile > max(22, DOS_W // 16 + 1):
                continue
            y_tile = (item.y_pos >> 4) - cam_ty
            if y_tile < 0 or y_tile > max(43, DOS_H // 16 + 1):
                continue
            runtime_num = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            if runtime_num is None:
                continue
            obj = self.runtime_objects[slot]
            obj.x = item.x_pos
            dy = 0
            if self.tick_count & 1:
                dy = item.y_delta + 1
                item.y_delta = dy
                if dy >= 4:
                    dy = 1 - dy
                    item.y_delta = dy
            item.y_pos += dy
            obj.y = item.y_pos
            obj.spr_num = (runtime_num & 0x1FFF) | 0x2000
            obj.counter = 0
            obj.data_y_velocity = 0
            obj.ref_index = index
            obj.x_velocity = 0
            obj.y_velocity = 0
            slot += 1
            count -= 1
        while count > 0:
            obj = self.runtime_objects[slot]
            obj.spr_num = 0xFFFF
            obj.ref_index = None
            slot += 1
            count -= 1

    def _update_runtime_bonuses(self) -> None:
        """Port shape of level_update_objects_bonuses() for objects_tbl[23..54]."""
        for slot in range(23, 55):
            obj = self.runtime_objects[slot]
            if not obj.active:
                continue
            obj.counter -= 1
            if obj.counter == 0:
                obj.ttl = 15  # blues: hit_counter=15 -> blink window before removal
                continue
            if obj.counter < 0:
                # Blink window: count down (blues decrements in the draw loop) and
                # remove when it expires. Rendering skips 3/4 frames -> flicker.
                if obj.ttl > 0:
                    obj.ttl -= 1
                if obj.ttl == 0:
                    obj.spr_num = 0xFFFF
                continue
            obj.x += obj.x_velocity >> 4
            if obj.x < 0:
                obj.x = 0
                obj.x_velocity = -obj.x_velocity
            obj.y += obj.data_y_velocity >> 4
            dy = obj.data_y_velocity + 9
            if dy < 256:
                obj.data_y_velocity = dy
            num = obj.spr_num & 0x1FFF
            if num in (229, 300, 306, 308, 310):
                if num != 229 and obj.counter > 50:
                    obj.counter = 50
                continue
            pos_tx = obj.x >> 4
            pos_ty = obj.y >> 4
            if dy > 0:
                tile = self.tile_num_at_tile(pos_tx, pos_ty)
                attr1 = self.level.tile_attributes1[tile] if tile is not None else 1
                if attr1 != 0:
                    # (No shake here: the ASM does not shake when a food bonus
                    # lands/bounces — only on hard player falls, big hits, etc.)
                    obj.data_y_velocity = -obj.data_y_velocity >> 1
                    was_left = obj.x_velocity < 0
                    delta = -8 if was_left else 8
                    obj.x_velocity -= delta
                    if was_left != (obj.x_velocity < 0):
                        obj.x_velocity = 0
            else:
                tile = self.tile_num_at_tile(pos_tx, pos_ty - 1)
                attr0 = self.level.tile_attributes0[tile] if tile is not None else 1
                if attr0 != 0:
                    obj.x_velocity = -obj.x_velocity
                    obj.x += obj.x_velocity >> 4
            if (self.tick_count & 1) == 0 and obj.x_velocity != 0:
                if num < 73:
                    obj.spr_num = (obj.spr_num & ~0x1FFF) | (num + 1)
                elif num == 73:
                    obj.spr_num = (obj.spr_num & ~0x1FFF) | 70

    def _update_runtime_score_objects(self) -> None:
        for slot in range(75, 91):
            obj = self.runtime_objects[slot]
            if not obj.active:
                continue
            obj.y -= 1
            obj.counter -= 1
            if obj.counter <= 0:
                obj.spr_num = 0xFFFF

    def _select_player_anim_state(self, inp: InputState) -> int:
        # Original default controls use Up as jump and Space as club/action.
        # run_game also accepts the explicit jump flag for easier testing, but
        # Space is intentionally not mapped to jump anymore.
        jump_pressed = inp.up
        mask = 0
        mask |= 1 if inp.right else 0
        mask <<= 1
        mask |= 1 if inp.left else 0
        mask <<= 1
        mask |= 1 if jump_pressed else 0
        mask <<= 1
        mask |= 1 if inp.down else 0
        mask <<= 1
        mask |= 1 if inp.fire or inp.action else 0
        if self.player.club_anim_duration != 0:
            mask = 0
        return PLAYER_ANIM_LUT[mask]

    def play_sound(self, num: int) -> None:
        self.sound.play_sound(num)

    def play_music(self, num: int) -> None:
        self.sound.play_music(num)

    def _update_transition(self) -> None:
        self._trans_t += 1
        if self._trans_phase == 1 and self._trans_t >= TRANS_CLOSE_FRAMES:
            # Apply the teleport at the fully-black apex, then open.
            dst_x, dst_y, cam_x, cam_y = self._trans_pending
            self.player.x = dst_x
            self.player.y = dst_y
            self.camera_x = cam_x
            self.camera_y = cam_y
            self._clamp_camera()
            self.snapshot_prev()  # avoid interpolating across the teleport
            self._trans_phase = 2
            self._trans_t = 0
            self._update_runtime_items()
        elif self._trans_phase == 2 and self._trans_t >= TRANS_OPEN_FRAMES:
            self._trans_phase = 0
            self._trans_t = 0
            self._trans_pending = None

    def _draw_transition(self, frame: Image.Image, alpha: float) -> None:
        """Curtain: phase 1 black rolls from top+bottom to centre (vertical
        close); phase 2 black recedes from centre to left+right (horizontal
        open). `alpha` smooths the motion between ticks."""
        if self._trans_phase == 0:
            return
        draw = ImageDraw.Draw(frame)
        if self._trans_phase == 1:
            f = min(1.0, (self._trans_t + alpha) / TRANS_CLOSE_FRAMES)
            h = int(f * (PLAY_H / 2))
            draw.rectangle((0, 0, DOS_W, h), fill=(0, 0, 0))
            draw.rectangle((0, PLAY_H - h, DOS_W, PLAY_H), fill=(0, 0, 0))
        else:
            f = min(1.0, (self._trans_t + alpha) / TRANS_OPEN_FRAMES)
            revealed = int(f * DOS_W)
            left = (DOS_W - revealed) // 2
            draw.rectangle((0, 0, left, PLAY_H), fill=(0, 0, 0))
            draw.rectangle((DOS_W - left, 0, DOS_W, PLAY_H), fill=(0, 0, 0))

    def _start_death(self) -> None:
        """blues level_player_die + level_player_death_animation setup."""
        if self.player.dying:
            return
        self.player.dying = True
        self.player.death_timer = 60
        self.player.death_flag = 0
        self.player.spr_num = 33  # death sprite
        # blues level_player_death_animation: launched UP (y_velocity=15, then
        # y_pos -= y_velocity each frame, y_velocity decays by 1 to a -16 floor
        # -> rises, slows, then falls), and flung toward screen centre by +/-5px.
        self.player.death_vy = 15
        self.player.death_vx = 5 if (self.player.x < self.camera_x + 160) else -5
        self.player.hit_counter = 0
        self.play_sound(7)

    def _update_death_animation(self) -> None:
        self.player.spr_num = 33
        self.player.x += self.player.death_vx
        yv = max(self.player.death_vy - 1, -16)
        self.player.y -= yv  # positive yv -> moves up
        self.player.death_vy = yv
        self._update_runtime_monsters()
        self._update_runtime_bonuses()
        # The camera does NOT follow the dying player (it stays where the player
        # died and the body flies out of frame).
        self.player.death_timer -= 1
        if self.player.death_timer <= 0:
            self._respawn_player()

    def _respawn_player(self) -> None:
        if self.player.lives > 0:
            self.player.lives -= 1
        self.player.dying = False
        self.player.energy = 3
        self.player.x = self.checkpoint_x
        self.player.y = self.checkpoint_y
        self.player.vx = self.player.vy = 0
        self.player.on_ground = False
        self.player.anim2_counter = 0
        self.player.jumping_counter = 0
        self.player.current_anim_num = 0xFF
        self.player.special_anim_num = 0xFF
        self.player.anim_0x40_flag = 0
        self.player.club_anim_duration = 0
        self.player.club_powerup_duration = 0
        for obj in self.runtime_objects:
            obj.spr_num = 0xFFFF
        self.player.prev_y = self.player.y
        self.player.death_flag = 0
        self.player.restart_level_flag = 0
        # Snap the camera onto the respawn point so the off-screen death check
        # doesn't immediately re-trigger.
        self.camera_x = max(0, self.player.x - DOS_W // 2)
        self.camera_y = max(0, self.player.y - PLAY_H // 2)
        self._clamp_camera()
        self._icam_x, self._icam_y = self.camera_x, self.camera_y

    def tick(self, inp: InputState) -> None:
        self.tick_count += 1
        self.player.frame_tick += 1
        # Pre-level map screen: pauses gameplay until it pans in and is skipped
        # or times out (one pan step per tick = DOS pacing).
        if self._map_intro is not None:
            self._map_intro_tick(inp)
            return
        # Level-completed bonuses animation: takes over the whole tick (one
        # animation step per engine tick = blues level_sync pacing) until done.
        if self._complete is not None:
            self._complete_tick(inp)
            return
        # Death animation: once dying, play the falling death sprite for ~60
        # frames (blues level_player_death_animation) before restarting.
        if self.player.dying:
            self._update_death_animation()
            return
        # Gate teleport curtain: pause gameplay while the transition plays.
        if self._trans_phase != 0:
            self._update_transition()
            return
        self._last_jump_held = bool(inp.up or inp.jump)
        # level_update_player() starts by hiding objects_tbl[0].  It will be
        # re-created later in the tick if the current club animation frame needs
        # a visible club overlay.
        self.runtime_objects[0].spr_num = 0xFFFF
        self._update_runtime_hit_animation()
        self._update_runtime_monsters()
        self._update_runtime_projectiles_and_hits()
        self._update_runtime_bonuses()
        self._update_runtime_score_objects()
        self._update_platforms()

        # Original direction handling changes hdir/facing immediately, but only
        # if the opposite direction is not also pressed.
        self._key_hdir = bool(inp.left or inp.right)
        self._input_hdir = 0
        if inp.right and not inp.left:
            if self.player.facing != 1:
                self.player.update_counter = 0
            self.player.facing = 1
            self._input_hdir = 1
            if self.player.update_counter == 0:
                self.player.special_anim_num = 0xFF
        elif inp.left and not inp.right:
            if self.player.facing != -1:
                self.player.update_counter = 0
            self.player.facing = -1
            self._input_hdir = -1
            if self.player.update_counter == 0:
                self.player.special_anim_num = 0xFF

        if self.player.hit_counter > 0:
            self.player.hit_counter -= 1
        state = self._select_player_anim_state(inp)
        if self.player.hit_counter >= 22:
            state = PLAYER_HIT_ANIM_NUM
        if self.player.current_anim_num != state:
            self.player.update_counter = 0
            self.player.special_anim_num = 0xFF
            if state == 2:
                self.player.anim2_counter = 0
        self.player.update_counter = min(0xFFFF, self.player.update_counter + 1)

        # If a club swing is inside its 0x40-tagged continuation, most player
        # animation handlers tail-call level_update_player_anim_3_6_7(current).
        if self.player.anim_0x40_flag != 0 and state != 8:
            self._update_player_state_attack_367(self.player.current_anim_num)
        # If the player has already left the ground, the original path changes
        # to level_update_player_jump(), not to a ground idle/run state.
        elif not self.player.on_ground and state not in (2, 3, 4, 5, 6, 7, 8):
            # blues anim_0 (idle/recovery) applies FRICTION only, never hdir
            # acceleration — even while airborne (e.g. lifted by a club swing).
            # Only the run state (1) steers in the air. Accelerating idle here is
            # what made the player drift in the held direction between club hits.
            if state == 0:
                self._apply_player_friction()
            else:
                self._apply_player_hdir_accel(PLAYER_RUN_MAX16)
            self._apply_player_gravity()
            if self.player.vy > 0:
                self.player.nojump_counter = 6
                self.player.spr_num = PLAYER_JUMP_FALL_SPR if self.player.jumping_counter >= 12 else PLAYER_JUMP_SPR
                self._sync_player_collision_size()
            else:
                # Keep the previous upward jump animation frame.
                self._set_anim_seq(2, current=True)
                self._step_player_anim()
        else:
            if state == 0:
                self._update_player_state_anim0_idle_or_slide(inp)
            elif state == 1:
                self._update_player_state_run()
            elif state == 2:
                self._update_player_state_jump()
            elif state in (3, 6, 7):
                self._update_player_state_attack_367(state)
            elif state == 4:
                self._update_player_state_attack_4(state)
            elif state == 5:
                self._update_player_state_attack_5(state)
            elif state == 8:
                self._update_player_state_hit()
            else:
                self._update_player_state_anim0_idle_or_slide(inp)

        # DOS order: x += x_velocity>>4, y += y_velocity>>4, then
        # level_update_player_decor() resolves floor/ceiling/side interactions
        # from the bottom-origin point.  Do not broad-sweep/snap here; that was
        # the main source of visible jitter and floor sinking.
        next_x = self.player.x + (self.player.vx >> 4)
        end_x = (self.level.width_tiles + 20) << 4
        if end_x > next_x >= 8 and next_x < 511 * 8:
            self.player.x = next_x
        self.player.y += self.player.vy >> 4
        self._asm_update_player_decor()
        self._apply_platform_landing()
        self._update_axe_collisions()
        self._update_player_collision()
        self._update_runtime_items()
        self._update_gates(inp)

        # blues level_update_player_decor: the player dies (level_player_die) when
        # they go off-screen -- more than TILEMAP_SCREEN_H/16 (11) tiles from the
        # camera top vertically, more than TILEMAP_SCREEN_W/16 (20) tiles
        # horizontally, or below the map bottom. This kills a fall into a pit once
        # the camera can no longer follow.
        cam_ty = self.camera_y >> 4
        cam_tx = self.camera_x >> 4
        py_diff = abs((self.player.y >> 4) - cam_ty)
        px_diff = abs((self.player.x >> 4) - cam_tx)
        off_screen = (
            py_diff > (PLAY_H // 16)
            or px_diff > (DOS_W // 16)
            or (self.player.y >= 0 and self.player.y > ((self.level.height_tiles + 1) << 4))
        )
        if off_screen or self.player.death_flag:
            self._start_death()

        if self.player.shake_screen_counter > 0:
            self.player.shake_screen_counter -= 1
        if self.player.restart_level_flag > 0:
            self.player.restart_level_flag -= 1
        if self.player.action_counter > 0:
            self.player.action_counter -= 1
        if self.player.club_powerup_duration > 0:
            self.player.club_powerup_duration -= 1
        if self.player.club_anim_duration > 0:
            self.player.club_anim_duration -= 1

        self._update_camera()

        # Deferred level switch requested by an end-of-level semaphore pickup.
        if self._pending_level is not None:
            self.load_level(self._pending_level)

    def _update_runtime_hit_animation(self) -> None:
        # level_update_objects_hit_animation(): walk backwards from current_hit_object,
        # move each spark up and advance sprites 53..57 before clearing.
        slot = self.current_hit_object_slot
        for _ in range(5):
            obj = self.runtime_objects[slot]
            if obj.active:
                num = (obj.spr_num & 0x1FFF) + 1
                obj.y -= 1
                if num < 58:
                    obj.spr_num = (obj.spr_num & ~0x1FFF) | num | 0x2000
                else:
                    obj.spr_num = 0xFFFF
            slot -= 1
            if slot < 6:
                slot = 10

    def _update_runtime_projectiles_and_hits(self) -> None:
        # objects_tbl[2..5]: club projectiles.  This mirrors the simple movement
        # and animation loop from level_update_objects_club_projectiles().
        for slot in range(2, 6):
            obj = self.runtime_objects[slot]
            if not obj.active:
                continue
            obj.x += obj.x_velocity >> 4
            obj.y += obj.y_velocity >> 4
            if obj.anim_words:
                word = obj.anim_words[obj.anim_index % len(obj.anim_words)]
                if word < 0:
                    obj.anim_index = (obj.anim_index + (word // 2)) % len(obj.anim_words)
                    word = obj.anim_words[obj.anim_index % len(obj.anim_words)]
                obj.anim_index = (obj.anim_index + 1) % len(obj.anim_words)
                obj.spr_num = (word & 0x1FFF) | 0x2000 | (0x8000 if obj.x_velocity < 0 else 0)
            if obj.x_friction == 0:
                obj.y_velocity += 32
            elif obj.x_friction == 1:
                obj.y_velocity -= 16
            if obj.y > self.world_h + 128 or obj.x < -128 or obj.x > self.world_w + 128:
                obj.spr_num = 0xFFFF


    def _monster_next_tick(self, ms: RuntimeMonsterState) -> bool:
        if ms.current_tick < 255:
            ms.current_tick += 1
        return (ms.current_tick >> 2) < ms.respawn_ticks

    def _activate_monster(self, ms: RuntimeMonsterState, *, x: int | None = None, y: int | None = None, flags: int | None = None) -> RuntimeObject | None:
        obj = self._find_free_monster_slot()
        if obj is None or ms.runtime_sprite == 0xFFFF:
            return None
        obj.x = ms.x_pos if x is None else x
        obj.y = ms.y_pos if y is None else y
        obj.spr_num = (ms.runtime_sprite & 0x1FFF) | 0x2000
        obj.x_velocity = 0
        obj.y_velocity = 0
        obj.x_friction = 0
        obj.anim_fallback = (ms.runtime_sprite - self.sprite_resolver.monster_runtime_base) & 0x1FFF
        obj.anim_ptr = self._monster_anim_start(ms.movement_type, ms.runtime_sprite)
        obj.anim_words = ()
        obj.anim_index = 0
        obj.counter = 0
        obj.data_y_velocity = 0
        obj.ref_index = ms.index
        obj.monster_state = 0
        obj.monster_obj_flags = 0
        obj.monster_flags = ms.record_flags if flags is None else flags
        obj.monster_energy = ms.energy
        obj.monster_hit_jump_counter = 0
        obj.monster_type = ms.movement_type
        obj.monster_base_spr = ms.runtime_sprite & 0x1FFF
        ms.active_slot = obj.slot
        ms.flags = obj.monster_flags
        return obj

    def _spawn_monsters(self) -> None:
        for ms in self.monster_states:
            if ms.active_slot is not None:
                continue
            if ms.runtime_sprite == 0xFFFF:
                continue
            # Beginner difficulty skips expert-only records; expert shows all.
            if not self.expert and self.level.monsters[ms.index].expert_only:
                continue
            typ = ms.movement_type
            if typ in (0, 10):
                rect = self.level.monsters[ms.index].trigger_rect_tiles
                if rect is None:
                    continue
                x0, y0, width, height = rect
                px = self.player.x >> 4
                py = self.player.y >> 4
                if px < x0 or px - x0 > width or py < y0 or py - y0 > height:
                    if typ == 10:
                        self._monster_next_tick(ms)
                    continue
                if typ == 0:
                    self.monster_type0_side ^= 1
                    x = self.player.x + (192 if self.monster_type0_side == 0 else -192)
                    y = self.player.y - 176
                    self._activate_monster(ms, x=x, y=y, flags=7)
                else:
                    if self._monster_next_tick(ms):
                        continue
                    dist_tbl = (120, 100, -90, 110, -120, 40, 80, 60)
                    x = self.player.x + dist_tbl[self.monster_type10_dist & 7]
                    self.monster_type10_dist += 1
                    ty = (self.player.y >> 4) + 4
                    tx = x >> 4
                    y = self.player.y
                    found = False
                    for _ in range(10):
                        tile0 = self.tile_num_at_tile(tx, ty)
                        tile1 = self.tile_num_at_tile(tx, ty - 1)
                        tile2 = self.tile_num_at_tile(tx, ty - 2)
                        if tile0 is not None and tile1 is not None and tile2 is not None:
                            if (self.level.tile_attributes1[tile0] != 0
                                    and self.level.tile_attributes1[tile1] == 0
                                    and self.level.tile_attributes1[tile2] == 0
                                    and not self._is_unstable_secret_floor(tx, ty)):
                                y = ty << 4
                                found = True
                                break
                        ty -= 1
                    # blues monster_func2_type10 returns false (no spawn) when no
                    # floor is found in range -> don't spawn the enemy in mid-air.
                    if found:
                        self._activate_monster(ms, x=x, y=y, flags=0x17)
                continue

            if typ == 12:
                if self.player.y <= ms.y_pos:
                    continue
                dx = abs(ms.x_pos - self.player.x)
                if dx >= DOS_W * 2:
                    continue
                if dx <= DOS_W:
                    dy = self.player.y - ms.y_pos
                    if dy >= 360 or dy <= 180:
                        continue
                if self._monster_next_tick(ms):
                    continue
                self._activate_monster(ms, flags=0x8F)
                ms.current_tick = 0
                continue

            if not self._monster_is_visible(ms.x_pos, ms.y_pos):
                continue
            flags = 0x17
            if typ == 2:
                flags = 5
            elif typ == 3:
                flags = 0x37
            elif typ in (4, 5, 6, 7, 8):
                flags = 5
                if typ == 4:  # blues monster_func2_type4: reset swing state
                    ms.type4_angle = 0
                    ms.type4_angle_step = 0
            elif typ == 9:
                flags = (ms.record_flags | 5) & 0xFF
                ms.x_step = 0
            elif typ == 11:
                if self._monster_next_tick(ms):
                    continue
                flags = 0x37
            obj = self._activate_monster(ms, flags=flags)
            if obj and typ == 11:
                obj.y -= self.prng.next_u8() & 0x3F

    def _monster_reset_or_despawn(self, obj: RuntimeObject, ms: RuntimeMonsterState, *, permanent: bool = False) -> None:
        obj.spr_num = 0xFFFF
        obj.anim_words = ()
        obj.ref_index = None
        obj.monster_state = 0
        ms.active_slot = None
        ms.flags &= ~4
        if permanent:
            ms.runtime_sprite = 0xFFFF
        else:
            ms.current_tick = 0

    def _monster_offscreen_helper(self, obj: RuntimeObject, ms: RuntimeMonsterState) -> None:
        dx = abs(obj.x - self.player.x)
        dy = abs(obj.y - self.player.y)
        if dx <= DOS_W and dy <= DOS_H + 124:
            return
        if obj.monster_state < 10:
            self._monster_reset_or_despawn(obj, ms)
        else:
            # blues monster_reset: permanent (record retired) only when the LIVE
            # flags (m->flags, mutated by the AI) lack bit 2 -- NOT the original
            # record flags. A killed area enemy flies off with bit 2 set, so the
            # record stays alive and respawns.
            self._monster_reset_or_despawn(obj, ms, permanent=(obj.monster_flags & 2) == 0)

    def _tile_monster_offset(self, tile_num: int, obj: RuntimeObject) -> int:
        attr = self.level.tile_attributes3[tile_num]
        if (attr & 0x30) == 0:
            return attr
        x = (obj.x & 15) // 3
        if attr & 0x10:
            return (attr & 15) + x
        return (attr & 15) - x

    def _update_monster_pos(self, obj: RuntimeObject, ms: RuntimeMonsterState) -> None:
        pos_tx = obj.x >> 4
        pos_ty = obj.y >> 4
        tile_below = self.tile_num_at_tile(pos_tx, pos_ty)
        tile_above = self.tile_num_at_tile(pos_tx, pos_ty - 1)
        step = -1 if obj.x_velocity < 0 else (1 if obj.x_velocity > 0 else 0)
        side_tile = self.tile_num_at_tile(pos_tx + step, pos_ty - 1)
        side_attr0 = self.level.tile_attributes0[side_tile] if side_tile is not None else 1

        if (obj.monster_flags & 0x40) and (obj.monster_flags & 0x80):
            if side_attr0 != 0:
                obj.x -= obj.x_velocity >> 4
            else:
                self._monster_change_prev_anim(obj)
                obj.y_velocity = 0
                obj.monster_flags &= ~0x80
                obj.y -= 16
            return

        if side_attr0 != 0:
            if obj.monster_flags & 0x40:
                self._monster_change_next_anim(obj)
                obj.monster_flags |= 0x80
                obj.y_velocity = -16
                obj.x += obj.x_velocity >> 2
                return
            obj.x_velocity = -obj.x_velocity
            obj.x += obj.x_velocity >> 4

        # blues SWAP(dl, dh): after moving up a row, test the tile that is now at
        # the monster's feet (tile_above) first; only if it is empty fall back to
        # the original lower tile. Getting this order wrong snaps a grounded
        # monster one tile too high every frame, so it bounces/jumps forever.
        obj.y -= 16
        tile = tile_above
        attr1 = self.level.tile_attributes1[tile] if tile is not None else 0
        if attr1 == 0:
            obj.y += 16
            tile = tile_below
            attr1 = self.level.tile_attributes1[tile] if tile is not None else 0
            if attr1 == 0:
                if obj.y_velocity < 256:
                    obj.y_velocity += 16
                return
        dy = self._tile_monster_offset(tile, obj) if tile is not None else 0
        obj.y &= ~15
        obj.y += dy
        if (obj.monster_flags & 0x20) == 0:
            y_vel = (-obj.y_velocity) >> 1
            if abs(y_vel) <= 32:
                y_vel = 0
            obj.y_velocity = y_vel
        else:
            obj.y_velocity = 0

    def _monster_update_y_velocity_or_reset(self, obj: RuntimeObject, ms: RuntimeMonsterState) -> None:
        if (obj.monster_flags & 1) and ((obj.spr_num & 0x2000) or self.player.y >= obj.y):
            if obj.y_velocity < 240:
                obj.y_velocity += 15
        else:
            # blues monster_reset uses the LIVE flags for the permanent decision.
            self._monster_reset_or_despawn(obj, ms, permanent=(obj.monster_flags & 2) == 0)

    def _update_monster_ai(self, obj: RuntimeObject, ms: RuntimeMonsterState) -> None:
        typ = ms.movement_type
        state = obj.monster_state
        # blues types 9 and 12 don't call monster_func1_helper; they manage
        # their own offscreen/despawn logic.
        if typ not in (9, 12):
            self._monster_offscreen_helper(obj, ms)
            if not obj.active:
                return

        if state == 0xFF:
            self._monster_update_y_velocity_or_reset(obj, ms)
            return
        if typ == 0:
            if state == 0:
                if self._monster_next_tick(ms):
                    return
                if obj.y - self.player.y >= 176:
                    self._monster_reset_or_despawn(obj, ms)
                else:
                    obj.monster_flags = 8
                    obj.monster_state = 1
            elif state == 1 and obj.y_velocity == 0:
                obj.monster_state = 2
                obj.x_velocity = 32 if obj.x < self.player.x else -32
                self._monster_change_next_anim(obj)
            return
        if typ == 1:
            return
        if typ == 2:
            speed = int(ms.extra.get("vertical_speed_step", 0)) << 4
            if speed == 0:
                speed = 16
            dy = obj.y - ms.y_pos
            if state == 0:
                obj.y_velocity = speed
                if dy > int(ms.extra.get("vertical_range_px", 0)):
                    obj.monster_state = 1
                    self._monster_change_next_anim(obj)
            elif state == 1:
                obj.y_velocity = -speed
                if dy < 0:
                    obj.monster_state = 0
                    self._monster_change_prev_anim(obj)
            return
        if typ == 3:
            if state == 0:
                if self._monster_next_tick(ms):
                    return
                dx = abs(ms.x_pos - self.player.x) >> 4
                if dx <= int(ms.extra.get("activation_x_range_tiles", 0)):
                    obj.monster_flags &= ~0x10
                    obj.monster_state = 1
                    obj.y_velocity = 32
                    self._monster_change_next_anim(obj)
            elif state == 1:
                tile = self.tile_num_at_tile(obj.x >> 4, obj.y >> 4)
                if tile is not None and self.level.tile_attributes1[tile] != 0:
                    obj.y &= ~15
                    obj.y_velocity = 0
                    obj.monster_state = 2
                    obj.monster_flags |= 0x48
                    obj.x_velocity = 48 if self.player.x >= obj.x else -48
                    self._monster_change_next_anim(obj)
            elif state == 2 and obj.x < 0:
                obj.x_velocity = -obj.x_velocity
            return
        if typ == 4:  # swinging / rotating hanging spider (blues monster_func1_type4)
            radius = int(ms.extra.get("swing_radius_px", 0))
            limit = int(ms.extra.get("swing_angle_limit", 0))
            if state == 0:
                if self._monster_next_tick(ms):
                    return
                dy = obj.y - ms.y_pos
                if radius > dy:
                    obj.y += 2  # lower until the string reaches full length
                else:
                    obj.monster_state = 1
            elif state == 1:
                self._monster_rotate_pos(obj, ms, radius)
                ms.type4_angle = (ms.type4_angle + 4) & 0xFF
                if ms.type4_angle >= limit:
                    ms.type4_angle = limit
                    obj.monster_state = 2
            elif state == 2:
                self._monster_rotate_pos(obj, ms, radius)
                if ms.type4_angle & 0x80:
                    ms.type4_angle_step += 1
                else:
                    ms.type4_angle_step -= 1
                ms.type4_angle = (ms.type4_angle + ms.type4_angle_step) & 0xFF
            return
        if typ == 5:
            if state == 0:
                obj.x_velocity = 1 if obj.x <= self.player.x else -1
                dx = abs(self.player.x - obj.x) >> 4
                if int(ms.extra.get("activation_x_range_tiles", 0)) < dx:
                    return
                dy = abs(self.player.y - obj.y) >> 4
                if int(ms.extra.get("activation_y_range_tiles", 0)) < dy:
                    return
                obj.monster_state = 10
                speed = int(ms.extra.get("dash_speed_step", 1)) << 4
                obj.y_velocity = speed
                obj.x_velocity = -speed if obj.x_velocity < 0 else speed
            elif state == 10:
                if abs(obj.y - self.player.y) <= 8:
                    obj.monster_state = 11
                    obj.y_velocity = 10
            return
        if typ == 7:
            if state == 0:
                obj.x_velocity = 1 if obj.x <= self.player.x else -1
                dx = abs(self.player.x - obj.x) >> 4
                if int(ms.extra.get("activation_x_range_tiles", 0)) < dx:
                    return
                obj.monster_state = 10
                speed = int(ms.extra.get("launch_speed_step", 1)) << 4
                obj.y_velocity = speed
                obj.x_velocity = -speed if obj.x_velocity < 0 else speed
                self._monster_change_next_anim(obj)
            return
        if typ == 6:
            if state == 0:
                obj.x_velocity = 1 if obj.x <= self.player.x else -1
                if (abs(self.player.x - obj.x) >> 4) <= int(ms.extra.get("activation_x_range_tiles", 0)):
                    obj.monster_state = 1
                    ms.type6_pattern_index = 0
            elif state == 1:
                pattern = ((0x40,0x28),(0x50,0x26),(0x10,0x30),(0x20,0x36),(0x10,0x3C),(-8,0x32),(-16,0x30),(-32,0x28),(-16,-1))
                ox, oy = pattern[ms.type6_pattern_index]
                if oy < 0:
                    ms.type6_pattern_index = 0
                    ox, oy = pattern[0]
                target_x = self.player.x + ox
                target_y = self.player.y - (oy + (5 if self.player.anim_0x40_flag else 0))
                moved = False
                if abs(target_x - obj.x) >= 3:
                    obj.x += 3 if target_x > obj.x else -3
                    moved = True
                if abs(target_y - obj.y) >= 3:
                    obj.y += 3 if target_y > obj.y else -3
                    moved = True
                if moved:
                    ms.type6_pattern_index = (ms.type6_pattern_index + 1) % (len(pattern) - 1)
            return
        if typ == 8:
            def _type8_jump() -> None:
                obj.y_velocity = -(int(ms.extra.get("jump_up_speed_step", 1)) << 4)
                speed = int(ms.extra.get("jump_horizontal_speed_step", 1)) << 4
                obj.x_velocity = -speed if self.player.x <= obj.x else speed
                obj.monster_state = 10
                obj.monster_flags |= 0x2C
                self._monster_change_next_anim(obj)
            if obj.x_velocity == 0:
                obj.x_velocity = 1 if obj.x <= self.player.x else -1
            if state == 0 and not self._monster_next_tick(ms):
                dx = abs(self.player.x - obj.x) >> 4
                dy = abs(self.player.y - obj.y) >> 4
                if dx <= int(ms.extra.get("activation_x_range_tiles", 0)) and dy <= int(ms.extra.get("activation_y_range_tiles", 0)):
                    _type8_jump()
            elif state == 10 and obj.y_velocity >= 0:
                obj.monster_state = 11
                self._monster_change_next_anim(obj)
            elif state == 11 and obj.y_velocity <= 0:
                obj.monster_state = 12
                self._monster_change_prev_anim(obj)
                self._monster_change_prev_anim(obj)
                obj.x_velocity = 0
                ms.current_tick = 0
            elif state == 12 and not self._monster_next_tick(ms):
                _type8_jump()
            return
        if typ == 9:
            # blues type9 special despawn (no generic offscreen helper).
            if state != 0xFF:
                left = int(ms.extra.get("patrol_left_x_px", obj.x))
                right = int(ms.extra.get("patrol_right_x_px", obj.x))
                dy = abs(obj.y - self.player.y)
                if (dy >= 190
                        or (self.player.x < left and self.player.x + 480 < left)
                        or (self.player.x >= left and right + 480 <= self.player.x)):
                    self._monster_reset_or_despawn(obj, ms)
                    return
            if state == 0:
                obj.x_velocity = ms.x_step
                nxt = ms.x_step + 3
                if nxt <= int(ms.extra.get("patrol_max_speed_step", 0)):
                    ms.x_step = nxt
                if obj.x > int(ms.extra.get("patrol_right_x_px", obj.x)):
                    obj.monster_state = 1
            elif state == 1:
                obj.x_velocity = ms.x_step
                nxt = ms.x_step - 3
                if nxt >= -int(ms.extra.get("patrol_max_speed_step", 0)):
                    ms.x_step = nxt
                if obj.x <= int(ms.extra.get("patrol_left_x_px", obj.x)):
                    obj.monster_state = 0
            return
        if typ == 10:
            if state == 0:
                obj.monster_flags |= 0x18
                if obj.y_velocity == 0:
                    obj.monster_state = 1
            elif state == 1:
                if obj.y_velocity != 0:
                    obj.monster_state = 0
                else:
                    ms.current_tick = 30
                    obj.monster_state = 2
                    self._monster_change_next_anim(obj)
            elif state == 2:
                if abs(obj.x_velocity) < 16 and obj.counter != 0:
                    speed = int(ms.extra.get("emerge_attack_speed_step", 1))
                    obj.x_velocity = -speed if obj.x >= self.player.x else speed
                    obj.monster_flags = 0x0F
                if (self.tick_count & 3) == 0:
                    ms.current_tick -= 1
                    if ms.current_tick <= 0:
                        obj.monster_state = 3
                        obj.y_velocity = 0
                        obj.x_velocity = -1 if obj.x_velocity < 0 else 0
                        obj.monster_flags = 0x36
                        self._monster_change_next_anim(obj)
            elif state == 3 and obj.counter != 0:
                self._monster_reset_or_despawn(obj, ms)
            return
        if typ == 11:
            if state == 0 and obj.counter != 0:
                obj.monster_state = 1
                obj.monster_flags &= ~0x10
                sx = int(ms.extra.get("leap_horizontal_speed_step", 1)) << 4
                obj.x_velocity = -sx if self.player.x <= obj.x else sx
                obj.y_velocity = -(int(ms.extra.get("leap_up_speed_step", 1)) << 4)
            elif state == 1:
                max_fall = int(ms.extra.get("max_fall_speed_step", 15)) << 4
                if obj.y_velocity < max_fall:
                    obj.y_velocity += 8
            return
        if typ == 12:
            if state == 0:
                speed = int(ms.extra.get("horizontal_speed_step", 1)) << 4
                obj.x_velocity = -speed if self.player.x < obj.x else speed
                obj.monster_state = 1
            elif state == 1:
                if obj.monster_obj_flags & 0x20:  # blues obj->data.m.flags
                    ms.current_tick = 0
                else:
                    ms.current_tick += 1
                    if ms.current_tick >= 154:
                        obj.monster_state = 0xFF
            return

    def _update_runtime_monsters(self) -> None:
        for slot in range(11, 23):
            obj = self.runtime_objects[slot]
            if not obj.active:
                continue
            if obj.ref_index is None or not (0 <= obj.ref_index < len(self.monster_states)):
                obj.spr_num = 0xFFFF
                continue
            ms = self.monster_states[obj.ref_index]
            if obj.hit_flash > 0:
                obj.hit_flash -= 1
            obj.y += obj.y_velocity >> 4
            if obj.x_velocity != -1:
                obj.x += obj.x_velocity >> 4
            if obj.monster_flags & 8:
                self._update_monster_pos(obj, ms)
            word = self._monster_anim_step(obj)
            dx = self.sprite_resolver.monster_runtime_base + (word & 0x1FFF)
            # The high bits are meaningful runtime flags. Preserve 0x2000 for
            # collidable/live objects and 0x4000 for stomp-hit visual state.
            flags = obj.spr_num & 0x6000
            if flags == 0:
                flags = 0x2000
            if obj.x_velocity < 0:
                dx |= 0x8000
            obj.spr_num = flags | (dx & 0x9FFF)
            obj.counter = ((word >> 8) & 0xE0)
            self._update_monster_ai(obj, ms)
        self._spawn_monsters()

    def _update_gates(self, inp: "InputState") -> None:
        """blues level_update_gates(): when the player does the down-action
        (action_counter set by anim 4/5) and holds down on a gate's enter tile,
        teleport to dst_pos and reframe the camera. Cave entrances / teleports."""
        if self.player.action_counter == 0 or not inp.down:
            return
        pos = ((self.player.y >> 4) << 8) | (self.player.x >> 4)
        for gate in self.level.gates:
            if gate.enter_pos == 0xFFFF:
                continue
            if gate.enter_pos == pos:
                self.player.vx = 0
                self.player.vy = 0
                self.player.action_counter = 0
                # Original: video_transition_close() -> teleport -> _open().
                # Start the curtain; the teleport is applied at the close apex.
                self._trans_phase = 1
                self._trans_t = 0
                self._trans_pending = (
                    (gate.dst_pos & 0xFF) << 4, (gate.dst_pos >> 8) << 4,
                    (gate.tilemap_pos & 0xFF) << 4, (gate.tilemap_pos >> 8) << 4,
                )
                break

    def _is_unstable_secret_floor(self, tx: int, ty: int) -> bool:
        """True if (tx,ty) holds an active secret whose solidity changes when
        revealed — monsters must not emerge there or they end up standing on a
        platform that disappears (the 'mid-air' enemies)."""
        pos = (tx & 0xFF) | ((ty & 0xFF) << 8)
        a1 = self.level.tile_attributes1
        for rec in self.bonuses_rt:
            if rec[0] == pos and a1[rec[1]] != a1[rec[2]]:
                return True
        return False

    def _init_secret_bonus_tiles(self) -> None:
        """blues load_level_data_init_secret_bonus_tiles().

        For each active secret record, hide the current map tile behind tile_num0
        and remember the original tile as tile_num1 (restored on reveal). Records
        are kept as mutable [pos, tile_num0, tile_num1, count] lists.
        """
        self.bonuses_rt: list[list[int]] = []
        self._bonus_draw_counter = 0
        complete_secrets = 0
        for b in self.level.bonuses:
            pos = b.pos & 0xFFFF
            rec = [pos, b.tile_num0 & 0xFF, b.tile_num1 & 0xFF, b.count & 0xFF]
            if pos != 0xFFFF:
                orig = self._tile_num_at_linear_offset(pos)
                if orig is not None:
                    rec[2] = orig
                    self._set_tile_num_at_linear_offset(pos, rec[1])
                complete_secrets += 1
            self.bonuses_rt.append(rec)
        # Level-completed bookkeeping (blues level_complete_*/level_current_*/
        # level_items_count_tbl). complete_secrets = active hidden-tile records.
        self.level_complete_secrets = complete_secrets
        self.level_complete_bonuses = 0
        self.level_current_secrets = 0
        self.level_items_count = [0] * 128
        self.level_items_total = 0

    def _add_object23_bonus(self, spr_num: int, x: int, y: int, x_vel: int, y_vel: int, count: int) -> None:
        """blues level_add_object23_bonus(): spawn `count` bonus objects fanning
        out from (x, y) with alternating horizontal velocity, TTL counter 198."""
        spawned = 0
        for slot in range(23, 55):
            if count <= 0:
                return
            obj = self.runtime_objects[slot]
            if obj.active:
                continue
            # Bonus drops become collidable once on screen; the original sets
            # 0x2000 at draw time. Set it here so food/fruit can be picked up.
            obj.spr_num = (spr_num & 0x1FFF) | 0x2000 | (spr_num & 0x8000)
            obj.counter = 198
            obj.x = x
            obj.y = y
            obj.x_velocity = x_vel
            obj.data_y_velocity = y_vel
            obj.y_velocity = 0
            obj.ttl = 0
            obj.ref_index = None
            x_vel = -x_vel
            spawned += 1
            if (spawned & 1) == 0:
                if spawned > 12:
                    x_vel = 0
                x_vel -= 16
                y_vel -= 16
            count -= 1

    def _random_bonus_spr_num(self) -> int:
        while True:
            num = self.prng.next_u8() & 0x7F
            if num < 0x5F:
                return 0x2080 + num

    def _update_found_bonus_tile(self, rec: list[int]) -> bool:
        """blues level_update_found_bonus_tile(): reveal the tile, clear record.

        When the revealed tile visibly changes (a hidden platform/wall, not a
        plain bonus-drop spot), flood the reveal to adjacent secret tiles of the
        same kind so the whole structure appears at once — matching the DOS
        behaviour the player observed. (The flat disasm for the original
        level_collide_axe_bonuses lives in a data-interleaved region that does
        not recover cleanly, so this is derived from observed behaviour + the
        readable blues adjacency test.)
        """
        pos = rec[0]
        rec[0] = 0xFFFF
        self._set_tile_num_at_linear_offset(pos, rec[2])
        self.level_current_secrets += 1  # blues ++level_current_secrets_count
        if rec[1] != rec[2]:
            self._flood_reveal_secret_neighbours(pos)
        return False

    def _flood_reveal_secret_neighbours(self, pos: int) -> None:
        stack = [pos]
        while stack:
            p = stack.pop()
            px, py = p & 0xFF, p >> 8
            for rec2 in self.bonuses_rt:
                if rec2[0] == 0xFFFF or rec2[1] == rec2[2]:
                    continue  # consumed, or a plain bonus-drop (no tile change)
                qx, qy = rec2[0] & 0xFF, rec2[0] >> 8
                if abs(qx - px) > 1 or abs(qy - py) > 1:
                    continue
                qp = rec2[0]
                rec2[0] = 0xFFFF
                self._set_tile_num_at_linear_offset(qp, rec2[2])
                stack.append(qp)

    def _handle_bonuses_found(self, obj: RuntimeObject, rec: list[int]) -> bool:
        """blues level_handle_bonuses_found(): spawn food/random bonus on a club
        hit and, when the record is exhausted, reveal its tile. Returns True if
        the record is not yet consumed (caller keeps it)."""
        pos = rec[0]
        x_pos = ((pos & 0xFF) << 4) + 12
        y_pos = ((pos >> 8) << 4) + 8
        self._init_object_hit_from_xy(x_pos, y_pos)
        count = rec[3]
        bx = (pos & 0xFF) << 4
        by = (pos >> 8) << 4
        lvl = self.level_index

        if count & 0x80:  # big random bonus: multi-hit, then one large bonus
            rec[3] = (count - 1) & 0xFF
            if rec[3] & 0x80:
                return False
            num = 0
            while num == 0:
                num = self.prng.next_u8() & 7
            self._add_object23_bonus(110 + num, bx, by - 112, 0, 0, 1)
            return self._update_found_bonus_tile(rec)

        # throttle repeated hits (draw-counter delta < 6 -> ignore)
        diff = abs(self.tick_count - self._bonus_draw_counter)
        self._bonus_draw_counter = self.tick_count
        if diff < 6:
            return True

        food_spr = 306 if lvl == 3 else (300 if lvl in (6, 7) else 308)
        if count & 0x40:  # tile-reveal / food secret
            if count == 64:  # final hit
                rec[3] = 0
                self._add_object23_bonus(food_spr, bx, by, 32, -48, 2)
                if lvl == 8:
                    self._add_object23_bonus(310, bx, by, 32, -48, 2)
                else:
                    self._add_object23_bonus(229, bx, by, 32, -48, 4)
            else:
                self._add_object23_bonus(food_spr, bx, by, 48, -96, 4)
                rec[3] = (rec[3] - 1) & 0xFF
                if (rec[3] & 0x80) == 0:
                    return True
                return self._update_found_bonus_tile(rec)
        else:  # small random bonus
            spr = self._random_bonus_spr_num()
            x_vel = 48
            if obj.slot < 1:
                if self.player.facing >= 0:
                    x_vel = -x_vel
            elif obj.x_velocity >= 0:
                x_vel = -x_vel
            self._add_object23_bonus(spr, bx, by, x_vel, -112, 1)

        rec[3] = (rec[3] - 1) & 0xFF
        if (rec[3] & 0x80) == 0:
            return True
        return self._update_found_bonus_tile(rec)

    def _collide_axe_bonuses(self, obj: RuntimeObject) -> bool:
        """blues level_collide_axe_bonuses(): a club/axe object striking a secret
        bonus tile. Crucially the loop does NOT stop after the first hit: once a
        secret is consumed/revealed it counts adjacent active secrets and only
        returns early when the revealed one is isolated, so neighbouring secret
        tiles in a cluster get activated by the same swing too."""
        obj_x = obj.x >> 4
        obj_y = obj.y - 16
        for rec in self.bonuses_rt:
            if rec[0] == 0xFFFF:
                continue
            bonus_x = rec[0] & 0xFF
            if abs(bonus_x - obj_x) > 1:
                continue
            bonus_y = rec[0] >> 8
            if abs((bonus_y << 4) - obj_y) >= 16:
                continue
            obj.spr_num = 0xFFFF
            if self._handle_bonuses_found(obj, rec):
                continue
            # The hit secret was consumed/revealed; count adjacent active
            # secrets. If none, stop; otherwise keep going so the cluster
            # neighbours get processed in the same pass.
            adjacent = 0
            for rec2 in self.bonuses_rt:
                if rec2[0] == 0xFFFF or rec2[0] == rec[0]:
                    continue
                if abs((rec2[0] & 0xFF) - bonus_x) > 1:
                    continue
                if abs((rec2[0] >> 8) - bonus_y) > 1:
                    continue
                adjacent += 1
            if adjacent == 0:
                return True
        return False

    def _add_bonus_object(self, spr_num: int, x: int, y: int, x_vel: int, y_vel: int, count: int) -> RuntimeObject | None:
        for slot in range(23, 55):
            obj = self.runtime_objects[slot]
            if not obj.active:
                obj.spr_num = spr_num & 0xFFFF
                obj.x = x
                obj.y = y
                obj.x_velocity = x_vel
                obj.y_velocity = 0
                obj.data_y_velocity = y_vel
                obj.counter = count
                obj.ttl = 0
                obj.ref_index = None
                return obj
        return None

    def _monster_die(self, obj: RuntimeObject, by_obj: RuntimeObject) -> None:
        ms = self.monster_states[obj.ref_index] if obj.ref_index is not None and 0 <= obj.ref_index < len(self.monster_states) else None
        score_num = (ms.score + 74) if ms is not None else 74
        self._add_score_object(obj, score_num)
        obj.monster_state = 0xFF
        if ms is None:
            obj.spr_num = 0xFFFF
            return
        if (obj.monster_flags & 1) == 0:
            # Non-respawning death: scatter 6 bones like level_monster_die()
            # (level_add_object23_bonus -> alternating velocities, TTL 198).
            self._add_object23_bonus(0x2046, obj.x, obj.y, 48, -128, 6)
            self._monster_reset_or_despawn(obj, ms, permanent=(obj.monster_flags & 2) == 0)
        else:
            # Respawning monster: switch to the death animation and fling it
            # off-screen (it then falls via the state==0xFF gravity path).
            self._monster_update_anim(obj)
            if (obj.monster_flags & ~0x37) != 0x88:
                obj.monster_flags &= ~8
            dy = min(self.player.club_power, 25)
            obj.y_velocity = -dy << 3
            dx = obj.y_velocity >> 1
            if (by_obj.spr_num & 0x8000) == 0:
                dx = -dx
            obj.x_velocity = dx

    def _collide_attack_object_with_monsters(self, attack_obj: RuntimeObject) -> bool:
        for slot in range(11, 23):
            obj = self.runtime_objects[slot]
            if not obj.active or obj.monster_state == 0xFF:
                continue
            if (obj.monster_flags & 0x10) != 0:
                continue
            if not self._objects_collide(attack_obj, obj, player_using_club=True):
                continue
            # blues sets obj->data.m.flags |= 0x40 here (object runtime flags),
            # NOT m->flags. Setting the m->flags 0x40 bit would wrongly trigger
            # the wall-climb path in _update_monster_pos when the monster survives.
            obj.monster_obj_flags |= 0x40
            obj.monster_energy -= self.player.club_power
            if obj.monster_energy < 0:
                self.play_sound(2)  # blues level_collide_axe_monsters
                self._monster_die(obj, attack_obj)
            else:
                obj.x -= obj.x_velocity >> 2
                obj.hit_flash = 6  # blink white on a surviving hit
            attack_obj.spr_num = 0xFFFF
            return True
        return False

    def _update_axe_collisions(self) -> None:
        # level_update_objects_axe(): projectiles first, visible club overlay next.
        for slot in range(2, 6):
            obj = self.runtime_objects[slot]
            if obj.active and self._collide_attack_object_with_monsters(obj):
                continue
            if obj.active:
                self._collide_axe_bonuses(obj)
        club = self.runtime_objects[0]
        if club.active and self._collide_attack_object_with_monsters(club):
            if self.player.vy != 0:
                self.player.vy = -80
        if club.active:
            self._collide_axe_bonuses(club)

    def _player_touches_deadly(self) -> bool:
        for x in (self.player.left + 2, self.player.x, self.player.right - 2):
            for y in (self.player.top + 4, self.player.bottom - 1):
                if self.deadly_at_pixel(x, y):
                    return True
        return False

    def _update_camera(self) -> None:
        px = self.player.x
        py = self.player.y - PLAYER_H // 2
        if px - self.camera_x < CAMERA_MARGIN_X:
            self.camera_x = px - CAMERA_MARGIN_X
        elif px - self.camera_x > DOS_W - CAMERA_MARGIN_X:
            self.camera_x = px - (DOS_W - CAMERA_MARGIN_X)
        # Vertical scrolling is intentionally our own (diverges from the ASM by
        # choice for a nicer feel): keep the player inside a relaxed dead zone of
        # the PLAY_H view so the camera doesn't twitch on small vertical moves.
        rel_y = py - self.camera_y
        if rel_y < CAMERA_MARGIN_TOP:
            self.camera_y = py - CAMERA_MARGIN_TOP
        elif rel_y > PLAY_H - CAMERA_MARGIN_BOTTOM_PLAY:
            self.camera_y = py - (PLAY_H - CAMERA_MARGIN_BOTTOM_PLAY)
        self._clamp_camera()

    def _clamp_camera(self) -> None:
        max_x = max(0, self.world_w - DOS_W)
        max_y = max(0, self.world_h - PLAY_H)
        self.camera_x = max(0, min(max_x, int(self.camera_x)))
        self.camera_y = max(0, min(max_y, int(self.camera_y)))

    def _tile_pixels(self, tile_num: int) -> list[int]:
        tile_num = self.level.tile_for_animation_frame(tile_num, (self.tick_count // 6) % 3)
        pixels = self.tile_cache.get(tile_num)
        if pixels is None:
            pixels = decode_planar_tile(resolve_tile_bytes(self.level, self.union_tiles, tile_num))
            self.tile_cache[tile_num] = pixels
        return pixels

    def _front_pixels(self, tile_num: int) -> list[int]:
        pixels = self.front_cache.get(tile_num)
        if pixels is None:
            pixels = decode_planar_tile(resolve_front_tile_bytes(self.level, self.front_tiles, tile_num))
            self.front_cache[tile_num] = pixels
        return pixels

    def _pixels_to_rgba(self, pixels: list[int]) -> Image.Image:
        rgb = self.rgb
        img = Image.new("RGBA", (TILE, TILE))
        img.putdata([(0, 0, 0, 0) if c == 0 else (*rgb[c], 255) for c in pixels])
        return img

    def _tile_image(self, tile_num: int) -> Image.Image:
        resolved = self.level.tile_for_animation_frame(tile_num, (self.tick_count // 6) % 3)
        img = self._tile_img_cache.get(resolved)
        if img is None:
            pixels = self.tile_cache.get(resolved)
            if pixels is None:
                pixels = decode_planar_tile(resolve_tile_bytes(self.level, self.union_tiles, resolved))
                self.tile_cache[resolved] = pixels
            img = self._pixels_to_rgba(pixels)
            self._tile_img_cache[resolved] = img
        return img

    def _front_image(self, tile_num: int) -> Image.Image:
        img = self._front_img_cache.get(tile_num)
        if img is None:
            img = self._pixels_to_rgba(self._front_pixels(tile_num))
            self._front_img_cache[tile_num] = img
        return img

    def _draw_tile_pixels(self, img: Image.Image, pixels: list[int], ox: int, oy: int) -> None:
        out = img.load()
        for y in range(TILE):
            py = oy + y
            if not 0 <= py < DOS_H:
                continue
            row = y * TILE
            for x in range(TILE):
                px = ox + x
                if not 0 <= px < DOS_W:
                    continue
                color = pixels[row + x]
                if color == 0:
                    continue
                out[px, py] = self.rgb[color]

    def _draw_sprite(self, frame: Image.Image, sprite_num: int, world_x: int, world_y: int, *, anchor_bottom: bool = True, tint: tuple | None = None, camera: tuple | None = None) -> None:
        cam_x, cam_y = camera if camera is not None else (self.camera_x, self.camera_y)
        flip_x = (sprite_num & 0x8000) != 0
        base_num = sprite_num & 0x1FFF
        key = (base_num, self.level_index, int(flip_x))
        spr = self.sprite_cache.get(key)
        if spr is None:
            spr = render_sprite_image(self.sprites_blob, self.sprite_tables, self.palette, base_num, transparent_zero=True)
            if flip_x:
                spr = spr.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            self.sprite_cache[key] = spr
        if tint is not None:
            # Hit flash: keep the silhouette (alpha), recolour every pixel.
            solid = Image.new("RGBA", spr.size, (*tint, 0))
            solid.putalpha(spr.getchannel("A"))
            spr = solid
        origin_x, origin_y = self.sprite_tables.origin(base_num)
        # The original offset table stores the anchor point inside each sprite.
        # For the player that anchor is the bottom contact/origin point.  The
        # previous runtime added these values and then subtracted height, which
        # made the player walk in the floor and made horizontal flip move the
        # whole sprite.  Keep the same world anchor for both facings.
        if flip_x:
            # Mirror a 0-based anchor coordinate: x -> width - 1 - x.
            # Using width - x caused a 1 px visual pop every time the player
            # flipped direction.
            origin_x = spr.width - 1 - origin_x
        sx = int(world_x - cam_x - origin_x)
        sy = int(world_y - cam_y - origin_y)
        frame.paste(spr, (sx, sy), spr)

    def _player_sprite_num(self) -> int:
        # spr_num comes from p2/staticres.c object_anim_tbl via the original
        # player_anim_lut state machine. Bit 15 is handled here as flip/facing.
        return self.player.spr_num | (0x8000 if self.player.facing < 0 else 0)

    def _draw_player_debug_overlay(self, frame: Image.Image) -> None:
        """Draw the ASM probe model, not a guessed broad hitbox."""
        draw = ImageDraw.Draw(frame)
        sx = self.player.x - self.camera_x
        sy = self.player.y - self.camera_y
        dx = 9 if self.player.vx > 0 else (-9 if self.player.vx < 0 else 0)
        probe_x = sx + dx
        top_y = sy - self.player.collision_h
        # bottom anchor cross
        draw.line((sx - 4, sy, sx + 4, sy), fill=(255, 0, 0))
        draw.line((sx, sy - 4, sx, sy + 4), fill=(255, 0, 0))
        # side probe column used by level_update_tile1/tile2
        draw.line((probe_x, sy - 1, probe_x, top_y), fill=(0, 255, 255))
        # tile under the bottom-origin point used by level_update_tile0
        tx = (self.player.x >> 4) * 16 - self.camera_x
        ty = (self.player.y >> 4) * 16 - self.camera_y
        draw.rectangle((tx, ty, tx + 15, ty + 15), outline=(255, 255, 0))

    def snapshot_prev(self) -> None:
        """Record positions before a tick so the renderer can interpolate
        between the previous and current tick (visual smoothing only; the engine
        tick stays accurate)."""
        self._icam_x = self.camera_x
        self._icam_y = self.camera_y
        self.player.ipx = self.player.x
        self.player.ipy = self.player.y
        for obj in self.runtime_objects:
            obj.ipx = obj.x
            obj.ipy = obj.y
            # Identity so the renderer never interpolates across a slot reuse
            # (items/bonuses/sparks reassign slots between ticks, which would
            # otherwise slide a brand-new object out of the old one's position).
            obj.iact = obj.spr_num != 0xFFFF
            obj.iref = obj.ref_index if obj.ref_index is not None else -1

    @staticmethod
    def _lerp(prev: int, cur: int, a: float) -> int:
        d = cur - prev
        if d > 48 or d < -48:  # teleport / spawn / wrap -> snap, don't slide
            return cur
        return int(prev + d * a)

    def render_frame(self, *, debug_overlay: bool = False, alpha: float | None = None) -> Image.Image:
        if self._map_intro is not None:
            return self._render_map_intro()
        if self._complete is not None:
            return self._render_complete()
        interp = alpha is not None
        a = alpha or 0.0
        cam_x = self._lerp(self._icam_x, self.camera_x, a) if interp else self.camera_x
        cam_y = self._lerp(self._icam_y, self.camera_y, a) if interp else self.camera_y
        # Screen shake (blues level_shake_screen): a damped vertical jolt while
        # shake_screen_counter is set (hard landings, big food, big hits).
        sc = self.player.shake_screen_counter
        if sc > 1:
            cam_y += sc if (self.tick_count & 1) else -sc
        self._last_cam = (cam_x, cam_y)

        # Static background -> one copy instead of 64000 per-pixel writes.
        frame = self._bg_image.copy()

        tx0 = cam_x // TILE
        ty0 = cam_y // TILE
        tx1 = min(self.level.width_tiles - 1, (cam_x + DOS_W) // TILE + 1)
        # Only the PLAY_H-tall playfield is rendered; the bottom is the HUD panel.
        ty1 = min(self.level.height_tiles - 1, (cam_y + PLAY_H) // TILE)

        for ty in range(ty0, ty1 + 1):
            row = ty * self.level.width_tiles
            oy = ty * TILE - cam_y
            for tx in range(tx0, tx1 + 1):
                tile_num = self.runtime_tilemap[row + tx]
                img = self._tile_image(tile_num)
                frame.paste(img, (tx * TILE - cam_x, oy), img)

        for platform in self.platforms:
            runtime_num = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
            if runtime_num is not None and abs(platform.x - cam_x) < DOS_W + 120 and abs(platform.y - cam_y) < DOS_H + 120:
                px = self._lerp(platform.prev_x, platform.x, a) if interp else platform.x
                py = self._lerp(platform.prev_y, platform.y, a) if interp else platform.y
                self._draw_sprite(frame, runtime_num, px, py, anchor_bottom=False, camera=(cam_x, cam_y))

        # Runtime objects: club overlay, projectiles, hit sparks, monsters, bonuses.
        for obj in self.runtime_objects:
            if obj.active and abs(obj.x - cam_x) < DOS_W + 160 and abs(obj.y - cam_y) < DOS_H + 160:
                tint = None
                if 11 <= obj.slot <= 22 and obj.hit_flash > 0:
                    tint = _BLINK_WHITE  # hit enemy flashes white
                elif 23 <= obj.slot <= 54 and obj.ttl > 0 and (self.tick_count & 1):
                    tint = _BLINK_BLACK  # expiring bonus blinks black (like the player)
                # Interpolate only when this slot held the SAME object last tick
                # (active then, and the same ref identity); otherwise snap to the
                # current position so a reused slot doesn't slide.
                same = (obj.iact and
                        obj.iref == (obj.ref_index if obj.ref_index is not None else -1))
                if interp and same:
                    ox = self._lerp(obj.ipx, obj.x, a)
                    oyp = self._lerp(obj.ipy, obj.y, a)
                else:
                    ox, oyp = obj.x, obj.y
                self._draw_sprite(frame, obj.spr_num, ox, oyp, anchor_bottom=True, tint=tint, camera=(cam_x, cam_y))

        # When hit, the player blinks black during the invincibility window.
        player_tint = _BLINK_BLACK if (self.player.hit_counter > 0 and (self.tick_count & 1)) else None
        ppx = self._lerp(self.player.ipx, self.player.x, a) if interp else self.player.x
        ppy = self._lerp(self.player.ipy, self.player.y, a) if interp else self.player.y
        self._draw_sprite(frame, self._player_sprite_num(), ppx, ppy, anchor_bottom=True, tint=player_tint, camera=(cam_x, cam_y))
        if debug_overlay:
            self._draw_player_debug_overlay(frame)

        # Foreground tiles are drawn after sprites when attr2 bit 0x40 says so.
        for ty in range(ty0, ty1 + 1):
            row = ty * self.level.width_tiles
            oy = ty * TILE - cam_y
            for tx in range(tx0, tx1 + 1):
                tile_num = self.runtime_tilemap[row + tx]
                if self.level.tile_attributes2[tile_num] & 0x40:
                    img = self._front_image(tile_num)
                    frame.paste(img, (tx * TILE - cam_x, oy), img)
        self._draw_transition(frame, a)
        self._draw_hud(frame)
        return frame

    def _panel_planar_image(self, src: bytes, w: int, h: int, transparent: int | None) -> Image.Image:
        """Decode a planar 4bpp ALLFONTS region into an RGBA image using the
        current palette; `transparent` (a palette index) becomes alpha 0."""
        pix = decode_planar_bitmap(src, w, h)
        rgb = self.rgb
        img = Image.new("RGBA", (w, h))
        img.putdata([(0, 0, 0, 0) if c == transparent else (*rgb[c], 255) for c in pix])
        return img

    def _build_panel_assets(self) -> None:
        """blues level_draw_panel graphics: panel background strip (320x23) +
        16x12 number/icon glyphs (0-9 digits, 10 heart, 12-16 BONUS letters)."""
        self._panel_bg = None
        self._panel_glyphs = {}
        blob = self._allfonts
        if not blob:
            return
        bg_off = 41 * 48
        bg_len = 320 * 23 // 2
        if len(blob) < bg_off + bg_len:
            return
        # The panel bg is opaque (blues uses transparent 0xFF, i.e. none).
        self._panel_bg = self._panel_planar_image(blob[bg_off:bg_off + bg_len], 320, 23, None)
        fnt = 48 * 41 + 160 * 23
        for num in range(17):
            o = fnt + num * 96
            if len(blob) < o + 96:
                break
            self._panel_glyphs[num] = self._panel_planar_image(blob[o:o + 96], 16, 12, 0)
        # Number font used by the level-completed screen (blues video_draw_number:
        # allfonts + 0x1C70, 16x11 glyphs, num*88 bytes), palette index 0 -> alpha.
        self._number_glyphs = {}
        nfnt = 0x1C70
        for num in range(10):
            o = nfnt + num * 88
            if len(blob) < o + 88:
                break
            self._number_glyphs[num] = self._panel_planar_image(blob[o:o + 88], 16, 11, 0)

    def _draw_panel_number(self, frame: Image.Image, offset: int, num: int) -> None:
        """blues video_draw_panel_number: 8px-aligned x, absolute y in the frame."""
        glyph = self._panel_glyphs.get(num)
        if glyph is None:
            return
        x = (offset % 40) * 8
        y = offset // 40
        frame.paste(glyph, (x, y), glyph)

    @staticmethod
    def _complete_offset_xy(offset: int) -> tuple[int, int]:
        """blues screen-byte offset -> pixel (x, y) for the bonus screen."""
        return (offset * 8) % 320, (offset * 8) // 320

    def _draw_number_glyph(self, frame: Image.Image, offset: int, digit: int) -> None:
        glyph = self._number_glyphs.get(digit)
        if glyph is None:
            return
        x, y = self._complete_offset_xy(offset)
        frame.paste(glyph, (x, y), glyph)

    def _blit_sprite_topleft(self, frame: Image.Image, sprite_num: int, x: int, y: int) -> None:
        """Blit a sprite with its top-left corner at (x, y), exactly as the
        original video_draw_sprite does (render_add_sprite stores x,y and blits
        the frame at that corner — the spr_offs/height anchor used by
        level_draw_objects is NOT applied to direct video_draw_sprite calls)."""
        base_num = sprite_num & 0x1FFF
        key = (base_num, self.level_index, 0)
        spr = self.sprite_cache.get(key)
        if spr is None:
            spr = render_sprite_image(self.sprites_blob, self.sprite_tables, self.palette, base_num, transparent_zero=True)
            self.sprite_cache[key] = spr
        frame.paste(spr, (x, y), spr)

    def _draw_letter_spr(self, frame: Image.Image, offset: int, code: int) -> None:
        """blues video_draw_character_spr: draws sprite 241+code at offset's pixel
        position (used for letters via video_draw_string2 and the '%' glyph)."""
        x, y = self._complete_offset_xy(offset)
        self._blit_sprite_topleft(frame, 241 + code, x, y)

    def _draw_string2(self, frame: Image.Image, offset: int, text: str) -> None:
        for ch in text:
            if ch != " ":
                self._draw_letter_spr(frame, offset, ord(ch) - 0x41)
            offset += 2

    def _complete_draw_score(self, frame: Image.Image) -> None:
        """blues level_completed_bonuses_animation_draw_score."""
        self._draw_string2(frame, 0x230, "SCORE")
        score_digits = 7
        score = self.score * 10
        for i in range(score_digits):
            digit = score % 10
            score //= 10
            self._draw_number_glyph(frame, 0x23C + (score_digits - 1 - i) * 2, digit)
        self._draw_string2(frame, 0x410, "LEVEL COMPLETED")
        percentage = 100
        total = self.level_complete_secrets + self.level_complete_bonuses
        if total != 0:
            current = self.level_current_secrets  # current_bonuses is always 0
            percentage = (current * 100) // total
        for i in range(3):
            digit = percentage % 10
            percentage //= 10
            self._draw_number_glyph(frame, 0x430 + (2 - i) * 2, digit)
            if percentage == 0:
                break
        self._draw_letter_spr(frame, 0x436, 0x1A)  # '%' glyph

    def _render_complete(self) -> Image.Image:
        """Render one frame of the level-completed bonuses animation: black
        screen, the cauldron, the tossed food, the player, and the score/percent
        overlay (blues video_clear + level_draw_objects + draw_score)."""
        frame = Image.new("RGB", (DOS_W, DOS_H), (0, 0, 0))
        # Player first (slot 1, behind the cauldron), then the cauldron (2-4),
        # then the food (55-74) on top, matching the object-table draw order.
        self._draw_sprite(frame, self._player_sprite_num(), self.player.x, self.player.y,
                          anchor_bottom=True, camera=(0, 0))
        for slot in (2, 3, 4):
            obj = self.runtime_objects[slot]
            if obj.active and -64 < obj.x < DOS_W + 64:
                self._draw_sprite(frame, obj.spr_num, obj.x, obj.y, anchor_bottom=True, camera=(0, 0))
        for slot in range(55, 75):
            obj = self.runtime_objects[slot]
            if obj.active:
                self._draw_sprite(frame, obj.spr_num, obj.x, obj.y, anchor_bottom=True, camera=(0, 0))
        self._complete_draw_score(frame)
        return frame

    def _draw_hud(self, frame: Image.Image) -> None:
        """Bottom status panel using the original ALLFONTS graphics."""
        # The panel area (below PLAY_H) is not part of the playfield. Fill it
        # black first so the 1px gap under the 23px panel strip stays black,
        # matching the DOS active-area boundary.
        ImageDraw.Draw(frame).rectangle((0, PLAY_H, DOS_W, DOS_H), fill=(0, 0, 0))
        if self._panel_bg is None:
            return
        frame.paste(self._panel_bg, (0, PLAY_H))
        p = self.player
        # lives
        self._draw_panel_number(frame, 0x1CED, min(p.lives, 9))
        # score (7 digits), value*10 like the original
        score = self.score * 10
        for i in range(7):
            self._draw_panel_number(frame, 0x1CF1 + (6 - i) * 2, score % 10)
            score //= 10
        # energy hearts (glyph 10)
        for i in range(max(0, p.energy)):
            self._draw_panel_number(frame, 0x1D01 + i * 2, 10)
        # BONUS letters (glyphs 12..16)
        bonus_pos = (0x1C91, 0x1BF2, 0x1CE3, 0x1C6C, 0x1C1D)
        for i in range(5):
            if (p.bonus_letters_mask >> i) & 1:
                self._draw_panel_number(frame, bonus_pos[i], 12 + i)


class GameApp(tk.Tk):
    def __init__(self, project_dir: Path, data_dir: Path, level_index: int = 0, scale: int = 3, *, audio_debug: bool = False) -> None:
        super().__init__()
        self.title("Prehistorik 2 runtime RE - run_game")
        self.scale = max(1, int(scale))
        self.world = RuntimeWorld(project_dir, data_dir, level_index, audio_debug=audio_debug)
        self.input = InputState()
        self.running = True
        self.show_debug = False  # F1 / Develop menu toggles the debug overlay
        self.interpolate = False  # View menu: render interpolation (engine stays tick-accurate)
        self.show_fps = False     # View menu: FPS/TPS overlay
        self.target_fps = 60      # View menu: render cap when interpolating (vsync-like)
        self.keep_aspect = True   # View menu: keep 320x200 aspect ratio
        self.integer_scale = True  # View menu: integer-only scaling
        self.last_tick = time.perf_counter()
        self.accum = 0.0
        self.photo: ImageTk.PhotoImage | None = None
        self._photo_size = (0, 0)
        self._overlay_items: list[int] = []  # canvas vector overlay item ids
        self._last_cam = (0, 0)              # camera used by the last render (for overlays)
        self._disp = (0, 0, float(self.scale), float(self.scale))  # off_x, off_y, sx, sy
        # FPS / ticks-per-second measurement.
        self._fps_t0 = time.perf_counter()
        self._fps_frames = 0
        self._fps_ticks0 = 0
        self._fps_text = ""

        self._build_menu()
        # The canvas fills the (resizable) window; the game image is centred on it
        # with letterboxing, so the window can be any size.
        self.resizable(True, True)
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas_image = None
        self._bind_keys()
        self.geometry(f"{DOS_W * self.scale}x{DOS_H * self.scale}")
        self.after(0, self._main_loop)

    def _display_size(self, win_w: int, win_h: int) -> tuple[int, int]:
        """Output (width, height) for the game image given the window size and
        the keep-aspect / integer-scaling toggles."""
        if not self.keep_aspect:
            return max(1, win_w), max(1, win_h)
        if self.integer_scale:
            s = max(1, min(win_w // DOS_W, win_h // DOS_H))
            return DOS_W * s, DOS_H * s
        s = min(win_w / DOS_W, win_h / DOS_H)
        return max(1, int(DOS_W * s)), max(1, int(DOS_H * s))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        develop = tk.Menu(menubar, tearoff=0)

        # Difficulty (beginner / expert)
        self._difficulty_var = tk.StringVar(value="expert" if self.world.expert else "beginner")
        diff = tk.Menu(develop, tearoff=0)
        for label, val in (("Beginner", "beginner"), ("Expert", "expert")):
            diff.add_radiobutton(label=label, value=val, variable=self._difficulty_var,
                                 command=self._apply_difficulty)
        develop.add_cascade(label="Difficulty", menu=diff)

        # Level selector
        self._level_var = tk.IntVar(value=self.world.level_index)
        levels = tk.Menu(develop, tearoff=0)
        for i in range(len(LEVEL_IDS)):
            levels.add_radiobutton(label="Level %d" % (i + 1), value=i, variable=self._level_var,
                                   command=self._apply_level)
        develop.add_cascade(label="Level", menu=levels)

        develop.add_separator()
        self._god_var = tk.BooleanVar(value=self.world.god_mode)
        develop.add_checkbutton(label="God mode", variable=self._god_var, command=self._apply_god)
        self._debug_var = tk.BooleanVar(value=self.show_debug)
        develop.add_checkbutton(label="Debug overlay (F1)", variable=self._debug_var,
                                command=self._apply_debug)
        develop.add_separator()
        develop.add_command(label="Restart level (R)", command=lambda: self.world.load_level(self.world.level_index))
        develop.add_command(label="Trigger level end (bonus screen)", command=self._trigger_level_end)

        menubar.add_cascade(label="Develop", menu=develop)

        # View menu
        view = tk.Menu(menubar, tearoff=0)
        self._interp_var = tk.BooleanVar(value=self.interpolate)
        view.add_checkbutton(label="Interpolation (smooth FPS)", variable=self._interp_var,
                             command=self._apply_interp)
        self._fps_var = tk.BooleanVar(value=self.show_fps)
        view.add_checkbutton(label="FPS / ticks overlay", variable=self._fps_var,
                             command=self._apply_fps)
        # Frame-rate cap for interpolation (vsync-like target).
        self._fpscap_var = tk.IntVar(value=self.target_fps)
        fr = tk.Menu(view, tearoff=0)
        for cap in (30, 50, 60, 75, 120, 144):
            fr.add_radiobutton(label="%d FPS" % cap, value=cap, variable=self._fpscap_var,
                               command=self._apply_fpscap)
        fr.add_radiobutton(label="Uncapped", value=0, variable=self._fpscap_var, command=self._apply_fpscap)
        view.add_cascade(label="Frame rate (interpolation)", menu=fr)
        view.add_separator()
        self._aspect_var = tk.BooleanVar(value=self.keep_aspect)
        view.add_checkbutton(label="Keep aspect ratio", variable=self._aspect_var, command=self._apply_aspect)
        self._intscale_var = tk.BooleanVar(value=self.integer_scale)
        view.add_checkbutton(label="Integer scaling", variable=self._intscale_var, command=self._apply_intscale)
        menubar.add_cascade(label="View", menu=view)

        # Audio menu
        audio = tk.Menu(menubar, tearoff=0)
        self._sound_var = tk.BooleanVar(value=self.world.sound.sound_enabled)
        self._music_var = tk.BooleanVar(value=self.world.sound.music_enabled)
        audio.add_checkbutton(label="Sound effects", variable=self._sound_var, command=self._apply_sound)
        audio.add_checkbutton(label="Music", variable=self._music_var, command=self._apply_music)
        menubar.add_cascade(label="Audio", menu=audio)
        self.config(menu=menubar)

    def _apply_interp(self) -> None:
        self.interpolate = self._interp_var.get()

    def _apply_fps(self) -> None:
        self.show_fps = self._fps_var.get()

    def _apply_fpscap(self) -> None:
        self.target_fps = self._fpscap_var.get()

    def _apply_aspect(self) -> None:
        self.keep_aspect = self._aspect_var.get()

    def _apply_intscale(self) -> None:
        self.integer_scale = self._intscale_var.get()

    def _apply_sound(self) -> None:
        self.world.sound.sound_enabled = self._sound_var.get()

    def _apply_music(self) -> None:
        en = self._music_var.get()
        self.world.sound.music_enabled = en
        if en:
            self.world.sound.resume_music()
        else:
            self.world.sound.stop_music()

    def _apply_difficulty(self) -> None:
        self.world.expert = (self._difficulty_var.get() == "expert")
        self.world.load_level(self.world.level_index)  # respawn with new monster set

    def _apply_level(self) -> None:
        self.world.load_level(self._level_var.get())

    def _apply_god(self) -> None:
        self.world.god_mode = self._god_var.get()

    def _trigger_level_end(self) -> None:
        """Develop helper: jump straight into the level-completed bonus screen
        with whatever food/secrets have been collected so far."""
        w = self.world
        if w._complete is None and not w.player.dying and w._trans_phase == 0:
            w._start_level_complete()

    def _apply_debug(self) -> None:
        self.show_debug = self._debug_var.get()

    def _bind_keys(self) -> None:
        pairs = {
            "Left": "left", "Right": "right", "Up": "up", "Down": "down",
            "a": "left", "d": "right", "w": "up", "s": "down",
            "space": "fire", "Control_L": "fire", "Control_R": "fire", "Return": "action",
        }
        for key, attr in pairs.items():
            self.bind(f"<{key}>", lambda e, a=attr: self._set_input(a, True))
            self.bind(f"<KeyRelease-{key}>", lambda e, a=attr: self._set_input(a, False))
        self.bind("<F1>", lambda _e: self._toggle_debug())
        self.bind("<r>", lambda _e: self.world.load_level(self.world.level_index))
        self.bind("<bracketleft>", lambda _e: self._change_level(-1))
        self.bind("<bracketright>", lambda _e: self._change_level(1))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _set_input(self, attr: str, value: bool) -> None:
        setattr(self.input, attr, value)

    def _toggle_debug(self) -> None:
        self.show_debug = not self.show_debug
        self._debug_var.set(self.show_debug)

    def _change_level(self, delta: int) -> None:
        self.world.load_level((self.world.level_index + delta) % len(LEVEL_IDS))
        self._level_var.set(self.world.level_index)

    def _main_loop(self) -> None:
        frame_start = time.perf_counter()
        # --- fixed-timestep logic ---------------------------------------
        now = frame_start
        self.accum += now - self.last_tick
        self.last_tick = now
        step = 1.0 / TICK_HZ
        self.accum = min(self.accum, step * 5)  # cap catch-up after a stall
        ticked = False
        while self.accum >= step:
            self.world.snapshot_prev()  # frame-1 state for interpolation
            self.world.tick(self.input)
            self.accum -= step
            ticked = True

        # --- present ----------------------------------------------------
        # Without interpolation: draw once per tick (so FPS == TPS exactly).
        # With interpolation: draw every loop, blending frame-1 -> frame-2 by
        # the leftover fraction of the tick.
        if self.interpolate:
            self._present(self.accum / step)
        elif ticked:
            self._present(None)

        # --- schedule next frame (compensate for this frame's work time) ---
        if self.interpolate and self.target_fps > 0:
            target_ms = 1000.0 / self.target_fps
        elif self.interpolate:
            target_ms = 1.0  # uncapped
        else:
            # Poll a bit faster than the tick so each tick is presented promptly.
            target_ms = 1000.0 / (TICK_HZ * 1.5)
        work_ms = (time.perf_counter() - frame_start) * 1000.0
        self.after(max(1, round(target_ms - work_ms)), self._main_loop)

    def _present(self, alpha: float | None) -> None:
        img = self.world.render_frame(debug_overlay=False, alpha=alpha)
        self._last_cam = self.world._last_cam
        win_w = max(1, self.canvas.winfo_width())
        win_h = max(1, self.canvas.winfo_height())
        out_w, out_h = self._display_size(win_w, win_h)
        if (out_w, out_h) != (DOS_W, DOS_H):
            img = img.resize((out_w, out_h), Image.Resampling.NEAREST)
        off_x = (win_w - out_w) // 2
        off_y = (win_h - out_h) // 2
        self._disp = (off_x, off_y, out_w / DOS_W, out_h / DOS_H)
        # Reuse the PhotoImage buffer (paste) unless its size changed -- a large
        # win in the PIL -> Tk pipeline.
        if self.photo is None or self._photo_size != (out_w, out_h):
            self.photo = ImageTk.PhotoImage(img)
            self._photo_size = (out_w, out_h)
            if self.canvas_image is None:
                self.canvas_image = self.canvas.create_image(off_x, off_y, anchor="nw", image=self.photo)
            else:
                self.canvas.itemconfig(self.canvas_image, image=self.photo)
        else:
            self.photo.paste(img)
        self.canvas.coords(self.canvas_image, off_x, off_y)

        # FPS / TPS measurement.
        self._fps_frames += 1
        dt = time.perf_counter() - self._fps_t0
        if dt >= 0.5:
            fps = self._fps_frames / dt
            tps = (self.world.tick_count - self._fps_ticks0) / dt
            self._fps_text = f"{fps:3.0f} fps   {tps:3.0f} tps"
            self._fps_t0 = time.perf_counter()
            self._fps_frames = 0
            self._fps_ticks0 = self.world.tick_count
        self._update_overlays()

    def _update_overlays(self) -> None:
        """All overlays live on the Tk canvas as independent vector items, above
        the game image -- never drawn into the game pixels."""
        for item in self._overlay_items:
            self.canvas.delete(item)
        self._overlay_items.clear()
        off_x, off_y, sx, sy = self._disp
        if self.show_fps:
            text = self._fps_text or "-- fps   -- tps"
            self._overlay_items.append(
                self.canvas.create_text(off_x + 5, off_y + 4, anchor="nw", text=text,
                                        fill="#00ff00", font=("Consolas", 9, "bold")))
        if self.show_debug:
            self._draw_debug_overlay_items(off_x, off_y, sx, sy)

    def _draw_debug_overlay_items(self, off_x: float, off_y: float, sx: float, sy: float) -> None:
        p = self.world.player
        cam_x, cam_y = self._last_cam

        def scr(wx, wy):  # world -> screen
            return off_x + (wx - cam_x) * sx, off_y + (wy - cam_y) * sy

        px0, py0 = scr(p.x, p.y)
        dx = 9 if p.vx > 0 else (-9 if p.vx < 0 else 0)
        probe_x = px0 + dx * sx
        top = py0 - p.collision_h * sy
        add = self._overlay_items.append
        add(self.canvas.create_line(px0 - 4 * sx, py0, px0 + 4 * sx, py0, fill="#ff0000"))
        add(self.canvas.create_line(px0, py0 - 4 * sy, px0, py0 + 4 * sy, fill="#ff0000"))
        add(self.canvas.create_line(probe_x, py0, probe_x, top, fill="#00ffff"))
        tx0, ty0 = scr((p.x >> 4) * 16, (p.y >> 4) * 16)
        add(self.canvas.create_rectangle(tx0, ty0, tx0 + 16 * sx, ty0 + 16 * sy, outline="#ffff00"))
        add(self.canvas.create_text(
            off_x + 5, off_y + DOS_H * sy - 14, anchor="sw", fill="#ffffff", font=("Consolas", 8),
            text=(f"L{self.world.level.level_id} t={self.world.tick_count} "
                  f"pos=({p.x},{p.y}) v=({p.vx},{p.vy}) g={int(p.on_ground)} "
                  f"spr={p.spr_num & 0x1FFF} anim={p.current_anim_num} noj={p.nojump_counter}")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Prehistorik 2 gameplay reverse-engineering runtime.")
    parser.add_argument("game_data", nargs="?", default="game_data", help="Folder containing original PRE2 game data")
    parser.add_argument("--level", type=int, default=1, help="1-based level number/ID index, default: 1")
    parser.add_argument("--scale", type=int, default=3, help="Integer nearest-neighbour window scale")
    parser.add_argument("--backend", choices=("auto", "pygame", "tk"), default="auto",
                        help="Rendering backend: pygame/SDL2 when available, or the original Tk/Pillow path")
    parser.add_argument("--fps", type=int, default=60, help="Target FPS for pygame interpolation mode")
    parser.add_argument("--audio-debug", action="store_true", help="Print pygame mixer/SFX diagnostics to stderr")
    args = parser.parse_args(argv)

    project_dir = Path(__file__).resolve().parent.parent
    data_dir = Path(args.game_data)
    if not data_dir.is_absolute():
        data_dir = project_dir / data_dir
    level_index = max(0, args.level - 1)
    if args.backend in ("auto", "pygame"):
        try:
            from runtime.pygame_backend import run_pygame_app
            return run_pygame_app(project_dir, data_dir, level_index, args.scale, args.fps, audio_debug=args.audio_debug)
        except ImportError as exc:
            if args.backend == "pygame":
                raise
            print(f"pygame backend unavailable ({exc}); falling back to Tk/Pillow", file=sys.stderr)
        except Exception as exc:
            if args.backend == "pygame":
                raise
            # If the accelerated backend fails during display initialisation, keep
            # the old path usable rather than refusing to launch.
            print(f"pygame backend failed ({exc}); falling back to Tk/Pillow", file=sys.stderr)

    try:
        app = GameApp(project_dir, data_dir, level_index, args.scale, audio_debug=args.audio_debug)
    except Exception as exc:
        messagebox.showerror("run_game failed to start", str(exc))
        raise
    app.mainloop()
    return 0

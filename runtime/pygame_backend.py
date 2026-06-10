from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image

from pre2lib.sprites import render_sprite_image
CHUNK_TILES = 16
CHUNK_PX = CHUNK_TILES * 16

from runtime.game import (
    DOS_H,
    DOS_W,
    MAP_MARKER_Y,
    MODE_LETTER_ADV,
    MODE_TEXT_Y1,
    MODE_TEXT_Y2,
    PLAY_H,
    TICK_HZ,
    TILE,
    LEVEL_IDS,
    InputState,
    RuntimeWorld,
    COS_TBL,
    SIN_TBL,
    _s8,
    _BLINK_BLACK,
    _BLINK_WHITE,
)


class PygameSurfaceRenderer:
    """Direct pygame/SDL2 renderer for the PRE2 runtime.

    The Tk backend composes every frame through Pillow and then uploads a resized
    PhotoImage to Tk.  This renderer keeps gameplay logic unchanged but converts
    decoded assets to pygame Surfaces once and blits them directly into a
    320x200 logical display surface.  With pygame.SCALED the final enlargement is
    handled by SDL2's renderer instead of a per-frame Pillow resize.
    """

    def __init__(self, world: RuntimeWorld, pygame_mod: Any) -> None:
        self.world = world
        self.pg = pygame_mod
        self.frame = self.pg.Surface((DOS_W, DOS_H)).convert()
        self._asset_generation: tuple[int, int] | None = None
        self._bg_surface: Any | None = None
        self._map_surface: Any | None = None
        self._intro_surface_key: int | None = None
        self._intro_surface: Any | None = None
        self._motif_tiled_surface: Any | None = None
        self._mode_letter_surfaces: dict[int, Any] = {}
        self._panel_bg_surface: Any | None = None
        self._panel_glyph_surfaces: dict[int, Any] = {}
        self._number_glyph_surfaces: dict[int, Any] = {}
        self._tile_surfaces: dict[tuple[int, int], Any] = {}
        self._front_surfaces: dict[int, Any] = {}
        self._sprite_surfaces: dict[tuple[int, int, bool, tuple[int, int, int] | None], Any] = {}
        self._tilemap_version = -1
        self._tile_chunks: dict[tuple[int, int, int], Any] = {}
        self._front_chunks: dict[tuple[int, int], Any] = {}

    def invalidate(self) -> None:
        self._asset_generation = None
        self._bg_surface = None
        self._map_surface = None
        self._intro_surface_key = None
        self._intro_surface = None
        self._motif_tiled_surface = None
        self._mode_letter_surfaces.clear()
        self._panel_bg_surface = None
        self._panel_glyph_surfaces.clear()
        self._number_glyph_surfaces.clear()
        self._tile_surfaces.clear()
        self._front_surfaces.clear()
        self._sprite_surfaces.clear()
        self._tilemap_version = -1
        self._tile_chunks.clear()
        self._front_chunks.clear()

    def _surface_from_pil(self, img: Image.Image, *, alpha: bool | None = None) -> Any:
        if alpha is None:
            alpha = img.mode == "RGBA"
        mode = "RGBA" if alpha else "RGB"
        if img.mode != mode:
            img = img.convert(mode)
        # frombuffer can reference the source bytes; copy before converting so the
        # pygame surface is independent from the temporary Python buffer.
        surf = self.pg.image.frombuffer(img.tobytes(), img.size, mode).copy()
        return surf.convert_alpha() if alpha else surf.convert()

    def _sync_assets(self) -> None:
        # Include the palette generation so the light/sun fade (which mutates the
        # active level palette each tick) re-bakes the cached colour surfaces.
        generation = (self.world.level_index, id(self.world.level),
                      getattr(self.world, "_palette_gen", 0))
        if generation == self._asset_generation:
            return
        self._asset_generation = generation
        self._tile_surfaces.clear()
        self._front_surfaces.clear()
        self._sprite_surfaces.clear()
        self._tilemap_version = -1
        self._tile_chunks.clear()
        self._front_chunks.clear()
        self._bg_surface = self._surface_from_pil(self.world._bg_image, alpha=False)
        self._panel_bg_surface = (
            self._surface_from_pil(self.world._panel_bg, alpha=True)
            if self.world._panel_bg is not None else None
        )
        self._panel_glyph_surfaces = {
            k: self._surface_from_pil(v, alpha=True)
            for k, v in getattr(self.world, "_panel_glyphs", {}).items()
        }
        self._number_glyph_surfaces = {
            k: self._surface_from_pil(v, alpha=True)
            for k, v in getattr(self.world, "_number_glyphs", {}).items()
        }
        self._map_surface = None

    def _tile_surface_for_phase(self, tile_num: int, phase: int) -> Any:
        resolved = self.world.level.tile_for_animation_frame(tile_num, phase)
        key = (resolved, phase)
        surf = self._tile_surfaces.get(key)
        if surf is None:
            # Temporarily ask RuntimeWorld to build the correct PIL tile for this
            # resolved frame, then cache the pygame surface permanently.
            pixels = self.world.tile_cache.get(resolved)
            if pixels is None:
                from pre2lib.formats import decode_planar_tile, resolve_tile_bytes

                pixels = decode_planar_tile(resolve_tile_bytes(self.world.level, self.world.union_tiles, resolved))
                self.world.tile_cache[resolved] = pixels
            img = self.world._pixels_to_rgba(pixels)
            surf = self._surface_from_pil(img, alpha=True)
            self._tile_surfaces[key] = surf
        return surf

    def _tile_surface(self, tile_num: int) -> Any:
        return self._tile_surface_for_phase(tile_num, (self.world.tick_count // 6) % 3)

    def _front_surface(self, tile_num: int) -> Any:
        surf = self._front_surfaces.get(tile_num)
        if surf is None:
            surf = self._surface_from_pil(self.world._front_image(tile_num), alpha=True)
            self._front_surfaces[tile_num] = surf
        return surf


    def _discard_dirty_chunks(self) -> None:
        w = self.world
        version = getattr(w, "_tilemap_version", 0)
        if version == self._tilemap_version:
            return
        dirty_tiles = list(dict.fromkeys(getattr(w, "_tile_dirty_positions", [])))
        if not dirty_tiles or len(dirty_tiles) > 2048:
            self._tile_chunks.clear()
            self._front_chunks.clear()
        else:
            dirty_chunks = {(tx // CHUNK_TILES, ty // CHUNK_TILES) for tx, ty in dirty_tiles}
            for key in list(self._tile_chunks):
                _phase, cx, cy = key
                if (cx, cy) in dirty_chunks:
                    del self._tile_chunks[key]
            for key in list(self._front_chunks):
                if key in dirty_chunks:
                    del self._front_chunks[key]
        if hasattr(w, "_tile_dirty_positions"):
            w._tile_dirty_positions.clear()
        self._tilemap_version = version

    def _build_tile_chunk(self, phase: int, cx: int, cy: int) -> Any:
        w = self.world
        surf = self.pg.Surface((CHUNK_PX, CHUNK_PX), self.pg.SRCALPHA).convert_alpha()
        surf.fill((0, 0, 0, 0))
        tx0 = cx * CHUNK_TILES
        ty0 = cy * CHUNK_TILES
        tx1 = min(w.level.width_tiles, tx0 + CHUNK_TILES)
        ty1 = min(w.level.height_tiles, ty0 + CHUNK_TILES)
        for ty in range(ty0, ty1):
            row = ty * w.level.width_tiles
            dy = (ty - ty0) * TILE
            for tx in range(tx0, tx1):
                tile_num = w.runtime_tilemap[row + tx]
                surf.blit(self._tile_surface_for_phase(tile_num, phase), ((tx - tx0) * TILE, dy))
        return surf

    def _tile_chunk(self, phase: int, cx: int, cy: int) -> Any:
        key = (phase, cx, cy)
        surf = self._tile_chunks.get(key)
        if surf is None:
            surf = self._build_tile_chunk(phase, cx, cy)
            self._tile_chunks[key] = surf
        return surf

    def _build_front_chunk(self, cx: int, cy: int) -> Any:
        w = self.world
        surf = self.pg.Surface((CHUNK_PX, CHUNK_PX), self.pg.SRCALPHA).convert_alpha()
        surf.fill((0, 0, 0, 0))
        tx0 = cx * CHUNK_TILES
        ty0 = cy * CHUNK_TILES
        tx1 = min(w.level.width_tiles, tx0 + CHUNK_TILES)
        ty1 = min(w.level.height_tiles, ty0 + CHUNK_TILES)
        for ty in range(ty0, ty1):
            row = ty * w.level.width_tiles
            dy = (ty - ty0) * TILE
            for tx in range(tx0, tx1):
                tile_num = w.runtime_tilemap[row + tx]
                if w.level.tile_attributes2[tile_num] & 0x40:
                    surf.blit(self._front_surface(tile_num), ((tx - tx0) * TILE, dy))
        return surf

    def _front_chunk(self, cx: int, cy: int) -> Any:
        key = (cx, cy)
        surf = self._front_chunks.get(key)
        if surf is None:
            surf = self._build_front_chunk(cx, cy)
            self._front_chunks[key] = surf
        return surf

    def _draw_tile_chunks(self, phase: int, cam_x: int, cam_y: int) -> None:
        cx0 = max(0, cam_x // CHUNK_PX)
        cy0 = max(0, cam_y // CHUNK_PX)
        cx1 = min((self.world.level.width_tiles - 1) // CHUNK_TILES, (cam_x + DOS_W) // CHUNK_PX)
        cy1 = min((self.world.level.height_tiles - 1) // CHUNK_TILES, (cam_y + PLAY_H) // CHUNK_PX)
        for cy in range(cy0, cy1 + 1):
            y = cy * CHUNK_PX - cam_y
            for cx in range(cx0, cx1 + 1):
                self.frame.blit(self._tile_chunk(phase, cx, cy), (cx * CHUNK_PX - cam_x, y))

    def _draw_front_chunks(self, cam_x: int, cam_y: int) -> None:
        cx0 = max(0, cam_x // CHUNK_PX)
        cy0 = max(0, cam_y // CHUNK_PX)
        cx1 = min((self.world.level.width_tiles - 1) // CHUNK_TILES, (cam_x + DOS_W) // CHUNK_PX)
        cy1 = min((self.world.level.height_tiles - 1) // CHUNK_TILES, (cam_y + PLAY_H) // CHUNK_PX)
        for cy in range(cy0, cy1 + 1):
            y = cy * CHUNK_PX - cam_y
            for cx in range(cx0, cx1 + 1):
                self.frame.blit(self._front_chunk(cx, cy), (cx * CHUNK_PX - cam_x, y))

    def _sprite_surface(self, sprite_num: int, tint: tuple[int, int, int] | None = None) -> Any:
        flip_x = (sprite_num & 0x8000) != 0
        base_num = sprite_num & 0x1FFF
        key = (base_num, self.world.level_index, flip_x, tint)
        surf = self._sprite_surfaces.get(key)
        if surf is None:
            img = render_sprite_image(
                self.world.sprites_blob,
                self.world.sprite_tables,
                self.world.palette,
                base_num,
                transparent_zero=True,
            )
            if flip_x:
                img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if tint is not None:
                solid = Image.new("RGBA", img.size, (*tint, 0))
                solid.putalpha(img.getchannel("A"))
                img = solid
            surf = self._surface_from_pil(img, alpha=True)
            self._sprite_surfaces[key] = surf
        return surf

    def _draw_sprite(
        self,
        target: Any,
        sprite_num: int,
        world_x: int,
        world_y: int,
        *,
        tint: tuple[int, int, int] | None = None,
        camera: tuple[int, int] | None = None,
    ) -> None:
        cam_x, cam_y = camera if camera is not None else (self.world.camera_x, self.world.camera_y)
        flip_x = (sprite_num & 0x8000) != 0
        base_num = sprite_num & 0x1FFF
        surf = self._sprite_surface(sprite_num, tint)
        origin_x, origin_y = self.world.sprite_tables.origin(base_num)
        if flip_x:
            origin_x = surf.get_width() - 1 - origin_x
        sx = int(world_x - cam_x - origin_x)
        sy = int(world_y - cam_y - origin_y)
        target.blit(surf, (sx, sy))

    def _draw_sprite_topleft(self, target: Any, sprite_num: int, x: int, y: int) -> None:
        target.blit(self._sprite_surface(sprite_num, None), (x, y))

    @staticmethod
    def _complete_offset_xy(offset: int) -> tuple[int, int]:
        return (offset * 8) % DOS_W, (offset * 8) // DOS_W

    def _draw_number_glyph(self, target: Any, offset: int, digit: int) -> None:
        glyph = self._number_glyph_surfaces.get(digit)
        if glyph is None:
            return
        target.blit(glyph, self._complete_offset_xy(offset))

    def _draw_letter_spr(self, target: Any, offset: int, code: int) -> None:
        x, y = self._complete_offset_xy(offset)
        self._draw_sprite_topleft(target, 241 + code, x, y)

    def _draw_string2(self, target: Any, offset: int, text: str) -> None:
        for ch in text:
            if ch != " ":
                self._draw_letter_spr(target, offset, ord(ch) - 0x41)
            offset += 2

    def _complete_draw_score(self, target: Any) -> None:
        w = self.world
        self._draw_string2(target, 0x230, "SCORE")
        score = w.score * 10
        for i in range(7):
            digit = score % 10
            score //= 10
            self._draw_number_glyph(target, 0x23C + (6 - i) * 2, digit)
        self._draw_string2(target, 0x410, "LEVEL COMPLETED")
        percentage = 100
        total = w.level_complete_secrets + w.level_complete_bonuses
        if total != 0:
            percentage = (w.level_current_secrets * 100) // total
        for i in range(3):
            digit = percentage % 10
            percentage //= 10
            self._draw_number_glyph(target, 0x430 + (2 - i) * 2, digit)
            if percentage == 0:
                break
        self._draw_letter_spr(target, 0x436, 0x1A)

    def _draw_panel_number(self, target: Any, offset: int, num: int) -> None:
        glyph = self._panel_glyph_surfaces.get(num)
        if glyph is None:
            return
        x = (offset % 40) * 8
        y = offset // 40
        target.blit(glyph, (x, y))

    def _draw_hud(self, target: Any) -> None:
        self.pg.draw.rect(target, (0, 0, 0), (0, PLAY_H, DOS_W, DOS_H - PLAY_H))
        if self._panel_bg_surface is None:
            return
        target.blit(self._panel_bg_surface, (0, PLAY_H))
        p = self.world.player
        self._draw_panel_number(target, 0x1CED, min(p.lives, 9))
        score = self.world.score * 10
        for i in range(7):
            self._draw_panel_number(target, 0x1CF1 + (6 - i) * 2, score % 10)
            score //= 10
        for i in range(max(0, p.energy)):
            self._draw_panel_number(target, 0x1D01 + i * 2, 10)
        bonus_pos = (0x1C91, 0x1BF2, 0x1CE3, 0x1C6C, 0x1C1D)
        for i in range(5):
            if (p.bonus_letters_mask >> i) & 1:
                self._draw_panel_number(target, bonus_pos[i], 12 + i)
        # Boss energy pips: screen-fixed, mirrors game.py _draw_hud.
        for i in range(getattr(self.world, "_boss_energy_count", 0)):
            self._draw_sprite(target, 0x135, 8 + i * 5, 170, camera=(0, 0))

    def _draw_transition(self, target: Any, alpha: float) -> None:
        w = self.world
        phase = w._trans_phase
        if phase == 0:
            return
        t = w._trans_t + alpha
        if phase == 1:
            h = int(min(PLAY_H // 2, (t / 12.0) * (PLAY_H // 2)))
            self.pg.draw.rect(target, (0, 0, 0), (0, 0, DOS_W, h))
            self.pg.draw.rect(target, (0, 0, 0), (0, PLAY_H - h, DOS_W, h + 1))
        else:
            left = int(max(0, (1.0 - t / 12.0) * (DOS_W // 2)))
            self.pg.draw.rect(target, (0, 0, 0), (0, 0, left, PLAY_H))
            self.pg.draw.rect(target, (0, 0, 0), (DOS_W - left, 0, left, PLAY_H))

    def _render_intro(self, alpha: float | None = None) -> Any:
        img = self.world._render_intro()
        key = id(img)  # copy id changes; use original frame from state instead if available
        intro = self.world._intro
        if intro is not None:
            stage = int(intro["stage"])
            frame = intro["stages"][stage].get("frame")
            key = id(frame)
            if self._intro_surface_key != key:
                self._intro_surface = self._surface_from_pil(frame if isinstance(frame, Image.Image) else img, alpha=False)
                self._intro_surface_key = key
        elif self._intro_surface_key != key:
            self._intro_surface = self._surface_from_pil(img, alpha=False)
            self._intro_surface_key = key
        self.frame.fill((0, 0, 0))
        if self._intro_surface is not None:
            self.frame.blit(self._intro_surface, (0, 0))
        return self.frame

    def _render_mode_select(self, alpha: float | None = None) -> Any:
        w = self.world
        m = w._mode_select
        a = alpha or 0.0
        scroll = m["pscroll"] + (m["scroll"] - m["pscroll"]) * a
        self.frame.fill((0, 0, 0))
        tiled = getattr(self, "_motif_tiled_surface", None)
        if tiled is None and getattr(w, "_motif_tiled", None) is not None:
            tiled = self._motif_tiled_surface = self._surface_from_pil(w._motif_tiled, alpha=False)
        if tiled is not None:
            sx = int(scroll) % DOS_W
            sy = int(scroll) % DOS_H
            self.frame.blit(tiled, (0, 0), (sx, sy, DOS_W, DOS_H))
        sel = "EXPERT" if m["sel"] == 1 else "BEGINNER"
        self._mode_draw_string("MODE", DOS_W // 2, MODE_TEXT_Y1)
        self._mode_draw_string(sel, DOS_W // 2, MODE_TEXT_Y2)
        return self.frame

    def _mode_letter_surface(self, code: int) -> Any:
        cache = self._mode_letter_surfaces
        s = cache.get(code)
        if s is None:
            img = self.world._mode_letter_img(code)
            s = cache[code] = self._surface_from_pil(img, alpha=True)
        return s

    def _mode_draw_string(self, text: str, cx: int, y: int) -> None:
        x = cx - len(text) * MODE_LETTER_ADV // 2
        for ch in text:
            if ch != " ":
                self.frame.blit(self._mode_letter_surface(ord(ch) - 0x41), (x, y))
            x += MODE_LETTER_ADV

    def _render_map_intro(self, alpha: float | None = None) -> Any:
        self.frame.fill((0, 0, 0))
        m = self.world._map_intro
        a = alpha or 0.0
        draw = int(round(m["pdraw"] + (m["draw"] - m["pdraw"]) * a))
        if self.world._map_image is not None:
            if self._map_surface is None:
                self._map_surface = self._surface_from_pil(self.world._map_image, alpha=False)
            self.frame.blit(self._map_surface, (draw, 0))
        # "You are here" marker: the player sprite standing on the level's spot.
        mx = m["marker_x"] + draw
        if -16 < mx < DOS_W + 16 and (self.world.tick_count & 4):
            self._draw_sprite(self.frame, m["marker_spr"], mx, MAP_MARKER_Y, camera=(0, 0))
        return self.frame

    def _draw_orbs(self, target: Any, cam_x: int, cam_y: int) -> None:
        # One-frame white spider-web/orb trail pixels from blues orb_tbl[20].
        # Do not mutate here: render interpolation can draw more than once per
        # logic tick, while the runtime clears and regenerates the particles.
        white = self.world.rgb[15] if getattr(self.world, "rgb", None) else (255, 255, 255)
        for orb in getattr(self.world, "orb_states", []):
            if not orb.active:
                continue
            a = orb.index_tbl & 0xFF
            x = orb.x + (((_s8(COS_TBL[a]) >> 2) * orb.radius) >> 4) - int(cam_x)
            y = orb.y + (((_s8(SIN_TBL[a]) >> 2) * orb.radius) >> 4) - int(cam_y)
            if 0 <= x < DOS_W and 0 <= y < PLAY_H:
                target.set_at((x, y), white)

    def _render_complete(self, alpha: float | None = None) -> Any:
        self.frame.fill((0, 0, 0))
        w = self.world
        interp = alpha is not None
        a = alpha or 0.0

        def pos(obj):
            if interp and obj.iact:
                return w._lerp(obj.ipx, obj.x, a), w._lerp(obj.ipy, obj.y, a)
            return obj.x, obj.y

        # blues level_draw_objects draws high->low, so higher slots are behind:
        # food (55-74) falls behind the cauldron (2-4); the player (1) is in front.
        for slot in range(55, 75):
            obj = w.runtime_objects[slot]
            if obj.active:
                ox, oy = pos(obj)
                self._draw_sprite(self.frame, obj.spr_num, ox, oy, camera=(0, 0))
        for slot in (2, 3, 4):
            obj = w.runtime_objects[slot]
            if obj.active and -64 < obj.x < DOS_W + 64:
                ox, oy = pos(obj)
                self._draw_sprite(self.frame, obj.spr_num, ox, oy, camera=(0, 0))
        ppx = w._lerp(w.player.ipx, w.player.x, a) if interp else w.player.x
        ppy = w._lerp(w.player.ipy, w.player.y, a) if interp else w.player.y
        self._draw_sprite(self.frame, w._player_sprite_num(), ppx, ppy, camera=(0, 0))
        self._complete_draw_score(self.frame)
        return self.frame

    def render(self, alpha: float | None = None) -> Any:
        self._sync_assets()
        w = self.world
        if w._the_end is not None:
            surf = self._surface_from_pil(w.render_frame(alpha=alpha), alpha=False)
            self.frame.blit(surf, (0, 0))
            return self.frame
        if w._intro is not None:
            return self._render_intro(alpha)
        if w._mode_select is not None:
            return self._render_mode_select(alpha)
        if w._map_intro is not None:
            return self._render_map_intro(alpha)
        if w._complete is not None:
            return self._render_complete(alpha)

        interp = alpha is not None
        a = alpha or 0.0
        cam_x = w._lerp(w._icam_x, w.camera_x, a) if interp else w.camera_x
        cam_y = w._lerp(w._icam_y, w.camera_y, a) if interp else w.camera_y
        sc = w.player.shake_screen_counter
        if sc > 1:
            cam_y += sc if (w.tick_count & 1) else -sc
        w._last_cam = (cam_x, cam_y)

        if self._bg_surface is not None:
            self.frame.blit(self._bg_surface, (0, 0))
        else:
            self.frame.fill((0, 0, 0))

        self._discard_dirty_chunks()
        phase = (w.tick_count // 6) % 3
        self._draw_tile_chunks(phase, cam_x, cam_y)

        for platform in w.platforms:
            runtime_num = w.sprite_resolver.platform_sprite(w.level, platform.sprite_num_raw)
            if runtime_num is not None and abs(platform.x - cam_x) < DOS_W + 120 and abs(platform.y - cam_y) < DOS_H + 120:
                px = w._lerp(platform.prev_x, platform.x, a) if interp else platform.x
                py = w._lerp(platform.prev_y, platform.y, a) if interp else platform.y
                self._draw_sprite(self.frame, runtime_num, px, py, camera=(cam_x, cam_y))

        self._draw_orbs(self.frame, int(cam_x), int(cam_y))

        # DOS level_draw_objects iterates slots HIGH -> LOW: higher slots are BEHIND
        # (gorilla parts 103-107 stack with 103 in front; the player draws over all
        # objects except the club/wing overlay slot 0, the top-most sprite).
        def draw_object(obj):
            if not (obj.active and abs(obj.x - cam_x) < DOS_W + 160 and abs(obj.y - cam_y) < DOS_H + 160):
                return
            tint = None
            if (11 <= obj.slot <= 22 and obj.hit_flash > 0) or (obj.spr_num & 0x4000):
                tint = _BLINK_WHITE  # hit enemy/boss flashes white
            elif 23 <= obj.slot <= 54 and obj.ttl > 0 and (w.tick_count & 1):
                tint = _BLINK_BLACK
            same = obj.iact and obj.iref == (obj.ref_index if obj.ref_index is not None else -1)
            if interp and same:
                ox = w._lerp(obj.ipx, obj.x, a)
                oy = w._lerp(obj.ipy, obj.y, a)
            else:
                ox, oy = obj.x, obj.y
            self._draw_sprite(self.frame, obj.spr_num, ox, oy, tint=tint, camera=(cam_x, cam_y))

        for slot in range(len(w.runtime_objects) - 1, 1, -1):
            draw_object(w.runtime_objects[slot])

        player_tint = _BLINK_BLACK if (w.player.hit_counter > 0 and (w.tick_count & 1)) else None
        ppx = w._lerp(w.player.ipx, w.player.x, a) if interp else w.player.x
        ppy = w._lerp(w.player.ipy, w.player.y, a) if interp else w.player.y
        self._draw_sprite(self.frame, w._player_sprite_num(), ppx, ppy, tint=player_tint, camera=(cam_x, cam_y))
        # Club / glider-wing overlay (slot 0) draws on top of the player.
        draw_object(w.runtime_objects[0])

        self._draw_front_chunks(cam_x, cam_y)

        self._draw_transition(self.frame, a)
        self._draw_hud(self.frame)
        if w._wipe is not None:
            self._draw_wipe(self.frame, a)
        return self.frame

    def _draw_wipe(self, target: Any, alpha: float) -> None:
        """Level-entry curtain open + level-exit iris close (mirrors
        RuntimeWorld._draw_wipe)."""
        w = self.world._wipe
        if w is None:
            return
        f = min(1.0, (w["t"] + alpha) / w["dur"])
        if w["kind"] == "open":
            revealed = int(f * DOS_W)
            left = (DOS_W - revealed) // 2
            self.pg.draw.rect(target, (0, 0, 0), (0, 0, left, DOS_H))
            self.pg.draw.rect(target, (0, 0, 0), (DOS_W - left, 0, left, DOS_H))
        else:  # iris close on (cx, cy): visible circle shrinks to nothing
            cx, cy = w["cx"], w["cy"]
            max_r = int(max(
                ((cx - dx) ** 2 + (cy - dy) ** 2) ** 0.5
                for dx in (0, DOS_W) for dy in (0, DOS_H)
            ))
            r = int((1.0 - f) * max_r)
            overlay = self.pg.Surface((DOS_W, DOS_H), self.pg.SRCALPHA)
            overlay.fill((0, 0, 0, 255))
            if r > 0:
                self.pg.draw.circle(overlay, (0, 0, 0, 0), (cx, cy), r)
            target.blit(overlay, (0, 0))


class PygameGameApp:
    """Standalone pygame runtime with a high-resolution developer UI overlay.

    The game itself is still rendered as a faithful 320x200 DOS framebuffer.
    Unlike the first pygame backend, that framebuffer is now scaled into a real
    window surface and all diagnostic/menu UI is drawn afterwards at native
    window resolution, so FPS/TPS and controls stay readable.
    """

    _CAPS = [30, 50, 60, 75, 120, 144, 0]
    _TABS = ("Develop", "View", "Audio", "Help")

    def __init__(self, project_dir: Path, data_dir: Path, level_index: int = 0, scale: int = 3, target_fps: int = 60, *, audio_debug: bool = False) -> None:
        import pygame

        self.pg = pygame
        self.project_dir = project_dir
        self.data_dir = data_dir
        self.scale = max(1, int(scale))
        self.target_fps = target_fps
        self.interpolate = False
        self.show_fps = False
        self.show_debug = False
        self.keep_aspect = True
        self.integer_scale = True
        self.menu_open = False
        self.menu_tab = 0
        self.menu_item = 0
        self.input = InputState()
        self.world = RuntimeWorld(project_dir, data_dir, level_index, audio_debug=audio_debug)
        self.renderer: PygameSurfaceRenderer | None = None
        self.last_tick = time.perf_counter()
        self.accum = 0.0
        self._fps_t0 = time.perf_counter()
        self._fps_frames = 0
        self._fps_ticks0 = 0
        self._fps_text = ""
        self._font: Any | None = None
        self._font_bold: Any | None = None
        self._font_small: Any | None = None
        self._game_rect = (0, 0, DOS_W * self.scale, DOS_H * self.scale)
        self._running = True

    def _init_display(self) -> None:
        pg = self.pg
        pg.init()
        pg.display.set_caption("Prehistorik 2 runtime RE - pygame/SDL2")
        # Do not use pygame.SCALED here. It scales every draw operation,
        # including fonts, which made FPS/TPS and the developer controls look
        # like tiny DOS pixels. We scale only the game framebuffer, then draw UI
        # directly at window resolution.
        flags = pg.RESIZABLE | pg.DOUBLEBUF
        size = (DOS_W * self.scale, DOS_H * self.scale)
        try:
            self.screen = pg.display.set_mode(size, flags, vsync=1)
        except TypeError:
            self.screen = pg.display.set_mode(size, flags)
        self.renderer = PygameSurfaceRenderer(self.world, pg)
        self._font = self._make_font(16, bold=False)
        self._font_bold = self._make_font(16, bold=True)
        self._font_small = self._make_font(13, bold=False)

    def _make_font(self, size: int, *, bold: bool = False) -> Any:
        pg = self.pg
        for name in ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"):
            try:
                font = pg.font.SysFont(name, size, bold=bold)
                if font is not None:
                    return font
            except Exception:
                pass
        return pg.font.Font(None, size + 4)

    def _event_to_input(self, event: Any, value: bool) -> None:
        key = event.key
        pg = self.pg
        mapping = {
            pg.K_LEFT: "left", pg.K_a: "left",
            pg.K_RIGHT: "right", pg.K_d: "right",
            pg.K_UP: "up", pg.K_w: "up",
            pg.K_DOWN: "down", pg.K_s: "down",
            pg.K_SPACE: "fire", pg.K_LCTRL: "fire", pg.K_RCTRL: "fire",
            pg.K_RETURN: "action",
        }
        attr = mapping.get(key)
        if attr is not None:
            setattr(self.input, attr, value)

    def _load_level(self, index: int) -> None:
        self.world.load_level(index % len(LEVEL_IDS))
        if self.renderer is not None:
            self.renderer.invalidate()

    def _restart_level(self) -> None:
        self._load_level(self.world.level_index)

    def _change_level(self, delta: int) -> None:
        self._load_level(self.world.level_index + delta)

    def _set_difficulty(self, expert: bool) -> None:
        self.world.expert = bool(expert)
        self._restart_level()

    def _toggle_camera_mode(self) -> None:
        new_mode = "vanilla" if self.world.camera_mode != "vanilla" else "smooth"
        self.world.set_camera_mode(new_mode)

    def _toggle_music(self) -> None:
        self.world.sound.music_enabled = not self.world.sound.music_enabled
        if self.world.sound.music_enabled:
            self.world.sound.resume_music()
        else:
            self.world.sound.stop_music()

    def _set_cap_relative(self, delta: int) -> None:
        caps = self._CAPS
        try:
            i = caps.index(self.target_fps)
        except ValueError:
            caps.append(self.target_fps)
            caps.sort(key=lambda v: 9999 if v == 0 else v)
            i = caps.index(self.target_fps)
        self.target_fps = caps[(i + delta) % len(caps)]

    def _trigger_level_end(self) -> None:
        w = self.world
        if w._complete is None and not w.player.dying and w._trans_phase == 0:
            w._start_level_complete()

    def _menu_items(self) -> list[dict[str, Any]]:
        tab = self._TABS[self.menu_tab]
        if tab == "Develop":
            return [
                {"label": "Difficulty", "value": "Expert" if self.world.expert else "Beginner", "activate": lambda: self._set_difficulty(not self.world.expert)},
                {"label": "Level", "value": f"{self.world.level_index + 1}/{len(LEVEL_IDS)}  id={self.world.level.level_id}", "adjust": self._change_level, "activate": lambda: self._change_level(1)},
                {"label": "God mode", "value": "On" if self.world.god_mode else "Off", "activate": lambda: setattr(self.world, "god_mode", not self.world.god_mode)},
                {"label": "Debug overlay", "value": "On" if self.show_debug else "Off", "activate": lambda: setattr(self, "show_debug", not self.show_debug)},
                {"label": "Restart level", "value": "R", "activate": self._restart_level},
                {"label": "Trigger level end", "value": "bonus screen", "activate": self._trigger_level_end},
            ]
        if tab == "View":
            cap = "Uncapped" if self.target_fps <= 0 else f"{self.target_fps} FPS"
            return [
                {"label": "Interpolation", "value": "On" if self.interpolate else "Off", "activate": lambda: setattr(self, "interpolate", not self.interpolate)},
                {"label": "Camera", "value": "Vanilla" if self.world.camera_mode == "vanilla" else "Smooth", "activate": self._toggle_camera_mode},
                {"label": "FPS / TPS overlay", "value": "On" if self.show_fps else "Off", "activate": lambda: setattr(self, "show_fps", not self.show_fps)},
                {"label": "Frame cap", "value": cap, "adjust": self._set_cap_relative, "activate": lambda: self._set_cap_relative(1)},
                {"label": "Keep aspect ratio", "value": "On" if self.keep_aspect else "Off", "activate": lambda: setattr(self, "keep_aspect", not self.keep_aspect)},
                {"label": "Integer scaling", "value": "On" if self.integer_scale else "Off", "activate": lambda: setattr(self, "integer_scale", not self.integer_scale)},
            ]
        if tab == "Audio":
            return [
                {"label": "Sound effects", "value": "On" if self.world.sound.sound_enabled else "Off", "activate": lambda: setattr(self.world.sound, "sound_enabled", not self.world.sound.sound_enabled)},
                {"label": "Music", "value": "On" if self.world.sound.music_enabled else "Off", "activate": self._toggle_music},
                {"label": "Test next SFX", "value": "F8", "activate": self.world.sound.play_next_test_sound},
            ]
        return [
            {"label": "F10 / M", "value": "open / close this menu"},
            {"label": "Left / Right", "value": "adjust selected item or switch tab"},
            {"label": "Up / Down", "value": "move selection"},
            {"label": "Enter / Space", "value": "activate selected item"},
            {"label": "F1", "value": "debug overlay"},
            {"label": "F2", "value": "FPS / TPS overlay"},
            {"label": "F3", "value": "interpolation"},
            {"label": "F4", "value": "smooth / vanilla camera"},
            {"label": "[ / ]", "value": "previous / next level"},
            {"label": "R", "value": "restart level"},
            {"label": "F8", "value": "test sound effect"},
            {"label": "Esc", "value": "close menu, then quit"},
        ]

    def _handle_menu_keydown(self, event: Any) -> bool:
        """Return True when a key was consumed by the menu."""
        pg = self.pg
        if event.key in (pg.K_F10, pg.K_m):
            self.menu_open = False
            return True
        if event.key == pg.K_ESCAPE:
            self.menu_open = False
            return True
        items = self._menu_items()
        if event.key in (pg.K_UP, pg.K_w):
            self.menu_item = (self.menu_item - 1) % max(1, len(items))
            return True
        if event.key in (pg.K_DOWN, pg.K_s):
            self.menu_item = (self.menu_item + 1) % max(1, len(items))
            return True
        if event.key in (pg.K_PAGEUP, pg.K_q):
            self.menu_tab = (self.menu_tab - 1) % len(self._TABS)
            self.menu_item = 0
            return True
        if event.key in (pg.K_PAGEDOWN, pg.K_e, pg.K_TAB):
            self.menu_tab = (self.menu_tab + 1) % len(self._TABS)
            self.menu_item = 0
            return True
        if event.key in (pg.K_LEFT, pg.K_RIGHT):
            direction = -1 if event.key == pg.K_LEFT else 1
            item = items[self.menu_item % max(1, len(items))] if items else {}
            adjust = item.get("adjust")
            if adjust is not None:
                adjust(direction)
            else:
                self.menu_tab = (self.menu_tab + direction) % len(self._TABS)
                self.menu_item = 0
            return True
        if event.key in (pg.K_RETURN, pg.K_SPACE):
            item = items[self.menu_item % max(1, len(items))] if items else {}
            action = item.get("activate")
            if action is not None:
                action()
            return True
        return False

    def _handle_keydown(self, event: Any) -> None:
        pg = self.pg
        if self.menu_open and self._handle_menu_keydown(event):
            return
        if event.key in (pg.K_F10, pg.K_m):
            self.menu_open = True
            return
        self._event_to_input(event, True)
        if event.key == pg.K_ESCAPE:
            self._running = False
        elif event.key == pg.K_F1:
            self.show_debug = not self.show_debug
        elif event.key == pg.K_F2:
            self.show_fps = not self.show_fps
        elif event.key == pg.K_F3:
            self.interpolate = not self.interpolate
        elif event.key == pg.K_F4:
            self._toggle_camera_mode()
        elif event.key == pg.K_F8:
            self.world.sound.play_next_test_sound()
        elif event.key == pg.K_r:
            self._restart_level()
        elif event.key == pg.K_LEFTBRACKET:
            self._change_level(-1)
        elif event.key == pg.K_RIGHTBRACKET:
            self._change_level(1)

    def _handle_events(self) -> None:
        pg = self.pg
        for event in pg.event.get():
            if event.type == pg.QUIT:
                self._running = False
            elif event.type == pg.KEYDOWN:
                self._handle_keydown(event)
            elif event.type == pg.KEYUP:
                # Do not let menu navigation release arrow keys cancel movement
                # unless they were actually used as gameplay input before.
                self._event_to_input(event, False)

    def _display_rect(self) -> tuple[int, int, int, int]:
        win_w, win_h = self.screen.get_size()
        if not self.keep_aspect:
            return 0, 0, max(1, win_w), max(1, win_h)
        if self.integer_scale:
            scale = max(1, min(win_w // DOS_W, win_h // DOS_H))
            out_w = DOS_W * scale
            out_h = DOS_H * scale
        else:
            scale = min(win_w / DOS_W, win_h / DOS_H)
            out_w = max(1, int(DOS_W * scale))
            out_h = max(1, int(DOS_H * scale))
        return (win_w - out_w) // 2, (win_h - out_h) // 2, out_w, out_h

    def _draw_text(self, text: str, x: int, y: int, *, font: Any | None = None, color: tuple[int, int, int] = (230, 255, 230), shadow: bool = True) -> int:
        font = font or self._font
        if font is None:
            return y
        if shadow:
            sh = font.render(text, True, (0, 0, 0))
            self.screen.blit(sh, (x + 1, y + 1))
        surf = font.render(text, True, color)
        self.screen.blit(surf, (x, y))
        return y + surf.get_height() + 2

    def _draw_runtime_overlay(self) -> None:
        off_x, off_y, out_w, out_h = self._game_rect
        sx = out_w / DOS_W
        sy = out_h / DOS_H
        y = off_y + 7
        if self.show_fps:
            y = self._draw_text(self._fps_text or "-- fps   -- tps", off_x + 8, y, font=self._font_bold, color=(80, 255, 110))
        if not self.show_debug:
            return
        p = self.world.player
        cam_x, cam_y = self.world._last_cam

        def scr(wx: float, wy: float) -> tuple[int, int]:
            return int(round(off_x + (wx - cam_x) * sx)), int(round(off_y + (wy - cam_y) * sy))

        px, py = scr(p.x, p.y)
        dx = 9 if p.vx > 0 else (-9 if p.vx < 0 else 0)
        probe_x, probe_y = scr(p.x + dx, p.y)
        _probe_top_x, probe_top_y = scr(p.x + dx, p.y - p.collision_h)
        tile_x, tile_y = scr((p.x >> 4) * 16, (p.y >> 4) * 16)
        line_w = max(1, int(round(min(sx, sy))))
        self.pg.draw.line(self.screen, (255, 0, 0), (px - int(4 * sx), py), (px + int(4 * sx), py), line_w)
        self.pg.draw.line(self.screen, (255, 0, 0), (px, py - int(4 * sy)), (px, py + int(4 * sy)), line_w)
        self.pg.draw.line(self.screen, (0, 255, 255), (probe_x, probe_y), (probe_x, probe_top_y), line_w)
        self.pg.draw.rect(self.screen, (255, 255, 0), (tile_x, tile_y, max(1, int(16 * sx)), max(1, int(16 * sy))), width=line_w)
        text = (f"L{self.world.level.level_id} t={self.world.tick_count} "
                f"pos=({p.x},{p.y}) v=({p.vx},{p.vy}) g={int(p.on_ground)} "
                f"spr={p.spr_num & 0x1FFF} anim={p.current_anim_num} noj={p.nojump_counter}")
        self._draw_text(text, off_x + 8, off_y + out_h - 22, font=self._font_small, color=(255, 255, 255))

    def _draw_menu(self) -> None:
        if not self.menu_open:
            # Small non-invasive hint. It is high-res now, so it does not blur.
            self._draw_text("F10 menu", 8, 6, font=self._font_small, color=(190, 210, 190))
            return
        pg = self.pg
        win_w, win_h = self.screen.get_size()
        panel_w = min(max(360, win_w - 80), 720)
        panel_h = min(max(260, win_h - 80), 430)
        x = (win_w - panel_w) // 2
        y = (win_h - panel_h) // 2
        panel = pg.Surface((panel_w, panel_h), pg.SRCALPHA)
        panel.fill((12, 14, 18, 230))
        self.screen.blit(panel, (x, y))
        pg.draw.rect(self.screen, (180, 180, 180), (x, y, panel_w, panel_h), width=1)
        title_y = y + 12
        self._draw_text("PRE2 pygame controls", x + 16, title_y, font=self._font_bold, color=(255, 255, 255))

        # Tabs.
        tab_x = x + 14
        tab_y = y + 42
        for i, tab in enumerate(self._TABS):
            label = f" {tab} "
            font = self._font_bold if i == self.menu_tab else self._font
            surf = font.render(label, True, (20, 20, 20) if i == self.menu_tab else (220, 220, 220))
            rect = surf.get_rect(topleft=(tab_x, tab_y))
            rect.inflate_ip(8, 6)
            pg.draw.rect(self.screen, (210, 220, 235) if i == self.menu_tab else (48, 54, 66), rect)
            pg.draw.rect(self.screen, (100, 110, 130), rect, width=1)
            self.screen.blit(surf, (tab_x + 4, tab_y + 3))
            tab_x += rect.width + 6

        items = self._menu_items()
        if items:
            self.menu_item %= len(items)
        row_y = y + 82
        row_h = 26
        for i, item in enumerate(items):
            selected = i == self.menu_item
            row_rect = (x + 16, row_y - 3, panel_w - 32, row_h)
            if selected:
                pg.draw.rect(self.screen, (56, 84, 120), row_rect)
            label = item.get("label", "")
            value = item.get("value", "")
            self._draw_text(label, x + 26, row_y, font=self._font_bold if selected else self._font, color=(255, 255, 255) if selected else (225, 225, 225), shadow=False)
            val_surf = (self._font or self.pg.font.Font(None, 20)).render(str(value), True, (190, 235, 255) if selected else (175, 195, 215))
            self.screen.blit(val_surf, (x + panel_w - 28 - val_surf.get_width(), row_y))
            row_y += row_h

        help1 = "Up/Down select   Left/Right adjust or switch tab   Enter activate"
        help2 = "F1 debug   F2 FPS   F3 interp   F4 camera   [/] level   R restart   F8 SFX   Esc close"
        self._draw_text(help1, x + 16, y + panel_h - 42, font=self._font_small, color=(210, 210, 210), shadow=False)
        self._draw_text(help2, x + 16, y + panel_h - 22, font=self._font_small, color=(210, 210, 210), shadow=False)

    def _present(self, alpha: float | None) -> None:
        assert self.renderer is not None
        frame = self.renderer.render(alpha)
        self._game_rect = self._display_rect()
        off_x, off_y, out_w, out_h = self._game_rect
        self.screen.fill((0, 0, 0))
        if (out_w, out_h) == (DOS_W, DOS_H):
            self.screen.blit(frame, (off_x, off_y))
        else:
            # Scale only the faithful game framebuffer. Text/menu overlays are
            # drawn after this at native window resolution.
            out = self.pg.transform.scale(frame, (out_w, out_h))
            self.screen.blit(out, (off_x, off_y))
        self._draw_runtime_overlay()
        self._draw_menu()
        self.pg.display.flip()

        self._fps_frames += 1
        dt = time.perf_counter() - self._fps_t0
        if dt >= 0.5:
            fps = self._fps_frames / dt
            tps = (self.world.tick_count - self._fps_ticks0) / dt
            backend = "pygame/SDL2"
            mode = "interp" if self.interpolate else "tick"
            cam = "vanilla-cam" if self.world.camera_mode == "vanilla" else "smooth-cam"
            self._fps_text = f"{fps:3.0f} fps   {tps:3.0f} tps   {backend}   {mode}   {cam}"
            self._fps_t0 = time.perf_counter()
            self._fps_frames = 0
            self._fps_ticks0 = self.world.tick_count

    def run(self) -> int:
        self._init_display()
        pg = self.pg
        clock = pg.time.Clock()
        step = 1.0 / TICK_HZ
        while self._running:
            frame_start = time.perf_counter()
            self._handle_events()
            self.accum += frame_start - self.last_tick
            self.last_tick = frame_start
            self.accum = min(self.accum, step * 5)
            ticked = False
            # Keep the menu responsive, but do not freeze gameplay behind it;
            # this mirrors normal debug-menu behaviour rather than a pause menu.
            while self.accum >= step:
                self.world.snapshot_prev()
                self.world.tick(self.input)
                self.accum -= step
                ticked = True
            if self.interpolate:
                self._present(self.accum / step)
                clock.tick(self.target_fps if self.target_fps > 0 else 0)
            elif ticked or self.menu_open or self.show_fps or self.show_debug:
                # UI overlays can change independently of game ticks, so refresh
                # them even while waiting for the next original 19 Hz tick.
                self._present(None)
                clock.tick(max(1, int(TICK_HZ * 2)))
            else:
                clock.tick(max(1, int(TICK_HZ * 2)))
        try:
            self.world.sound.stop_music()
        except Exception:
            pass
        pg.quit()
        return 0

def run_pygame_app(project_dir: Path, data_dir: Path, level_index: int = 0, scale: int = 3, target_fps: int = 60, *, audio_debug: bool = False) -> int:
    return PygameGameApp(project_dir, data_dir, level_index, scale, target_fps, audio_debug=audio_debug).run()

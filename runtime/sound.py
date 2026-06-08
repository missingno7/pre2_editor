"""Audio for the runtime.

Sound effects and music assignment mirror blues p2/sound.c:
- play_sound(num): 11 raw 8-bit/8000 Hz PCM samples from SAMPLE.SQZ, indexed by
  a fixed size table (sound_sizes_tbl / offsets).
- play_music(num): a .TRK file per music number (trk_names_tbl).

Status / known blockers (verified against the data + blues unpack.c):
- SAMPLE.SQZ uses a compression signature (0x0000) that neither the EAT
  (0x4CB4) nor SQZ (0x10xx) decoder accepts, so the sample bank can't be
  extracted yet — sound effects are wired but silent until that format is
  decoded.
- The .TRK files ARE EAT-compressed (decodable) but contain a Titus tracker
  module; playing them needs the tracker player from blues mixer.c.

Everything here degrades to a safe no-op so the game runs with audio off.
"""
from __future__ import annotations

from pathlib import Path

# blues p2/sound.c sound_sizes_tbl (11 effects).
SOUND_SIZES = (0x188E, 0x1C80, 0x235E, 0x19E6, 0x0AB2, 0x0912,
               0x0000, 0x35D2, 0x06C4, 0x1C86, 0x0E2E)

# blues p2/sound.c trk_names_tbl (music number -> file).
TRK_NAMES = (
    "PRES.TRK", "CODE.TRK", "CARTE.TRK", "PRESENTA.TRK", "GLACE.TRK",
    None, None, None, None, "MINES.TRK", "MYSTERY.TRK", None, None,
    "MONSTER.TRK", "FINAL.TRK", "BRAVO.TRK", "KOOL.TRK", "BOULA.TRK",
)

SAMPLE_RATE = 8000


class SoundEngine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.sound_enabled = True
        self.music_enabled = True
        self._music_num = -1
        self._ok = False
        self._samples: bytes | None = None
        self._offsets: list[int] = []
        self._cache: dict[int, object] = {}
        self._mixer = None
        self._init_backend()
        self._load_samples()

    def _init_backend(self) -> None:
        try:
            import pygame
            pygame.mixer.quit()
            pygame.mixer.init(frequency=SAMPLE_RATE, size=8, channels=1, buffer=512)
            self._mixer = pygame.mixer
            self._ok = True
        except Exception:
            self._mixer = None
            self._ok = False

    def _load_samples(self) -> None:
        # Build the offset table regardless; load the bank when its format is
        # decodable (currently a known blocker for SAMPLE.SQZ).
        off = 0
        for size in SOUND_SIZES:
            self._offsets.append(off)
            off += size
        try:
            from pre2lib.formats import unpack_file, find_data_file
            self._samples = unpack_file(find_data_file(self.data_dir, "SAMPLE.SQZ"))
        except Exception:
            self._samples = None

    # --- public API (mirrors blues play_sound / play_music) ---------------
    def play_sound(self, num: int) -> None:
        if not (self.sound_enabled and self._ok and self._samples):
            return
        if not (0 <= num < len(SOUND_SIZES)):
            return
        size = SOUND_SIZES[num]
        if size == 0:
            return
        snd = self._cache.get(num)
        if snd is None:
            start = self._offsets[num]
            raw = self._samples[start:start + size]
            if len(raw) < size:
                return
            try:
                # SAMPLE PCM is 8-bit unsigned; the mixer was opened as signed
                # 8-bit, so bias by 0x80.
                buf = bytes((b - 0x80) & 0xFF for b in raw)
                snd = self._mixer.Sound(buffer=buf)
            except Exception:
                return
            self._cache[num] = snd
        try:
            snd.play()
        except Exception:
            pass

    def play_music(self, num: int) -> None:
        if not (self.music_enabled and self._ok):
            return
        if num == self._music_num:
            return
        if not (0 <= num < len(TRK_NAMES)) or TRK_NAMES[num] is None:
            return
        # .TRK is an EAT-compressed Titus tracker module; pygame can't play it
        # directly and the tracker player isn't ported yet. Record the request
        # so toggling/selection logic is correct once playback exists.
        self._music_num = num

    def stop_music(self) -> None:
        self._music_num = -1
        if self._ok:
            try:
                self._mixer.music.stop()
            except Exception:
                pass

"""Audio for the runtime.

Sound effects and music assignment mirror blues p2/sound.c:
- play_sound(num): 11 raw 8-bit/8000 Hz PCM effects from SAMPLE.SQZ (SQV codec),
  indexed by a fixed size table (sound_sizes_tbl / offsets).
- play_music(num): a .TRK file per music number (trk_names_tbl). The .TRK files
  are EAT-compressed standard ProTracker .MOD modules.

The mixer runs at 44100/16-bit/stereo (so the .MOD music sounds right); the
8-bit/8000 Hz effects are resampled/converted to that format with numpy. Audio
degrades to a safe no-op if a backend or asset is unavailable.
"""
from __future__ import annotations

import io
from pathlib import Path

MIX_RATE = 44100

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
        self._last_music = -1
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
            import numpy  # noqa: F401  (used in play_sound)
            pygame.mixer.quit()
            pygame.mixer.init(frequency=MIX_RATE, size=-16, channels=2, buffer=512)
            self._mixer = pygame.mixer
            self._ok = True
        except Exception:
            self._mixer = None
            self._ok = False

    def _load_samples(self) -> None:
        off = 0
        for size in SOUND_SIZES:
            self._offsets.append(off)
            off += size
        try:
            from pre2lib.formats import unpack_file, find_data_file
            self._samples = unpack_file(find_data_file(self.data_dir, "SAMPLE.SQZ"))
        except Exception:
            self._samples = None

    def _make_sound(self, raw: bytes):
        """Convert an 8-bit/8000 Hz SIGNED-PCM effect to a mixer Sound
        (44100 Hz / int16 / stereo) via nearest-neighbour resampling.

        The samples are signed 8-bit: blues mixer.c does `(int8_t)data[pos]*256`,
        i.e. sign-extend then scale to int16 (silence = 0)."""
        import numpy as np
        import pygame.sndarray
        src = np.frombuffer(raw, dtype=np.int8).astype(np.int16) << 8
        n_out = max(1, len(src) * MIX_RATE // SAMPLE_RATE)
        idx = (np.arange(n_out) * SAMPLE_RATE // MIX_RATE).clip(0, len(src) - 1)
        mono = src[idx]
        stereo = np.column_stack((mono, mono)).astype(np.int16)
        return pygame.sndarray.make_sound(np.ascontiguousarray(stereo))

    # --- public API (mirrors blues play_sound / play_music) ---------------
    def play_sound(self, num: int) -> None:
        if not (self.sound_enabled and self._ok and self._samples):
            return
        if not (0 <= num < len(SOUND_SIZES)) or SOUND_SIZES[num] == 0:
            return
        snd = self._cache.get(num)
        if snd is None:
            start = self._offsets[num]
            raw = self._samples[start:start + SOUND_SIZES[num]]
            try:
                snd = self._make_sound(raw)
            except Exception:
                return
            self._cache[num] = snd
        try:
            snd.play()
        except Exception:
            pass

    def play_music(self, num: int) -> None:
        if not (0 <= num < len(TRK_NAMES)) or TRK_NAMES[num] is None:
            return
        self._last_music = num  # remembered so toggling music back on can resume
        if num == self._music_num:
            return
        self._music_num = num
        if not (self.music_enabled and self._ok):
            return
        try:
            from pre2lib.formats import unpack_file, find_data_file
            mod = unpack_file(find_data_file(self.data_dir, TRK_NAMES[num]))
            self._mixer.music.load(io.BytesIO(mod))
            self._mixer.music.play(-1)
        except Exception:
            pass

    def resume_music(self) -> None:
        """Re-start the current level's music (used when Music is toggled back on)."""
        if self._last_music >= 0:
            self._music_num = -1
            self.play_music(self._last_music)

    def stop_music(self) -> None:
        self._music_num = -1
        if self._ok:
            try:
                self._mixer.music.stop()
            except Exception:
                pass

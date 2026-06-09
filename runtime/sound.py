"""Audio for the runtime.

Sound effects and music assignment mirror blues p2/sound.c:
- play_sound(num): 11 raw 8-bit/8000 Hz PCM effects from SAMPLE.SQZ (SQV codec),
  indexed by a fixed size table (sound_sizes_tbl / offsets).
- play_music(num): a .TRK file per music number (trk_names_tbl). The .TRK files
  are EAT-compressed standard ProTracker .MOD modules.

The mixer is requested as 44100 Hz / 16-bit / stereo so the .MOD music sounds
right.  The SAMPLE.SQZ effects are tiny signed 8-bit / 8000 Hz PCM snippets; we
convert them to the actual pygame mixer format before playing.  Audio degrades
to a safe no-op if a backend or asset is unavailable, and can print diagnostics
with PRE2_AUDIO_DEBUG=1 or run_game.py --audio-debug.
"""
from __future__ import annotations

import io
import os
import sys
from array import array
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


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "debug"}


class SoundEngine:
    def __init__(self, data_dir: Path, *, debug: bool | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.debug = _truthy(os.environ.get("PRE2_AUDIO_DEBUG")) if debug is None else bool(debug)
        self.sound_enabled = True
        self.music_enabled = True
        self._music_num = -1
        self._last_music = -1
        self._ok = False
        self._samples: bytes | None = None
        self._offsets: list[int] = []
        self._cache: dict[int, object] = {}
        self._mixer = None
        self._mix_rate = MIX_RATE
        self._mix_format = -16
        self._mix_channels = 2
        self._test_sound_num = 0
        self._init_backend()
        self._load_samples()

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[pre2 audio] {message}", file=sys.stderr)

    def _init_backend(self) -> None:
        try:
            import pygame

            # pre_init gives pygame.init()/display backends the same desired
            # mixer parameters if they initialise audio later. mixer.init() here
            # makes sound available even when the Tk renderer is used.
            pygame.mixer.pre_init(frequency=MIX_RATE, size=-16, channels=2, buffer=512)
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=MIX_RATE, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(16)
            init = pygame.mixer.get_init()
            if init:
                self._mix_rate, self._mix_format, self._mix_channels = init
            self._mixer = pygame.mixer
            self._ok = bool(init)
            self._log(f"pygame mixer init={init}, channels={pygame.mixer.get_num_channels()}")
        except Exception as exc:
            self._mixer = None
            self._ok = False
            self._log(f"pygame mixer unavailable: {exc!r}")

    def _load_samples(self) -> None:
        off = 0
        for size in SOUND_SIZES:
            self._offsets.append(off)
            off += size
        try:
            from pre2lib.formats import unpack_file, find_data_file
            self._samples = unpack_file(find_data_file(self.data_dir, "SAMPLE.SQZ"))
            self._log(f"loaded SAMPLE.SQZ: {len(self._samples)} decoded bytes, expected {sum(SOUND_SIZES)}")
        except Exception as exc:
            self._samples = None
            self._log(f"SAMPLE.SQZ unavailable: {exc!r}")

    def _make_sound(self, raw: bytes):
        """Convert signed 8-bit / 8000 Hz mono PCM into pygame's mixer format.

        This intentionally does not require numpy.  The effects are short, so a
        pure-Python conversion is fast enough and avoids the old hidden optional
        dependency where music worked but sound effects silently did nothing.
        """
        if not self._mixer:
            raise RuntimeError("pygame mixer is not initialised")
        rate = int(self._mix_rate or MIX_RATE)
        channels = max(1, int(self._mix_channels or 2))
        fmt = int(self._mix_format or -16)
        if abs(fmt) != 16:
            # We request 16-bit. If a strange backend gives something else,
            # reinitialise to the format this converter knows how to feed.
            self._log(f"unexpected mixer format {fmt}; reinitialising as signed 16-bit")
            self._mixer.quit()
            self._mixer.init(frequency=MIX_RATE, size=-16, channels=2, buffer=512)
            self._mixer.set_num_channels(16)
            self._mix_rate, self._mix_format, self._mix_channels = self._mixer.get_init()
            rate = self._mix_rate
            channels = self._mix_channels
            fmt = self._mix_format

        n_out = max(1, len(raw) * rate // SAMPLE_RATE)
        pcm = array("h")
        append = pcm.append
        for out_i in range(n_out):
            b = raw[(out_i * SAMPLE_RATE) // rate]
            # SAMPLE.SQZ is signed 8-bit PCM: 0 is silence, >=128 wraps negative.
            signed = b - 256 if b >= 128 else b
            sample = max(-32768, min(32767, signed << 8))
            for _ in range(channels):
                append(sample)
        if sys.byteorder != "little":
            pcm.byteswap()
        snd = self._mixer.Sound(buffer=pcm.tobytes())
        snd.set_volume(1.0)
        return snd

    # --- public API (mirrors blues play_sound / play_music) ---------------
    def play_sound(self, num: int) -> None:
        if not self.sound_enabled:
            self._log(f"play_sound({num}) ignored: sound effects disabled")
            return
        if not self._ok:
            self._log(f"play_sound({num}) ignored: mixer not initialised")
            return
        if not self._samples:
            self._log(f"play_sound({num}) ignored: SAMPLE.SQZ not loaded")
            return
        if not (0 <= num < len(SOUND_SIZES)) or SOUND_SIZES[num] == 0:
            self._log(f"play_sound({num}) ignored: invalid/empty sound")
            return
        snd = self._cache.get(num)
        if snd is None:
            start = self._offsets[num]
            raw = self._samples[start:start + SOUND_SIZES[num]]
            try:
                snd = self._make_sound(raw)
            except Exception as exc:
                self._log(f"play_sound({num}) conversion failed: {exc!r}")
                return
            self._cache[num] = snd
            self._log(f"cached sound {num}: {len(raw)} bytes")
        try:
            ch = snd.play()
            self._log(f"play_sound({num}) -> channel={ch}")
        except Exception as exc:
            self._log(f"play_sound({num}) playback failed: {exc!r}")

    def play_next_test_sound(self) -> None:
        """Cycle through audible effects; useful with the pygame F8 hotkey."""
        for _ in range(len(SOUND_SIZES)):
            num = self._test_sound_num % len(SOUND_SIZES)
            self._test_sound_num += 1
            if SOUND_SIZES[num] != 0:
                self._log(f"test SFX {num}")
                self.play_sound(num)
                return

    def play_music(self, num: int) -> None:
        if not (0 <= num < len(TRK_NAMES)) or TRK_NAMES[num] is None:
            return
        self._last_music = num  # remembered so toggling music back on can resume
        if num == self._music_num:
            return
        self._music_num = num
        if not self.music_enabled:
            self._log(f"play_music({num}) remembered but ignored: music disabled")
            return
        if not (self._ok and self._mixer):
            self._log(f"play_music({num}) ignored: mixer not initialised")
            return
        try:
            from pre2lib.formats import unpack_file, find_data_file
            mod = unpack_file(find_data_file(self.data_dir, TRK_NAMES[num]))
            self._mixer.music.load(io.BytesIO(mod))
            # Keep tracker music a bit below full scale so the raw SFX are not
            # hidden by loud MOD channels.
            self._mixer.music.set_volume(0.65)
            self._mixer.music.play(-1)
            self._log(f"play_music({num}) {TRK_NAMES[num]}: {len(mod)} decoded bytes")
        except Exception as exc:
            self._log(f"play_music({num}) failed: {exc!r}")

    def resume_music(self) -> None:
        """Re-start the current level's music (used when Music is toggled back on)."""
        if self._last_music >= 0:
            self._music_num = -1
            self.play_music(self._last_music)

    def stop_music(self) -> None:
        self._music_num = -1
        if self._ok and self._mixer:
            try:
                self._mixer.music.stop()
            except Exception as exc:
                self._log(f"stop_music failed: {exc!r}")

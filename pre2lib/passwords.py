"""Level passwords, matching the REAL PRE2.EXE algorithm.

Recovered from the decompressed main program (disasm/PRE2_main.bin, routine at
image offset 0x9559):

    machine_value (computed once at first use, stored at DGROUP[0xA32F]):
        dl/dh = add/sbb checksum over the 16 BIOS ROM bytes F000:FFF0..FFFF
                (includes the BIOS date string and the model byte), then for
                every option ROM between C000:0000 and EFFF:0000 (0xAA55
                signature, video BIOS first): dl += / dh ^= the first 0x80
                bytes; if the result is 0, use 0x20.
    password_word(seed) = ROL16((seed ^ 0x55A3) * machine_value, 3)
        seed = level_num + (10 if expert else 0)
        rendered as 4 hex digits (high nibble first) using items 283..298.

So passwords are MACHINE-SPECIFIC: they depend on the emulated BIOS + video
BIOS bytes. Different DOSBox versions/forks/machine= settings produce different
machine values and therefore different passwords. The default multiplier below
(0xB297) is the value blues measured on stock DOSBox; if your emulator differs,
calibrate with `derive_machine_multiplier()` from one observed password (the
level-1 beginner password works: its seed makes the factor odd => unique
solution), then pass the result as `multiplier=` to the helpers here.
"""
from __future__ import annotations

from dataclasses import dataclass

from pre2lib.formats import LEVEL_IDS


PASSWORD_DIGITS = "0123456789ABCDEF"

# Machine value observed on stock DOSBox (blues' "to match dosbox" constant).
DEFAULT_MACHINE_MULTIPLIER = 0xB297

PASSWORD_XOR = 0x55A3
PASSWORD_ROL = 3  # the original reads cs:[5]; resolves to 3 for this binary


@dataclass(frozen=True)
class LevelPassword:
    level_index: int
    level_id: str
    beginner: str
    expert: str
    beginner_seed: int
    expert_seed: int


def _rol16(value: int, count: int) -> int:
    value &= 0xFFFF
    count &= 15
    return ((value << count) | (value >> (16 - count))) & 0xFFFF


def _ror16(value: int, count: int) -> int:
    return _rol16(value, 16 - (count & 15))


def password_word_from_seed(seed: int, multiplier: int = DEFAULT_MACHINE_MULTIPLIER) -> int:
    """The original hash: ROL16((seed ^ 0x55A3) * machine_value, 3)."""
    value = (seed ^ PASSWORD_XOR) & 0xFFFF
    value = (value * (multiplier & 0xFFFF)) & 0xFFFF
    return _rol16(value, PASSWORD_ROL)


def format_password_word(value: int) -> str:
    return "".join(PASSWORD_DIGITS[(value >> shift) & 0xF] for shift in (12, 8, 4, 0))


def parse_password_word(password: str) -> int:
    text = password.strip().upper()
    if len(text) != 4 or any(c not in PASSWORD_DIGITS for c in text):
        raise ValueError("password must be 4 hex characters (0-9 A-F)")
    value = 0
    for ch in text:
        value = (value << 4) | PASSWORD_DIGITS.index(ch)
    return value


def _modinv_u16(value: int) -> int:
    """Multiplicative inverse of an ODD value modulo 2**16."""
    if (value & 1) == 0:
        raise ValueError("no unique inverse for an even factor")
    inv = 1
    for _ in range(5):  # Newton iteration doubles correct bits: 5 rounds > 16 bits
        inv = (inv * (2 - value * inv)) & 0xFFFF
    return inv


def derive_machine_multiplier(password: str, level_index: int = 0, *, expert: bool = False) -> int:
    """Recover this machine's multiplier from ONE observed in-game password.

    Use a level whose seed is EVEN (seed ^ 0x55A3 is then odd and uniquely
    invertible mod 2^16) — e.g. the level 1 beginner password (seed 0).
    """
    seed = level_index + (10 if expert else 0)
    factor = (seed ^ PASSWORD_XOR) & 0xFFFF
    word = _ror16(parse_password_word(password), PASSWORD_ROL)
    return (word * _modinv_u16(factor)) & 0xFFFF


def level_password(level_index: int, *, expert: bool = False,
                   multiplier: int = DEFAULT_MACHINE_MULTIPLIER) -> str:
    if level_index < 0 or level_index >= len(LEVEL_IDS):
        raise ValueError(f"level_index must be 0..{len(LEVEL_IDS) - 1}")
    seed = level_index + (10 if expert else 0)
    return format_password_word(password_word_from_seed(seed, multiplier))


def all_level_passwords(multiplier: int = DEFAULT_MACHINE_MULTIPLIER) -> list[LevelPassword]:
    rows: list[LevelPassword] = []
    for index, level_id in enumerate(LEVEL_IDS):
        beginner_seed = index
        expert_seed = index + 10
        rows.append(
            LevelPassword(
                level_index=index,
                level_id=level_id,
                beginner=format_password_word(password_word_from_seed(beginner_seed, multiplier)),
                expert=format_password_word(password_word_from_seed(expert_seed, multiplier)),
                beginner_seed=beginner_seed,
                expert_seed=expert_seed,
            )
        )
    return rows

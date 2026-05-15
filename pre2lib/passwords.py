from __future__ import annotations

from dataclasses import dataclass

from pre2lib.formats import LEVEL_IDS


PASSWORD_DIGITS = "0123456789ABCDEF"


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
    return ((value << count) | (value >> (16 - count))) & 0xFFFF


def password_word_from_seed(seed: int) -> int:
    """Match the original game's random_get_number3(seed)."""

    value = (seed ^ 0x55A3) & 0xFFFF
    value = (value * 0xB297) & 0xFFFF
    return _rol16(value, 3)


def format_password_word(value: int) -> str:
    return "".join(PASSWORD_DIGITS[(value >> shift) & 0xF] for shift in (12, 8, 4, 0))


def level_password(level_index: int, *, expert: bool = False) -> str:
    if level_index < 0 or level_index >= len(LEVEL_IDS):
        raise ValueError(f"level_index must be 0..{len(LEVEL_IDS) - 1}")
    seed = level_index + (10 if expert else 0)
    return format_password_word(password_word_from_seed(seed))


def all_level_passwords() -> list[LevelPassword]:
    rows: list[LevelPassword] = []
    for index, level_id in enumerate(LEVEL_IDS):
        beginner_seed = index
        expert_seed = index + 10
        rows.append(
            LevelPassword(
                level_index=index,
                level_id=level_id,
                beginner=format_password_word(password_word_from_seed(beginner_seed)),
                expert=format_password_word(password_word_from_seed(expert_seed)),
                beginner_seed=beginner_seed,
                expert_seed=expert_seed,
            )
        )
    return rows

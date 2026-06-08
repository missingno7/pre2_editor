from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pre2lib.formats import unpack_file
from runtime.game import InputState


@dataclass(frozen=True, slots=True)
class DemoStep:
    mask: int
    count: int


def load_keyb_demo(path: str | Path) -> list[DemoStep]:
    """Decode KEYB.SQZ demo input records used by PRE2.

    p2/level.c reads little-endian words as:
      mask = word & 0xff
      counter = word >> 8

    If the second byte of the decompressed stream is 0xff, the DOS code skips
    the first word before playback.  We preserve that quirk here so trace runs
    can be compared against the same stream shape.
    """
    data = unpack_file(path)
    off = 0
    if len(data) >= 2 and data[1] == 0xFF:
        off = 2
    steps: list[DemoStep] = []
    while off + 1 < len(data):
        word = int.from_bytes(data[off:off + 2], "little")
        off += 2
        steps.append(DemoStep(mask=word & 0xFF, count=word >> 8))
    return steps


def input_from_demo_mask(mask: int) -> InputState:
    return InputState(
        left=bool(mask & 0x01),
        right=bool(mask & 0x02),
        up=bool(mask & 0x04),
        down=bool(mask & 0x08),
        fire=bool(mask & 0x10),
    )


def expand_demo_inputs(steps: list[DemoStep], max_ticks: int) -> list[InputState]:
    out: list[InputState] = []
    for step in steps:
        # The engine keeps using the current mask while demo_counter is non-zero;
        # the exact inclusive/exclusive edge will be audited against DOSBox, but
        # count ticks is the most useful deterministic trace unit for now.
        repeat = max(1, step.count)
        out.extend(input_from_demo_mask(step.mask) for _ in range(repeat))
        if len(out) >= max_ticks:
            break
    if len(out) < max_ticks:
        out.extend(InputState() for _ in range(max_ticks - len(out)))
    return out[:max_ticks]

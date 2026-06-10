#!/usr/bin/env python3
"""Small reproducible probes for PRE2 boss/static tables in PRE2_load.bin.

This does not try to label functions.  It is deliberately conservative: it
searches for exact byte/word signatures copied from the readable blues/p2
reference and dumps the suspicious tree-boss data area.  Use it when deciding
whether a runtime table is really ASM-confirmed or only blues-derived.
"""
from __future__ import annotations

from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "disasm" / "PRE2_load.bin"
STATICRES = ROOT / "reverse_engineering" / "reference" / "blues_p2" / "staticres.c"
BOSSES = ROOT / "reverse_engineering" / "reference" / "blues_p2" / "bosses.c"


def extract_c_array_numbers(path: Path, name: str) -> list[int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"{re.escape(name)}\s*\[[^\]]*\]\s*=\s*\{{(.*?)\}};", text, re.S)
    if not m:
        return []
    body = re.sub(r"/\*.*?\*/|//.*", "", m.group(1), flags=re.S)
    return [int(tok, 0) & 0xFFFF for tok in re.findall(r"-?0x[0-9a-fA-F]+|-?\d+", body)]


def pack_u8(values: list[int]) -> bytes:
    return bytes(v & 0xFF for v in values)


def pack_words(values: list[int]) -> bytes:
    return b"".join(struct.pack("<H", v & 0xFFFF) for v in values)


def find_all(blob: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    if not needle:
        return out
    start = 0
    while True:
        idx = blob.find(needle, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + 1


def dump_words(blob: bytes, start: int, end: int) -> None:
    print(f"\nword dump {start:#06x}..{end:#06x}")
    for off in range(start, end, 16):
        chunk = blob[off:off + 16]
        words = [chunk[i] | (chunk[i + 1] << 8) for i in range(0, len(chunk) - 1, 2)]
        print(f"{off:04x}: " + " ".join(f"{w:04x}" for w in words))


def main() -> None:
    blob = BIN.read_bytes()
    print(f"loaded {BIN.relative_to(ROOT)}: {len(blob)} bytes")

    probes: list[tuple[str, bytes]] = []
    for name in ("boss_gorilla_data", "boss_gorilla_spr_tbl", "boss_minotaur_seq_data"):
        vals = extract_c_array_numbers(STATICRES, name)
        if name.endswith("seq_data"):
            probes.append((name + " exact-u8", pack_u8(vals)))
            probes.append((name + " first-8-u8", pack_u8(vals[:8])))
            probes.append((name + " first-16-u8", pack_u8(vals[:16])))
        else:
            probes.append((name + " exact-u16", pack_words(vals)))
            probes.append((name + " first-8-u16", pack_words(vals[:8])))

    # Tree arrays live in bosses.c, but in the flat binary they do not appear as
    # clean contiguous C arrays; this checks that claim each time we re-run it.
    for name in ("boss_level5_pos1_data", "boss_level5_pos2_data", "boss_level5_pos3_data"):
        vals = extract_c_array_numbers(BOSSES, name)
        probes.append((name + " exact-u16", pack_words(vals)))
        probes.append((name + " first-6-u16", pack_words(vals[:6])))

    for label, needle in probes:
        hits = find_all(blob, needle)
        print(f"{label:40s}: " + (", ".join(f"{h:#06x}" for h in hits[:8]) if hits else "no exact hit"))

    dump_words(blob, 0x8650, 0x86D0)


if __name__ == "__main__":
    main()

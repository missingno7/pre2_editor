#!/usr/bin/env python3
"""Small helper for repeated PRE2.EXE binary/ASM archaeology.

Usage examples:

    python reverse_engineering/tools/asm_search.py bytes BF CD DD EC F6 FB FE FF 00
    python reverse_engineering/tools/asm_search.py text "PREHISTORIK"

The existing disassembly in this repo is a flat objdump pass over the unpacked
load module.  That is useful, but not enough for tick-perfect work.  This helper
keeps byte-pattern searches reproducible while we build a better segmented map.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def parse_hex_bytes(parts: list[str]) -> bytes:
    return bytes(int(p, 16) for p in parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["bytes", "text"])
    parser.add_argument("pattern", nargs="+")
    parser.add_argument("--bin", default="disasm/PRE2_load.bin")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    blob = (root / args.bin).read_bytes()
    needle = parse_hex_bytes(args.pattern) if args.mode == "bytes" else " ".join(args.pattern).encode("ascii")

    start = 0
    count = 0
    while True:
        pos = blob.find(needle, start)
        if pos < 0:
            break
        print(f"0x{pos:05X}")
        count += 1
        start = pos + 1
    print(f"matches: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

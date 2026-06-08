#!/usr/bin/env python3
"""Recursive-descent 16-bit disassembler for PRE2_load.bin.

The flat objdump sweep in disasm/ decodes data as code and is ~unusable for
logic reading.  The unpacked load module is < 64 KiB, so it behaves as a single
16-bit segment: NEAR call/jmp/jcc relative targets resolve directly in linear
file-offset space.  We follow only reachable control flow from a set of seed
entry points, which cleanly separates real code from data.

Outputs:
  - a sorted listing of every reachable instruction, OR
  - the set of code byte-ranges (so callers can mask out data), OR
  - cross-references (who calls/jmps a given offset).

Usage:
  python recdis.py listing               # full reachable listing
  python recdis.py listing 0x1234        # listing of the function at 0x1234
  python recdis.py ranges                # code vs data ranges summary
  python recdis.py xref 0x1234           # callers/jumpers of 0x1234
  python recdis.py funcs                 # list discovered call targets (functions)
"""
from __future__ import annotations

import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_X86, CS_MODE_16
from capstone.x86 import X86_OP_IMM

ROOT = Path(__file__).resolve().parents[2]
LOAD_BIN = ROOT / "disasm" / "PRE2_load.bin"

# Entry point (load-module linear offset) per disasm/PRE2_report.md, plus the
# verified real-code video module so we cover routines only reached via far
# pointers / indirect dispatch we cannot statically follow.
DEFAULT_SEEDS = [0xF6A8, 0x0003, 0x99A0, 0x9D00, 0xA014, 0xA02C]

# Mnemonics that terminate a linear run (control does not fall through).
STOP = {"ret", "retf", "iret", "iretd", "jmp", "hlt"}
# Unconditional/!conditional branch mnemonics whose target we enqueue.
CALLS = {"call"}
JMPS = {
    "jmp", "je", "jne", "jz", "jnz", "jg", "jge", "jl", "jle", "ja", "jae",
    "jb", "jbe", "jo", "jno", "js", "jns", "jp", "jnp", "jcxz", "loop",
    "loope", "loopne",
}


def load() -> bytes:
    return LOAD_BIN.read_bytes()


def scan_call_targets(blob: bytes) -> set[int]:
    """Linear scan for E8 (near call) and 9A (far call) and collect targets.

    Over-includes (some bytes aren't really calls), but real function entries
    recur consistently; bogus seeds produce short invalid runs we can ignore.
    """
    out: set[int] = set()
    n = len(blob)
    for i in range(n - 4):
        b = blob[i]
        if b == 0xE8:  # call rel16
            rel = int.from_bytes(blob[i + 1:i + 3], "little", signed=True)
            t = (i + 3 + rel) & 0xFFFF
            if 0 <= t < n:
                out.add(t)
        elif b == 0x9A:  # call far seg:off
            o = int.from_bytes(blob[i + 1:i + 3], "little")
            s = int.from_bytes(blob[i + 3:i + 5], "little")
            t = (s * 16 + o) & 0xFFFFF
            if 0 <= t < n:
                out.add(t)
    return out


def disassemble(blob: bytes, seeds=None):
    """Returns (insns dict off->(size,mnem,opstr), call_targets set, xrefs dict)."""
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.detail = True
    seeds = list(seeds if seeds is not None else DEFAULT_SEEDS)
    seen: dict[int, tuple] = {}
    call_targets: set[int] = set()
    xrefs: dict[int, list[int]] = {}
    work = list(seeds)
    n = len(blob)

    def branch_target(insn):
        # Far call/jmp (9A/EA): absolute seg:off -> linear = seg*16+off (load base 0).
        op0 = insn.bytes[0]
        if op0 in (0x9A, 0xEA):
            o = int.from_bytes(insn.bytes[1:3], "little")
            s = int.from_bytes(insn.bytes[3:5], "little")
            return (s * 16 + o) & 0xFFFFF
        if len(insn.operands) != 1:
            return None
        op = insn.operands[0]
        if op.type != X86_OP_IMM:
            return None  # indirect (register/memory) — can't follow statically
        return op.imm & 0xFFFF  # near targets live in segment/linear space

    while work:
        off = work.pop()
        while 0 <= off < n and off not in seen:
            chunk = blob[off:off + 16]
            insns = list(md.disasm(chunk, off, count=1))
            if not insns:
                break
            insn = insns[0]
            mnem = insn.mnemonic
            seen[off] = (insn.size, mnem, insn.op_str)
            tgt = None
            if mnem in CALLS or mnem in JMPS:
                tgt = branch_target(insn)
                if tgt is not None and 0 <= tgt < n:
                    xrefs.setdefault(tgt, []).append(off)
                    if mnem in CALLS:
                        call_targets.add(tgt)
                    if tgt not in seen:
                        work.append(tgt)
            nxt = off + insn.size
            if mnem in STOP:
                break  # no fall-through
            off = nxt
    return seen, call_targets, xrefs


def fmt_listing(blob, seen, lo=None, hi=None):
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    out = []
    for off in sorted(seen):
        if lo is not None and off < lo:
            continue
        if hi is not None and off >= hi:
            continue
        size, mnem, ops = seen[off]
        raw = blob[off:off + size].hex()
        out.append(f"{off:05X}: {raw:<16} {mnem} {ops}")
    return "\n".join(out)


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "ranges"
    blob = load()
    seeds = list(DEFAULT_SEEDS)
    if "--scan" in args:
        args.remove("--scan")
        seeds += list(scan_call_targets(blob))
    seen, calls, xrefs = disassemble(blob, seeds)

    if cmd == "listing":
        if len(args) > 1:
            start = int(args[1], 0)
            # listing of one function: from start until a gap > 0 (next data)
            offs = sorted(o for o in seen if o >= start)
            lo = start
            hi = None
            prev = None
            for o in offs:
                if prev is not None and o - prev > seen[prev][0]:
                    hi = o
                    break
                prev = o
            print(fmt_listing(blob, seen, lo, hi))
        else:
            print(fmt_listing(blob, seen))
    elif cmd == "funcs":
        for t in sorted(calls):
            print(f"0x{t:05X}  ({len(xrefs.get(t, []))} xrefs)")
    elif cmd == "xref":
        tgt = int(args[1], 0)
        for src in sorted(xrefs.get(tgt, [])):
            sz, mn, op = seen[src]
            print(f"0x{src:05X}: {mn} {op}")
        print(f"total: {len(xrefs.get(tgt, []))}")
    else:  # ranges
        offs = sorted(seen)
        total_code = sum(seen[o][0] for o in offs)
        # merge into contiguous ranges
        ranges = []
        cur_lo = cur_hi = None
        for o in offs:
            sz = seen[o][0]
            if cur_hi is not None and o <= cur_hi:
                cur_hi = max(cur_hi, o + sz)
            else:
                if cur_lo is not None:
                    ranges.append((cur_lo, cur_hi))
                cur_lo, cur_hi = o, o + sz
        if cur_lo is not None:
            ranges.append((cur_lo, cur_hi))
        print(f"blob size      : {len(blob)}")
        print(f"reachable insns: {len(seen)}")
        print(f"code bytes     : {total_code} ({100*total_code/len(blob):.1f}%)")
        print(f"call targets   : {len(calls)}")
        print(f"code ranges    : {len(ranges)}")
        for lo, hi in ranges:
            print(f"  0x{lo:05X}-0x{hi:05X}  ({hi-lo} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

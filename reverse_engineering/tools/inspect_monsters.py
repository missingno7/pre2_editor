#!/usr/bin/env python3
"""Inspect runtime-normalized monster records and animation streams.

This is intentionally based on run_game's RuntimeWorld so it shows the same
sprite base fixups and monster_anim_tbl lookup that the runtime currently uses.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from runtime.game import RuntimeWorld  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--level', type=int, default=1, help='1-based level index')
    ap.add_argument('--game-data', default='game_data')
    args = ap.parse_args()

    world = RuntimeWorld(ROOT, ROOT / args.game_data, max(0, args.level - 1))
    print('idx,type,expert,raw_spr,runtime_spr,energy,respawn,score,x,y,extra,anim_head')
    for ms in world.monster_states:
        m = world.level.monsters[ms.index]
        rel = ms.runtime_sprite - world.sprite_resolver.monster_runtime_base
        anim = world._monster_anim_for(ms.movement_type, ms.runtime_sprite)
        head = ' '.join(f'{(w & 0xffff):04X}' for w in anim[:12])
        print(f'{ms.index},{ms.movement_type},{int(m.expert_only)},0x{m.sprite_num_raw:04X},{ms.runtime_sprite}({rel}),{ms.energy},{ms.respawn_ticks},{ms.score},{ms.x_pos},{ms.y_pos},{m.extra},{head}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Trace run_game player state for repeatable gameplay RE.

Examples:
    python reverse_engineering/tools/trace_player.py --steps 40 --inputs R:40
    python reverse_engineering/tools/trace_player.py --steps 30 --inputs U+R:12,R:18

Input letters:
    L/R/U/D = directions, F = club/fire, A = action/return.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from runtime.game import InputState, RuntimeWorld  # noqa: E402
from runtime.demo import expand_demo_inputs, load_keyb_demo  # noqa: E402


def parse_inputs(spec: str, steps: int) -> list[InputState]:
    out: list[InputState] = []
    if not spec:
        return [InputState() for _ in range(steps)]
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        keys, _, count_s = part.partition(':')
        count = int(count_s) if count_s else 1
        state = InputState()
        for key in keys.split('+'):
            key = key.strip().upper()
            if key in ('', 'NONE', '-'):
                continue
            if key == 'L':
                state.left = True
            elif key == 'R':
                state.right = True
            elif key == 'U':
                state.up = True
            elif key == 'D':
                state.down = True
            elif key == 'F':
                state.fire = True
            elif key == 'A':
                state.action = True
            else:
                raise ValueError(f'unknown input key {key!r}')
        out.extend(InputState(left=state.left, right=state.right, up=state.up, down=state.down, jump=state.jump, action=state.action, fire=state.fire) for _ in range(count))
    if len(out) < steps:
        out.extend(InputState() for _ in range(steps - len(out)))
    return out[:steps]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--level', type=int, default=1, help='1-based level index')
    ap.add_argument('--steps', type=int, default=60)
    ap.add_argument('--inputs', default='', help='R:30,U+R:10,F:5 etc.')
    ap.add_argument('--demo-keyb', action='store_true', help='Use original KEYB.SQZ demo input stream instead of --inputs')
    ap.add_argument('--game-data', default='game_data')
    ap.add_argument('--objects', action='store_true', help='Append objects_tbl club/projectile/hit/bonus/item/score summary columns')
    args = ap.parse_args()

    world = RuntimeWorld(ROOT, ROOT / args.game_data, max(0, args.level - 1))
    if args.demo_keyb:
        inputs = expand_demo_inputs(load_keyb_demo(ROOT / args.game_data / 'KEYB.SQZ'), args.steps)
    else:
        inputs = parse_inputs(args.inputs, args.steps)
    header = 'tick,input,x,y,vx,vy,spr,anim,ground,tile_flags,nojump,jump_counter,friction,death'
    if args.objects:
        header += ',club,projectiles,hits,monsters,bonuses,items,scores,club_anim,anim40,club_power'
    print(header)
    for tick, inp in enumerate(inputs, 1):
        keys = ''.join(k for k, v in [('L', inp.left), ('R', inp.right), ('U', inp.up), ('D', inp.down), ('F', inp.fire), ('A', inp.action)] if v) or '-'
        world.tick(inp)
        p = world.player
        row = f'{tick},{keys},{p.x},{p.y},{p.vx},{p.vy},{p.spr_num & 0x1FFF},{p.current_anim_num},{int(p.on_ground)},{p.tile_flags},{p.nojump_counter},{p.jumping_counter},{p.x_friction},{p.death_flag}'
        if args.objects:
            club = world.runtime_objects[0]
            club_s = '-' if not club.active else f'{club.spr_num & 0x1FFF}@{club.x}:{club.y}'
            projs = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}' for o in world.runtime_objects[2:6] if o.active) or '-'
            hits = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}' for o in world.runtime_objects[6:11] if o.active) or '-'
            monsters = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}:t{o.monster_type}:s{o.monster_state}:e{o.monster_energy}' for o in world.runtime_objects[11:23] if o.active) or '-'
            bonuses = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}' for o in world.runtime_objects[23:55] if o.active) or '-'
            items = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}' for o in world.runtime_objects[55:75] if o.active) or '-'
            scores = '|'.join(f'{o.slot}:{o.spr_num & 0x1FFF}@{o.x}:{o.y}:{o.counter}' for o in world.runtime_objects[75:91] if o.active) or '-'
            row += f',{club_s},{projs},{hits},{monsters},{bonuses},{items},{scores},{p.club_anim_duration},{p.anim_0x40_flag},{p.club_power}'
        print(row)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

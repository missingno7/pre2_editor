"""Report Prehistorik 2 animated tile groups from original LEVEL*.SQZ files.

Usage:
    python reverse_engineering/scripts/analyze_animated_tiles.py game_data

This uses the same parser as the viewer/editor shell. The original engine marks
only the first tile of each 3-tile animation group with attr2 bit 0x80; the two
following consecutive tile IDs are phases of the same animation as well.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pre2lib.formats import LEVEL_IDS, load_level


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_data", nargs="?", default="game_data")
    args = parser.parse_args()
    root = Path(args.game_data)

    for level_index, level_id in enumerate(LEVEL_IDS):
        level = load_level(root, level_index)
        groups = level.animated_tile_groups
        print(f"Level {level_id}: {len(groups)} animation groups")
        for group in groups:
            members = ", ".join(f"0x{tile:02X}" for tile in group.members)
            usages = []
            for phase, tile in enumerate(group.members):
                count = sum(1 for map_tile in level.tilemap if map_tile == tile)
                usages.append(f"phase {phase}: tile 0x{tile:02X} used {count}×")
            print(f"  base 0x{group.base_tile:02X}; members {members}; " + "; ".join(usages))
        print()


if __name__ == "__main__":
    main()

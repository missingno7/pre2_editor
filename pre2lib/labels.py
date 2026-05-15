from __future__ import annotations

"""Human-facing labels used by the viewer/editor UI.

Prehistorik 2 level files store sprite numbers and behavior records, not text
names. The reverse-engineered `blues` code gives authoritative semantic labels
for a handful of special runtime objects (checkpoint, bomb, light toggles,
end-of-level object, etc.). The remaining labels below are intentionally editor
annotations based on the decoded game graphics; they are kept separate from the
binary parser so they can be refined without pretending the names came from the
level file itself.
"""

MONSTER_VISUAL_NAMES: dict[int, str] = {
    313: "Small cave beast",
    314: "Small cave beast",
    317: "Spider web hazard",
    318: "Spider",
    319: "Large hanging spider",
    330: "Flying wasp",
    331: "Flying wasp",
    335: "Segmented flying worm",
    336: "Segmented flying worm",
    341: "Small ground grub",
    342: "Hanging larva",
    345: "Burrowing green creature",
    349: "Green dinosaur",
    352: "Green dinosaur",
    355: "Caveman enemy",
    356: "Caveman enemy",
    359: "Owl-like jumper",
    366: "Small crawling bug",
    370: "Ground centipede",
    381: "Large bird",
    382: "Large bird",
    383: "Large bird",
    386: "Worm / ground crawler",
    396: "Cat rider",
    397: "Cat rider",
    402: "Pterodactyl",
    440: "Masked stone head",
    441: "Masked stone head",
    453: "Stone bat",
    454: "Stone bat",
}

# Exact/special names are preferred when the runtime code comments or logic make
# the semantic role clear. Other entries are conservative visual names.
ITEM_VISUAL_NAMES: dict[int, str] = {
    66: "Thrown bone / small weapon",
    70: "Bone",
    71: "Bone",
    72: "Bone",
    92: "BONUS letter B",
    93: "BONUS letter O",
    94: "BONUS letter N",
    95: "BONUS letter U",
    96: "BONUS letter S",
    97: "Flying insect bonus",
    99: "Lighter / end-level reward sprite",
    101: "Fork",
    102: "Knife",
    103: "Spoon",
    110: "Slot machine bonus",
    111: "Ice cream sundae",
    112: "Bananas",
    113: "Grapes",
    114: "French fries",
    115: "Donut",
    116: "Meat on bone",
    117: "Burger",
    118: "Cheese / slice bonus",
    128: "Small banana",
    129: "Lemon",
    130: "Chili pepper",
    131: "Cup / drink",
    132: "Apple",
    133: "Watermelon slice",
    134: "White vegetable",
    135: "Pear",
    136: "Cherries",
    137: "Black grapes",
    138: "Pineapple",
    139: "Strawberry",
    140: "Dessert glass",
    141: "Lollipop",
    142: "Wrapped candy",
    143: "Wrapped candy",
    144: "Candy cane",
    145: "Tap",  # Code comment in blues: `num == 145 /* tap */`
    146: "Cake slice",
    147: "Popsicle",
    148: "Popsicle",
    149: "Brick / food square",
    150: "Ice cream cone",
    151: "Ice cream cone",
    152: "Ice cream cone",
    153: "Pudding",
    154: "Pastry",
    155: "Donut",
    156: "Small device bonus",
    158: "Weight",
    159: "Burger",
    160: "Burger",
    161: "Meat on bone",
    162: "Fish",
    163: "Fried egg",
    164: "Sausage",
    165: "Cheese / snack",
    166: "French fries",
    167: "Soda cup",
    168: "Drink can",
    169: "Hat",
    170: "Bomb",  # Code comment in blues.
    171: "Drink glass",
    172: "Cup",
    173: "Boot",
    174: "Special collectable",
    175: "Trophy",
    176: "Beer mug",
    181: "Light off pickup",  # Code comment in blues.
    182: "Light-related pickup",
    183: "Small gem",
    184: "Candelabra",
    185: "Ring",
    186: "Goblet",
    187: "Bag",
    188: "Sword",
    189: "Stairs / platform marker",
    190: "Treasure chest",
    191: "Fruit bowl",
    193: "Stone face",
    194: "Hammer",
    195: "Bottle",
    196: "Pencil",
    197: "Floppy disk",
    198: "Tongs / fork",
    199: "Bag / sack",
    200: "Joystick",
    201: "Card",
    202: "Pac-Man icon",
    203: "Potted flower",
    204: "Boot",
    205: "Tools",
    206: "Toy car",
    207: "Toy vehicle",
    208: "Bus",
    209: "Coin",
    210: "Knight figurine",
    211: "Gift",
    212: "Computer",
    213: "UFO",
    214: "Hourglass",
    215: "Heart card",
    216: "Diamond card",
    217: "Spade card",
    218: "Club card",
    219: "Playing card back",
    220: "Skull badge",
    222: "Grenade-like bonus",
    223: "Dynamite-like bonus",
    224: "Heart",  # Runtime treats this as a special pickup.
    226: "End-of-level semaphore",  # Code comment in blues.
    227: "1UP / extra life marker",
    228: "Checkpoint",  # Code comment in blues.
    233: "Sun on",
    234: "Sun off",
    235: "Hand / glove",
    241: "Letter token A",
    242: "Letter token B",
    243: "Letter token C",
    244: "Letter token D",
    245: "Letter token E",
    246: "Letter token F",
    247: "Letter token G",
    248: "Letter token H",
    249: "Letter token I",
    251: "Letter token K",
    252: "Letter token L",
    253: "Letter token M",
    254: "Letter token N",
    255: "Letter token O",
    256: "Letter token P",
    258: "Letter token R",
    260: "Letter token T",
    261: "Letter token U",
    263: "Letter token W",
    265: "Letter token Y",
    270: "Punctuation token !",
    271: "Punctuation token .",
    277: "Animated cutscene / sign object",
    278: "Level marker / traffic-light object",
    279: "Level marker / traffic-light object",
    281: "Password sign",
    282: "Password prompt text",
    283: "Password digit 0",
    284: "Password digit 1",
    285: "Password digit 2",
    286: "Password digit 3",
    287: "Password digit 4",
    289: "Password digit 6",
    290: "Password digit 7",
    291: "Password digit 8",
    293: "Password letter A",
    294: "Password letter B",
    295: "Password letter C",
    298: "Password letter F",
    311: "Hourglass checkpoint / timer object",
}

PLATFORM_VISUAL_NAMES: dict[int, str] = {
    230: "Small moving platform",
    232: "Moving platform",
    299: "Moving platform",
    304: "Moving platform",
    305: "Moving platform",
    307: "Moving platform",
}


# Global placement catalog for enemies.  These are runtime/global sprite IDs,
# not per-level raw record numbers.  The editor converts them back to the
# currently open level's raw monster sprite namespace when building a draft.
# The default behavior is only an initial draft choice; the placement inspector
# lets the user pick any known behavior before placing.
MONSTER_PLACEMENT_DEFAULT_BEHAVIOR: dict[int, int] = {
    313: 9,
    314: 9,
    317: 1,
    318: 2,
    319: 2,
    330: 5,
    331: 5,
    335: 9,
    336: 9,
    341: 8,
    342: 8,
    345: 8,
    349: 8,
    352: 0,
    355: 9,
    356: 9,
    359: 11,
    366: 3,
    370: 10,
    381: 10,
    382: 9,
    383: 9,
    386: 10,
    396: 8,
    397: 8,
    402: 7,
    440: 12,
    441: 9,
    453: 6,
    454: 6,
}

MONSTER_PLACEMENT_VISUALS: tuple[int, ...] = tuple(sorted(MONSTER_VISUAL_NAMES))

MONSTER_BEHAVIOR_VISUAL_OVERRIDES: dict[tuple[int, int], str] = {
    # Trigger-region spawners do not visually correspond to their base frame
    # labels in the raw sprite annotation table. These two are confirmed from
    # Level 1 gameplay/editor inspection.
    (381, 10): "Small cave beast spawner",
    (386, 10): "Large turtle spawner",
}


def monster_visual_name(sprite_num: int | None, movement_type: int | None = None) -> str:
    if sprite_num is None:
        return "Unknown enemy"
    if movement_type is not None:
        override = MONSTER_BEHAVIOR_VISUAL_OVERRIDES.get((sprite_num, movement_type))
        if override is not None:
            return override
    return MONSTER_VISUAL_NAMES.get(sprite_num, f"Unlabeled enemy visual {sprite_num}")


def monster_display_name(sprite_num: int | None, behavior_name: str, movement_type: int | None = None) -> str:
    visual = monster_visual_name(sprite_num, movement_type)
    return f"{visual} — {behavior_name}"


def item_visual_name(sprite_num: int | None) -> str:
    if sprite_num is None:
        return "Unknown item"
    return ITEM_VISUAL_NAMES.get(sprite_num, f"Unlabeled item visual {sprite_num}")


def platform_visual_name(sprite_num: int | None) -> str:
    if sprite_num is None:
        return "Unknown platform"
    return PLATFORM_VISUAL_NAMES.get(sprite_num, f"Unlabeled platform visual {sprite_num}")


PLATFORM_BEHAVIOR_NAMES: dict[int, str] = {
    0: "Vertical shuttle — starts upward",
    1: "Diagonal shuttle — up-right / down-left",
    2: "Horizontal shuttle — starts right",
    3: "Diagonal shuttle — down-right / up-left",
    4: "Vertical shuttle — starts downward",
    5: "Diagonal shuttle — down-left / up-right",
    6: "Horizontal shuttle — starts left",
    7: "Diagonal shuttle — up-left / down-right",
    8: "Falling platform — drops when ridden, then resets",
}


def platform_behavior_name(platform_type: int) -> str:
    return PLATFORM_BEHAVIOR_NAMES.get(platform_type, f"Unknown platform behavior {platform_type}")


def platform_display_name(sprite_num: int | None, platform_type: int) -> str:
    return f"{platform_visual_name(sprite_num)} — {platform_behavior_name(platform_type)}"

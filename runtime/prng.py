from __future__ import annotations


class Pre2Prng:
    """Original-looking PRE2 pseudo-random generator.

    This is a direct Python port of the small `Prng.cs` routine found in the
    uploaded C# project.  The four seed bytes also occur together in the
    unpacked PRE2 load module, so keep this isolated as runtime state rather
    than using Python's random module for gameplay decisions.
    """

    def __init__(self) -> None:
        self.tmp1 = 0x05
        self.tmp2 = 0x22
        self.tmp3 = 0x86
        self.tmp4 = 0xE58D
        # random_get_number2 state (blues g_vars.random.e; game_run seeds 0x1234).
        self.e = 0x1234

    def next_u16_2(self) -> int:
        """blues random_get_number2: e = ror16(e + 0x9248, 3)."""
        x = (self.e + 0x9248) & 0xFFFF
        self.e = ((x >> 3) | (x << 13)) & 0xFFFF
        return self.e

    def next_u8(self) -> int:
        self.tmp4 = (self.tmp4 + self.tmp1) & 0xFFFF

        self.tmp1 = (self.tmp1 + 3) & 0xFF
        self.tmp1 = (self.tmp1 + ((self.tmp4 & 0xFF00) >> 8)) & 0xFF

        self.tmp2 = (self.tmp2 + self.tmp3) & 0xFF
        self.tmp2 = (self.tmp2 + self.tmp2) & 0xFF
        self.tmp2 = (self.tmp2 + self.tmp1) & 0xFF

        self.tmp3 ^= self.tmp1
        self.tmp3 ^= self.tmp2
        self.tmp3 &= 0xFF

        return self.tmp2

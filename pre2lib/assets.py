from __future__ import annotations

from pathlib import Path


def find_data_file(directory: str | Path, filename: str) -> Path:
    """Return a game-data file path, accepting DOS uppercase names on case-sensitive FSes.

    The original distribution stores files as e.g. ``LEVEL1.SQZ``.  Older editor
    code used lowercase names, which works on Windows but not on Linux/macOS
    case-sensitive directories.  Keep the caller API simple and resolve the real
    path here.
    """
    base = Path(directory)
    direct = base / filename
    if direct.exists():
        return direct
    lower = filename.lower()
    upper = filename.upper()
    for candidate in (base / upper, base / lower):
        if candidate.exists():
            return candidate
    try:
        for child in base.iterdir():
            if child.name.lower() == lower:
                return child
    except FileNotFoundError:
        pass
    return direct

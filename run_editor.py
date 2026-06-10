"""Launcher for the Prehistorik 2 level editor.

The UI lives in :mod:`ui.app`; this file stays as the user-facing entry point:
    python gui.py [game_data_folder]
"""
from ui.app import main


if __name__ == "__main__":
    main()

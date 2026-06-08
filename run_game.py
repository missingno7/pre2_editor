"""Launcher for the Prehistorik 2 gameplay runtime reverse-engineering app.

Run with:
    python run_game.py [game_data_folder]
"""
from runtime.game import main


if __name__ == "__main__":
    raise SystemExit(main())

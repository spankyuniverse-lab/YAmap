"""Запуск через `python -m yamap`."""

import sys

from .cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОтменено пользователем.", file=sys.stderr)
        sys.exit(130)
